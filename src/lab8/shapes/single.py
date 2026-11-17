"""Shape B — one bounded ReAct agent with host-controlled refund.

The agent can only request read-only tools. The host alone validates and
commits a refund. All financial amounts are computed from trusted order
and current policy data, never from the customer's ticket.
"""

from __future__ import annotations

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.mock_llm import complete
from lab8.runtime import run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.trace import Trace


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    run_id = make_run_id("single", claim_id, seed)
    budget = Budget()
    trace = Trace(run_id)
    trace.add("start", {"shape": "single", "claim_id": claim_id, "seed": seed})

    # The runtime only retains the last tool response. Keep earlier read-only
    # results in this local closure, not in the trace (which must be redacted).
    state: dict = {
        "claim": None,
        "order": None,
        "policies": None,
        "refund_history": None,
        "quote": None,
    }

    def decide(observation: dict) -> dict:
        last_tool = observation.get("last_tool")
        result = observation.get("last_result") or {}

        if last_tool is None:
            return {"tool": "get_claim_view", "args": {"claim_id": claim_id}}

        if last_tool == "get_claim_view":
            if not result.get("found"):
                return {"final": {"status": "failed", "error_reason": "unknown_claim"}}
            state["claim"] = result
            if not result.get("order_id"):
                return {"final": {"status": "needs_info", "error_reason": "missing_order_id"}}
            return {
                "tool": "lookup_order",
                "args": {"order_id": result["order_id"], "principal_id": result["principal_id"]},
            }

        if last_tool == "lookup_order":
            if not result.get("visible"):
                return {
                    "final": {
                        "status": "escalated",
                        "error_reason": result.get("reason", "order_not_visible"),
                    }
                }
            state["order"] = result["order"]
            return {"tool": "lookup_policy", "args": {}}

        if last_tool == "lookup_policy":
            state["policies"] = result.get("policies", [])
            if not state["policies"]:
                return {"final": {"status": "failed", "error_reason": "no_current_policy"}}
            claim = state["claim"]
            return {
                "tool": "lookup_refund_history",
                "args": {"order_id": claim["order_id"], "principal_id": claim["principal_id"]},
            }

        if last_tool == "lookup_refund_history":
            if not result.get("visible"):
                return {
                    "final": {
                        "status": "escalated",
                        "error_reason": result.get("reason", "refund_history_not_visible"),
                    }
                }
            state["refund_history"] = result
            return {"tool": "compute_compensation", "args": {"order": state["order"]}}

        if last_tool == "compute_compensation":
            state["quote"] = result
            amount = int(result.get("amount_vnd", 0))
            # No write tool is ever exposed to the agent.
            return {
                "final": {
                    "status": "quote_ready" if amount > 0 else "denied",
                    "proposal_amount": amount,
                }
            }

        return {"final": {"status": "failed", "error_reason": "unexpected_tool"}}

    conn = connect(store_path)
    try:
        result = run_react(
            conn,
            budget,
            observation={"claim": {"claim_id": claim_id}},
            decide=decide,
            max_steps=8,
        )
        final = result["final"]
        status = final.get("status", "failed")
        proposal_amount = int(final.get("proposal_amount", 0))
        error_reason = final.get("error_reason")

        trace.add(
            "react",
            {
                "stopped": result["stopped"],
                "status": status,
                "tool_calls": budget.tool_calls,
                "proposal_amount": proposal_amount,
            },
        )

        if status == "quote_ready":
            # The host performs approval, per-order cap and idempotency checks.
            # Pass the FULL quote, not quote minus previous refunds.
            refund = issue_refund(
                conn,
                claim_id,
                proposal_amount,
                crash_at=crash_at,
            )
            if refund["ok"]:
                status = "resolved"
                error_reason = None
            elif refund.get("conflict") or refund.get("reason") == "amount_conflict":
                # A retry with a different amount must not overwrite a committed refund.
                status = "failed"
                error_reason = "amount_conflict"
            else:
                status = "denied"
                error_reason = refund.get("reason", "refund_denied")
            trace.add(
                "refund_result",
                {"ok": refund["ok"], "reason": refund["reason"]},
            )

        # Generate a deterministic customer-facing draft using the provided
        # MockLLM. It updates Budget's model calls, tokens and simulated cost.
        # Never use generated text to determine policy, quote or approval.
        # The prompt includes only non-sensitive run metadata, not raw ticket,
        # order rows, headers or private notes.
        draft = complete(
            budget,
            seed=seed,
            purpose="draft",
            prompt=(
                f"Draft a brief acknowledgement for claim {claim_id}. "
                f"Outcome: {status}. "
                f"Proposed refund amount (VND): {proposal_amount}."
            ),
        )
        trace.add("customer_draft", {"generated": bool(draft)})

        return finish(
            conn,
            shape="single",
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal_amount,
            budget=budget,
            trace=trace,
            error_reason=error_reason,
        )
    except InjectedCrash:
        # Deliberately propagate injected crashes for recovery testing.
        raise
    except Exception as exc:
        # Ordinary runtime failures should produce a trace and failed RunResult.
        # Do not log exception messages, which might contain sensitive data.
        trace.add("error", {"type": type(exc).__name__})
        return finish(
            conn,
            shape="single",
            claim_id=claim_id,
            seed=seed,
            status="failed",
            proposal_amount=int((state.get("quote") or {}).get("amount_vnd", 0)),
            budget=budget,
            trace=trace,
            error_reason="runtime_error",
        )
    finally:
        conn.close()
