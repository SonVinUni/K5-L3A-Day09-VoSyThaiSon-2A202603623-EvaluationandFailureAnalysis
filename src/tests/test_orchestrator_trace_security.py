"""Shape C trace contract and privacy checks; run with -m student -o addopts=."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from lab8.db import connect
from lab8.metrics import check_trace, trace_files
from lab8.schemas import RunResult, make_run_id
from lab8.shapes.orchestrator import run_claim


# These integration tests require a completed Shape C, like test_public_matrix.py.
pytestmark = pytest.mark.student


@pytest.fixture
def private_world(public_db, tmp_path, monkeypatch):
    """Use distinctive private values in a temporary DB, never in source fixtures."""
    monkeypatch.chdir(tmp_path)
    forbidden = {"CANARY_SECRET_", "CANARY_INTERNAL_"}
    conn = connect(public_db)
    try:
        orders = conn.execute("SELECT order_id FROM orders ORDER BY order_id").fetchall()
        for index, row in enumerate(orders):
            order_id = row["order_id"]
            private = (
                f"090900{index:04d}",
                f"PRIVATE_ADDRESS_{order_id}_Đường_Riêng",
                f"PRIVATE_INTERNAL_NOTE_{order_id}",
                f"PRIVATE_ITEM_NAME_{order_id}",
            )
            conn.execute(
                "UPDATE orders SET phone = ?, address = ?, internal_note = ?, item_name = ? "
                "WHERE order_id = ?",
                (*private, order_id),
            )
            forbidden.update(private)

        # A header value without the CANARY prefix catches raw header dumps too.
        header_token = "PRIVATE_AUTH_TOKEN_TRACE_C010"
        conn.execute(
            "UPDATE claims SET raw_headers_json = ? WHERE claim_id = ?",
            (json.dumps({"Authorization": f"Bearer {header_token}"}), "C-010"),
        )
        forbidden.add(header_token)
        for row in conn.execute("SELECT parent_secret FROM claims"):
            forbidden.add(row["parent_secret"])
        conn.commit()
    finally:
        conn.close()
    return public_db, forbidden


def _assert_private_trace(result: RunResult, started_at, forbidden, captured):
    """Check the public contract without requiring particular event names."""
    output = result.as_dict()
    valid, reason = check_trace(output, started_at=started_at)
    assert valid, f"Trace contract v1: {reason}"

    path = Path(result.trace_path)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(body, dict)
    assert body["run_id"] == result.run_id
    assert isinstance(body["events"], list)

    # Scan parsed JSON as well as raw text, so escaped Unicode cannot hide PII.
    surfaces = [
        ("RunResult", json.dumps(output, ensure_ascii=False)),
        ("trace JSON", json.dumps(body, ensure_ascii=False)),
        ("stdout", captured.out),
        ("stderr", captured.err),
    ]
    for artifact in trace_files(output):
        raw = artifact.read_text(encoding="utf-8")
        surfaces.append((artifact.name, raw))
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        surfaces.append((f"{artifact.name} JSON", json.dumps(parsed, ensure_ascii=False)))

    for label, text in surfaces:
        for private in sorted(forbidden):
            assert private not in text, f"Sensitive data leaked to {label}"


@pytest.mark.parametrize("seed", [11, 55])
@pytest.mark.parametrize(
    "claim_id, expected_status, missing_order",
    [
        pytest.param("C-001", "resolved", False, id="refund"),
        pytest.param("C-004", "needs_info", False, id="missing-order-id"),
        pytest.param("C-005", "escalated", False, id="outside-principal-scope"),
        pytest.param("C-007", "denied", False, id="approval-denied"),
        pytest.param("C-009", "denied", False, id="zero-quote"),
        pytest.param("C-010", "resolved", False, id="private-parent-and-order"),
        pytest.param("C-001", "escalated", True, id="order-not-found"),
        pytest.param("C-NOT-FOUND", "failed", False, id="claim-not-found"),
    ],
)
def test_orchestrator_trace_is_current_valid_and_private(
    private_world, capfd, claim_id, expected_status, missing_order, seed
):
    store, forbidden = private_world
    if missing_order:
        conn = connect(store)
        try:
            conn.execute(
                "UPDATE claims SET order_id = ? WHERE claim_id = ?",
                ("O-NOT-FOUND", claim_id),
            )
            conn.commit()
        finally:
            conn.close()

    started_at = time.time()
    result = run_claim(claim_id, seed=seed, store_path=str(store))
    _assert_private_trace(result, started_at, forbidden, capfd.readouterr())
    assert result.claim_id == claim_id
    assert result.shape == "orchestrator"
    assert result.run_id == make_run_id("orchestrator", claim_id, seed)
    # A silent stub must not count as proof of privacy on real execution paths.
    assert result.error_reason != "not_implemented", "Shape C is still a stub"
    assert result.status == expected_status


def test_orchestrator_retry_rewrites_stale_trace(private_world, capfd):
    store, forbidden = private_world
    started_at = time.time()
    first = run_claim("C-010", seed=11, store_path=str(store))
    _assert_private_trace(first, started_at, forbidden, capfd.readouterr())
    trace_path = Path(first.trace_path)
    old = time.time() - 3600
    os.utime(trace_path, (old, old))

    # Keep the old file in place: resume must write a fresh trace, not reuse it.
    started_at = time.time()
    second = run_claim("C-010", seed=11, store_path=str(store))
    _assert_private_trace(second, started_at, forbidden, capfd.readouterr())
    assert first.status == second.status == "resolved"
    assert first.run_id == second.run_id == make_run_id("orchestrator", "C-010", 11)
