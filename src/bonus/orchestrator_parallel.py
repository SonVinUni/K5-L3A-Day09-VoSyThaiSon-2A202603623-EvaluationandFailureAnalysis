"""Bonus B1: Orchestrator với Investigator chạy song song và mô hình Critical Path.
Theo nghiên cứu của Google Research 2026: 'Towards a Science of Scaling Agent Systems'.
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
from typing import Any

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import make_handoff
from lab8.mock_llm import complete
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import compute_compensation, lookup_order, lookup_policy, tool_cost


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, store_path=store_path, crash_at=crash_at)
    finally:
        conn.close()


def _worker_lookup_order(store_path: str, order_id: str | None, principal_id: str) -> dict[str, Any]:
    """Worker chạy trong thread riêng với connection SQLite độc lập."""
    c = connect(store_path)
    try:
        return lookup_order(c, order_id, principal_id)
    finally:
        c.close()


def _worker_lookup_policy(store_path: str) -> dict[str, Any]:
    """Worker chạy trong thread riêng với connection SQLite độc lập."""
    c = connect(store_path)
    try:
        return lookup_policy(c)
    finally:
        c.close()


def _run(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None,
) -> RunResult:
    budget = Budget()
    run_id = make_run_id("orchestrator_parallel", claim_id, seed)
    trace = _Trace(run_id)
    budget.add_tool(tool_cost())

    # 1. Đọc claim view từ SQLite
    claim_row = conn.execute(
        "SELECT claim_id, principal_id, order_id, ticket FROM claims WHERE claim_id = ?",
        (claim_id,),
    ).fetchone()

    if claim_row is None:
        trace.add("error", {"reason": "unknown_claim"})
        return finish(
            conn,
            shape="orchestrator_parallel",
            claim_id=claim_id,
            seed=seed,
            status="failed",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason="unknown_claim",
        )

    # 2. Xử lý thiếu order_id -> needs_info
    if not claim_row["order_id"]:
        complete(budget, seed=seed, purpose="draft", prompt="needs_info")
        trace.add("decision", {"status": "needs_info"})
        return finish(
            conn,
            shape="orchestrator_parallel",
            claim_id=claim_id,
            seed=seed,
            status="needs_info",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason="missing_order_id",
        )

    # 3. Dựng Handoff an toàn
    handoff = make_handoff(
        request_id=f"REQ-{claim_id}",
        claim_id=claim_id,
        task="check_applicable_policy_parallel",
        facts={},
        allowed_tools=["lookup_order", "lookup_policy", "compute_compensation"],
    )
    trace.add("handoff", safe_dump(handoff))

    # 4. CHẠY SONG SONG CÁC INVESTIGATOR ĐỘC LẬP BẰNG THREAD POOL
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_order = executor.submit(
            _worker_lookup_order, store_path, claim_row["order_id"], claim_row["principal_id"]
        )
        future_policy = executor.submit(_worker_lookup_policy, store_path)

        # Join theo thứ tự cố định để kết quả mang tính tất định (deterministic)
        order_view = future_order.result()
        policy_view = future_policy.result()

    budget.add_tool(tool_cost())
    budget.add_tool(tool_cost())

    trace.add(
        "investigator_parallel",
        {
            "lookup_order": order_view,
            "policy_count": len(policy_view.get("policies") or []),
            "execution": "concurrent_threads",
        },
    )

    # 5. Kiểm tra quyền sở hữu đơn hàng
    if not order_view.get("visible"):
        complete(budget, seed=seed, purpose="draft", prompt="escalated")
        return finish(
            conn,
            shape="orchestrator_parallel",
            claim_id=claim_id,
            seed=seed,
            status="escalated",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason=order_view.get("reason"),
        )

    # 6. Tính toán tiền bồi thường
    budget.add_tool(tool_cost())
    quote_payload = compute_compensation(conn, order_view["order"])
    proposal = int(quote_payload["amount_vnd"])
    trace.add("quote", quote_payload)
    complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")

    # 7. Tính toán mô hình Latency Critical Path (Amdahl's Law)
    t_order = 45
    t_policy = 35
    t_quote = 25
    t_claim = 20
    seq_latency = t_claim + t_order + t_policy + t_quote
    parallel_critical_path = t_claim + max(t_order, t_policy) + t_quote
    speedup = round(seq_latency / parallel_critical_path, 3)

    trace.add(
        "latency_model",
        {
            "sequential_ms": seq_latency,
            "critical_path_ms": parallel_critical_path,
            "speedup": speedup,
        },
    )

    # 8. Quote <= 0 -> denied
    if proposal <= 0:
        return finish(
            conn,
            shape="orchestrator_parallel",
            claim_id=claim_id,
            seed=seed,
            status="denied",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason=quote_payload.get("reason"),
        )

    # 9. Quote > 0 -> Gọi Host ghi sổ (Single writer duy nhất)
    try:
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise

    if not outcome["ok"]:
        status = "failed" if outcome["conflict"] else "denied"
        return finish(
            conn,
            shape="orchestrator_parallel",
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=outcome["reason"],
        )

    return finish(
        conn,
        shape="orchestrator_parallel",
        claim_id=claim_id,
        seed=seed,
        status="resolved",
        proposal_amount=proposal,
        budget=budget,
        trace=trace,
        error_reason=None,
    )


class _Trace:
    def __init__(self, run_id: str) -> None:
        from lab8.trace import Trace

        self._inner = Trace(run_id)

    def add(self, kind: str, payload: dict) -> None:
        self._inner.add(kind, payload)

    def write(self) -> str:
        return self._inner.write()

    @property
    def run_id(self) -> str:
        return self._inner.run_id