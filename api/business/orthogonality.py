"""Pairwise independence / association metrics for contingency tables (orthogonality analysis)."""
from __future__ import annotations

import math
from typing import Any

import numpy as np


def contingency_from_labeled_counts(
    rows: list[tuple[Any, Any, int]],
) -> tuple[list[str], list[str], list[list[float]]]:
    """Build matrix from (row_label, col_label, count) rows."""
    row_keys: list[str] = []
    col_keys: list[str] = []
    seen_r: dict[str, int] = {}
    seen_c: dict[str, int] = {}
    for a, b, _ in rows:
        sa = str(a) if a is not None else ""
        sb = str(b) if b is not None else ""
        if sa not in seen_r:
            seen_r[sa] = len(row_keys)
            row_keys.append(sa)
        if sb not in seen_c:
            seen_c[sb] = len(col_keys)
            col_keys.append(sb)
    r, c = len(row_keys), len(col_keys)
    mat = [[0.0] * c for _ in range(r)]
    for a, b, n in rows:
        sa = str(a) if a is not None else ""
        sb = str(b) if b is not None else ""
        mat[seen_r[sa]][seen_c[sb]] += float(n)
    return row_keys, col_keys, mat


def chi_square_cramers_residuals(
    matrix: list[list[float]],
) -> dict[str, Any]:
    """Pearson chi-square, p-value (approx), Cramér's V, standardized Pearson residuals."""
    r = len(matrix)
    c = len(matrix[0]) if r else 0
    if r < 2 or c < 2:
        return {
            "error": "Need at least 2 rows and 2 columns with data for this test.",
            "chi2": None,
            "p_value": None,
            "cramers_v": None,
            "df": None,
            "n": 0,
        }

    row_sums = [sum(matrix[i][j] for j in range(c)) for i in range(r)]
    col_sums = [sum(matrix[i][j] for i in range(r)) for j in range(c)]
    n = sum(row_sums)
    if n <= 0:
        return {"error": "Empty table", "chi2": None, "p_value": None, "cramers_v": None, "df": None, "n": 0}

    chi2 = 0.0
    expected: list[list[float]] = [[0.0] * c for _ in range(r)]
    for i in range(r):
        for j in range(c):
            e = row_sums[i] * col_sums[j] / n if n else 0.0
            expected[i][j] = e
            o = matrix[i][j]
            if e > 0:
                chi2 += (o - e) ** 2 / e

    df = (r - 1) * (c - 1)

    denom = min(max(r - 1, 1), max(c - 1, 1))
    cramers_v = None
    if n > 0 and denom > 0:
        cramers_v = math.sqrt(chi2 / (n * denom))

    residuals: list[list[float | None]] = [[None] * c for _ in range(r)]
    for i in range(r):
        for j in range(c):
            e = expected[i][j]
            o = matrix[i][j]
            if e > 1e-12:
                residuals[i][j] = (o - e) / math.sqrt(e)

    p_value = None
    if df > 0 and chi2 >= 0:
        p_value = max(0.0, min(1.0, chi2_survival(chi2, df)))

    return {
        "chi2": round(chi2, 6),
        "p_value": round(p_value, 6) if p_value is not None else None,
        "cramers_v": round(cramers_v, 6) if cramers_v is not None else None,
        "df": df,
        "n": int(n),
        "expected": expected,
        "standardized_residuals": residuals,
    }


def chi2_survival(x: float, k: int) -> float:
    """Approximate P(Chi2_k >= x) using Wilson–Hilferty transform."""
    if x <= 0 or k <= 0:
        return 1.0
    try:
        t = (x / k) ** (1 / 3)
    except ValueError:
        return 0.0
    mu = 1 - 2 / (9 * k)
    sigma = math.sqrt(2 / (9 * k))
    z = (t - mu) / sigma if sigma > 0 else 0.0
    return max(0.0, min(1.0, 1.0 - _phi(z)))


def _phi(z: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2)))


def _prune_zero_margin_matrix(matrix: list[list[float]]) -> list[list[float]]:
    """Drop rows and columns whose marginal sums are zero."""
    r = len(matrix)
    c = len(matrix[0]) if r else 0
    if r == 0 or c == 0:
        return matrix
    row_sums = [sum(matrix[i][j] for j in range(c)) for i in range(r)]
    col_sums = [sum(matrix[i][j] for i in range(r)) for j in range(c)]
    ri = [i for i in range(r) if row_sums[i] > 0]
    cj = [j for j in range(c) if col_sums[j] > 0]
    return [[matrix[i][j] for j in cj] for i in ri]


def cramers_v_multinomial_bootstrap_ci(
    matrix: list[list[float]],
    *,
    n_boot: int = 400,
    confidence: float = 0.95,
    random_state: int = 42,
    min_n: int = 10,
) -> tuple[float | None, float | None]:
    """Bootstrap percentile CI for Cramér's V by multinomial resampling of *n* individuals.

    Treats the contingency table as multinomial(n; p_ij) over non-empty cells; each bootstrap
    redraws the full table and recomputes V (after pruning empty margins).
    """
    base = chi_square_cramers_residuals(matrix)
    if base.get("error") or base.get("cramers_v") is None:
        return None, None
    n = int(base["n"])
    if n < min_n:
        return None, None

    r = len(matrix)
    c = len(matrix[0]) if r else 0
    flat_p: list[float] = []
    idx_map: list[tuple[int, int]] = []
    for i in range(r):
        for j in range(c):
            v = float(matrix[i][j])
            if v > 0.0:
                flat_p.append(v / n)
                idx_map.append((i, j))
    if len(flat_p) < 2:
        return None, None

    p = np.asarray(flat_p, dtype=float)
    p = p / p.sum()
    rng = np.random.default_rng(random_state)
    vals: list[float] = []
    for _ in range(n_boot):
        counts = rng.multinomial(n, p)
        mat = [[0.0] * c for _ in range(r)]
        for k, cnt in enumerate(counts):
            i, j = idx_map[k]
            mat[i][j] = float(cnt)
        mat_p = _prune_zero_margin_matrix(mat)
        if len(mat_p) < 2 or len(mat_p[0]) < 2:
            continue
        st = chi_square_cramers_residuals(mat_p)
        cv = st.get("cramers_v")
        if cv is not None:
            vals.append(float(cv))

    if len(vals) < max(40, n_boot // 5):
        return None, None

    q_lo = (1.0 - confidence) / 2.0 * 100.0
    q_hi = (1.0 + confidence) / 2.0 * 100.0
    lo = float(np.percentile(vals, q_lo))
    hi = float(np.percentile(vals, q_hi))
    return (round(lo, 4), round(hi, 4))


def chi_square_cramers_residuals_with_ci(
    matrix: list[list[float]],
    *,
    cramers_ci: bool = True,
    n_boot: int = 400,
    confidence: float = 0.95,
    random_state: int = 42,
) -> dict[str, Any]:
    """Like :func:`chi_square_cramers_residuals` plus optional bootstrap CI for Cramér's V."""
    stats = chi_square_cramers_residuals(matrix)
    if not cramers_ci or stats.get("error"):
        return stats
    lo, hi = cramers_v_multinomial_bootstrap_ci(
        matrix,
        n_boot=n_boot,
        confidence=confidence,
        random_state=random_state,
    )
    out = dict(stats)
    out["cramers_v_ci_low"] = lo
    out["cramers_v_ci_high"] = hi
    out["cramers_v_ci_level"] = confidence
    return out


def interpret_association(cramers_v: float | None, p_value: float | None) -> str:
    if cramers_v is None or p_value is None:
        return "Insufficient data for interpretation."
    if p_value > 0.05:
        return "No significant association at α=0.05 (dimensions appear closer to independent in this sample)."
    if cramers_v < 0.1:
        return "Significant but weak association (small effect size)."
    if cramers_v < 0.3:
        return "Significant association with moderate effect size."
    if cramers_v < 0.5:
        return "Strong association; dimensions are not orthogonal in this sample."
    return "Very strong association; dimensions are clearly not orthogonal in this sample."

