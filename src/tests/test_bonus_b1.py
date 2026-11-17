"""Test bộ Bonus B1: Orchestrator song song và Critical Path (Google Research 2026)."""

import json
from pathlib import Path

from bonus.orchestrator_parallel import run_claim as run_parallel
from lab8.db import init_store, load_json, load_jsonl
from lab8.shapes.orchestrator import run_claim as run_sequential


def test_parallel_matches_sequential_outcome_and_ledger(tmp_path):
    """Test 1: Chứng minh bản song song và tuần tự cho kết quả khớp nhau 100%."""
    world = load_json("data/public_world_state.json")
    claims = load_jsonl("data/public_claims.jsonl")

    # DB cho bản tuần tự
    db_seq = tmp_path / "seq.sqlite"
    init_store(db_seq, world, claims)
    res_seq = run_sequential("C-001", seed=11, store_path=str(db_seq))

    # DB cho bản song song
    db_par = tmp_path / "par.sqlite"
    init_store(db_par, world, claims)
    res_par = run_parallel("C-001", seed=11, store_path=str(db_par))

    # 1. Kết quả nghiệp vụ phải trùng khớp tuyệt đối
    assert res_par.status == res_seq.status == "resolved"
    assert res_par.proposal_amount == res_seq.proposal_amount == 35000
    assert len(res_par.refund_effects) == len(res_seq.refund_effects) == 1
    assert res_par.refund_effects[0]["amount_vnd"] == 35000

    # 2. Kiểm tra trace chứa mô hình Critical Path
    trace_data = json.loads(Path(res_par.trace_path).read_text(encoding="utf-8"))
    events = [e for e in trace_data["events"] if e["kind"] == "latency_model"]
    assert len(events) == 1
    latency = events[0]["payload"]
    assert latency["critical_path_ms"] < latency["sequential_ms"]
    assert latency["speedup"] > 1.0


def test_parallel_idempotency_and_no_thread_lock(tmp_path):
    """Test 2: Chứng minh chạy lại (retry) đạt tính lũy nghiệm và các thread không bị khóa DB."""
    world = load_json("data/public_world_state.json")
    claims = load_jsonl("data/public_claims.jsonl")
    db_path = tmp_path / "parallel_retry.sqlite"
    init_store(db_path, world, claims)

    # Chạy lần 1
    res1 = run_parallel("C-001", seed=22, store_path=str(db_path))
    assert res1.status == "resolved"
    assert res1.committed_audit_count == 1

    # Chạy lại lần 2 trên cùng file DB -> Vẫn an toàn, không bị duplicate và không bị database lock
    res2 = run_parallel("C-001", seed=22, store_path=str(db_path))
    assert res2.status == "resolved"
    assert res2.committed_audit_count == 1
    assert len(res2.refund_effects) == 1
