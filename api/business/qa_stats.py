"""Statistical computations for QA / inter-rater reliability.

Provides:
  - Krippendorff's alpha (nominal, ordinal, set-valued)
  - Confusion matrix building
  - Per-code precision/recall for multi-label dimensions
"""
from __future__ import annotations

import json
from collections import defaultdict


# ── Distance functions ────────────────────────────────────────────────────────

def _jaccard_distance(a_str: str, b_str: str) -> float:
    """1 - |A∩B|/|A∪B| for JSON-encoded sets."""
    try:
        a = set(json.loads(a_str)) if a_str else set()
        b = set(json.loads(b_str)) if b_str else set()
    except (json.JSONDecodeError, TypeError):
        a = {a_str}
        b = {b_str}
    if not a and not b:
        return 0.0
    union = a | b
    return 1.0 - len(a & b) / len(union)


_ORDINAL_MAP = {"high": 3, "medium": 2, "low": 1}


def _ordinal_distance_sq(a_str: str, b_str: str) -> float:
    a_val = _ORDINAL_MAP.get(a_str.lower().strip(), 2) if a_str else 2
    b_val = _ORDINAL_MAP.get(b_str.lower().strip(), 2) if b_str else 2
    return float((a_val - b_val) ** 2)


def _nominal_distance(a: str, b: str) -> float:
    return 0.0 if a == b else 1.0


# ── Generic Krippendorff's Alpha ─────────────────────────────────────────────

def krippendorff_alpha(
    units: list[dict],
    distance_fn=None,
    value_type: str = "nominal",
) -> float | None:
    """Compute Krippendorff's alpha.

    Parameters
    ----------
    units      : list of {"unit": str, "rater": str|int, "value": str}
    distance_fn: callable(a, b) -> float — custom distance; chosen by value_type if None
    value_type : "nominal" | "ordinal" | "set"
    """
    if distance_fn is None:
        if value_type == "set":
            distance_fn = _jaccard_distance
        elif value_type == "ordinal":
            distance_fn = _ordinal_distance_sq
        else:
            distance_fn = _nominal_distance

    unit_ratings: dict[str, list[str]] = defaultdict(list)
    for item in units:
        unit_ratings[item["unit"]].append(item["value"])

    valid = {u: vals for u, vals in unit_ratings.items() if len(vals) >= 2}
    if not valid:
        return None

    do_sum = 0.0
    do_count = 0
    for vals in valid.values():
        m = len(vals)
        for i in range(m):
            for j in range(i + 1, m):
                do_sum += distance_fn(vals[i], vals[j])
                do_count += 1
    if do_count == 0:
        return None
    do = do_sum / do_count

    all_values = [v for vals in valid.values() for v in vals]
    n = len(all_values)
    if n < 2:
        return None

    de_sum = 0.0
    de_count = 0
    for i in range(n):
        for j in range(i + 1, n):
            de_sum += distance_fn(all_values[i], all_values[j])
            de_count += 1
    de = de_sum / de_count if de_count > 0 else 0.0

    if de == 0:
        return 1.0
    return 1.0 - (do / de)


def interpret_alpha(alpha: float | None) -> str:
    if alpha is None:
        return "insufficient data"
    if alpha >= 0.80:
        return "good agreement"
    if alpha >= 0.67:
        return "tentative agreement"
    return "needs improvement"


# ── Confusion Matrix (binary) ────────────────────────────────────────────────

def binary_confusion_matrix(pairs: list[tuple[str, str]]) -> dict:
    """2×2 confusion matrix from (llm_value, human_value) pairs."""
    def _to_bool(v: str) -> bool:
        return str(v).strip().lower() in ("1", "1.0", "yes", "true")

    tp = fp = fn = tn = 0
    for llm_val, human_val in pairs:
        pred, truth = _to_bool(llm_val), _to_bool(human_val)
        if pred and truth:
            tp += 1
        elif pred and not truth:
            fp += 1
        elif not pred and truth:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


# ── Multi-label per-code precision/recall ────────────────────────────────────

def multilabel_code_metrics(pairs: list[tuple[str, str]]) -> list[dict]:
    """Per-code precision/recall for multi-label dimensions.

    pairs: list of (llm_json_array_str, human_json_array_str).
    """
    def _parse(v: str) -> set:
        if not v:
            return set()
        try:
            arr = json.loads(v)
            if isinstance(arr, list):
                return {str(x).strip().lower() for x in arr if x}
            return {str(v).strip().lower()}
        except (json.JSONDecodeError, TypeError):
            return {str(v).strip().lower()} if v else set()

    all_codes: set[str] = set()
    parsed_pairs = []
    for llm_val, human_val in pairs:
        llm_set, human_set = _parse(llm_val), _parse(human_val)
        all_codes.update(llm_set)
        all_codes.update(human_set)
        parsed_pairs.append((llm_set, human_set))

    results = []
    for code in sorted(all_codes):
        tp = sum(1 for ls, hs in parsed_pairs if code in ls and code in hs)
        fp = sum(1 for ls, hs in parsed_pairs if code in ls and code not in hs)
        fn = sum(1 for ls, hs in parsed_pairs if code not in ls and code in hs)
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        results.append({
            "code": code, "tp": tp, "fp": fp, "fn": fn,
            "precision": round(prec, 4), "recall": round(rec, 4),
        })
    return results
