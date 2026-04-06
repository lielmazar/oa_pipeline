"""core/orthogonality.py — Taxonomy orthogonality analysis.

Computes:
  - Cramér's V between dimension pairs
  - Conditional entropy / mutual information
  - Within-dimension Jaccard similarity
  - Tautological pair detection
  - Pointwise mutual information (PMI)
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np


def _open_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_json_array(val: str | None) -> list[str]:
    if not val:
        return []
    try:
        arr = json.loads(val)
        if isinstance(arr, list):
            return [str(x).strip() for x in arr if x and str(x).strip().lower() not in ("none", "")]
        return [str(val).strip()] if val.strip() else []
    except (json.JSONDecodeError, TypeError):
        return [str(val).strip()] if val and val.strip() else []


def _get_dimension_values(db_path: Path, ml_only: bool = False):
    """Load per-paper dimension values. Returns list of dicts with parsed arrays."""
    conn = _open_ro(db_path)
    where = ""
    if ml_only:
        where = (
            "WHERE llm_ml_role IS NOT NULL AND llm_ml_role NOT IN ('[]','null','') "
            "AND LOWER(llm_ml_role) NOT LIKE '%\"none\"%'"
        )
    rows = conn.execute(f"""
        SELECT openalex_id, llm_fuel_classification, llm_ml_role,
               llm_ml_algorithm, llm_ml_data_source, llm_scope,
               matched_fuel_subgroups, matched_impact_subgroups,
               fwci, title, doi
        FROM filtered_records {where}
    """).fetchall()
    conn.close()

    papers = []
    for r in rows:
        papers.append({
            "id": r["openalex_id"],
            "fuel_class": _parse_json_array(r["llm_fuel_classification"]),
            "ml_role": _parse_json_array(r["llm_ml_role"]),
            "ml_algorithm": _parse_json_array(r["llm_ml_algorithm"]),
            "ml_data_source": r["llm_ml_data_source"] if r["llm_ml_data_source"] and r["llm_ml_data_source"].lower() not in ("none", "", "null") else None,
            "scope": _parse_json_array(r["llm_scope"]),
            "fuel_subgroups": _parse_json_array(r["matched_fuel_subgroups"]),
            "impact_subgroups": _parse_json_array(r["matched_impact_subgroups"]),
            "fwci": r["fwci"],
            "title": r["title"],
            "doi": r["doi"],
        })
    return papers


# ── Cramér's V ────────────────────────────────────────────────────────────────

def cramers_v_matrix(db_path: Path, ml_only: bool = False) -> dict:
    """Compute Cramér's V between all pairs of classification dimensions.

    Returns {"dimensions": [...], "matrix": [[v, ...], ...], "pvalues": [[p, ...], ...]}.
    """
    from scipy.stats import chi2_contingency

    papers = _get_dimension_values(db_path, ml_only)
    dims = ["fuel_class", "ml_role", "ml_algorithm", "ml_data_source"]
    dim_labels = ["Fuel Classification", "ML Role", "ML Algorithm", "ML Data Source"]

    # For scope if available
    if any(p["scope"] for p in papers):
        dims.append("scope")
        dim_labels.append("Value Chain")

    n = len(dims)
    v_matrix = [[0.0] * n for _ in range(n)]
    p_matrix = [[1.0] * n for _ in range(n)]

    for i in range(n):
        v_matrix[i][i] = 1.0
        p_matrix[i][i] = 0.0
        for j in range(i + 1, n):
            # Build contingency table from co-occurring labels
            counts: dict[tuple, int] = Counter()
            for p in papers:
                vals_i = p[dims[i]] if isinstance(p[dims[i]], list) else ([p[dims[i]]] if p[dims[i]] else [])
                vals_j = p[dims[j]] if isinstance(p[dims[j]], list) else ([p[dims[j]]] if p[dims[j]] else [])
                for vi in vals_i:
                    for vj in vals_j:
                        counts[(vi, vj)] += 1

            if not counts:
                continue

            labels_i = sorted(set(k[0] for k in counts))
            labels_j = sorted(set(k[1] for k in counts))
            if len(labels_i) < 2 or len(labels_j) < 2:
                continue

            table = np.zeros((len(labels_i), len(labels_j)))
            idx_i = {v: k for k, v in enumerate(labels_i)}
            idx_j = {v: k for k, v in enumerate(labels_j)}
            for (vi, vj), cnt in counts.items():
                table[idx_i[vi]][idx_j[vj]] = cnt

            try:
                chi2, pval, dof, _ = chi2_contingency(table)
                n_obs = table.sum()
                k = min(len(labels_i), len(labels_j))
                v = math.sqrt(chi2 / (n_obs * (k - 1))) if n_obs > 0 and k > 1 else 0
                v_matrix[i][j] = v_matrix[j][i] = round(v, 4)
                p_matrix[i][j] = p_matrix[j][i] = round(pval, 6)
            except Exception:
                pass

    return {"dimensions": dim_labels, "matrix": v_matrix, "pvalues": p_matrix}


# ── Conditional Entropy ───────────────────────────────────────────────────────

def conditional_entropy_analysis(db_path: Path, ml_only: bool = True) -> dict:
    """Compute H(ml_role), H(ml_role|fuel_class), MI, NMI, and per-fuel-class entropy."""
    from scipy.stats import entropy as sp_entropy

    papers = _get_dimension_values(db_path, ml_only)

    # Marginal H(ml_role)
    role_counts = Counter()
    total_roles = 0
    for p in papers:
        for r in p["ml_role"]:
            role_counts[r] += 1
            total_roles += 1

    if total_roles == 0:
        return {"h_role": 0, "h_role_given_fuel": 0, "mi": 0, "nmi": 0, "per_fuel": []}

    role_probs = np.array([c / total_roles for c in role_counts.values()])
    h_role = float(sp_entropy(role_probs, base=2))

    # H(fuel_class)
    fuel_counts = Counter()
    total_fuels = 0
    for p in papers:
        for f in p["fuel_class"]:
            fuel_counts[f] += 1
            total_fuels += 1
    fuel_probs = np.array([c / total_fuels for c in fuel_counts.values()]) if total_fuels > 0 else np.array([1.0])
    h_fuel = float(sp_entropy(fuel_probs, base=2))

    # H(ml_role | fuel_class)
    fuel_role_counts: dict[str, Counter] = defaultdict(Counter)
    fuel_totals: dict[str, int] = Counter()
    for p in papers:
        for f in p["fuel_class"]:
            for r in p["ml_role"]:
                fuel_role_counts[f][r] += 1
                fuel_totals[f] += 1

    h_cond = 0.0
    per_fuel = []
    for f in sorted(fuel_totals.keys(), key=lambda x: -fuel_totals[x]):
        n_f = fuel_totals[f]
        probs = np.array([c / n_f for c in fuel_role_counts[f].values()])
        h_f = float(sp_entropy(probs, base=2))
        per_fuel.append({"fuel_class": f, "count": n_f, "h_role": round(h_f, 4)})
        h_cond += (n_f / total_fuels) * h_f if total_fuels > 0 else 0

    mi = h_role - h_cond
    nmi = mi / h_role if h_role > 0 else 0.0

    return {
        "h_role": round(h_role, 4),
        "h_fuel": round(h_fuel, 4),
        "h_role_given_fuel": round(h_cond, 4),
        "mi": round(mi, 4),
        "nmi": round(nmi, 4),
        "uncertainty_reduction_pct": round(nmi * 100, 1),
        "per_fuel": per_fuel,
    }


# ── Within-dimension Jaccard ─────────────────────────────────────────────────

def jaccard_within_dimension(db_path: Path, dimension: str = "ml_role", ml_only: bool = True) -> dict:
    """Compute pairwise Jaccard similarity for codes within a dimension.

    Returns {"codes": [...], "matrix": [[j, ...], ...], "shared_papers": {code_a|code_b: [ids...]}}.
    """
    papers = _get_dimension_values(db_path, ml_only)

    dim_key = dimension
    code_papers: dict[str, set] = defaultdict(set)
    for p in papers:
        vals = p.get(dim_key, [])
        if isinstance(vals, list):
            for v in vals:
                code_papers[v].add(p["id"])
        elif vals:
            code_papers[vals].add(p["id"])

    codes = sorted(code_papers.keys())
    n = len(codes)
    matrix = [[0.0] * n for _ in range(n)]
    shared: dict[str, list] = {}

    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            a = code_papers[codes[i]]
            b = code_papers[codes[j]]
            union = a | b
            inter = a & b
            jacc = len(inter) / len(union) if union else 0.0
            matrix[i][j] = matrix[j][i] = round(jacc, 4)
            if inter:
                shared[f"{codes[i]}|{codes[j]}"] = list(inter)[:50]  # cap at 50

    return {"codes": codes, "matrix": matrix, "shared_papers": shared}


# ── Tautological Pair Detection ──────────────────────────────────────────────

def tautological_pairs(db_path: Path, ml_only: bool = True) -> dict:
    """Detect fuel_class → ml_role pairs with high overlap.

    Returns {"pairs": [{"fuel_class", "ml_role", "overlap_pct", "count", "flagged", "minority_examples": [...]}]}.
    """
    papers = _get_dimension_values(db_path, ml_only)

    fuel_role_counts: dict[str, Counter] = defaultdict(Counter)
    fuel_totals: dict[str, int] = Counter()
    fuel_papers: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for p in papers:
        for f in p["fuel_class"]:
            for r in p["ml_role"]:
                fuel_role_counts[f][r] += 1
                fuel_totals[f] += 1
                if len(fuel_papers[f][r]) < 10:
                    fuel_papers[f][r].append({
                        "id": p["id"], "title": p["title"], "doi": p["doi"],
                    })

    results = []
    for f in sorted(fuel_totals.keys(), key=lambda x: -fuel_totals[x]):
        if not fuel_role_counts[f]:
            continue
        top_role = fuel_role_counts[f].most_common(1)[0]
        overlap_pct = round(top_role[1] / fuel_totals[f] * 100, 1) if fuel_totals[f] > 0 else 0
        flagged = overlap_pct > 50

        # Find minority examples (papers with this fuel but DIFFERENT role)
        minority = []
        for role, examples in fuel_papers[f].items():
            if role != top_role[0]:
                minority.extend(examples[:5])
        minority = minority[:5]

        results.append({
            "fuel_class": f,
            "top_ml_role": top_role[0],
            "overlap_pct": overlap_pct,
            "count": top_role[1],
            "total": fuel_totals[f],
            "flagged": flagged,
            "minority_examples": minority,
        })

    return {"pairs": results}


# ── PMI Analysis ──────────────────────────────────────────────────────────────

def pmi_analysis(db_path: Path, ml_only: bool = True, min_count: int = 5) -> dict:
    """Compute PMI for all role × algorithm pairs.

    Returns {"pairs": [{"role", "algorithm", "pmi", "count", "flag"}]}.
    """
    papers = _get_dimension_values(db_path, ml_only)

    role_counts = Counter()
    algo_counts = Counter()
    pair_counts = Counter()
    total = 0

    for p in papers:
        for r in p["ml_role"]:
            for a in p["ml_algorithm"]:
                role_counts[r] += 1
                algo_counts[a] += 1
                pair_counts[(r, a)] += 1
                total += 1

    if total == 0:
        return {"pairs": [], "total": 0}

    results = []
    for (r, a), count in pair_counts.items():
        if count < min_count:
            continue
        p_pair = count / total
        p_role = role_counts[r] / total
        p_algo = algo_counts[a] / total
        if p_role == 0 or p_algo == 0:
            continue
        pmi = math.log2(p_pair / (p_role * p_algo))
        flag = "near-deterministic" if pmi > 3.0 else ("anti-specific" if pmi < -1.0 else "")
        results.append({
            "role": r, "algorithm": a,
            "pmi": round(pmi, 3), "count": count,
            "flag": flag,
        })

    results.sort(key=lambda x: -x["pmi"])
    return {"pairs": results, "total": total}


# ── Data Quality Audit ────────────────────────────────────────────────────────

def data_quality_audit(db_path: Path) -> dict:
    """Run data quality checks and return flagged issues."""
    conn = _open_ro(db_path)

    # Check columns exist
    cols = {r[1] for r in conn.execute("PRAGMA table_info(filtered_records)").fetchall()}

    results = {}

    # 1. ml_role != none but ml_algorithm == none
    if "llm_ml_role" in cols and "llm_ml_algorithm" in cols:
        row = conn.execute("""
            SELECT COUNT(*) AS cnt FROM filtered_records
            WHERE llm_ml_role IS NOT NULL AND llm_ml_role NOT IN ('[]','null','')
              AND LOWER(llm_ml_role) NOT LIKE '%"none"%'
              AND (llm_ml_algorithm IS NULL OR llm_ml_algorithm IN ('[]','null','')
                   OR LOWER(llm_ml_algorithm) LIKE '%"none"%')
        """).fetchone()
        results["role_without_algorithm"] = row["cnt"]

    # 2. is_hydrocarbon=0 but ml_role != none (scope leaks)
    if "llm_is_hydrocarbon" in cols and "llm_ml_role" in cols:
        row = conn.execute("""
            SELECT COUNT(*) AS cnt FROM filtered_records
            WHERE (llm_is_hydrocarbon = 0 OR llm_is_hydrocarbon = 0.0)
              AND llm_ml_role IS NOT NULL AND llm_ml_role NOT IN ('[]','null','')
              AND LOWER(llm_ml_role) NOT LIKE '%"none"%'
        """).fetchone()
        results["scope_leaks"] = row["cnt"]

    # 3. Confidence distribution
    if "llm_confidence" in cols:
        rows = conn.execute("""
            SELECT llm_confidence AS conf, COUNT(*) AS cnt FROM filtered_records
            WHERE llm_confidence IS NOT NULL
            GROUP BY llm_confidence
        """).fetchall()
        results["confidence_distribution"] = {r["conf"]: r["cnt"] for r in rows}

        # ML-positive confidence
        if "llm_ml_role" in cols:
            rows = conn.execute("""
                SELECT llm_confidence AS conf, COUNT(*) AS cnt FROM filtered_records
                WHERE llm_confidence IS NOT NULL
                  AND llm_ml_role IS NOT NULL AND llm_ml_role NOT IN ('[]','null','')
                  AND LOWER(llm_ml_role) NOT LIKE '%"none"%'
                GROUP BY llm_confidence
            """).fetchall()
            results["ml_positive_confidence"] = {r["conf"]: r["cnt"] for r in rows}

    # 4. Papers with 3+ ml_role labels
    if "llm_ml_role" in cols:
        row = conn.execute("""
            SELECT COUNT(*) AS cnt FROM filtered_records
            WHERE (SELECT COUNT(*) FROM json_each(llm_ml_role)) >= 3
              AND llm_ml_role IS NOT NULL AND llm_ml_role NOT IN ('[]','null','')
        """).fetchone()
        results["multi_role_papers"] = row["cnt"]

    # 5. Distribution of "other_ml"
    if "llm_ml_algorithm" in cols:
        row = conn.execute("""
            SELECT COUNT(DISTINCT openalex_id) AS cnt FROM filtered_records, json_each(llm_ml_algorithm)
            WHERE LOWER(value) = 'other_ml'
        """).fetchone()
        results["other_ml_count"] = row["cnt"]

    # 6. Distribution of "other" in fuel_classification
    if "llm_fuel_classification" in cols:
        row = conn.execute("""
            SELECT COUNT(DISTINCT openalex_id) AS cnt FROM filtered_records, json_each(llm_fuel_classification)
            WHERE LOWER(value) = 'other'
        """).fetchone()
        results["other_fuel_count"] = row["cnt"]

    # Total counts
    row = conn.execute("SELECT COUNT(*) AS cnt FROM filtered_records").fetchone()
    results["total_papers"] = row["cnt"]

    if "llm_ml_role" in cols:
        row = conn.execute("""
            SELECT COUNT(*) AS cnt FROM filtered_records
            WHERE llm_ml_role IS NOT NULL AND llm_ml_role NOT IN ('[]','null','')
              AND LOWER(llm_ml_role) NOT LIKE '%"none"%'
        """).fetchone()
        results["ml_positive_papers"] = row["cnt"]

    conn.close()
    return results


# ── Exemplar Paper Extraction ─────────────────────────────────────────────────

def exemplar_papers(db_path: Path, min_papers: int = 3) -> dict:
    """For each ml_role × fuel_classification cell with >= min_papers, pick the top paper by FWCI.

    Returns {"exemplars": [{"fuel_class", "ml_role", "cell_count", "title", "doi", "fwci", ...}]}.
    """
    papers = _get_dimension_values(db_path, ml_only=True)

    # Group by (fuel_class, ml_role) cells
    cells: dict[tuple, list] = defaultdict(list)
    for p in papers:
        for f in p["fuel_class"]:
            for r in p["ml_role"]:
                cells[(f, r)].append(p)

    results = []
    for (f, r), cell_papers in sorted(cells.items()):
        if len(cell_papers) < min_papers:
            continue
        # Pick top by FWCI
        best = max(cell_papers, key=lambda x: (x.get("fwci") or 0))
        results.append({
            "fuel_class": f,
            "ml_role": r,
            "cell_count": len(cell_papers),
            "title": best["title"],
            "doi": best["doi"],
            "fwci": best.get("fwci"),
            "id": best["id"],
        })

    results.sort(key=lambda x: (-x["cell_count"], x["fuel_class"], x["ml_role"]))
    return {"exemplars": results}
