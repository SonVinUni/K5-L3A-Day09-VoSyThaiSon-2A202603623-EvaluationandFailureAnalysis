"""Statistics students implement (M2 judge calibration, M4 paired bootstrap).

Rules: a judge is a classifier -- measure it like one. A higher mean is not
"better" until the paired CI of the difference excludes 0.
Reference values to self-check are in tests/test_lab9_stats.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import comb

POS = "FAIL"   # the judge's job is to FIND failures, so FAIL is the positive class


def confusion(human: Sequence[str], judge: Sequence[str]) -> dict[str, int]:
    """tp/fp/fn/tn with FAIL as positive."""
    tp = sum(h == POS and j == POS for h, j in zip(human, judge))
    fp = sum(h != POS and j == POS for h, j in zip(human, judge))
    fn = sum(h == POS and j != POS for h, j in zip(human, judge))
    tn = sum(h != POS and j != POS for h, j in zip(human, judge))
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def precision_recall(human: Sequence[str], judge: Sequence[str]) -> tuple[float, float]:
    cm = confusion(human, judge)
    p = cm["tp"] / (cm["tp"] + cm["fp"]) if cm["tp"] + cm["fp"] else 0.0
    r = cm["tp"] / (cm["tp"] + cm["fn"]) if cm["tp"] + cm["fn"] else 0.0
    return p, r


def cohen_kappa(human: Sequence[str], judge: Sequence[str]) -> float:
    """kappa = (p_o - p_e) / (1 - p_e). Raise ValueError when undefined (p_e == 1):
    a constant judge has no signal and must FAIL calibration, not pass on a technicality."""
    n = len(human)
    if n == 0:
        raise ValueError("kappa undefined: no labels")
    if len(judge) != n:
        raise ValueError("human and judge label counts differ")
    p_o = sum(h == j for h, j in zip(human, judge)) / n
    pos_h = sum(h == POS for h in human) / n
    pos_j = sum(j == POS for j in judge) / n
    p_e = pos_h * pos_j + (1 - pos_h) * (1 - pos_j)
    if p_e == 1.0:
        raise ValueError("kappa undefined: labels are constant, judge has no signal")
    return (p_o - p_e) / (1.0 - p_e)


def rogan_gladen(observed_rate: float, sensitivity: float, specificity: float) -> float:
    """Correct an observed failure rate for judge error; clip to [0, 1]."""
    corrected = (observed_rate + specificity - 1.0) / (sensitivity + specificity - 1.0)
    return max(0.0, min(1.0, corrected))


def paired_bootstrap(a: Sequence[float], b: Sequence[float], *, n_resamples: int = 1000,
                     rng: int = 2027, confidence: float = 0.95) -> tuple[float, float, float]:
    """a[i], b[i] = per-item mean over seeds, SAME items, SAME order.
    Return (mean(a)-mean(b), lo, hi) with scipy.stats.bootstrap(paired=True, method="percentile",
    rng=numpy.random.default_rng(rng)). If a == b everywhere return (0.0, 0.0, 0.0)."""
    import numpy
    from scipy.stats import bootstrap

    a = list(a)
    b = list(b)
    delta = float(numpy.mean(a) - numpy.mean(b))
    if all(x == y for x, y in zip(a, b)):
        return (0.0, 0.0, 0.0)
    res = bootstrap((a, b), statistic=lambda x, y: float(numpy.mean(x) - numpy.mean(y)),
                    paired=True, method="percentile",
                    n_resamples=n_resamples, confidence_level=confidence,
                    random_state=numpy.random.default_rng(rng))
    return (delta, float(res.confidence_interval.low), float(res.confidence_interval.high))


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that AT LEAST ONE of k trials drawn from n (c of them passing) passes.
    Unbiased estimator (Chen et al. 2021): 1 - C(n-c, k) / C(n, k). Raise ValueError if k > n."""
    if not (0 <= k <= n) or k == 0:
        raise ValueError(f"need 0 < k <= n (got n={n}, c={c}, k={k})")
    if not (0 <= c <= n):
        raise ValueError(f"need 0 <= c <= n (got n={n}, c={c}, k={k})")
    return 1.0 - comb(n - c, k) / comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """pass^k (tau-bench, Yao et al. 2024): probability that ALL k trials drawn from n pass.
    Unbiased estimator: C(c, k) / C(n, k). Raise ValueError if k > n.
    pass^k <= pass@1 <= pass@k; a reliable agent keeps pass^k close to pass@1."""
    if not (0 <= k <= n) or k == 0:
        raise ValueError(f"need 0 < k <= n (got n={n}, c={c}, k={k})")
    if not (0 <= c <= n):
        raise ValueError(f"need 0 <= c <= n (got n={n}, c={c}, k={k})")
    return comb(c, k) / comb(n, k)
