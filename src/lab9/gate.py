"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item


def _pairs(rep: dict) -> set[tuple[str, int]]:
    return {(r["item_id"], r["seed"]) for r in rep["rows"]}


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    import math

    problems: list[str] = []
    pm = pr.get("manifest", {})

    # 1. NaN / inf metric values: a value that compares as "not below the floor" by accident.
    for m, v in (pr.get("metrics") or {}).items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            problems.append(f"metric {m} is {v!r} (NaN/inf): a crashed grader silently scored 'missing'")

    # 2. row count: manifest says items x seeds, rows must carry exactly that many DISTINCT pairs.
    manifest_rows = int(pm.get("items") or 0) * int(len(pm.get("seeds") or []))
    pairs = _pairs(pr)
    if manifest_rows and len(pr["rows"]) != manifest_rows:
        problems.append(
            f"rows carry {len(pr['rows'])} entries but manifest claims {pm.get('items')} items x "
            f"{len(pm.get('seeds', []))} seeds = {manifest_rows}: dropped or duplicated rows"
        )
    if manifest_rows and len(pairs) != len(pr["rows"]):
        problems.append("duplicate (item_id, seed) pairs among rows")
    declared = {(r["item_id"], s) for r in pr["rows"] for s in pm.get("seeds") or []}
    if declared and pairs != declared:
        missing = sorted(s for s in (pm.get("seeds") or []) if not any(r["seed"] == s for r in pr["rows"]))
        problems.append(
            f"manifest declares seeds {sorted(pm.get('seeds') or [])} but rows only cover "
            f"{sorted({r['seed'] for r in pr['rows']})} (missing {missing}): cherry-picked seeds"
        )

    # 3. the compared reports must cover the same trials.
    if _pairs(pr) != _pairs(base):
        problems.append(
            "(item_id, seed) set of the PR differs from the baseline's: cannot pair a trial "
            "that does not exist on one side"
        )

    # 4. broken graders must not become a report at all.
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s) recorded in the PR")
    graded = [m["id"] for m in spec.get("metrics", []) if m["id"] in (pr["rows"][0] if pr["rows"] else {})]
    for r in pr["rows"]:
        bad = [m for m in graded if r.get(m) is None]
        if bad:
            problems.append(f"row {r['item_id']}@{r['seed']} has None for graded metric(s) {bad} (crashed grader)")
            break

    # 5. relabelling an item out of a slice hides its regression from the slice gate.
    base_tags = {r["item_id"]: set(r.get("slices") or []) for r in base["rows"]}
    for r in pr["rows"]:
        want = base_tags.get(r["item_id"])
        if want is not None and set(r.get("slices") or []) != want:
            problems.append(
                f"item {r['item_id']} carries slice tags {sorted(r.get('slices') or [])}, "
                f"baseline has {sorted(want)}: relabelling out of a slice is not allowed"
            )
            break

    # 6. ratchet: on the same eval-set version the spec may only get stricter.
    if base_spec is not None:
        same_set = spec.get("eval_set_version") == base_spec.get("eval_set_version")
        if same_set:
            pr_metrics = {m["id"]: m for m in spec.get("metrics", [])}
            for bm in base_spec.get("metrics", []):
                bth = bm.get("threshold") or {}
                if bm.get("blocking") and bm["id"] not in pr_metrics:
                    problems.append(f"metric {bm['id']} was blocking on main and disappeared from the spec")
                    continue
                pm2 = pr_metrics.get(bm["id"], {})
                pth = pm2.get("threshold") or {}
                if "min" in bth and "min" not in pth:
                    problems.append(f"metric {bm['id']} dropped its min floor {bth['min']}")
                if "min" in bth and "min" in pth and pth["min"] < bth["min"]:
                    problems.append(f"metric {bm['id']} min loosened {bth['min']} -> {pth['min']}")
                if "max" in bth and "max" not in pth:
                    problems.append(f"metric {bm['id']} dropped its max cap {bth['max']}")
                if "max" in bth and "max" in pth and pth["max"] > bth["max"]:
                    problems.append(f"metric {bm['id']} max loosened {bth['max']} -> {pth['max']}")
                if "max_increase" in bth and pth.get("max_increase", 0) > bth["max_increase"]:
                    problems.append(f"metric {bm['id']} max_increase loosened {bth['max_increase']} -> {pth.get('max_increase')}")
                if bth.get("no_significant_drop") and not pth.get("no_significant_drop"):
                    problems.append(f"metric {bm['id']} dropped no_significant_drop")
                if bm.get("blocking") and not pm2.get("blocking"):
                    problems.append(f"metric {bm['id']} was blocking on main and is no longer")
            base_slices = {s["name"]: s for s in base_spec.get("slices") or []}
            pr_slices = {s["name"]: s for s in spec.get("slices") or []}
            for name, bs in base_slices.items():
                ps = pr_slices.get(name)
                if ps is None:
                    problems.append(f"slice {name} was defined on main and disappeared")
                    continue
                if "min" in bs and "min" not in ps:
                    problems.append(f"slice {name} dropped its min floor {bs['min']}")
                if "min" in bs and "min" in ps and ps["min"] < bs["min"]:
                    problems.append(f"slice {name} min loosened {bs['min']} -> {ps['min']}")
                if bs.get("no_significant_drop") and not ps.get("no_significant_drop"):
                    problems.append(f"slice {name} dropped no_significant_drop")
                if bs.get("blocking") and not ps.get("blocking"):
                    problems.append(f"slice {name} was blocking and is no longer")
    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})
    for s in spec.get("slices") or []:
        name, mid = s["name"], s["metric"]
        sl = f"slice:{name}/{mid}"
        a = per_item(pr, mid, name)
        b = per_item(base, mid, name)
        if not a:
            lines.append(f"  {sl:<26} no rows in slice {name!r}")
            continue
        value = sum(a.values()) / len(a)
        note = f"value {value:.3f}"
        fail = False
        if "min" in s and value < s["min"]:
            fail = True
            note += f" < min {s['min']}"
        if s.get("no_significant_drop"):
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note += f"; Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                fail = True
        label = "BLOCK" if (fail and s.get("blocking")) else ("fail(warn)" if fail else "ok")
        lines.append(f"  {sl:<26} {note:<44} {label}")
        if fail and s.get("blocking"):
            blocked.append(sl)
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
