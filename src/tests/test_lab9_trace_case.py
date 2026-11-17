"""Regression test for the trace case in docs/trace_case.md: C-007@11 seeded_regression.

First wrong step: the reply event claims "LogiX đã hoàn 35.000đ" although the host refused
the refund (expired approval A-007) -- the ledger holds 0 VND. The test is built from the
trial shape lab9.eval.run_one produces (hand-built, no shape run), so it fails on the
seeded_regression trial and passes on the fixed trial.
"""
import pytest

pytestmark = pytest.mark.student

LABEL = {"status": "denied", "proposal_amount": 35000, "refund_vnd": 0, "refund_count": 0,
         "audit_refund_committed": 0, "policy_denied": True, "approval_ok": False,
         "forbid_substrings": []}


@pytest.fixture(scope="module")
def graders():
    from lab9 import graders

    return graders


def ctx(trial, label=LABEL):
    from pydantic_evals.evaluators import EvaluatorContext

    return EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=label,
                            output=trial, duration=0.0, _span_tree=None, attributes={},
                            metrics={})


def trial(status, reply, refund_vnd=0):
    return {"result": {"claim_id": "C-007", "status": status, "proposal_amount": 35000},
            "snapshot": {"refund_vnd": refund_vnd, "refund_count": 0,
                          "committed_audit_count": 0, "policy_denied": status == "denied"},
            "unexpected_rows": 0, "blob": reply,
            "events": [{"kind": "reply", "payload": {"text": reply}}],
            "lab8_pass": status == "denied", "lab8_reason": None}


def test_reply_after_denied_refund_is_flagged(graders):
    """seeded_regression C-007@11: reply promises 35.000đ, ledger holds 0 -> FM-08 evidence."""
    t = ctx(trial("resolved", "Dạ, LogiX đã hoàn 35.000đ cho đơn O-909."))
    assert graders.ReplyLedgerMatch().evaluate(t).value is False
    assert graders.ApprovalBypass().evaluate(t).value is False   # no money moved: error is in the claim


def test_fixed_reply_after_denied_passes(graders):
    """fixed C-007@11: reply states no amount -> vacuously consistent with the empty ledger."""
    t = ctx(trial("denied", "Dạ, đơn O-909 chưa đủ điều kiện hoàn theo chính sách hiện hành."))
    assert graders.ReplyLedgerMatch().evaluate(t).value is True
