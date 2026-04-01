// ── Combined ReportStage ───────────────────────────────────────────────────────

function ReportStage({ pipeline, isLLM, pipelineStatus }) {
  const [topN, setTopN] = useState(20);
  const [querySql, setQuerySql] = useState('SELECT * FROM filtered_records LIMIT 20');
  const [chartConfigs, setChartConfigs] = useState(null); // null = loading
  const [refreshKey, setRefreshKey] = useState(0);
  const [recordViewer, setRecordViewer] = useState(null); // { sql, title } | null

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

  const scrollToChart = id => {
    const el = document.getElementById(`chart-${id}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const openRecordViewer = (sql, title) => setRecordViewer({ sql, title });

  // Stage summary data
  const recordCount = pipelineStatus?.record_count;
  const llmDone = pipelineStatus?.llm_done;

  const keywordSummary = useMemo(() => {
    const groups = _migrateGroups(pipeline.config || {});
    const totalTerms = groups.reduce((s, g) => s + _totalTerms(g), 0);
    return { groups: groups.length, terms: totalTerms };
  }, [pipeline.config]);

  return (
    <div>
      {/* ── Stage Summary ── */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 mb-5">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">
          {isLLM ? 'LLM Report — Stage Summary' : 'Search Report — Stage Summary'}
        </h3>
        <div className="flex flex-wrap gap-8">
          <div>
            <div className="text-xs text-gray-400 mb-0.5">Keywords configured</div>
            <div className="text-base font-semibold text-gray-800">
              {keywordSummary.groups} group{keywordSummary.groups !== 1 ? 's' : ''}, {keywordSummary.terms} terms
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-400 mb-0.5">Search results</div>
            <div className="text-2xl font-bold text-gray-900">
              {recordCount != null ? (
                <button
                  onClick={() => openRecordViewer('SELECT * FROM filtered_records', 'All Search Results')}
                  className="underline decoration-blue-400 hover:text-blue-600 transition-colors">
                  {recordCount.toLocaleString()}
                </button>
              ) : <span className="text-gray-300 text-lg">—</span>}
            </div>
            <div className="text-xs text-gray-400">filtered records</div>
          </div>
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
      </div>

      {/* ── Chart TOC ── */}
      {visible.length > 0 && (
        <div className="sticky top-12 z-20 bg-white border border-gray-200 rounded-xl mb-4 shadow-sm">
          <div className="px-4 py-2 flex items-center gap-2 overflow-x-auto">
            <span className="text-xs text-gray-400 font-medium flex-shrink-0 mr-1">Jump to:</span>
            {visible.map(id => (
              <button key={id} onClick={() => scrollToChart(id)}
                className="flex-shrink-0 text-xs px-2.5 py-1 rounded-full border border-gray-200 text-gray-600 hover:bg-emerald-50 hover:border-emerald-300 hover:text-emerald-700 transition whitespace-nowrap">
                {cfgMap[id]?.title || id}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ── Charts toolbar ── */}
      <div className="bg-white rounded-xl border border-gray-200 px-5 py-3 mb-4 flex items-center gap-4">
        <span className="text-sm font-medium text-gray-600">Top N</span>
        <input type="range" min="5" max="50" step="5" value={topN}
          onChange={e => setTopN(Number(e.target.value))}
          className="w-36 accent-emerald-600" />
        <span className="text-sm font-semibold text-emerald-700 w-6">{topN}</span>
        <button onClick={() => setRefreshKey(k => k + 1)} title="Refresh all charts"
          className="ml-auto text-sm text-gray-500 hover:text-gray-700 border border-gray-200 hover:border-gray-400 px-3 py-1 rounded-lg transition flex items-center gap-1.5">
          ↻ Refresh
        </button>
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
            <div key={`${id}-${topN}-${refreshKey}`} id={`chart-${id}`}>
              <ChartPanel chartId={id} pipelineId={pipeline.id}
                topN={topN}
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
