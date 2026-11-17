"""Shape C must resume on the same database without a second refund or audit."""

from __future__ import annotations

import json

import pytest

from lab8.db import connect
from lab8.durability import InjectedCrash
from lab8.shapes.orchestrator import run_claim


# A completed Shape C is required; keep these out of the default host gate.
pytestmark = pytest.mark.student

CLAIM_ID = "C-012"
REFUND_VND = 35000


def _snapshot(store):
    """Read committed state through a new connection, as a restarted process would."""
    conn = connect(store)
    try:
        refunds = [
            dict(row)
            for row in conn.execute("SELECT * FROM refund_ledger ORDER BY claim_id")
        ]
        audits = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM audit_events WHERE event_type = 'refund_committed' "
                "ORDER BY audit_key"
            )
        ]
        checkpoint = conn.execute(
            "SELECT result_json FROM checkpoints WHERE claim_id = ? AND step = 'issue_refund'",
            (CLAIM_ID,),
        ).fetchone()
        return {
            "refunds": refunds,
            "audits": audits,
            "checkpoint": json.loads(checkpoint["result_json"]) if checkpoint else None,
        }
    finally:
        conn.close()


def _assert_single_refund(state, baseline, *, committed, checkpoint):
    own_refunds = [row for row in state["refunds"] if row["claim_id"] == CLAIM_ID]
    own_audits = [row for row in state["audits"] if row["claim_id"] == CLAIM_ID]
    assert len(own_refunds) == int(committed)
    assert len(own_audits) == int(committed)
    assert sum(row["amount_vnd"] for row in own_refunds) == (REFUND_VND if committed else 0)

    # Seeded refunds must survive; no new payment may appear under another claim.
    assert [row for row in state["refunds"] if row["claim_id"] != CLAIM_ID] == baseline["refunds"]
    assert [row for row in state["audits"] if row["claim_id"] != CLAIM_ID] == baseline["audits"]
    if committed:
        assert own_refunds[0]["order_id"] == "O-107"
        assert own_refunds[0]["idempotency_key"] == f"refund:{CLAIM_ID}"
        assert own_refunds[0]["status"] == "committed"
        assert json.loads(own_audits[0]["details_json"])["amount_vnd"] == REFUND_VND
    assert (state["checkpoint"] is not None) == checkpoint
    if checkpoint:
        assert state["checkpoint"]["claim_id"] == CLAIM_ID
        assert state["checkpoint"]["amount_vnd"] == REFUND_VND


@pytest.mark.parametrize("seed", [11, 55])
@pytest.mark.parametrize(
    "crash_at, committed_at_crash, checkpoint_at_crash",
    [
        pytest.param("before_effect", False, False, id="before-refund"),
        pytest.param("after_effect_before_checkpoint", True, False, id="refund-before-checkpoint"),
        pytest.param("after_checkpoint", True, True, id="after-checkpoint"),
    ],
)
def test_orchestrator_crash_then_resume_refunds_exactly_once(
    public_db, tmp_path, monkeypatch, seed, crash_at, committed_at_crash, checkpoint_at_crash
):
    monkeypatch.chdir(tmp_path)
    baseline = _snapshot(public_db)
    assert not any(row["claim_id"] == CLAIM_ID for row in baseline["refunds"])
    assert not any(row["claim_id"] == CLAIM_ID for row in baseline["audits"])
    assert baseline["checkpoint"] is None

    # Ignoring crash_at or swallowing InjectedCrash must fail, even with one refund.
    with pytest.raises(InjectedCrash, match=f"^{crash_at}$"):
        run_claim(CLAIM_ID, seed=seed, store_path=str(public_db), crash_at=crash_at)

    _assert_single_refund(
        _snapshot(public_db),
        baseline,
        committed=committed_at_crash,
        checkpoint=checkpoint_at_crash,
    )

    # Resume the SAME database without crash injection, then retry once more.
    resumed_state = None
    for _ in range(2):
        result = run_claim(CLAIM_ID, seed=seed, store_path=str(public_db))
        state = _snapshot(public_db)
        _assert_single_refund(state, baseline, committed=True, checkpoint=True)
        assert result.claim_id == CLAIM_ID
        assert result.status == "resolved"
        assert result.proposal_amount == REFUND_VND
        assert len(result.refund_effects) == 1
        assert result.refund_effects[0]["claim_id"] == CLAIM_ID
        assert result.refund_effects[0]["amount_vnd"] == REFUND_VND
        assert result.committed_audit_count == 1
        if resumed_state is not None:
            assert state == resumed_state, "Retry changed the committed refund or audit"
        resumed_state = state
