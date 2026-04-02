// ── Stage constants ───────────────────────────────────────────────────────────

const STAGE_IDS = [
  'keyword_selection', 'openalex_search', 'report', 'llm_classification', 'llm_report',
];

const STAGE_LABELS_MAP = {
  keyword_selection: '① Keywords',
  openalex_search:   '② Search',
  report:            '③ Report',
  llm_classification:'④ LLM',
  llm_report:        '⑤ LLM Report',
};

const STAGE_COLORS = {
  keyword_selection: 'bg-gray-200 text-gray-700',
  openalex_search:   'bg-blue-100 text-blue-800',
  report:            'bg-emerald-100 text-emerald-800',
  llm_classification:'bg-purple-100 text-purple-800',
  llm_report:        'bg-green-100 text-green-800',
};

// Chart color palette matching the Python report
const CHART_COLORS = [
  '#2563EB','#059669','#D97706','#DC2626','#7C3AED',
  '#0891B2','#65A30D','#C026D3','#EA580C','#4F46E5',
  '#0D9488','#CA8A04','#BE185D','#6D28D9','#0284C7',
];

// IDs in display order (non-LLM first, LLM-only after)
const CHART_ORDER = [
  'publications_by_year','top_fuel_terms','top_impact_terms',
  'fuel_impact_cooccurrence',
  'top_countries','top_journals','oa_status','work_types','top_institutions',
  'llm_scope','llm_ml_paradigm','llm_ml_usage',
  'fuel_impact_model_cooccurrence',
];

