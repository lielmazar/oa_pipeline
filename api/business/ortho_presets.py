"""Preset orthogonality pairs — aligned with heatmap chart definitions."""
from __future__ import annotations

from pathlib import Path

from api.business.orthogonality import (
    chi_square_cramers_residuals,
    contingency_from_labeled_counts,
    interpret_association,
)
from api.db.research import fetch_pairwise_contingency

ORTHO_PRESET_ROWS: list[dict] = [
    {
        "id": "fuel_mlrole",
        "a": "llm_fuel_classification",
        "b": "llm_ml_role",
        "json_x": True,
        "json_y": True,
        "chart_id": "fuelclass_x_mlrole",
        "label": "Fuel classification × ML role",
    },
    {
        "id": "mlrole_algo",
        "a": "llm_ml_role",
        "b": "llm_ml_algorithm",
        "json_x": True,
        "json_y": True,
        "chart_id": "mlrole_x_algorithm",
        "label": "ML role × algorithm",
    },
    {
        "id": "fuel_algo",
        "a": "llm_fuel_classification",
        "b": "llm_ml_algorithm",
        "json_x": True,
        "json_y": True,
        "chart_id": "fuelclass_x_algorithm",
        "label": "Fuel classification × algorithm",
    },
    {
        "id": "fuel_ds",
        "a": "llm_fuel_classification",
        "b": "llm_ml_data_source",
        "json_x": True,
        "json_y": False,
        "chart_id": "fuelclass_x_datasource",
        "label": "Fuel classification × ML data source",
    },
    {
        "id": "scope_mlrole",
        "a": "llm_scope",
        "b": "llm_ml_role",
        "json_x": True,
        "json_y": True,
        "chart_id": "scope_x_mlrole",
        "label": "Scope × ML role",
    },
    {
        "id": "scope_fuel",
        "a": "llm_scope",
        "b": "llm_fuel_classification",
        "json_x": True,
        "json_y": True,
        "chart_id": "scope_x_fuelclass",
        "label": "Scope × fuel classification",
    },
]


def compute_ortho_preset_overview(db_path: Path, subset_inner_sql: str | None) -> list[dict]:
    out: list[dict] = []
    for spec in ORTHO_PRESET_ROWS:
        raw = fetch_pairwise_contingency(
            db_path,
            subset_inner_sql,
            spec["a"],
            spec["b"],
            spec["json_x"],
            spec["json_y"],
        )
        if "error" in raw:
            out.append(
                {
                    **spec,
                    "error": raw["error"],
                    "stats": None,
                    "interpretation": None,
                }
            )
            continue
        rows = raw.get("rows") or []
        row_labels, col_labels, matrix = contingency_from_labeled_counts(rows)
        stats = chi_square_cramers_residuals(matrix)
        err_msg = stats.get("error")
        interp = err_msg or interpret_association(stats.get("cramers_v"), stats.get("p_value"))
        out.append(
            {
                **spec,
                "error": None,
                "stats": stats,
                "interpretation": interp,
                "row_labels": row_labels,
                "col_labels": col_labels,
            }
        )
    return out
