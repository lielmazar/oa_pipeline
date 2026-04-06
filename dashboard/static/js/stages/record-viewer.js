// ── RecordViewerPanel helpers (pure, outside component) ──────────────────────

function _rvBuildWhere(fs) {
  const parts = [];
  if (fs.textSearch?.trim()) {
    const q = fs.textSearch.trim().replace(/'/g, "''");
    parts.push(`(title LIKE '%${q}%' OR abstract_text LIKE '%${q}%')`);
  }
  if (fs.yearFrom && !isNaN(parseInt(fs.yearFrom,10))) parts.push(`publication_year >= ${parseInt(fs.yearFrom,10)}`);
  if (fs.yearTo   && !isNaN(parseInt(fs.yearTo,10)))   parts.push(`publication_year <= ${parseInt(fs.yearTo,10)}`);
  if (fs.oaStatus === 'open_any') {
    parts.push(`(open_access_is_oa = 1 OR open_access_any_repository_has_fulltext = 1)`);
  } else if (fs.oaStatus) {
    parts.push(`open_access_oa_status = '${fs.oaStatus.replace(/'/g,"''")}'`);
  }
  if (fs.recordType) parts.push(`type = '${fs.recordType.replace(/'/g,"''")}'`);
  if (fs.publisherFilter?.trim()) {
    const p = fs.publisherFilter.trim().replace(/'/g, "''");
    parts.push(`primary_location_source_host_organization_name LIKE '%${p}%'`);
  }
  if (fs.sourceType) parts.push(`primary_location_source_type = '${fs.sourceType.replace(/'/g,"''")}'`);
  // Fuel subgroup: prefer DB column (matched_fuel_subgroups), fall back to terms list
  if (fs.fuelSubgroupName) {
    const sgName = fs.fuelSubgroupName.replace(/'/g, "''");
    parts.push(
      `(EXISTS (SELECT 1 FROM json_each(matched_fuel_subgroups) WHERE value = '${sgName}')` +
      ` OR EXISTS (SELECT 1 FROM json_each(matched_fuel_terms) WHERE value IN (${(fs.fuelSubgroupTerms||[]).map(t=>`'${t.replace(/'/g,"''")}'`).join(',')||"''"})))`
    );
  }
  // Specific fuel term within a subgroup
  if (fs.fuelTermName) {
    const t = fs.fuelTermName.replace(/'/g, "''");
    parts.push(`EXISTS (SELECT 1 FROM json_each(matched_fuel_terms) WHERE LOWER(value) = LOWER('${t}'))`);
  }
  // Impact subgroup: same dual approach
  if (fs.impactSubgroupName) {
    const sgName = fs.impactSubgroupName.replace(/'/g, "''");
    parts.push(
      `(EXISTS (SELECT 1 FROM json_each(matched_impact_subgroups) WHERE value = '${sgName}')` +
      ` OR EXISTS (SELECT 1 FROM json_each(matched_impact_terms) WHERE value IN (${(fs.impactSubgroupTerms||[]).map(t=>`'${t.replace(/'/g,"''")}'`).join(',')||"''"})))`
    );
  }
  // Specific impact term within a subgroup
  if (fs.impactTermName) {
    const t = fs.impactTermName.replace(/'/g, "''");
    parts.push(`EXISTS (SELECT 1 FROM json_each(matched_impact_terms) WHERE LOWER(value) = LOWER('${t}'))`);
  }
  // LLM field filters
  if (fs.llmConfidence) {
    parts.push(`llm_confidence = '${fs.llmConfidence.replace(/'/g, "''")}'`);
  }
  if (fs.llmMlRole) {
    const v = fs.llmMlRole.replace(/'/g, "''");
    parts.push(`EXISTS (SELECT 1 FROM json_each(llm_ml_role) WHERE LOWER(value) = LOWER('${v}'))`);
  }
  if (fs.llmMlAlgorithm) {
    const v = fs.llmMlAlgorithm.replace(/'/g, "''");
    parts.push(`EXISTS (SELECT 1 FROM json_each(llm_ml_algorithm) WHERE LOWER(value) = LOWER('${v}'))`);
  }
  if (fs.llmFuelClassification) {
    const v = fs.llmFuelClassification.replace(/'/g, "''");
    parts.push(`EXISTS (SELECT 1 FROM json_each(llm_fuel_classification) WHERE LOWER(value) = LOWER('${v}'))`);
  }
  if (fs.llmScope) {
    const v = fs.llmScope.replace(/'/g, "''");
    parts.push(`EXISTS (SELECT 1 FROM json_each(llm_scope) WHERE LOWER(value) = LOWER('${v}'))`);
  }
  if (fs.llmDataSource) {
    const v = fs.llmDataSource.replace(/'/g, "''");
    parts.push(`llm_ml_data_source = '${v}'`);
  }
  if (fs.llmIsHydrocarbon !== '') {
    if (fs.llmIsHydrocarbon === '1') parts.push(`llm_is_hydrocarbon = 1`);
    else if (fs.llmIsHydrocarbon === '0') parts.push(`(llm_is_hydrocarbon = 0 OR llm_is_hydrocarbon IS NULL)`);
  }
  return parts;
}

// Build SQL for saving a subset (base query + WHERE conditions, no ORDER BY)
function _rvSubsetSql(base, where) {
  const clean = base.trim().replace(/;+$/, '');
  let s = `SELECT * FROM (${clean}) _q`;
  if (where.length) s += ` WHERE ${where.join(' AND ')}`;
  return s;
}
function _rvPageSql(base, where, sortBy, sortDir, page, perPage) {
  const clean = base.trim().replace(/;+$/,'');
  let sql = `SELECT * FROM (${clean}) _q`;
  if (where.length) sql += ` WHERE ${where.join(' AND ')}`;
  if (sortBy) sql += ` ORDER BY ${sortBy} ${sortDir === 'asc' ? 'ASC' : 'DESC'}`;
  return sql + ` LIMIT ${perPage} OFFSET ${(page-1)*perPage}`;
}
function _rvCountSql(base, where) {
  const clean = base.trim().replace(/;+$/,'');
  let sql = `SELECT COUNT(*) FROM (${clean}) _q`;
  if (where.length) sql += ` WHERE ${where.join(' AND ')}`;
  return sql;
}
function _rvExportSql(base, where, sortBy, sortDir) {
  const clean = base.trim().replace(/;+$/,'');
  let sql = `SELECT * FROM (${clean}) _q`;
  if (where.length) sql += ` WHERE ${where.join(' AND ')}`;
  if (sortBy) sql += ` ORDER BY ${sortBy} ${sortDir === 'asc' ? 'ASC' : 'DESC'}`;
  return sql;
}

// ── RecordViewerPanel (non-modal floating record browser) ─────────────────────

const RV_PER_PAGE = 50;

function RecordViewerPanel({ pipeline, initialSql, title, onClose,
  initialFuelSubgroup, initialImpactSubgroup, onSubsetSaved, embedded = false }) {
  // SQL base (what the user can edit)
  const [sql, setSql] = useState(initialSql || 'SELECT * FROM filtered_records');

  // Counts + paginated rows fetched from server
  const [totalCount, setTotalCount]   = useState(null);
  const [page, setPage]               = useState(1);
  const [rows, setRows]               = useState([]);
  const [cols, setCols]               = useState([]);
  const [loading, setLoading]         = useState(false);
  const [countLoading, setCountLoading] = useState(false);
  const [error, setError]             = useState(null);
  const [exporting, setExporting]     = useState(false);

  // Detail modal
  const [selectedRecord, setSelectedRecord] = useState(null);
  // Expanded row key (openalex_id or index string)
  const [expandedId, setExpandedId] = useState(null);

  // Collapsible sections
  const [showFilters, setShowFilters] = useState(true);
  const [showSql, setShowSql]         = useState(false);

  // Filter state
  const [filterText, setFilterText]               = useState('');
  const [subgroupFilter, setSubgroupFilter]       = useState(initialFuelSubgroup || '');   // fuel subgroup
  const [fuelTermFilter, setFuelTermFilter]       = useState('');   // specific fuel term within subgroup
  const [impactSubgroupFilter, setImpactSubgroupFilter] = useState(initialImpactSubgroup || ''); // impact subgroup
  const [impactTermFilter, setImpactTermFilter]   = useState('');   // specific impact term within subgroup
  const [yearFrom, setYearFrom]                   = useState('');
  const [yearTo, setYearTo]                       = useState('');
  const [oaStatus, setOaStatus]                   = useState('');
  const [recType, setRecType]                     = useState('');
  const [publisherFilter, setPublisherFilter]     = useState('');
  const [sourceType, setSourceType]               = useState('');
  const [sortBy, setSortBy]                       = useState('');
  const [sortDir, setSortDir]                     = useState('desc');

  // LLM field filters
  const [llmConfidence, setLlmConfidence]                 = useState('');
  const [llmMlRole, setLlmMlRole]                         = useState('');
  const [llmMlAlgorithm, setLlmMlAlgorithm]               = useState('');
  const [llmFuelClassification, setLlmFuelClassification] = useState('');
  const [llmScope, setLlmScope]                           = useState('');
  const [llmDataSource, setLlmDataSource]                 = useState('');
  const [llmIsHydrocarbon, setLlmIsHydrocarbon]           = useState('');

  // Save-as-subset prompt state
  const [savePromptOpen, setSavePromptOpen] = useState(false);
  const [subsetName, setSubsetName]         = useState('');
  const [savingSubset, setSavingSubset]     = useState(false);

  // Derive subgroup definitions from pipeline config
  const fuelSubgroups = useMemo(() => {
    const groups = _migrateGroups(pipeline.config || {});
    return groups[0]?.subgroups || [];
  }, [pipeline.config]);

  const impactSubgroups = useMemo(() => {
    const groups = _migrateGroups(pipeline.config || {});
    return groups[1]?.subgroups || [];
  }, [pipeline.config]);

  // Resolve selected subgroup → term list for SQL
  const fuelSubgroupTerms = useMemo(() => {
    if (!subgroupFilter) return null;
    const sg = fuelSubgroups.find(s => s.name === subgroupFilter);
    return sg?.terms?.length ? sg.terms : null;
  }, [subgroupFilter, fuelSubgroups]);

  const impactSubgroupTerms = useMemo(() => {
    if (!impactSubgroupFilter) return null;
    const sg = impactSubgroups.find(s => s.name === impactSubgroupFilter);
    return sg?.terms?.length ? sg.terms : null;
  }, [impactSubgroupFilter, impactSubgroups]);

  // Build WHERE parts — all filters are server-side (accurate count + pagination)
  const whereParts = useMemo(
    () => _rvBuildWhere({
      textSearch: filterText, yearFrom, yearTo, oaStatus, recordType: recType,
      publisherFilter, sourceType,
      fuelSubgroupName: subgroupFilter || null,
      fuelSubgroupTerms,
      fuelTermName: fuelTermFilter || null,
      impactSubgroupName: impactSubgroupFilter || null,
      impactSubgroupTerms,
      impactTermName: impactTermFilter || null,
      llmConfidence, llmMlRole, llmMlAlgorithm, llmFuelClassification, llmScope, llmDataSource, llmIsHydrocarbon,
    }),
    [filterText, yearFrom, yearTo, oaStatus, recType, publisherFilter, sourceType, subgroupFilter, fuelSubgroupTerms, fuelTermFilter, impactSubgroupFilter, impactSubgroupTerms, impactTermFilter, llmConfidence, llmMlRole, llmMlAlgorithm, llmFuelClassification, llmScope, llmDataSource, llmIsHydrocarbon]
  );

  // ── Fetch count ────────────────────────────────────────────────────────────
  const fetchCount = useCallback(async (baseSql, where) => {
    setCountLoading(true);
    try {
      const countSql = _rvCountSql(baseSql, where);
      const res = await apiFetch('/api/db/query', { method: 'POST', body: JSON.stringify({ pipeline_id: pipeline.id, sql: countSql }) });
      const d = await res.json();
      if (d.error) { setTotalCount(null); return; }
      const v = d.rows?.[0]?.[0];
      setTotalCount(v != null ? Number(v) : null);
    } catch { setTotalCount(null); } finally { setCountLoading(false); }
  }, [pipeline.id]);

  // ── Fetch one page ─────────────────────────────────────────────────────────
  const fetchPage = useCallback(async (baseSql, where, sb, sd, pg) => {
    setLoading(true);
    setError(null);
    try {
      const pageSql = _rvPageSql(baseSql, where, sb, sd, pg, RV_PER_PAGE);
      const res = await apiFetch('/api/db/query', { method: 'POST', body: JSON.stringify({ pipeline_id: pipeline.id, sql: pageSql }) });
      const d = await res.json();
      if (d.error) { setError(d.error); setRows([]); setCols([]); return; }
      setCols(d.columns || []);
      setRows(d.rows || []);
    } catch (e) { setError(String(e)); } finally { setLoading(false); }
  }, [pipeline.id]);

  // Run on mount
  useEffect(() => {
    fetchCount(sql, []);
    fetchPage(sql, [], '', 'desc', 1);
  }, []); // eslint-disable-line

  // Re-run when filters / sort change (debounce text search)
  const filterRef = useRef(null);
  useEffect(() => {
    clearTimeout(filterRef.current);
    filterRef.current = setTimeout(() => {
      fetchCount(sql, whereParts);
      fetchPage(sql, whereParts, sortBy, sortDir, 1);
      setPage(1);
      setExpandedId(null);
    }, filterText ? 350 : 0);
    return () => clearTimeout(filterRef.current);
  }, [whereParts, sortBy, sortDir]); // eslint-disable-line

  const goPage = (pg) => {
    setPage(pg);
    setExpandedId(null);
    fetchPage(sql, whereParts, sortBy, sortDir, pg);
  };

  const runNewSql = () => {
    fetchCount(sql, whereParts);
    fetchPage(sql, whereParts, sortBy, sortDir, 1);
    setPage(1);
    setExpandedId(null);
  };

  // ── CSV export (full result set, no pagination) ────────────────────────────
  const _CSV_EXCLUDE = new Set([
    'is_retracted', 'matched_fuel_stems', 'matched_impact_stems',
    'open_access_is_oa',  // duplicate of top-level is_oa
  ]);
  const _CSV_RENAME = {
    primary_location_source_host_organization_name: 'publisher',
    primary_location_source_display_name: 'journal',
    primary_location_source_type: 'source_type',
    primary_location_source_issn_l: 'issn_l',
    primary_location_is_oa: 'location_is_oa',
    primary_location_landing_page_url: 'landing_page_url',
    primary_location_license: 'license',
    open_access_any_repository_has_fulltext: 'any_repo_fulltext',
    open_access_oa_status: 'oa_status',
    open_access_oa_url: 'oa_url',
    best_oa_location_license: 'best_oa_license',
    best_oa_location_pdf_url: 'best_oa_pdf_url',
    best_oa_location_version: 'best_oa_version',
  };

  const exportCsv = async () => {
    setExporting(true);
    try {
      const exportSql = _rvExportSql(sql, whereParts, sortBy, sortDir);
      const res = await apiFetch('/api/db/query', {
        method: 'POST',
        body: JSON.stringify({ pipeline_id: pipeline.id, sql: exportSql, max_rows: 200000 }),
      });
      const d = await res.json();
      if (d.error || !d.rows) { alert(d.error || 'Export failed'); return; }
      // Filter and rename columns
      const idxs = d.columns.map((c, i) => (!_CSV_EXCLUDE.has(c) ? i : -1)).filter(i => i >= 0);
      const headers = idxs.map(i => _CSV_RENAME[d.columns[i]] || d.columns[i]);
      const csvRows = [
        headers.join(','),
        ...d.rows.map(r => idxs.map(i => `"${String(r[i] ?? '').replace(/"/g, '""')}"`).join(','))
      ];
      const a = document.createElement('a');
      a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csvRows.join('\n'));
      a.download = `records_${pipeline.id}.csv`;
      a.click();
    } finally { setExporting(false); }
  };

  // ── Open full record detail via SQL (avoids path-encoding issues with openalex URLs) ──
  const openRecord = async (oaId) => {
    if (!oaId) return;
    const safId = oaId.replace(/'/g, "''");
    const res = await apiFetch('/api/db/query', {
      method: 'POST',
      body: JSON.stringify({
        pipeline_id: pipeline.id,
        sql: `SELECT * FROM filtered_records WHERE openalex_id = '${safId}'`,
      }),
    });
    const d = await res.json();
    if (!d.error && d.rows?.[0] && d.columns) {
      setSelectedRecord(Object.fromEntries(d.columns.map((c, i) => [c, d.rows[0][i]])));
    }
  };

  // Escape key (non-embedded only — in embedded mode the split layout owns the close)
  useEffect(() => {
    if (embedded) return;
    const h = e => { if (e.key === 'Escape' && !selectedRecord) onClose(); };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, [onClose, selectedRecord, embedded]);

  // ── Drill-down sub-viewer ─────────────────────────────────────────────────
  const [drillDown, setDrillDown] = useState(null); // { sql, title }

  // Parse SQL text to determine what DB column a generic alias (e.g. x_label, y_label) maps to.
  // Returns { col: 'llm_fuel_classification', isJson: true } or null.
  const _inferAliasCol = useCallback((sqlText, alias) => {
    // Build map of table-alias → json_each source column: json_each(xxx) a → { a: 'xxx' }
    const jeMap = {};
    const jeRe = /json_each\((\w+)\)\s+(?:AS\s+)?(\w+)\b/gi;
    let m;
    while ((m = jeRe.exec(sqlText)) !== null) jeMap[m[2]] = m[1];
    // Look for: tbl.value AS alias
    const vmRe = new RegExp(`(\\w+)\\.value\\s+AS\\s+${alias}\\b`, 'i');
    const vm = sqlText.match(vmRe);
    if (vm && jeMap[vm[1]]) return { col: jeMap[vm[1]], isJson: true };
    // Look for: CASE ... END AS alias or col_name AS alias
    const dmRe = new RegExp(`(?:ELSE\\s+)?(\\w+)\\s+AS\\s+${alias}\\b`, 'i');
    const dm = sqlText.match(dmRe);
    if (dm && dm[1].toLowerCase() !== 'value') return { col: dm[1], isJson: false };
    return null;
  }, []);

  // Build a drill-down SQL from a table row by mapping dimension columns to
  // filtered_records WHERE conditions.
  const buildDrillSql = useCallback((rowCols, rowData, skipCol) => {
    const DIM_MAP = {
      year: 'publication_year', publication_year: 'publication_year',
      oa_status: 'open_access_oa_status', open_access_oa_status: 'open_access_oa_status',
      status: 'open_access_oa_status',
      type: 'type', record_type: 'type',
      journal: 'primary_location_source_display_name',
      source: 'llm_ml_data_source',
    };
    const conditions = [];
    rowCols.forEach((col, i) => {
      if (col === skipCol) return;
      const val = rowData[i];
      if (val == null || val === '' || val === 'Unknown/≤2000') return;
      const lc = col.toLowerCase();
      const safe = String(val).replace(/'/g, "''");
      if (lc === 'x_label' || lc === 'y_label') {
        // Heatmap chart generic alias — parse the current SQL to infer actual DB column
        const inferred = _inferAliasCol(sql, col);
        if (!inferred) return;
        if (inferred.isJson) {
          conditions.push(`EXISTS (SELECT 1 FROM json_each(${inferred.col}) WHERE LOWER(value) = LOWER('${safe}'))`);
        } else {
          conditions.push(`LOWER(${inferred.col}) = LOWER('${safe}')`);
        }
      } else if (lc === 'subgroup' || lc.includes('subgroup')) {
        const base = lc.includes('impact') ? 'matched_impact_subgroups' : 'matched_fuel_subgroups';
        conditions.push(`EXISTS (SELECT 1 FROM json_each(${base}) WHERE value = '${safe}')`);
      } else if (lc === 'term' || lc.includes('_term')) {
        const base = lc.includes('impact') ? 'matched_impact_terms' : 'matched_fuel_terms';
        conditions.push(`EXISTS (SELECT 1 FROM json_each(${base}) WHERE LOWER(value) = LOWER('${safe}'))`);
      } else if (lc === 'country') {
        conditions.push(`EXISTS (SELECT 1 FROM json_each(authors_countries) WHERE value = '${safe}')`);
      } else if (lc === 'institution') {
        conditions.push(`EXISTS (SELECT 1 FROM json_each(authors_institutions) WHERE value = '${safe}')`);
      } else if (lc === 'fuel_type') {
        conditions.push(`EXISTS (SELECT 1 FROM json_each(llm_fuel_classification) WHERE LOWER(value) = LOWER('${safe}'))`);
      } else if (lc === 'role') {
        conditions.push(`EXISTS (SELECT 1 FROM json_each(llm_ml_role) WHERE LOWER(value) = LOWER('${safe}'))`);
      } else if (lc === 'algorithm') {
        conditions.push(`EXISTS (SELECT 1 FROM json_each(llm_ml_algorithm) WHERE LOWER(value) = LOWER('${safe}'))`);
      } else {
        const mapped = DIM_MAP[lc] || DIM_MAP[col] || null;
        if (!mapped) return;
        conditions.push(`${mapped} = '${safe}'`);
      }
    });
    if (!conditions.length) return null;
    return `SELECT * FROM filtered_records WHERE ${conditions.join(' AND ')}`;
  }, [sql, _inferAliasCol]);

  // ── Derived ────────────────────────────────────────────────────────────────
  const ci           = useCallback(n => cols.indexOf(n), [cols]);
  const TITLE_I      = ci('title');
  const isRecordView = TITLE_I >= 0;
  const totalPages   = totalCount != null ? Math.ceil(totalCount / RV_PER_PAGE) : null;

  // Which columns are present (for dynamic filters)
  const hasCol       = useCallback(n => cols.length === 0 || cols.includes(n), [cols]);
  const isCountCol   = (name) => {
    const lc = (name || '').toLowerCase();
    return lc === 'count' || lc === 'cnt' || lc === 'total' || lc.endsWith('_count') || lc.endsWith('_total');
  };

  const hasActiveFilters = !!(filterText || subgroupFilter || fuelTermFilter || impactSubgroupFilter || impactTermFilter || yearFrom || yearTo || oaStatus || recType || publisherFilter || sourceType || sortBy || llmConfidence || llmMlRole || llmMlAlgorithm || llmFuelClassification || llmScope || llmDataSource || llmIsHydrocarbon);
  const clearFilters = () => {
    setFilterText('');
    setSubgroupFilter(''); setFuelTermFilter('');
    setImpactSubgroupFilter(''); setImpactTermFilter('');
    setYearFrom(''); setYearTo(''); setOaStatus(''); setRecType('');
    setPublisherFilter(''); setSourceType('');
    setSortBy('');
    setLlmConfidence(''); setLlmMlRole(''); setLlmMlAlgorithm('');
    setLlmFuelClassification(''); setLlmScope(''); setLlmDataSource(''); setLlmIsHydrocarbon('');
  };

  // Subgroup change handlers — reset corresponding term filter when subgroup changes
  const handleFuelSubgroupChange = (name) => {
    setSubgroupFilter(name);
    setFuelTermFilter('');
  };
  const handleImpactSubgroupChange = (name) => {
    setImpactSubgroupFilter(name);
    setImpactTermFilter('');
  };

  // ── Save as subset ─────────────────────────────────────────────────────────
  const doSaveSubset = async () => {
    const name = subsetName.trim();
    if (!name) return;
    setSavingSubset(true);
    try {
      const subsetSql = _rvSubsetSql(sql, whereParts);
      const res = await apiFetch('/api/subsets', {
        method: 'POST',
        body: JSON.stringify({ pipeline_id: pipeline.id, name, sql: subsetSql }),
      });
      if (res.ok) {
        setSavePromptOpen(false);
        setSubsetName('');
        if (onSubsetSaved) onSubsetSaved();
      } else {
        const d = await res.json();
        alert(d.error || 'Failed to save subset');
      }
    } finally { setSavingSubset(false); }
  };

  // ── Collapsible bar ────────────────────────────────────────────────────────
  const SectionBar = ({ label, open, onToggle, badge, extra }) => (
    <button onClick={onToggle}
      className="w-full flex items-center gap-2 px-4 py-2.5 text-left bg-gray-50 border-b border-gray-200 hover:bg-gray-100 transition flex-shrink-0">
      <svg className={`w-3 h-3 text-gray-400 transition-transform flex-shrink-0 ${open ? 'rotate-90' : ''}`}
        fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M9 5l7 7-7 7"/>
      </svg>
      <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{label}</span>
      {badge}
      <div className="ml-auto flex items-center gap-2">{extra}</div>
    </button>
  );

  // ── Subgroup pill row ──────────────────────────────────────────────────────
  const SubgroupPills = ({ label, subgroups, active, onSelect }) => {
    if (!subgroups.length) return null;
    return (
      <div>
        <div className="text-xs text-gray-400 mb-1.5 font-medium">{label}</div>
        <div className="flex flex-wrap gap-1.5">
          <button onClick={() => onSelect('')}
            className={`text-xs px-2.5 py-1 rounded-full border transition ${!active ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'}`}>
            All
          </button>
          {subgroups.map(sg => (
            <button key={sg.name}
              onClick={() => onSelect(sg.name === active ? '' : sg.name)}
              className={`text-xs px-2.5 py-1 rounded-full border transition ${active === sg.name ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'}`}>
              {sg.name}
            </button>
          ))}
        </div>
      </div>
    );
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className={embedded
        ? "flex flex-col h-full bg-white overflow-hidden"
        : "fixed right-0 flex flex-col bg-white border-l border-gray-200 shadow-2xl z-30"}
      style={embedded ? {} : { top: '48px', height: 'calc(100vh - 48px)', width: '480px' }}>

      {/* Header */}
      <div className="relative flex-shrink-0">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-white">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm font-semibold text-gray-800 truncate">{title || 'Record Viewer'}</span>
            <span className="text-xs text-gray-400 flex-shrink-0">
              {countLoading ? '…' : totalCount != null ? `${totalCount.toLocaleString()} records` : ''}
            </span>
          </div>
          <div className="flex items-center gap-2 ml-2 flex-shrink-0">
            {hasActiveFilters && onSubsetSaved && (
              <button onClick={() => setSavePromptOpen(true)}
                title="Save current filtered view as a named subset for the report"
                className="flex items-center gap-1 text-xs px-2.5 py-1 border border-indigo-300 rounded-lg text-indigo-600 hover:bg-indigo-50 transition">
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4"/>
                </svg>
                Save subset
              </button>
            )}
            <button onClick={exportCsv} disabled={exporting || !totalCount}
              title="Export all matching records as CSV"
              className="flex items-center gap-1 text-xs px-2.5 py-1 border border-gray-300 rounded-lg text-gray-600 hover:bg-gray-50 hover:border-green-400 hover:text-green-700 transition disabled:opacity-40">
              {exporting
                ? <span className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin inline-block" />
                : <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>}
              CSV
            </button>
            <button onClick={onClose} title="Close (Esc)"
              className="p-1 rounded text-gray-400 hover:text-gray-700 hover:bg-gray-200 transition">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12"/>
              </svg>
            </button>
          </div>
        </div>
        {/* Save-as-subset inline prompt */}
        {savePromptOpen && (
          <div className="absolute top-0 left-0 right-0 flex items-center gap-2 px-4 py-3 bg-indigo-50 border-b border-indigo-200 z-10">
            <span className="text-xs text-indigo-700 font-medium whitespace-nowrap">Subset name:</span>
            <input autoFocus value={subsetName} onChange={e => setSubsetName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') doSaveSubset(); if (e.key === 'Escape') { setSavePromptOpen(false); setSubsetName(''); } }}
              placeholder="e.g. SAF papers 2018–2024"
              className="flex-1 text-xs border border-indigo-300 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400" />
            <button onClick={doSaveSubset} disabled={!subsetName.trim() || savingSubset}
              className="text-xs px-3 py-1.5 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 transition flex items-center gap-1">
              {savingSubset && <span className="w-2.5 h-2.5 border border-white border-t-transparent rounded-full animate-spin inline-block" />}
              Save
            </button>
            <button onClick={() => { setSavePromptOpen(false); setSubsetName(''); }}
              className="text-xs text-indigo-500 hover:text-indigo-700 transition">Cancel</button>
          </div>
        )}
      </div>

      {/* Filter & Order (collapsible) */}
      <div className="flex-shrink-0 border-b border-gray-200">
        <SectionBar label="Filter & Order" open={showFilters} onToggle={() => setShowFilters(f => !f)}
          badge={hasActiveFilters
            ? <span className="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full font-medium">active</span>
            : null}
          extra={hasActiveFilters && (
            <button onClick={e => { e.stopPropagation(); clearFilters(); }}
              className="text-xs text-gray-400 hover:text-red-500 transition">Clear all</button>
          )}
        />
        {showFilters && (
          <div className="px-3 py-2.5 space-y-2 bg-white">

            {/* Fuel sub-group + optional term drill-down */}
            {hasCol('matched_fuel_subgroups') && (
              <>
                <SubgroupPills label="Fuel" subgroups={fuelSubgroups} active={subgroupFilter} onSelect={handleFuelSubgroupChange} />
                {subgroupFilter && (() => {
                  const terms = fuelSubgroups.find(s => s.name === subgroupFilter)?.terms || [];
                  if (!terms.length) return null;
                  return (
                    <div className="pl-2 border-l-2 border-orange-200 flex flex-wrap gap-1">
                      <button onClick={() => setFuelTermFilter('')}
                        className={`text-xs px-2 py-0.5 rounded-full border transition ${!fuelTermFilter ? 'bg-orange-400 text-white border-orange-400' : 'bg-white text-gray-500 border-gray-300 hover:border-orange-300'}`}>All</button>
                      {terms.map(t => (
                        <button key={t} onClick={() => setFuelTermFilter(t === fuelTermFilter ? '' : t)}
                          className={`text-xs px-2 py-0.5 rounded-full border transition ${fuelTermFilter === t ? 'bg-orange-400 text-white border-orange-400' : 'bg-white text-gray-500 border-gray-300 hover:border-orange-300'}`}>{t}</button>
                      ))}
                    </div>
                  );
                })()}
              </>
            )}

            {/* Impact sub-group + optional term drill-down */}
            {hasCol('matched_impact_subgroups') && (
              <>
                <SubgroupPills label="Impact" subgroups={impactSubgroups} active={impactSubgroupFilter} onSelect={handleImpactSubgroupChange} />
                {impactSubgroupFilter && (() => {
                  const terms = impactSubgroups.find(s => s.name === impactSubgroupFilter)?.terms || [];
                  if (!terms.length) return null;
                  return (
                    <div className="pl-2 border-l-2 border-teal-200 flex flex-wrap gap-1">
                      <button onClick={() => setImpactTermFilter('')}
                        className={`text-xs px-2 py-0.5 rounded-full border transition ${!impactTermFilter ? 'bg-teal-500 text-white border-teal-500' : 'bg-white text-gray-500 border-gray-300 hover:border-teal-300'}`}>All</button>
                      {terms.map(t => (
                        <button key={t} onClick={() => setImpactTermFilter(t === impactTermFilter ? '' : t)}
                          className={`text-xs px-2 py-0.5 rounded-full border transition ${impactTermFilter === t ? 'bg-teal-500 text-white border-teal-500' : 'bg-white text-gray-500 border-gray-300 hover:border-teal-300'}`}>{t}</button>
                      ))}
                    </div>
                  );
                })()}
              </>
            )}

            {/* Text search — show when title or abstract in cols */}
            {(hasCol('title') || hasCol('abstract_text')) && (
              <div className="relative">
                <svg className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-400 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0"/>
                </svg>
                <input value={filterText} onChange={e => setFilterText(e.target.value)}
                  placeholder="Search title / abstract…"
                  className="w-full pl-7 pr-6 py-1 text-xs border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400" />
                {filterText && (
                  <button onClick={() => setFilterText('')} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-sm leading-none">×</button>
                )}
              </div>
            )}

            {/* Year + OA + Type — show only when those columns present */}
            {(hasCol('publication_year') || hasCol('open_access_oa_status') || hasCol('type')) && (
              <div className="flex flex-wrap gap-1.5">
                {hasCol('publication_year') && (
                  <>
                    <input type="number" value={yearFrom} onChange={e => setYearFrom(e.target.value)}
                      placeholder="Year from"
                      className="w-24 text-xs border border-gray-300 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-blue-400" />
                    <input type="number" value={yearTo} onChange={e => setYearTo(e.target.value)}
                      placeholder="Year to"
                      className="w-24 text-xs border border-gray-300 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-blue-400" />
                  </>
                )}
                {hasCol('open_access_oa_status') && (
                  <select value={oaStatus} onChange={e => setOaStatus(e.target.value)}
                    className="text-xs border border-gray-300 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-blue-400 bg-white">
                    <option value="">OA: any</option>
                    <option value="open_any">Open</option>
                    <option value="gold">Gold</option>
                    <option value="green">Green</option>
                    <option value="hybrid">Hybrid</option>
                    <option value="bronze">Bronze</option>
                    <option value="closed">Closed</option>
                  </select>
                )}
                {hasCol('type') && (
                  <select value={recType} onChange={e => setRecType(e.target.value)}
                    className="text-xs border border-gray-300 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-blue-400 bg-white">
                    <option value="">Type: any</option>
                    <option value="article">Article</option>
                    <option value="proceedings-article">Conference</option>
                    <option value="review">Review</option>
                    <option value="book-chapter">Book ch.</option>
                    <option value="preprint">Preprint</option>
                    <option value="report">Report</option>
                  </select>
                )}
              </div>
            )}

            {/* Publisher + Source type */}
            {(hasCol('primary_location_source_host_organization_name') || hasCol('primary_location_source_type')) && (
              <div className="flex flex-wrap gap-1.5">
                {hasCol('primary_location_source_host_organization_name') && (
                  <div className="relative flex-1 min-w-24">
                    <input value={publisherFilter} onChange={e => setPublisherFilter(e.target.value)}
                      placeholder="Publisher…"
                      className="w-full text-xs border border-gray-300 rounded px-2 pr-5 py-1 focus:outline-none focus:ring-1 focus:ring-blue-400" />
                    {publisherFilter && (
                      <button onClick={() => setPublisherFilter('')} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-sm">×</button>
                    )}
                  </div>
                )}
                {hasCol('primary_location_source_type') && (
                  <select value={sourceType} onChange={e => setSourceType(e.target.value)}
                    className="text-xs border border-gray-300 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-blue-400 bg-white">
                    <option value="">Source: any</option>
                    <option value="journal">Journal</option>
                    <option value="conference">Conference</option>
                    <option value="repository">Repository</option>
                    <option value="ebook platform">eBook</option>
                    <option value="book series">Book series</option>
                  </select>
                )}
              </div>
            )}

            {/* LLM classification filters */}
            {(hasCol('llm_confidence') || hasCol('llm_ml_role') || hasCol('llm_fuel_classification')) && (
              <div>
                <div className="text-xs text-gray-400 mb-1.5 font-medium">LLM Fields</div>
                <div className="flex flex-wrap gap-1.5">
                  {hasCol('llm_confidence') && (
                    <select value={llmConfidence} onChange={e => setLlmConfidence(e.target.value)}
                      className="text-xs border border-gray-300 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-purple-400 bg-white">
                      <option value="">Confidence: any</option>
                      <option value="High">High</option>
                      <option value="Medium">Medium</option>
                      <option value="Low">Low</option>
                    </select>
                  )}
                  {hasCol('llm_is_hydrocarbon') && (
                    <select value={llmIsHydrocarbon} onChange={e => setLlmIsHydrocarbon(e.target.value)}
                      className="text-xs border border-gray-300 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-purple-400 bg-white">
                      <option value="">Hydrocarbon: any</option>
                      <option value="1">Yes</option>
                      <option value="0">No</option>
                    </select>
                  )}
                  {hasCol('llm_fuel_classification') && (
                    <div className="relative flex-1 min-w-24">
                      <input value={llmFuelClassification} onChange={e => setLlmFuelClassification(e.target.value)}
                        placeholder="Fuel classification…"
                        className="w-full text-xs border border-gray-300 rounded px-2 pr-5 py-1 focus:outline-none focus:ring-1 focus:ring-purple-400" />
                      {llmFuelClassification && (
                        <button onClick={() => setLlmFuelClassification('')} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-sm">×</button>
                      )}
                    </div>
                  )}
                  {hasCol('llm_ml_role') && (
                    <div className="relative flex-1 min-w-24">
                      <input value={llmMlRole} onChange={e => setLlmMlRole(e.target.value)}
                        placeholder="ML role…"
                        className="w-full text-xs border border-gray-300 rounded px-2 pr-5 py-1 focus:outline-none focus:ring-1 focus:ring-purple-400" />
                      {llmMlRole && (
                        <button onClick={() => setLlmMlRole('')} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-sm">×</button>
                      )}
                    </div>
                  )}
                  {hasCol('llm_ml_algorithm') && (
                    <div className="relative flex-1 min-w-24">
                      <input value={llmMlAlgorithm} onChange={e => setLlmMlAlgorithm(e.target.value)}
                        placeholder="ML algorithm…"
                        className="w-full text-xs border border-gray-300 rounded px-2 pr-5 py-1 focus:outline-none focus:ring-1 focus:ring-purple-400" />
                      {llmMlAlgorithm && (
                        <button onClick={() => setLlmMlAlgorithm('')} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-sm">×</button>
                      )}
                    </div>
                  )}
                  {hasCol('llm_scope') && (
                    <div className="relative flex-1 min-w-24">
                      <input value={llmScope} onChange={e => setLlmScope(e.target.value)}
                        placeholder="Value chain…"
                        className="w-full text-xs border border-gray-300 rounded px-2 pr-5 py-1 focus:outline-none focus:ring-1 focus:ring-purple-400" />
                      {llmScope && (
                        <button onClick={() => setLlmScope('')} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-sm">×</button>
                      )}
                    </div>
                  )}
                  {hasCol('llm_ml_data_source') && (
                    <div className="relative flex-1 min-w-24">
                      <input value={llmDataSource} onChange={e => setLlmDataSource(e.target.value)}
                        placeholder="Data source…"
                        className="w-full text-xs border border-gray-300 rounded px-2 pr-5 py-1 focus:outline-none focus:ring-1 focus:ring-purple-400" />
                      {llmDataSource && (
                        <button onClick={() => setLlmDataSource('')} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-sm">×</button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Sort — dynamic from available columns */}
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-gray-400 flex-shrink-0">Sort by</span>
              <select value={sortBy} onChange={e => setSortBy(e.target.value)}
                className="flex-1 text-xs border border-gray-300 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-blue-400 bg-white">
                <option value="">— default —</option>
                {cols.length > 0
                  ? cols.map(c => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)
                  : <>
                      <option value="publication_year">publication year</option>
                      <option value="fwci">fwci</option>
                      <option value="cited_by_count">cited by count</option>
                      <option value="title">title</option>
                      <option value="llm_confidence">llm confidence</option>
                      <option value="llm_ml_role">ml role</option>
                      <option value="llm_ml_algorithm">ml algorithm</option>
                    </>
                }
              </select>
              {sortBy && (
                <button onClick={() => setSortDir(d => d === 'asc' ? 'desc' : 'asc')}
                  className="flex-shrink-0 text-xs border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition">
                  {sortDir === 'desc' ? '↓ desc' : '↑ asc'}
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* SQL Query (collapsible) */}
      <div className="flex-shrink-0 border-b border-gray-200">
        <SectionBar label="SQL Query" open={showSql} onToggle={() => setShowSql(s => !s)}
          extra={<code className="text-xs bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded font-mono">filtered_records</code>}
        />
        {showSql && (
          <div className="px-4 py-3 bg-white">
            <textarea value={sql} onChange={e => setSql(e.target.value)} rows={3} spellCheck={false}
              onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runNewSql(); }}}
              className="w-full text-xs font-mono border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400 resize-none" />
            <div className="flex items-center gap-2 mt-1.5">
              <button onClick={runNewSql} disabled={loading}
                className="px-3 py-1 bg-indigo-600 text-white text-xs rounded hover:bg-indigo-700 transition disabled:opacity-50 flex items-center gap-1.5">
                {loading && <span className="w-2.5 h-2.5 border border-white border-t-transparent rounded-full animate-spin inline-block" />}
                Run
              </button>
              <span className="text-xs text-gray-400">Ctrl+Enter</span>
            </div>
          </div>
        )}
      </div>

      {/* Results */}
      <div className="flex-1 overflow-y-auto">
        {loading && rows.length === 0 && (
          <div className="flex items-center justify-center py-16 text-gray-400">
            <span className="w-5 h-5 border-2 border-gray-300 border-t-indigo-500 rounded-full animate-spin mr-2" />
            Loading…
          </div>
        )}
        {loading && rows.length > 0 && (
          <div className="px-4 py-1.5 bg-indigo-50 text-xs text-indigo-500 flex items-center gap-1.5">
            <span className="w-3 h-3 border border-indigo-400 border-t-transparent rounded-full animate-spin inline-block" />
            Loading…
          </div>
        )}
        {error && <div className="p-4 text-red-600 text-xs font-mono bg-red-50">{error}</div>}
        {!loading && !error && rows.length === 0 && (
          <div className="p-8 text-center text-gray-400 text-sm">No records match the current filters</div>
        )}
        {rows.length > 0 && (
          isRecordView ? (
            <div>
              {rows.map((row, ri) => {
                const DOI_I    = ci('doi'),   YEAR_I   = ci('publication_year');
                const FWCI_I   = ci('fwci'),  OA_I     = ci('open_access_oa_status');
                const ABS_I    = ci('abstract_text');
                const MFUEL_I  = ci('matched_fuel_terms');
                const MIMP_I   = ci('matched_impact_terms');
                const SRC_I    = ci('primary_location_source_display_name');
                const OAID_I   = ci('openalex_id');
                const parseArr = v => { if (!v) return []; try { const p=JSON.parse(v); return Array.isArray(p)?p:[]; } catch { return []; } };
                const OA_CHIP  = { gold:'bg-yellow-50 text-yellow-600', green:'bg-green-50 text-green-600', hybrid:'bg-purple-50 text-purple-600', bronze:'bg-orange-50 text-orange-600' };

                const MFUEL_SG_I = ci('matched_fuel_subgroups');
                const CITE_I     = ci('cited_by_count');

                const oaId    = OAID_I >= 0 ? (row[OAID_I] || '') : '';
                const rowKey  = oaId || String(ri);
                const isExp   = expandedId === rowKey;

                const ttl     = row[TITLE_I] || '';
                const doi     = DOI_I  >= 0 ? (row[DOI_I]  || '') : '';
                const year    = YEAR_I >= 0 ? row[YEAR_I]  : null;
                const fwci    = FWCI_I >= 0 && row[FWCI_I] != null ? Number(row[FWCI_I]) : null;
                const oaSt    = OA_I   >= 0 ? (row[OA_I]   || '') : '';
                const abstr   = ABS_I  >= 0 ? (row[ABS_I]  || '') : '';
                const src     = SRC_I  >= 0 ? (row[SRC_I]  || '') : '';
                const mfuel   = MFUEL_I >= 0 ? row[MFUEL_I] : null;
                const mimp    = MIMP_I  >= 0 ? row[MIMP_I]  : null;
                const fuel    = parseArr(mfuel);
                const imp     = parseArr(mimp);
                const citeCount = CITE_I >= 0 && row[CITE_I] != null ? Number(row[CITE_I]) : null;
                // Prefer DB-stored subgroups; fall back to client-side computation
                const dbSgs   = MFUEL_SG_I >= 0 ? row[MFUEL_SG_I] : null;
                const rowSgs  = dbSgs ? parseArr(dbSgs) : computeRowSubgroups(mfuel, fuelSubgroups);
                const doiUrl  = doi ? `https://doi.org/${doi.replace(/^https?:\/\/(dx\.)?doi\.org\//,'')}` : null;
                const fwciCls = !fwci ? '' : fwci>=3 ? 'text-green-700 font-bold' : fwci>=1 ? 'text-blue-700' : 'text-gray-500';

                return (
                  <div key={rowKey}
                    onClick={() => setExpandedId(isExp ? null : rowKey)}
                    className={`px-4 py-3 cursor-pointer select-none border-b border-gray-100 ${isExp ? 'bg-blue-50' : 'hover:bg-gray-50'}`}>

                    {/* Title */}
                    <div className={`text-xs font-medium leading-snug text-gray-900 ${isExp ? '' : 'line-clamp-2'}`}>
                      {doiUrl
                        ? <a href={doiUrl} target="_blank" rel="noopener" onClick={e => e.stopPropagation()}
                            className="hover:underline decoration-blue-400 hover:text-blue-700">{ttl||doi}</a>
                        : (ttl||doi||'—')}
                    </div>

                    {/* Meta chips */}
                    <div className="flex flex-wrap gap-1 mt-1.5 items-center">
                      {year  && <span className="text-xs text-gray-500 font-medium">{year}</span>}
                      {oaSt  && <span className={`text-xs px-1.5 py-0.5 rounded ${OA_CHIP[oaSt]||'bg-gray-100 text-gray-500'}`}>{oaSt}</span>}
                      {src   && <span className="text-xs text-gray-400 italic truncate max-w-[140px]">{src}</span>}
                      {citeCount != null && citeCount > 0 && <span className="text-xs text-gray-400">cited {citeCount.toLocaleString()}×</span>}
                      {fwci != null && <span className={`text-xs ml-auto flex-shrink-0 ${fwciCls}`}>FWCI {fwci.toFixed(1)}</span>}
                    </div>

                    {/* Fuel sub-group chips + ALL fuel terms */}
                    {(rowSgs.length > 0 || fuel.length > 0) && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {rowSgs.map(sg => <span key={sg} className="text-xs px-2 py-0.5 bg-blue-100 text-blue-700 rounded-full">{sg}</span>)}
                        {fuel.map(t => <span key={t} className="text-xs text-orange-600 bg-orange-50 px-1.5 py-0.5 rounded">{t}</span>)}
                      </div>
                    )}

                    {/* ALL impact terms */}
                    {imp.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {imp.map(t => <span key={t} className="text-xs text-teal-600 bg-teal-50 px-1.5 py-0.5 rounded">{t}</span>)}
                      </div>
                    )}

                    {/* Abstract — always fully visible when expanded */}
                    {abstr && (
                      <p className={`text-xs text-gray-500 mt-2 leading-relaxed ${isExp ? '' : 'line-clamp-2'}`}>{abstr}</p>
                    )}

                    {/* Expanded action bar */}
                    {isExp && (
                      <div className="mt-3 flex items-center gap-2" onClick={e => e.stopPropagation()}>
                        {oaId && (
                          <button onClick={() => openRecord(oaId)}
                            className="text-xs px-3 py-1.5 bg-white border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 hover:border-blue-400 hover:text-blue-700 transition flex items-center gap-1.5">
                            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 10h16M4 14h16M4 18h7"/>
                            </svg>
                            View all fields
                          </button>
                        )}
                        <span className="text-xs text-gray-400 ml-auto">click to collapse</span>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="text-xs w-full">
                <thead className="bg-gray-50 border-b sticky top-0">
                  <tr>
                    {cols.map(c => (
                      <th key={c} className="px-3 py-2 text-left font-medium whitespace-nowrap">
                        {isCountCol(c)
                          ? <span className="flex items-center gap-1">{c}<span className="text-gray-400 font-normal text-xs" title="Click counts to explore records">⊞</span></span>
                          : c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                      {r.map((v, j) => {
                        if (isCountCol(cols[j]) && v != null && Number(v) > 0) {
                          const dSql = buildDrillSql(cols, r, cols[j]);
                          const dimLabel = cols.filter((c, ci) => !isCountCol(c) && r[ci] != null).map(c => r[cols.indexOf(c)]).filter(Boolean).join(' · ');
                          return (
                            <td key={j} className="px-3 py-1.5">
                              {dSql ? (
                                <button
                                  onClick={() => setDrillDown({ sql: dSql, title: `Records: ${dimLabel || String(v)}` })}
                                  className="font-mono font-semibold text-blue-600 hover:text-blue-800 hover:underline decoration-dotted transition"
                                  title="Click to view matching records">
                                  {Number(v).toLocaleString()}
                                </button>
                              ) : (
                                <span className="font-mono font-semibold text-gray-700">{Number(v).toLocaleString()}</span>
                              )}
                            </td>
                          );
                        }
                        return <td key={j} className="px-3 py-1.5 max-w-xs truncate">{String(v ?? '')}</td>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}
      </div>

      {/* Pagination */}
      {totalPages != null && totalPages > 1 && (
        <div className="flex-shrink-0 flex items-center justify-between px-4 py-2 border-t border-gray-200 bg-gray-50 text-xs text-gray-500">
          <span>Page {page} of {totalPages} · {totalCount.toLocaleString()} total</span>
          <div className="flex items-center gap-1">
            <button disabled={page <= 1} onClick={() => goPage(1)}
              className="px-2 py-1 border rounded hover:bg-white disabled:opacity-40">«</button>
            <button disabled={page <= 1} onClick={() => goPage(page - 1)}
              className="px-2 py-1 border rounded hover:bg-white disabled:opacity-40">‹</button>
            <input type="number" min={1} max={totalPages} value={page}
              onChange={e => { const v = Math.max(1, Math.min(totalPages, Number(e.target.value)||1)); goPage(v); }}
              className="w-12 border rounded px-1 py-0.5 text-center focus:outline-none focus:ring-1 focus:ring-blue-400" />
            <button disabled={page >= totalPages} onClick={() => goPage(page + 1)}
              className="px-2 py-1 border rounded hover:bg-white disabled:opacity-40">›</button>
            <button disabled={page >= totalPages} onClick={() => goPage(totalPages)}
              className="px-2 py-1 border rounded hover:bg-white disabled:opacity-40">»</button>
          </div>
        </div>
      )}

      {selectedRecord && (
        <RecordModal record={selectedRecord} fuelSubgroups={fuelSubgroups}
          onClose={() => setSelectedRecord(null)} />
      )}

      {/* Drill-down modal — opened by clicking a count value in table view */}
      {drillDown && (
        <div className="fixed inset-0 bg-gray-900/60 flex items-end sm:items-center justify-center z-50 p-0 sm:p-4"
          onClick={e => e.target === e.currentTarget && setDrillDown(null)}>
          <div className="bg-white rounded-t-2xl sm:rounded-xl shadow-2xl w-full sm:max-w-2xl flex flex-col"
            style={{ maxHeight: 'calc(100vh - 48px)' }}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 flex-shrink-0">
              <span className="text-sm font-semibold text-gray-800 truncate">{drillDown.title}</span>
              <button onClick={() => setDrillDown(null)}
                className="p-1 rounded text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition ml-2 flex-shrink-0">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12"/>
                </svg>
              </button>
            </div>
            <div className="flex-1 overflow-hidden min-h-0">
              <RecordViewerPanel
                key={drillDown.sql}
                pipeline={pipeline}
                initialSql={drillDown.sql}
                title={drillDown.title}
                onClose={() => setDrillDown(null)}
                onSubsetSaved={onSubsetSaved}
                embedded={true}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
