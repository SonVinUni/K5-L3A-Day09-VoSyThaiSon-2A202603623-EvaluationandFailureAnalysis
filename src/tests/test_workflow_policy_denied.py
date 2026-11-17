"""Workflow shape: host writes policy_denied when quote > 0 but approval is rejected.

Companion test for the group's workflow implementation (CP2/CP3). It pins the
business rule from docs/business_rules.md: a quote that the host refuses must
still leave a `policy_denied` audit and no ledger row — a shape that skips
`durability.issue_refund` cannot produce this.
"""

from __future__ import annotations

import pytest

from lab8.db import connect, init_store
from lab8.shapes import SHAPES
from tests.conftest import BY_ID, CLAIMS, WORLD


def _store(tmp_path, name: str):
    path = tmp_path / name
    init_store(path, WORLD, CLAIMS).close()
    return path


@pytest.mark.student
def test_workflow_denied_approval_records_policy_denied(tmp_path):
    expected = BY_ID["C-007"]["expected"]
    assert expected["policy_denied"] is True, "label must require a policy_denied audit"

    store = _store(tmp_path, "workflow-c007-test.sqlite")
    result = SHAPES["workflow"]("C-007", seed=11, store_path=str(store))
    conn = connect(store)
    try:
        assert result.status == "denied"
        assert result.proposal_amount == int(expected["proposal_amount"]), (
            "proposal_amount must stay the FULL quote even when the host denies"
        )
        assert result.refund_effects == []
        ledger_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = 'C-007'"
        ).fetchone()["n"]
        denied_audits = conn.execute(
            """
            SELECT COUNT(*) AS n FROM audit_events
            WHERE claim_id = 'C-007' AND event_type = 'policy_denied'
            """
        ).fetchone()["n"]
        committed = conn.execute(
            """
            SELECT COUNT(*) AS n FROM audit_events
            WHERE claim_id = 'C-007' AND event_type = 'refund_committed'
            """
        ).fetchone()["n"]
    finally:
        conn.close()
    assert ledger_rows == 0, "no ledger row when the host denies"
    assert denied_audits >= 1, "host must have written a policy_denied audit"
    assert committed == 0, "no refund_committed audit when nothing was paid"
