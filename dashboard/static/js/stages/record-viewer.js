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
  if (fs.hideRetracted) parts.push(`(is_retracted IS NULL OR is_retracted = 0)`);
  // Fuel subgroup: prefer DB column (matched_fuel_subgroups), fall back to terms list
  if (fs.fuelSubgroupName) {
    const sgName = fs.fuelSubgroupName.replace(/'/g, "''");
    parts.push(
      `(EXISTS (SELECT 1 FROM json_each(matched_fuel_subgroups) WHERE value = '${sgName}')` +
      ` OR EXISTS (SELECT 1 FROM json_each(matched_fuel_terms) WHERE value IN (${(fs.fuelSubgroupTerms||[]).map(t=>`'${t.replace(/'/g,"''")}'`).join(',')||"''"})))`
    );
  }
  // Impact subgroup: same dual approach
  if (fs.impactSubgroupName) {
    const sgName = fs.impactSubgroupName.replace(/'/g, "''");
    parts.push(
      `(EXISTS (SELECT 1 FROM json_each(matched_impact_subgroups) WHERE value = '${sgName}')` +
      ` OR EXISTS (SELECT 1 FROM json_each(matched_impact_terms) WHERE value IN (${(fs.impactSubgroupTerms||[]).map(t=>`'${t.replace(/'/g,"''")}'`).join(',')||"''"})))`
    );
  }
  return parts;
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

function RecordViewerPanel({ pipeline, initialSql, title, onClose }) {
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
  const [subgroupFilter, setSubgroupFilter]       = useState('');   // fuel
  const [impactSubgroupFilter, setImpactSubgroupFilter] = useState(''); // impact
  const [yearFrom, setYearFrom]                   = useState('');
  const [yearTo, setYearTo]                       = useState('');
  const [oaStatus, setOaStatus]                   = useState('');
  const [recType, setRecType]                     = useState('');
  const [publisherFilter, setPublisherFilter]     = useState('');
  const [sourceType, setSourceType]               = useState('');
  const [hideRetracted, setHideRetracted]         = useState(true);
  const [sortBy, setSortBy]                       = useState('');
  const [sortDir, setSortDir]                     = useState('desc');

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
      publisherFilter, sourceType, hideRetracted,
      fuelSubgroupName: subgroupFilter || null,
      fuelSubgroupTerms,
      impactSubgroupName: impactSubgroupFilter || null,
      impactSubgroupTerms,
    }),
    [filterText, yearFrom, yearTo, oaStatus, recType, publisherFilter, sourceType, hideRetracted, subgroupFilter, fuelSubgroupTerms, impactSubgroupFilter, impactSubgroupTerms]
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
      const csvRows = [
        d.columns.join(','),
        ...d.rows.map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))
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

  // Escape key
  useEffect(() => {
    const h = e => { if (e.key === 'Escape' && !selectedRecord) onClose(); };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, [onClose, selectedRecord]);

  // ── Derived ────────────────────────────────────────────────────────────────
  const ci           = useCallback(n => cols.indexOf(n), [cols]);
  const TITLE_I      = ci('title');
  const isRecordView = TITLE_I >= 0;
  const totalPages   = totalCount != null ? Math.ceil(totalCount / RV_PER_PAGE) : null;

  const hasActiveFilters = !!(filterText || subgroupFilter || impactSubgroupFilter || yearFrom || yearTo || oaStatus || recType || publisherFilter || sourceType || !hideRetracted || sortBy);
  const clearFilters = () => {
    setFilterText(''); setSubgroupFilter(''); setImpactSubgroupFilter('');
    setYearFrom(''); setYearTo(''); setOaStatus(''); setRecType('');
    setPublisherFilter(''); setSourceType('');
    setHideRetracted(true); setSortBy('');
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
    <div className="fixed right-0 flex flex-col bg-white border-l border-gray-200 shadow-2xl z-30"
      style={{ top: '48px', height: 'calc(100vh - 48px)', width: '480px' }}>

      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-white flex-shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-semibold text-gray-800 truncate">{title || 'Record Viewer'}</span>
          <span className="text-xs text-gray-400 flex-shrink-0">
            {countLoading ? '…' : totalCount != null ? `${totalCount.toLocaleString()} records` : ''}
          </span>
        </div>
        <div className="flex items-center gap-2 ml-2 flex-shrink-0">
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
          <div className="px-4 py-3 space-y-3 bg-white">

            {/* Fuel sub-group filter */}
            <SubgroupPills
              label="Fuel sub-group"
              subgroups={fuelSubgroups}
              active={subgroupFilter}
              onSelect={setSubgroupFilter}
            />

            {/* Impact sub-group filter */}
            <SubgroupPills
              label="Impact sub-group"
              subgroups={impactSubgroups}
              active={impactSubgroupFilter}
              onSelect={setImpactSubgroupFilter}
            />

            {/* Text search */}
            <div className="relative">
              <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400 pointer-events-none"
                fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0"/>
              </svg>
              <input value={filterText} onChange={e => setFilterText(e.target.value)}
                placeholder="Search title / abstract…"
                className="w-full pl-8 pr-7 py-1.5 text-xs border border-gray-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-blue-400" />
              {filterText && (
                <button onClick={() => setFilterText('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-sm leading-none">×</button>
              )}
            </div>

            {/* Year range */}
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-xs text-gray-400 mb-1">Year from</div>
                <input type="number" value={yearFrom} onChange={e => setYearFrom(e.target.value)}
                  placeholder="e.g. 2018"
                  className="w-full text-xs border border-gray-300 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-400" />
              </div>
              <div>
                <div className="text-xs text-gray-400 mb-1">Year to</div>
                <input type="number" value={yearTo} onChange={e => setYearTo(e.target.value)}
                  placeholder="e.g. 2024"
                  className="w-full text-xs border border-gray-300 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-400" />
              </div>
            </div>

            {/* OA status + Record type */}
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-xs text-gray-400 mb-1">OA status</div>
                <select value={oaStatus} onChange={e => setOaStatus(e.target.value)}
                  className="w-full text-xs border border-gray-300 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-400 bg-white">
                  <option value="">Any</option>
                  <option value="open_any">Open (any)</option>
                  <option value="gold">Gold OA</option>
                  <option value="green">Green OA</option>
                  <option value="hybrid">Hybrid OA</option>
                  <option value="bronze">Bronze OA</option>
                  <option value="closed">Closed</option>
                </select>
              </div>
              <div>
                <div className="text-xs text-gray-400 mb-1">Record type</div>
                <select value={recType} onChange={e => setRecType(e.target.value)}
                  className="w-full text-xs border border-gray-300 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-400 bg-white">
                  <option value="">Any</option>
                  <option value="article">Article</option>
                  <option value="proceedings-article">Conference paper</option>
                  <option value="review">Review</option>
                  <option value="book-chapter">Book chapter</option>
                  <option value="dataset">Dataset</option>
                  <option value="preprint">Preprint</option>
                  <option value="supplementary-materials">Supplemental material</option>
                  <option value="report">Report</option>
                  <option value="paratext">Paratext</option>
                </select>
              </div>
            </div>

            {/* Publisher filter */}
            <div>
              <div className="text-xs text-gray-400 mb-1">Publisher (contains)</div>
              <div className="relative">
                <input value={publisherFilter} onChange={e => setPublisherFilter(e.target.value)}
                  placeholder="e.g. Elsevier, Springer…"
                  className="w-full text-xs border border-gray-300 rounded-lg px-2 pr-6 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-400" />
                {publisherFilter && (
                  <button onClick={() => setPublisherFilter('')}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-sm">×</button>
                )}
              </div>
            </div>

            {/* Source type */}
            <div>
              <div className="text-xs text-gray-400 mb-1">Source type</div>
              <select value={sourceType} onChange={e => setSourceType(e.target.value)}
                className="w-full text-xs border border-gray-300 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-400 bg-white">
                <option value="">Any</option>
                <option value="journal">Journal</option>
                <option value="conference">Conference</option>
                <option value="repository">Repository</option>
                <option value="ebook platform">eBook platform</option>
                <option value="book series">Book series</option>
                <option value="metadata">Metadata</option>
              </select>
            </div>

            {/* Retracted toggle with explanation */}
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5 cursor-pointer select-none">
                <input type="checkbox" checked={hideRetracted} onChange={e => setHideRetracted(e.target.checked)}
                  className="rounded border-gray-300 text-blue-600 focus:ring-blue-400" />
                <span className="text-xs text-gray-600">Hide retracted papers</span>
              </label>
              <span className="text-xs text-gray-400 cursor-help"
                title="Retracted papers are publications that have been formally withdrawn by the journal after issues were found (e.g. data fabrication, errors). They are kept in the database but flagged.">
                ⓘ
              </span>
            </div>

            {/* Sort */}
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-400 flex-shrink-0">Sort by</span>
              <select value={sortBy} onChange={e => setSortBy(e.target.value)}
                className="flex-1 text-xs border border-gray-300 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-400 bg-white">
                <option value="">— relevance —</option>
                <option value="publication_year">Year</option>
                <option value="fwci">FWCI</option>
                <option value="cited_by_count">Citations</option>
                <option value="title">Title (A–Z)</option>
                <option value="llm_confidence">LLM confidence</option>
              </select>
              {sortBy && (
                <button onClick={() => setSortDir(d => d === 'asc' ? 'desc' : 'asc')}
                  className="flex-shrink-0 text-xs border border-gray-300 rounded-lg px-2.5 py-1.5 hover:bg-gray-50 transition min-w-[52px] text-center">
                  {sortDir === 'desc' ? '↓ Desc' : '↑ Asc'}
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
                const RETRACTED_I = ci('is_retracted');

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
                const isRetracted = RETRACTED_I >= 0 && row[RETRACTED_I];
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
                      {isRetracted && <span className="text-xs px-1.5 py-0.5 rounded bg-red-100 text-red-600 font-semibold">RETRACTED</span>}
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
                  <tr>{cols.map(c => <th key={c} className="px-3 py-2 text-left font-medium whitespace-nowrap">{c}</th>)}</tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                      {r.map((v, j) => <td key={j} className="px-3 py-1.5 max-w-xs truncate">{String(v ?? '')}</td>)}
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
    </div>
  );
}
