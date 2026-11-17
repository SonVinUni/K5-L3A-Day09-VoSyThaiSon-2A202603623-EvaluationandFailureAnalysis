"""Test đóng góp của Người 3 (Shape C Orchestrator)."""

from lab8.shapes.orchestrator import run_claim
from lab8.db import init_store, load_json, load_jsonl


def test_orchestrator_needs_info_on_missing_order(tmp_path):
    """Kiểm tra Orchestrator trả về needs_info khi claim thiếu order_id."""
    world = load_json("data/public_world_state.json")
    claims = load_jsonl("data/public_claims.jsonl")
    db_path = tmp_path / "test_n3.sqlite"
    init_store(db_path, world, claims)

    # Claim C-004 là claim thiếu order_id (T04)
    res = run_claim("C-004", seed=11, store_path=str(db_path))

    assert res.status == "needs_info"
    assert res.proposal_amount == 0
    assert res.error_reason == "missing_order_id"