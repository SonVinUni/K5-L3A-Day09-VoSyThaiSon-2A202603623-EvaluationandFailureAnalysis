import json
from pathlib import Path

from lab8.db import connect, init_store, load_json, load_jsonl
from lab8.shapes.single import run_claim


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def make_store(tmp_path):
    """Build a fresh isolated SQLite database for each test."""
    world = load_json(DATA_DIR / "public_world_state.json")
    claims = load_jsonl(DATA_DIR / "public_claims.jsonl")
    store = tmp_path / "single_agent_test.sqlite"
    conn = init_store(store, world, claims)
    conn.close()
    return store


def test_single_agent_resolves_claim_and_tracks_budget(tmp_path, monkeypatch):
    """C-001: correct quote/refund, bounded read tools, model usage and safe trace."""
    monkeypatch.chdir(tmp_path)  # Keep generated trace inside this test's temp folder.
    store = make_store(tmp_path)

    result = run_claim("C-001", seed=11, store_path=str(store))

    assert result.shape == "single"
    assert result.status == "resolved"
    assert result.proposal_amount == 35_000
    assert result.tool_calls == 5
    assert result.tool_calls <= 8
    assert result.model_calls >= 1
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.cost_estimate > 0
    assert result.committed_audit_count == 1
    assert len(result.refund_effects) == 1
    assert result.refund_effects[0]["amount_vnd"] == 35_000

    trace_path = Path(result.trace_path)
    assert trace_path.is_file()
    content = trace_path.read_text(encoding="utf-8")
    trace = json.loads(content)
    assert trace["run_id"] == result.run_id
    assert isinstance(trace["events"], list)
    assert trace["events"]
    for forbidden in (
        "CANARY_SECRET_", "parent_secret", "raw_headers",
        "internal_note", "phone", "address", "item_name",
    ):
        assert forbidden not in content


def test_single_agent_retry_does_not_duplicate_refund(tmp_path, monkeypatch):
    """A retry of C-001 against the SAME store must not commit a second refund."""
    monkeypatch.chdir(tmp_path)
    store = make_store(tmp_path)

    first = run_claim("C-001", seed=11, store_path=str(store))
    second = run_claim("C-001", seed=11, store_path=str(store))

    assert first.status == "resolved"
    assert second.status == "resolved"
    assert second.committed_audit_count == 1

    conn = connect(store)
    try:
        ledger_count = conn.execute(
            "SELECT COUNT(*) FROM refund_ledger WHERE claim_id = ?", ("C-001",)
        ).fetchone()[0]
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE claim_id = ? AND event_type = 'refund_committed'", ("C-001",)
        ).fetchone()[0]
    finally:
        conn.close()

    assert ledger_count == 1
    assert audit_count == 1
