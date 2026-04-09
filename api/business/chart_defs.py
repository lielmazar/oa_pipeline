"""
Chart query definitions — single source of truth for chart SQL.

No Flask, no matplotlib.  The API serves these configs as JSON; the
frontend (React) owns rendering.  The frontend uses the SQL templates
to query its locally-loaded copy of the pipeline research DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ChartRenderer = Literal["bar", "hbar", "pie", "cooccurrence_3d_bar", "cooccurrence_3d", "heatmap"]


@dataclass(frozen=True)
class ChartDefinition:
    """Declarative chart specification independent of rendering implementation."""

    id: str
    title: str
    sql_template: str
    renderer: ChartRenderer
    x_key: str
    y_key: str
    llm_only: bool = False
    params: tuple[str, ...] = ()
    subgroup_key: str | None = None
    default_top_n: int | None = None

    def format_sql(self, top_n: int = 20, cutoff_year: int = 2000) -> str:
        return (
            self.sql_template
            .replace("{top_n}", str(top_n))
            .replace("{cutoff_year}", str(cutoff_year))
        )


_CHART_DEFINITIONS: dict[str, ChartDefinition] = {
    "publications_by_year": ChartDefinition(
        id="publications_by_year",
        title="Publications by Year",
        sql_template=(
            "SELECT "
            "  CASE WHEN publication_year IS NULL OR publication_year <= {cutoff_year} "
            "       THEN 'Unknown/≤{cutoff_year}' "
            "       ELSE CAST(publication_year AS TEXT) END AS year, "
            "  COUNT(*) AS count "
            "FROM filtered_records "
            "GROUP BY 1 "
            "ORDER BY MIN(CASE WHEN publication_year IS NULL OR publication_year <= {cutoff_year} "
            "             THEN 0 ELSE publication_year END)"
        ),
        renderer="bar", x_key="year", y_key="count",
        params=("cutoff_year",),
    ),
    "top_countries": ChartDefinition(
        id="top_countries",
        title="Top Contributing Countries",
        sql_template=(
            "SELECT value AS country, COUNT(*) AS count "
            "FROM filtered_records, json_each(authors_countries) "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        renderer="hbar", x_key="country", y_key="count",
        params=("top_n",),
    ),
    "top_journals": ChartDefinition(
        id="top_journals",
        title="Top Journals / Sources",
        sql_template=(
            "SELECT primary_location_source_display_name AS journal, COUNT(*) AS count "
            "FROM filtered_records "
            "WHERE primary_location_source_display_name IS NOT NULL "
            "GROUP BY journal ORDER BY count DESC LIMIT {top_n}"
        ),
        renderer="hbar", x_key="journal", y_key="count",
        params=("top_n",),
    ),
    "oa_status": ChartDefinition(
        id="oa_status",
        title="Open Access Status",
        sql_template=(
            "SELECT COALESCE(open_access_oa_status, 'unknown') AS status, COUNT(*) AS count "
            "FROM filtered_records GROUP BY 1 ORDER BY count DESC"
        ),
        renderer="pie", x_key="status", y_key="count",
    ),
    "work_types": ChartDefinition(
        id="work_types",
        title="Work Types",
        sql_template=(
            "SELECT COALESCE(type, 'unknown') AS type, COUNT(*) AS count "
            "FROM filtered_records GROUP BY 1 ORDER BY count DESC LIMIT 10"
        ),
        renderer="bar", x_key="type", y_key="count",
    ),
    "top_institutions": ChartDefinition(
        id="top_institutions",
        title="Top Institutions",
        sql_template=(
            "SELECT value AS institution, COUNT(*) AS count "
            "FROM filtered_records, json_each(authors_institutions) "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        renderer="hbar", x_key="institution", y_key="count",
        params=("top_n",),
    ),
    "llm_scope": ChartDefinition(
        id="llm_scope",
        title="Fuel Classification (LLM)",
        sql_template=(
            "SELECT value AS fuel_type, COUNT(*) AS count "
            "FROM filtered_records, json_each(llm_fuel_classification) "
            "WHERE value IS NOT NULL AND value != '' "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        renderer="hbar", x_key="fuel_type", y_key="count",
        llm_only=True,
        params=("top_n",),
    ),
    "llm_ml_role": ChartDefinition(
        id="llm_ml_role",
        title="ML/AI Functional Role (LLM)",
        sql_template=(
            "SELECT value AS role, COUNT(*) AS count "
            "FROM filtered_records, json_each(llm_ml_role) "
            "WHERE value IS NOT NULL AND value != '' AND LOWER(value) != 'none' "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        renderer="hbar", x_key="role", y_key="count",
        llm_only=True,
        params=("top_n",),
    ),
    "llm_ml_algorithm": ChartDefinition(
        id="llm_ml_algorithm",
        title="ML/AI Algorithm Family (LLM)",
        sql_template=(
            "SELECT value AS algorithm, COUNT(*) AS count "
            "FROM filtered_records, json_each(llm_ml_algorithm) "
            "WHERE value IS NOT NULL AND value != '' "
            "  AND LOWER(value) NOT IN ('none', 'other_ml') "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        renderer="hbar", x_key="algorithm", y_key="count",
        llm_only=True,
        params=("top_n",),
    ),
    "llm_ml_usage": ChartDefinition(
        id="llm_ml_usage",
        title="ML/AI Usage Rate (LLM)",
        sql_template=(
            "SELECT "
            "  CASE WHEN llm_ml_role IS NOT NULL "
            "            AND llm_ml_role NOT IN ('[]', 'null', '') "
            "            AND LOWER(llm_ml_role) NOT LIKE '%\"none\"%' "
            "       THEN 'Uses ML/AI' ELSE 'No ML/AI' END AS uses_ml, "
            "  COUNT(*) AS count "
            "FROM filtered_records GROUP BY 1"
        ),
        renderer="pie", x_key="uses_ml", y_key="count",
        llm_only=True,
    ),
    "fuel_impact_model_cooccurrence": ChartDefinition(
        id="fuel_impact_model_cooccurrence",
        title="Fuel × Impact × Model Type Co-occurrence",
        sql_template=(
            "SELECT DISTINCT openalex_id, "
            "  REPLACE(fs.value, 'Feedstocks and Processed', 'Feedstocks and Processes') AS fuel_subgroup, "
            "  REPLACE(isg.value, 'Feedstocks and Processed', 'Feedstocks and Processes') AS impact_subgroup, "
            "  json_extract(m.value, '$.type') AS model_type, "
            "  json_extract(m.value, '$.name') AS model_name "
            "FROM filtered_records, "
            "  json_each(matched_fuel_subgroups) fs, "
            "  json_each(matched_impact_subgroups) isg, "
            "  json_each(llm_models) m "
            "WHERE matched_fuel_subgroups IS NOT NULL AND matched_fuel_subgroups != '[]' "
            "  AND matched_impact_subgroups IS NOT NULL AND matched_impact_subgroups != '[]' "
            "  AND llm_models IS NOT NULL AND llm_models != '[]' AND llm_models != 'null' "
            "  AND json_valid(llm_models) "
            "  AND json_extract(m.value, '$.type') IS NOT NULL "
        ),
        renderer="cooccurrence_3d", x_key="fuel_subgroup", y_key="impact_subgroup",
        llm_only=True,
    ),
    "fuel_subgroup_ml_cooccurrence": ChartDefinition(
        id="fuel_subgroup_ml_cooccurrence",
        title="Fuel Subgroup × Impact Subgroup (ML/AI Papers Only)",
        sql_template=(
            "SELECT DISTINCT openalex_id, "
            "  REPLACE(fs.value, 'Feedstocks and Processed', 'Feedstocks and Processes') AS fuel_subgroup, "
            "  REPLACE(isg.value, 'Feedstocks and Processed', 'Feedstocks and Processes') AS impact_subgroup "
            "FROM filtered_records, "
            "  json_each(matched_fuel_subgroups) fs, json_each(matched_impact_subgroups) isg "
            "WHERE matched_fuel_subgroups IS NOT NULL AND matched_fuel_subgroups != '[]' "
            "  AND matched_impact_subgroups IS NOT NULL AND matched_impact_subgroups != '[]' "
            "  AND llm_ml_role IS NOT NULL AND llm_ml_role NOT IN ('[]', 'null', '') "
            "  AND LOWER(llm_ml_role) NOT LIKE '%\"none\"%' "
        ),
        renderer="cooccurrence_3d_bar", x_key="fuel_subgroup", y_key="openalex_id",
        llm_only=True,
    ),
    "fuelclass_x_mlrole": ChartDefinition(
        id="fuelclass_x_mlrole",
        title="Fuel Classification × ML Role",
        sql_template=(
            "SELECT f.value AS x_label, r.value AS y_label, "
            "  COUNT(DISTINCT openalex_id) AS count "
            "FROM filtered_records, "
            "  json_each(llm_fuel_classification) f, json_each(llm_ml_role) r "
            "WHERE LOWER(f.value) NOT IN ('none', '') AND f.value IS NOT NULL "
            "  AND LOWER(r.value) NOT IN ('none', '') AND r.value IS NOT NULL "
            "GROUP BY f.value, r.value"
        ),
        renderer="heatmap", x_key="x_label", y_key="y_label",
        llm_only=True,
    ),
    "mlrole_x_algorithm": ChartDefinition(
        id="mlrole_x_algorithm",
        title="ML Role × Algorithm Family",
        sql_template=(
            "SELECT r.value AS x_label, a.value AS y_label, "
            "  COUNT(DISTINCT openalex_id) AS count "
            "FROM filtered_records, "
            "  json_each(llm_ml_role) r, json_each(llm_ml_algorithm) a "
            "WHERE LOWER(r.value) NOT IN ('none', '') AND r.value IS NOT NULL "
            "  AND LOWER(a.value) NOT IN ('none', 'other_ml', '') AND a.value IS NOT NULL "
            "GROUP BY r.value, a.value"
        ),
        renderer="heatmap", x_key="x_label", y_key="y_label",
        llm_only=True,
    ),
    "fuelclass_x_algorithm": ChartDefinition(
        id="fuelclass_x_algorithm",
        title="Fuel Classification × Algorithm Family",
        sql_template=(
            "SELECT f.value AS x_label, a.value AS y_label, "
            "  COUNT(DISTINCT openalex_id) AS count "
            "FROM filtered_records, "
            "  json_each(llm_fuel_classification) f, json_each(llm_ml_algorithm) a "
            "WHERE LOWER(f.value) NOT IN ('none', '') AND f.value IS NOT NULL "
            "  AND LOWER(a.value) NOT IN ('none', 'other_ml', '') AND a.value IS NOT NULL "
            "GROUP BY f.value, a.value"
        ),
        renderer="heatmap", x_key="x_label", y_key="y_label",
        llm_only=True,
    ),
    "fuelclass_x_datasource": ChartDefinition(
        id="fuelclass_x_datasource",
        title="Fuel Classification × ML Data Source",
        sql_template=(
            "SELECT f.value AS x_label, "
            "  CASE WHEN llm_ml_data_source IS NULL OR llm_ml_data_source IN ('', 'none', 'null') "
            "       THEN 'none' ELSE llm_ml_data_source END AS y_label, "
            "  COUNT(DISTINCT openalex_id) AS count "
            "FROM filtered_records, json_each(llm_fuel_classification) f "
            "WHERE LOWER(f.value) NOT IN ('none', '') AND f.value IS NOT NULL "
            "  AND llm_ml_data_source IS NOT NULL "
            "  AND LOWER(llm_ml_data_source) NOT IN ('none', '', 'null') "
            "GROUP BY f.value, y_label"
        ),
        renderer="heatmap", x_key="x_label", y_key="y_label",
        llm_only=True,
    ),
    "scope_x_mlrole": ChartDefinition(
        id="scope_x_mlrole",
        title="Value-Chain Scope × ML Role",
        sql_template=(
            "SELECT s.value AS x_label, r.value AS y_label, "
            "  COUNT(DISTINCT openalex_id) AS count "
            "FROM filtered_records, "
            "  json_each(llm_scope) s, json_each(llm_ml_role) r "
            "WHERE LOWER(s.value) NOT IN ('none', '') AND s.value IS NOT NULL "
            "  AND LOWER(r.value) NOT IN ('none', '') AND r.value IS NOT NULL "
            "GROUP BY s.value, r.value"
        ),
        renderer="heatmap", x_key="x_label", y_key="y_label",
        llm_only=True,
    ),
    "scope_x_fuelclass": ChartDefinition(
        id="scope_x_fuelclass",
        title="Value-Chain Scope × Fuel Classification",
        sql_template=(
            "SELECT s.value AS x_label, f.value AS y_label, "
            "  COUNT(DISTINCT openalex_id) AS count "
            "FROM filtered_records, "
            "  json_each(llm_scope) s, json_each(llm_fuel_classification) f "
            "WHERE LOWER(s.value) NOT IN ('none', '') AND s.value IS NOT NULL "
            "  AND LOWER(f.value) NOT IN ('none', '') AND f.value IS NOT NULL "
            "GROUP BY s.value, f.value"
        ),
        renderer="heatmap", x_key="x_label", y_key="y_label",
        llm_only=True,
    ),
    "llm_ml_datasource": ChartDefinition(
        id="llm_ml_datasource",
        title="ML Data Source Distribution (LLM)",
        sql_template=(
            "SELECT CASE WHEN llm_ml_data_source IS NULL OR llm_ml_data_source IN ('', 'none', 'null') "
            "            THEN 'none' ELSE llm_ml_data_source END AS source, "
            "  COUNT(*) AS count "
            "FROM filtered_records "
            "WHERE llm_ml_role IS NOT NULL AND llm_ml_role NOT IN ('[]', 'null', '') "
            "  AND LOWER(llm_ml_role) NOT LIKE '%\"none\"%' "
            "GROUP BY 1 ORDER BY count DESC"
        ),
        renderer="pie", x_key="source", y_key="count",
        llm_only=True,
    ),
    "llm_value_chain": ChartDefinition(
        id="llm_value_chain",
        title="Value-Chain Position (LLM)",
        sql_template=(
            "SELECT value AS stage, COUNT(*) AS count "
            "FROM filtered_records, json_each(llm_scope) "
            "WHERE value IS NOT NULL AND value != '' AND LOWER(value) != 'none' "
            "GROUP BY value ORDER BY count DESC LIMIT {top_n}"
        ),
        renderer="hbar", x_key="stage", y_key="count",
        llm_only=True,
        params=("top_n",),
    ),
}


def get_chart_definition(chart_id: str) -> ChartDefinition | None:
    return _CHART_DEFINITIONS.get(chart_id)


def has_chart(chart_id: str) -> bool:
    return chart_id in _CHART_DEFINITIONS


def get_chart_configs(top_n: int = 20, cutoff_year: int = 2000) -> list[dict]:
    """Return all chart configs with SQL interpolated — sent to the frontend as-is."""
    configs: list[dict] = []
    for cid, spec in _CHART_DEFINITIONS.items():
        entry = {
            "id": cid,
            "title": spec.title,
            "sql": spec.format_sql(top_n=top_n, cutoff_year=cutoff_year),
            "renderer": spec.renderer,
            "x_key": spec.x_key,
            "y_key": spec.y_key,
            "llm_only": spec.llm_only,
            "params": list(spec.params),
        }
        if spec.subgroup_key:
            entry["subgroup_key"] = spec.subgroup_key
        configs.append(entry)
    return configs
