/** Build subset-scoped drilldown SQL (SELECT * FROM _fr_subset WHERE …) for chart clicks. */

export function escSql(s) {
  return `'${String(s ?? '').replace(/'/g, "''")}'`;
}

function heatmapPairSql(chartId, x, y) {
  const pairs = {
    fuelclass_x_mlrole: ['llm_fuel_classification', 'llm_ml_role'],
    mlrole_x_algorithm: ['llm_ml_role', 'llm_ml_algorithm'],
    fuelclass_x_algorithm: ['llm_fuel_classification', 'llm_ml_algorithm'],
    scope_x_mlrole: ['llm_scope', 'llm_ml_role'],
    scope_x_fuelclass: ['llm_scope', 'llm_fuel_classification'],
  };
  const cols = pairs[chartId];
  if (!cols) return null;
  const [colA, colB] = cols;
  return (
    `SELECT * FROM _fr_subset WHERE EXISTS (SELECT 1 FROM json_each(${colA}) a WHERE a.value = ${escSql(x)}) `
    + `AND EXISTS (SELECT 1 FROM json_each(${colB}) b WHERE b.value = ${escSql(y)})`
  );
}

function usesMlSql(label) {
  const s = String(label ?? '');
  if (s.includes('Uses')) {
    return (
      'llm_ml_role IS NOT NULL AND llm_ml_role NOT IN (\'[]\', \'null\', \'\') '
      + 'AND LOWER(llm_ml_role) NOT LIKE \'%"none"%\''
    );
  }
  return (
    '(llm_ml_role IS NULL OR llm_ml_role IN (\'[]\', \'null\', \'\') '
    + 'OR LOWER(llm_ml_role) LIKE \'%"none"%\')'
  );
}

/**
 * @param {{ chartId: string, row: unknown[], cutoffYear?: number }} opts
 * @returns {string | null}
 */
export function buildChartDrilldownSql({ chartId, row, cutoffYear = 2000 }) {
  if (!chartId || !row?.length) return null;
  const r0 = row[0];
  const r1 = row[1];

  switch (chartId) {
    case 'publications_by_year': {
      const label = String(r0 ?? '');
      if (label.startsWith('Unknown') || label.includes('≤')) {
        return `SELECT * FROM _fr_subset WHERE (publication_year IS NULL OR publication_year <= ${cutoffYear})`;
      }
      const y = parseInt(label, 10);
      if (!Number.isNaN(y)) {
        return `SELECT * FROM _fr_subset WHERE publication_year = ${y}`;
      }
      return `SELECT * FROM _fr_subset WHERE CAST(publication_year AS TEXT) = ${escSql(label)}`;
    }
    case 'work_types':
      return `SELECT * FROM _fr_subset WHERE COALESCE(type, 'unknown') = ${escSql(r0)}`;
    case 'oa_status':
      return `SELECT * FROM _fr_subset WHERE COALESCE(open_access_oa_status, 'unknown') = ${escSql(r0)}`;
    case 'top_countries':
      return `SELECT * FROM _fr_subset WHERE EXISTS (SELECT 1 FROM json_each(authors_countries) j WHERE j.value = ${escSql(r0)})`;
    case 'top_journals':
      return `SELECT * FROM _fr_subset WHERE primary_location_source_display_name = ${escSql(r0)}`;
    case 'top_institutions':
      return `SELECT * FROM _fr_subset WHERE EXISTS (SELECT 1 FROM json_each(authors_institutions) j WHERE j.value = ${escSql(r0)})`;
    case 'llm_scope':
      return `SELECT * FROM _fr_subset WHERE EXISTS (SELECT 1 FROM json_each(llm_fuel_classification) j WHERE j.value = ${escSql(r0)})`;
    case 'llm_value_chain':
      return `SELECT * FROM _fr_subset WHERE EXISTS (SELECT 1 FROM json_each(llm_scope) j WHERE j.value = ${escSql(r0)})`;
    case 'llm_ml_role':
      return `SELECT * FROM _fr_subset WHERE EXISTS (SELECT 1 FROM json_each(llm_ml_role) j WHERE j.value = ${escSql(r0)})`;
    case 'llm_ml_algorithm':
      return `SELECT * FROM _fr_subset WHERE EXISTS (SELECT 1 FROM json_each(llm_ml_algorithm) j WHERE j.value = ${escSql(r0)})`;
    case 'llm_ml_usage':
      return `SELECT * FROM _fr_subset WHERE (${usesMlSql(r0)})`;
    case 'llm_ml_datasource': {
      const src = String(r0 ?? '');
      if (src === 'none') {
        return (
          'SELECT * FROM _fr_subset WHERE (llm_ml_data_source IS NULL OR llm_ml_data_source IN (\'\', \'none\', \'null\') '
          + 'OR LOWER(llm_ml_data_source) = \'none\')'
        );
      }
      return `SELECT * FROM _fr_subset WHERE llm_ml_data_source = ${escSql(src)}`;
    }
    case 'fuelclass_x_mlrole':
    case 'mlrole_x_algorithm':
    case 'fuelclass_x_algorithm':
    case 'scope_x_mlrole':
    case 'scope_x_fuelclass':
      return heatmapPairSql(chartId, r0, r1);
    case 'fuelclass_x_datasource': {
      const y = String(r1 ?? '');
      const yClause = y === 'none'
        ? '(llm_ml_data_source IS NULL OR llm_ml_data_source IN (\'\', \'none\', \'null\') OR LOWER(llm_ml_data_source) = \'none\')'
        : `llm_ml_data_source = ${escSql(y)}`;
      return (
        `SELECT * FROM _fr_subset WHERE EXISTS (SELECT 1 FROM json_each(llm_fuel_classification) f WHERE f.value = ${escSql(r0)}) `
        + `AND ${yClause}`
      );
    }
    default:
      return null;
  }
}
