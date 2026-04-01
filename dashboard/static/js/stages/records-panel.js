
// ── RecordsPanel (browseable DB table) ────────────────────────────────────────

function RecordsPanel({ pipeline, isLLM }) {
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState('publication_year');
  const [search, setSearch] = useState('');
  const [records, setRecords] = useState(null);
  const [schema, setSchema] = useState([]);
  const [visibleCols, setVisibleCols] = useState([]);   // [] = all
  const [searchInCols, setSearchInCols] = useState([]); // [] = default
  const [sqlText, setSqlText] = useState('');
  const [sqlResult, setSqlResult] = useState(null);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [subgroupFilter, setSubgroupFilter] = useState('');  // '' = all
  const searchInputRef = useRef(null);

  // Subgroup definitions from pipeline config (group 0 = fuel terms)
  const fuelSubgroups = useMemo(() => {
    const config = pipeline.config || {};
    const groups = _migrateGroups(config);
    return groups[0]?.subgroups || [];
  }, [pipeline.config]);

  // Load schema once to populate column pickers
  useEffect(() => {
    apiFetch(`/api/db/schema?pipeline_id=${pipeline.id}`)
      .then(r => r.json())
      .then(cols => {
        if (Array.isArray(cols)) setSchema(cols.map(c => c.name));
      })
      .catch(() => {});
  }, [pipeline.id]);

  const loadRecords = useCallback(async (p, s, q, vcols, scols) => {
    const params = new URLSearchParams({ pipeline_id: pipeline.id, page: p, sort: s, dir: 'desc', q: q || '' });
    if (vcols && vcols.length) params.set('visible_cols', vcols.join(','));
    if (scols && scols.length) params.set('search_cols', scols.join(','));
    try {
      const res = await apiFetch(`/api/db/records?${params}`);
      const data = await res.json();
      if (!data.error) setRecords(data);
    } catch {}
  }, [pipeline.id]);

  useEffect(() => { loadRecords(1, 'publication_year', '', [], []); }, [loadRecords]);

  const doSearch = () => {
    const q = searchInputRef.current?.value || '';
    setSearch(q); setPage(1);
    loadRecords(1, sort, q, visibleCols, searchInCols);
  };
  const doSort = s => { setSort(s); setPage(1); loadRecords(1, s, search, visibleCols, searchInCols); };
  const doPage = p => { setPage(p); loadRecords(p, sort, search, visibleCols, searchInCols); };
  const applyColChange = (vcols) => { setVisibleCols(vcols); setPage(1); loadRecords(1, sort, search, vcols, searchInCols); };
  const applySearchCols = (scols) => { setSearchInCols(scols); };

  const handleRowClick = async rowId => {
    const res = await apiFetch(`/api/db/records/${encodeURIComponent(rowId)}?pipeline_id=${pipeline.id}`);
    const rec = await res.json();
    if (!res.ok) return;
    setSelectedRecord(rec);
  };

  const runSql = async () => {
    const sql = sqlText.trim();
    if (!sql) return;
    const res = await apiFetch('/api/db/query', {
      method: 'POST',
      body: JSON.stringify({ pipeline_id: pipeline.id, sql }),
    });
    setSqlResult(await res.json());
  };

  const exportCsv = () => {
    if (!sqlResult?.rows) return;
    const csv = [
      sqlResult.columns.join(','),
      ...sqlResult.rows.map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))
    ].join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
    a.download = `pipeline_${pipeline.id}_query.csv`;
    a.click();
  };

  const exportTableCsv = () => {
    if (!records?.rows) return;
    const csv = [
      records.columns.join(','),
      ...records.rows.map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))
    ].join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
    a.download = `pipeline_${pipeline.id}_records_p${page}.csv`;
    a.click();
  };

  const perPage = records?.per_page || 100;
  const totalPages = records ? Math.ceil(records.total / perPage) : 0;

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h3 className="font-semibold text-lg mb-1">{isLLM ? 'Stage 6: LLM Results Review' : 'Stage 3: Results Review'}</h3>
        <p className="text-sm text-gray-500 mb-4">Browse and search the filtered records database. Click a row for full details.</p>
        {records && (
          <div className="flex gap-4 text-sm text-gray-600 mb-4">
            <span><strong>{(records.total || 0).toLocaleString()}</strong> records</span>
          </div>
        )}

        {/* Search + sort controls */}
        <div className="flex gap-3 mb-3 flex-wrap">
          <input ref={searchInputRef} type="text" placeholder="Search..."
            defaultValue={search}
            onKeyDown={e => e.key === 'Enter' && doSearch()}
            className="flex-1 min-w-[200px] border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
          {schema.length > 0 && (
            <ColPickerDropdown label="Search in" allCols={schema} selected={searchInCols} onChange={applySearchCols} />
          )}
          <select value={sort} onChange={e => doSort(e.target.value)}
            className="border rounded-lg px-3 py-2 text-sm">
            <option value="publication_year">Year ↓</option>
            <option value="title">Title ↑</option>
            <option value="fwci">FWCI ↓</option>
            {isLLM && <>
              <option value="llm_fuel_classification">LLM Fuel Type</option>
              <option value="llm_confidence">LLM Confidence</option>
              <option value="llm_is_hydrocarbon">Is Hydrocarbon</option>
            </>}
          </select>
          <button onClick={doSearch}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 transition">Search</button>
        </div>

        {/* Sub-group filter (only shown when subgroups are configured) */}
        {fuelSubgroups.length > 1 && (
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-medium text-gray-600">Fuel sub-group:</span>
            <div className="flex flex-wrap gap-1.5">
              <button onClick={() => setSubgroupFilter('')}
                className={`text-xs px-2.5 py-1 rounded-full border transition ${!subgroupFilter ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'}`}>
                All
              </button>
              {fuelSubgroups.map(sg => (
                <button key={sg.name} onClick={() => setSubgroupFilter(sg.name === subgroupFilter ? '' : sg.name)}
                  className={`text-xs px-2.5 py-1 rounded-full border transition ${subgroupFilter === sg.name ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'}`}>
                  {sg.name}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Export */}
        <div className="flex gap-3 mb-4 items-center flex-wrap">
          {records?.rows?.length > 0 && (
            <button onClick={exportTableCsv}
              className="border rounded-lg px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 transition">
              Export CSV (page)
            </button>
          )}
        </div>

        <div className="min-h-[12rem] rounded-xl border border-gray-200 overflow-hidden">
          {!records ? (
            <div className="text-gray-400 text-center py-12">Loading records…</div>
          ) : !records.rows?.length ? (
            <div className="text-gray-400 text-center py-12">No records found</div>
          ) : (() => {
            const cols = records.columns;
            const ci = name => cols.indexOf(name);
            const TITLE = ci('title'); const DOI = ci('doi'); const YEAR = ci('publication_year');
            const FWCI  = ci('fwci'); const TYPE = ci('type'); const OA = ci('open_access_oa_status');
            const ABS   = ci('abstract_text'); const MFUEL = ci('matched_fuel_terms');
            const MIMP  = ci('matched_impact_terms'); const INST = ci('authors_institutions');
            const SOURCE= ci('primary_location_source_display_name');
            const parseArr = v => { if (!v) return []; try { const p = JSON.parse(v); return Array.isArray(p) ? p : []; } catch { return []; } };
            const OA_CHIP = { gold: 'bg-yellow-50 text-yellow-600', green: 'bg-green-50 text-green-600', hybrid: 'bg-purple-50 text-purple-600', bronze: 'bg-orange-50 text-orange-600' };

            return (
              <div className="divide-y divide-gray-100">
                {records.rows.map((row, ri) => {
                  const matchedJson  = MFUEL >= 0 ? row[MFUEL] : null;
                  const rowSubgroups = computeRowSubgroups(matchedJson, fuelSubgroups);
                  if (subgroupFilter && !rowSubgroups.includes(subgroupFilter)) return null;

                  const rowId   = row[ci('openalex_id')] || ri;
                  const title   = TITLE >= 0 ? (row[TITLE] || '') : '';
                  const doi     = DOI  >= 0 ? (row[DOI]   || '') : '';
                  const year    = YEAR >= 0 ? row[YEAR]   : null;
                  const fwci    = FWCI >= 0 && row[FWCI] != null ? Number(row[FWCI]) : null;
                  const oaStat  = OA   >= 0 ? (row[OA]    || '') : '';
                  const abstr   = ABS  >= 0 ? (row[ABS]   || '') : '';
                  const insts   = parseArr(INST >= 0 ? row[INST] : null).slice(0, 3);
                  const source  = SOURCE >= 0 ? (row[SOURCE] || '') : '';
                  const fuelArr = parseArr(matchedJson);
                  const impArr  = parseArr(MIMP >= 0 ? row[MIMP] : null);
                  const doiUrl  = doi ? `https://doi.org/${doi.replace(/^https?:\/\/(dx\.)?doi\.org\//, '')}` : null;
                  const fwciCls = !fwci ? 'bg-gray-100 text-gray-400' : fwci >= 3 ? 'bg-green-100 text-green-700 font-bold' : fwci >= 1 ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500';

                  return (
                    <div key={ri} onClick={() => handleRowClick(rowId)}
                      className="px-5 py-4 hover:bg-blue-50 cursor-pointer transition-colors group">
                      {/* Row 1: title */}
                      <div className="flex items-start gap-3">
                        <div className="flex-1 min-w-0">
                          <span className="text-sm font-semibold text-gray-900 group-hover:text-blue-700 transition-colors leading-snug">
                            {doiUrl ? (
                              <a href={doiUrl} target="_blank" rel="noopener"
                                onClick={e => e.stopPropagation()}
                                className="hover:underline decoration-blue-400">{title || rowId}</a>
                            ) : (title || rowId)}
                          </span>
                        </div>
                        {/* FWCI badge */}
                        {fwci != null && (
                          <span className={`flex-shrink-0 text-xs px-2 py-0.5 rounded-full ${fwciCls}`} title="Field-Weighted Citation Impact">
                            FWCI {fwci.toFixed(1)}
                          </span>
                        )}
                      </div>

                      {/* Row 2: meta chips */}
                      <div className="flex flex-wrap gap-1.5 mt-1.5 items-center">
                        {year && <span className="text-xs text-gray-500 font-medium">{year}</span>}
                        {oaStat && <span className={`text-xs px-1.5 py-0.5 rounded ${OA_CHIP[oaStat] || 'bg-gray-100 text-gray-500'}`}>{oaStat}</span>}
                        {source && <span className="text-xs text-gray-400 italic truncate max-w-[220px]">{source}</span>}
                        {rowSubgroups.map(sg => (
                          <span key={sg} className="text-xs px-2 py-0.5 bg-blue-100 text-blue-700 rounded-full">{sg}</span>
                        ))}
                        {fuelArr.length > 0 && (
                          <span className="text-xs text-orange-500">
                            {fuelArr.slice(0, 3).join(' · ')}{fuelArr.length > 3 ? ` +${fuelArr.length - 3}` : ''}
                          </span>
                        )}
                        {impArr.length > 0 && (
                          <span className="text-xs text-teal-500">
                            {impArr.slice(0, 2).join(' · ')}{impArr.length > 2 ? ` +${impArr.length - 2}` : ''}
                          </span>
                        )}
                      </div>

                      {/* Row 3: abstract snippet + institutions */}
                      {(abstr || insts.length > 0) && (
                        <div className="flex gap-4 mt-1.5">
                          {abstr && (
                            <p className="text-xs text-gray-400 leading-relaxed flex-1 min-w-0 line-clamp-2">
                              {abstr.slice(0, 220)}{abstr.length > 220 ? '…' : ''}
                            </p>
                          )}
                          {insts.length > 0 && (
                            <div className="flex-shrink-0 text-xs text-gray-400 max-w-[220px] text-right">
                              {insts.map((inst, i) => <div key={i} className="truncate">{inst}</div>)}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })()}
        </div>

        {totalPages > 0 && (
          <div className="flex justify-between items-center mt-3 text-sm text-gray-500 flex-wrap gap-2">
            <span>{(records?.total || 0).toLocaleString()} records</span>
            <div className="flex items-center gap-2">
              <button disabled={page <= 1} onClick={() => doPage(1)}
                className="px-2 py-1 border rounded hover:bg-gray-100 disabled:opacity-40 text-xs">«</button>
              <button disabled={page <= 1} onClick={() => doPage(page - 1)}
                className="px-3 py-1 border rounded hover:bg-gray-100 disabled:opacity-40">← Prev</button>
              <span className="flex items-center gap-1 text-xs">
                <input type="number" min="1" max={totalPages} value={page}
                  onChange={e => { const p = Math.max(1, Math.min(totalPages, Number(e.target.value) || 1)); doPage(p); }}
                  className="w-12 border rounded px-1 py-0.5 text-center focus:outline-none focus:ring-1 focus:ring-blue-400" />
                <span>/ {totalPages}</span>
              </span>
              <button disabled={page >= totalPages} onClick={() => doPage(page + 1)}
                className="px-3 py-1 border rounded hover:bg-gray-100 disabled:opacity-40">Next →</button>
              <button disabled={page >= totalPages} onClick={() => doPage(totalPages)}
                className="px-2 py-1 border rounded hover:bg-gray-100 disabled:opacity-40 text-xs">»</button>
            </div>
          </div>
        )}
      </div>

      {selectedRecord && <RecordModal record={selectedRecord} fuelSubgroups={fuelSubgroups} onClose={() => setSelectedRecord(null)} />}
    </div>
  );
}

// ── QueryPanel ────────────────────────────────────────────────────────────────

function QueryPanel({ pipeline, initialSql, chartConfigs }) {
  const [sqlText, setSqlText] = useState(initialSql || 'SELECT * FROM filtered_records LIMIT 20');
  const [sqlResult, setSqlResult] = useState(null);
  const [running, setRunning] = useState(false);

  // Sync textarea when parent pushes a chart query via initialSql
  useEffect(() => {
    if (initialSql) setSqlText(initialSql);
  }, [initialSql]);

  const runSql = async () => {
    const sql = sqlText.trim();
    if (!sql) return;
    setRunning(true);
    const res = await apiFetch('/api/db/query', {
      method: 'POST',
      body: JSON.stringify({ pipeline_id: pipeline.id, sql }),
    });
    setSqlResult(await res.json());
    setRunning(false);
  };

  const exportCsv = () => {
    if (!sqlResult?.rows) return;
    const csv = [
      sqlResult.columns.join(','),
      ...sqlResult.rows.map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))
    ].join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
    a.download = `pipeline_${pipeline.id}_query.csv`;
    a.click();
  };

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-3">
          <h4 className="font-semibold text-sm">SQL Query</h4>
          {chartConfigs && chartConfigs.length > 0 && (
            <select className="text-xs border rounded px-2 py-1 text-gray-600"
              defaultValue=""
              onChange={e => { if (e.target.value) setSqlText(e.target.value); e.target.value = ''; }}>
              <option value="">Load chart query…</option>
              {chartConfigs.map(c => (
                <option key={c.id} value={c.sql}>{c.title}</option>
              ))}
            </select>
          )}
        </div>
        <textarea value={sqlText} onChange={e => setSqlText(e.target.value)} rows={6}
          placeholder="SELECT * FROM filtered_records LIMIT 20"
          onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runSql(); } }}
          className="w-full border rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-400 mb-2" />
        <p className="text-xs text-gray-400 mb-3">Ctrl+Enter to run · Table: <code className="bg-gray-100 px-1 rounded">filtered_records</code></p>
        <div className="flex gap-3 items-center">
          <button onClick={runSql} disabled={running}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 transition disabled:opacity-50 flex items-center gap-2">
            {running && <span className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />}
            Run Query
          </button>
          {sqlResult?.rows && (
            <button onClick={exportCsv}
              className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-sm hover:bg-gray-50 transition">
              Export CSV
            </button>
          )}
        </div>
      </div>

      {sqlResult && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          {sqlResult.error ? (
            <p className="text-red-600 text-sm p-4">{sqlResult.error}</p>
          ) : !sqlResult.rows?.length ? (
            <p className="text-gray-400 text-sm p-4">No results</p>
          ) : (
            <>
              <div className="px-4 py-2 border-b border-gray-100 bg-gray-50 text-xs text-gray-500">
                {sqlResult.rows.length} row(s){sqlResult.truncated ? ' (truncated)' : ''}
              </div>
              <div className="overflow-x-auto">
                <table className="text-xs w-full">
                  <thead className="bg-gray-50 border-b">
                    <tr>{sqlResult.columns.map(c => <th key={c} className="px-3 py-2 text-left font-medium whitespace-nowrap">{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {sqlResult.rows.map((r, i) => (
                      <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                        {r.map((v, j) => <td key={j} className="px-3 py-1.5 max-w-xs truncate">{String(v ?? '')}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

