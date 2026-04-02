// ── Combined ReportStage ───────────────────────────────────────────────────────

const TOP_N = 20; // fixed, no UI slider

function ReportStage({ pipeline, isLLM, pipelineStatus }) {
  const [querySql, setQuerySql] = useState('SELECT * FROM filtered_records LIMIT 20');
  const [chartConfigs, setChartConfigs] = useState(null); // null = loading
  const [recordViewer, setRecordViewer] = useState(null); // { sql, title } | null
  const [expandedSg, setExpandedSg] = useState({}); // "gi-si" → bool

  useEffect(() => {
    apiFetch('/api/charts/configs')
      .then(r => r.json())
      .then(list => setChartConfigs((list || []).filter(c => !c.llm_only || isLLM)))
      .catch(() => setChartConfigs([]));
  }, [isLLM]);

  const visible = useMemo(() => {
    if (!chartConfigs) return [];
    return CHART_ORDER.filter(id => chartConfigs.some(c => c.id === id));
  }, [chartConfigs]);

  const cfgMap = useMemo(
    () => Object.fromEntries((chartConfigs || []).map(c => [c.id, c])),
    [chartConfigs]
  );

  const openRecordViewer = (sql, title) => setRecordViewer({ sql, title });

  // ── Pipeline status ──────────────────────────────────────────────────────
  const recordCount = pipelineStatus?.record_count;
  const llmDone     = pipelineStatus?.llm_done;
  const dbSizeMb    = pipelineStatus?.db_size_mb;


  // Derive richer group data including actual term lists
  const groupDataFull = useMemo(() => {
    const groups = _migrateGroups(pipeline.config || {});
    return groups.map(g => ({
      name: g.name,
      subgroups: (g.subgroups || []).map(sg => ({
        name: sg.name,
        terms: sg.terms || [],
        termCount: (sg.terms || []).length,
        isOthers: !!sg.is_others,
      })),
      totalTerms: (g.subgroups || []).reduce((s, sg) => s + (sg.terms || []).length, 0),
    }));
  }, [pipeline.config]);

  // Pull funnel stats from pipeline_status search_progress
  const prog = pipelineStatus?.search_progress;

  return (
    <div>
      {/* ── Stage Summary card ── */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 mb-5">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-4">
          {isLLM ? 'LLM Report — Stage Summary' : 'Search Report — Stage Summary'}
        </h3>

        {/* Top stats row */}
        <div className="flex flex-wrap gap-8 mb-4">
          <div>
            <div className="text-xs text-gray-400 mb-0.5">Saved records</div>
            <div className="text-2xl font-bold text-gray-900">
              {recordCount != null ? (
                <button
                  onClick={() => openRecordViewer('SELECT * FROM filtered_records', 'All Search Results')}
                  className="underline decoration-blue-400 hover:text-blue-600 transition-colors">
                  {recordCount.toLocaleString()}
                </button>
              ) : <span className="text-gray-300 text-lg">—</span>}
            </div>
            <div className="text-xs text-gray-400">publications matched keywords</div>
          </div>

          {prog?.seen > 0 && (
            <div>
              <div className="text-xs text-gray-400 mb-0.5">Records scanned</div>
              <div className="text-base font-semibold text-gray-700">{prog.seen.toLocaleString()}</div>
              {prog.inserted > 0 && (
                <div className="text-xs text-gray-400">{(prog.inserted / prog.seen * 100).toFixed(3)}% match rate</div>
              )}
            </div>
          )}

          {dbSizeMb != null && (
            <div>
              <div className="text-xs text-gray-400 mb-0.5">Database size</div>
              <div className="text-base font-semibold text-gray-700">{dbSizeMb} MB</div>
            </div>
          )}

          {isLLM && (
            <div>
              <div className="text-xs text-gray-400 mb-0.5">LLM classified</div>
              <div className="text-2xl font-bold text-gray-900">
                {llmDone != null ? (
                  <button
                    onClick={() => openRecordViewer(
                      'SELECT * FROM filtered_records WHERE llm_is_hydrocarbon IS NOT NULL',
                      'LLM Classified Records'
                    )}
                    className="underline decoration-purple-400 hover:text-purple-600 transition-colors">
                    {llmDone.toLocaleString()}
                  </button>
                ) : <span className="text-gray-300 text-lg">—</span>}
              </div>
              <div className="text-xs text-gray-400">LLM processed records</div>
            </div>
          )}
        </div>

        {/* Screening funnel (when stage 2 data available) */}
        {prog?.seen > 0 && (() => {
          const sk = prog.skip_stats || {};
          const notEn = sk['not_english'] || 0;
          const noDoi = sk['no_doi'] || 0;
          const noAbs = sk['no_abstract'] || 0;
          const noKw  = sk['no_keywords'] || 0;
          const emAbs = sk['empty_abstract'] || 0;
          const inserted = prog.inserted || 0;
          const otherSkip = prog.seen - notEn - noDoi - noAbs - noKw - emAbs - inserted;

          return (
            <div className="border-t border-gray-100 pt-3 mb-4">
              <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Screening funnel</div>
              <div className="space-y-1 font-mono text-xs">
                {[
                  { label: 'records scanned', count: prog.seen, bold: false },
                  notEn > 0 && { label: '↳ non-English (pre-screened)', count: notEn, dim: true },
                  noDoi > 0 && { label: '↳ no DOI', count: noDoi, dim: true },
                  noAbs > 0 && { label: '↳ no abstract', count: noAbs, dim: true },
                  noKw  > 0 && { label: '↳ no matching keywords', count: noKw, dim: true },
                  (emAbs + Math.max(0,otherSkip)) > 0 && { label: '↳ other reasons', count: emAbs + Math.max(0,otherSkip), dim: true },
                  { label: '✓ saved', count: inserted, green: true },
                ].filter(Boolean).map((row, i) => (
                  <div key={i} className={`flex gap-3 ${row.dim ? 'pl-4 text-gray-400' : ''}`}>
                    <span className={`w-28 text-right ${row.green ? 'text-green-700 font-bold' : row.dim ? 'text-gray-400' : 'text-gray-700 font-semibold'}`}>
                      {row.count.toLocaleString()}
                    </span>
                    <span className={row.green ? 'text-green-700 font-semibold' : ''}>{row.label}</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })()}

        {/* Keyword group/subgroup breakdown — expandable */}
        {groupDataFull.length > 0 && (
          <div className="border-t border-gray-100 pt-4 space-y-4">
            {groupDataFull.map((g, gi) => (
              <div key={gi}>
                <div className="text-xs text-gray-500 font-semibold mb-2">
                  {g.name}
                  <span className="ml-1.5 text-gray-400 font-normal">— {g.totalTerms} terms</span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {g.subgroups.map((sg, si) => {
                    const key = `${gi}-${si}`;
                    const isExp = !!expandedSg[key];
                    const chipCls = sg.isOthers
                      ? 'border-gray-200 bg-gray-50 text-gray-400'
                      : gi === 0
                        ? 'border-orange-200 bg-orange-50 text-orange-700'
                        : 'border-teal-200 bg-teal-50 text-teal-700';
                    return (
                      <div key={si} className="flex flex-col">
                        <button
                          onClick={() => setExpandedSg(prev => ({ ...prev, [key]: !isExp }))}
                          className={`text-xs px-2 py-0.5 rounded-full border flex items-center gap-1 transition-colors ${chipCls} hover:opacity-80`}
                          title="Click to show/hide terms"
                        >
                          {sg.name}
                          <span className="opacity-60">({sg.termCount})</span>
                          <span className="ml-0.5 opacity-50">{isExp ? '▲' : '▼'}</span>
                        </button>
                        {isExp && sg.terms.length > 0 && (
                          <div className="mt-1 ml-1 p-2 bg-gray-50 rounded-lg border border-gray-200 max-w-xs">
                            <div className="flex flex-wrap gap-1">
                              {sg.terms.map(t => (
                                <span key={t} className="text-xs bg-white border border-gray-200 rounded px-1.5 py-0.5 text-gray-600">{t}</span>
                              ))}
                            </div>
                          </div>
                        )}
                        {isExp && sg.terms.length === 0 && (
                          <div className="mt-1 ml-1 text-xs text-gray-400 italic">No terms</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Charts grid ── */}
      {chartConfigs === null ? (
        <div className="flex items-center justify-center py-20 text-gray-400 mb-4">
          <span className="w-5 h-5 border-2 border-gray-300 border-t-blue-500 rounded-full animate-spin mr-2" />
          Loading…
        </div>
      ) : visible.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center text-gray-400 mb-6">
          No chart data available yet — run the search stage first.
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
          {visible.map(id => (
            <div key={id} id={`chart-${id}`}>
              <ChartPanel chartId={id} pipelineId={pipeline.id}
                topN={TOP_N}
                title={cfgMap[id]?.title || id}
                sql={cfgMap[id]?.sql || ''}
                onViewQuery={sql => setQuerySql(sql)} />
            </div>
          ))}
        </div>
      )}

      {/* ── Query panel ── */}
      <QueryPanel pipeline={pipeline} initialSql={querySql} chartConfigs={chartConfigs || []} />

      {/* ── Non-modal record viewer ── */}
      {recordViewer && (
        <RecordViewerPanel
          key={recordViewer.sql}
          pipeline={pipeline}
          initialSql={recordViewer.sql}
          title={recordViewer.title}
          onClose={() => setRecordViewer(null)}
        />
      )}
    </div>
  );
}
