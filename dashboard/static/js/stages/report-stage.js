// ── Combined ReportStage ───────────────────────────────────────────────────────

const TOP_N = 20;
const SPLIT_MIN = 25;  // minimum % for each pane
const SPLIT_MAX = 75;
const NAV_HEIGHT = 48; // px — top app bar height

function ReportStage({ pipeline, isLLM, pipelineStatus }) {
  const [chartConfigs, setChartConfigs] = useState(null);
  const [recordViewer, setRecordViewer] = useState(null);
  const [expandedSg, setExpandedSg]     = useState({});

  // ── Saved subsets ──────────────────────────────────────────────────────────
  const [subsets, setSubsets]           = useState([]);
  const [activeSubsetIdx, setActiveSubsetIdx] = useState(null);

  // ── Pinned charts ──────────────────────────────────────────────────────────
  // Each pinned entry is a snapshot: { key, chartId, subsetIdx, title }
  const [pinnedCharts, setPinnedCharts] = useState([]);
  const [cooccPinned, setCooccPinned]   = useState(false);         // co-occurrence panel

  const pinChart = useCallback((id, title) => {
    setPinnedCharts(prev => [
      ...prev,
      { key: `${id}_${Date.now()}`, chartId: id, subsetIdx: activeSubsetIdx, title: title || id },
    ]);
  }, [activeSubsetIdx]);

  const unpinChart = useCallback(key => {
    setPinnedCharts(prev => prev.filter(p => p.key !== key));
  }, []);

  // ── Split pane ─────────────────────────────────────────────────────────────
  const [splitRatio, setSplitRatio]     = useState(58);   // left pane %
  const [narrowTab, setNarrowTab]       = useState('charts'); // 'charts' | 'records'
  const [isNarrow, setIsNarrow]         = useState(() => window.innerWidth < 768);
  const splitContainerRef               = useRef(null);
  const isDragging                      = useRef(false);

  useEffect(() => {
    const fn = () => setIsNarrow(window.innerWidth < 768);
    window.addEventListener('resize', fn);
    return () => window.removeEventListener('resize', fn);
  }, []);

  // When viewer is closed, reset tab to charts
  useEffect(() => {
    if (!recordViewer) setNarrowTab('charts');
  }, [recordViewer]);

  const onDividerMouseDown = useCallback((e) => {
    e.preventDefault();
    isDragging.current = true;
    document.body.style.cursor      = 'col-resize';
    document.body.style.userSelect  = 'none';

    const onMove = (ev) => {
      if (!splitContainerRef.current) return;
      const rect = splitContainerRef.current.getBoundingClientRect();
      const pct  = Math.max(SPLIT_MIN, Math.min(SPLIT_MAX,
        (ev.clientX - rect.left) / rect.width * 100));
      setSplitRatio(Math.round(pct * 10) / 10);
    };
    const onUp = () => {
      isDragging.current = false;
      document.body.style.cursor     = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, []);

  // Touch support for divider
  const onDividerTouchStart = useCallback((e) => {
    const touch = e.touches[0];
    const onMove = (ev) => {
      if (!splitContainerRef.current) return;
      const t = ev.touches[0];
      const rect = splitContainerRef.current.getBoundingClientRect();
      const pct = Math.max(SPLIT_MIN, Math.min(SPLIT_MAX,
        (t.clientX - rect.left) / rect.width * 100));
      setSplitRatio(Math.round(pct * 10) / 10);
    };
    const onEnd = () => {
      document.removeEventListener('touchmove', onMove);
      document.removeEventListener('touchend', onEnd);
    };
    document.addEventListener('touchmove', onMove, { passive: true });
    document.addEventListener('touchend', onEnd);
  }, []);

  // ── Data loading ───────────────────────────────────────────────────────────
  const loadSubsets = useCallback(() => {
    apiFetch(`/api/subsets?pipeline_id=${pipeline.id}`)
      .then(r => r.json())
      .then(data => setSubsets(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, [pipeline.id]);

  useEffect(() => { loadSubsets(); }, [loadSubsets]);

  const deleteSubset = async (idx) => {
    if (!confirm(`Delete subset "${subsets[idx]?.name}"?`)) return;
    await apiFetch('/api/subsets', {
      method: 'DELETE',
      body: JSON.stringify({ pipeline_id: pipeline.id, index: idx }),
    });
    if (activeSubsetIdx === idx) setActiveSubsetIdx(null);
    loadSubsets();
  };

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

  const openDatasetRecords = useCallback(() => {
    const sql   = (activeSubsetIdx != null && subsets[activeSubsetIdx])
      ? subsets[activeSubsetIdx].sql
      : 'SELECT * FROM filtered_records';
    const label = activeSubsetIdx != null ? subsets[activeSubsetIdx].name : 'All Records';
    setRecordViewer({ sql, title: label });
    if (isNarrow) setNarrowTab('records');
  }, [activeSubsetIdx, subsets, isNarrow]);

  const openChartRecords = useCallback((sql, chartTitle) => {
    setRecordViewer({ sql, title: chartTitle });
    if (isNarrow) setNarrowTab('records');
  }, [isNarrow]);

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

  // ── Shared: main content (charts + sidebar controls) ───────────────────────
  const renderMainContent = (inSplit) => (
    <div className={inSplit ? 'space-y-4' : 'space-y-5'}>

      {/* Keyword groups card */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <div className="flex items-start justify-between gap-4 flex-wrap mb-3">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
            {isLLM ? 'LLM Report' : 'Search Report'} — Keyword Groups
          </h3>
          <button onClick={openDatasetRecords}
            className="text-xs px-3 py-1.5 rounded-lg border border-blue-200 text-blue-600 hover:bg-blue-50 transition flex items-center gap-1.5 flex-shrink-0">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 10h16M4 14h16M4 18h16"/>
            </svg>
            Open Records Viewer
          </button>
        </div>
        {groupDataFull.length > 0 ? (
          <div className="space-y-4">
            {groupDataFull.map((g, gi) => (
              <div key={gi}>
                <div className="text-xs text-gray-500 font-semibold mb-2">
                  {g.name}
                  <span className="ml-1.5 text-gray-400 font-normal">— {g.totalTerms} terms</span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {g.subgroups.map((sg, si) => {
                    const key    = `${gi}-${si}`;
                    const isExp  = !!expandedSg[key];
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
                          title="Click to show/hide terms">
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
        ) : (
          <p className="text-xs text-gray-400 italic">No keyword groups configured yet.</p>
        )}
      </div>

      {/* Dataset / subset selector */}
      {subsets.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 px-5 py-3 flex items-center gap-3 flex-wrap">
          <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide flex-shrink-0">Dataset</span>
          <button
            onClick={() => setActiveSubsetIdx(null)}
            className={`text-xs px-3 py-1.5 rounded-full border transition ${activeSubsetIdx === null ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'}`}>
            All records
          </button>
          {subsets.map((s, i) => (
            <button key={i}
              onClick={() => setActiveSubsetIdx(i === activeSubsetIdx ? null : i)}
              className={`text-xs px-3 py-1.5 rounded-full border transition ${activeSubsetIdx === i ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'}`}>
              {s.name}
            </button>
          ))}
          {activeSubsetIdx != null && (
            <button onClick={() => deleteSubset(activeSubsetIdx)}
              className="ml-auto text-xs text-red-400 hover:text-red-600 transition">
              Delete subset
            </button>
          )}
        </div>
      )}

      {/* Terms Co-occurrence (dynamic heatmap, user-selectable axes) */}
      <TermsCooccurrencePanel
        pipeline={pipeline}
        pipelineId={pipeline.id}
        isLLM={isLLM}
        onOpenRecords={openChartRecords}
        isPinned={cooccPinned}
        onTogglePin={() => setCooccPinned(p => !p)}
      />

      {/* Charts grid */}
      {chartConfigs === null ? (
        <div className="flex items-center justify-center py-20 text-gray-400">
          <span className="w-5 h-5 border-2 border-gray-300 border-t-blue-500 rounded-full animate-spin mr-2" />
          Loading…
        </div>
      ) : visible.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center text-gray-400">
          No chart data available yet — run the search stage first.
        </div>
      ) : (
        <div className={`grid gap-4 ${inSplit ? 'grid-cols-1 xl:grid-cols-2' : 'grid-cols-1 lg:grid-cols-2'}`}>
          {visible.map(id => (
            <div key={id} id={`chart-${id}`}>
              <ChartPanel chartId={id} pipelineId={pipeline.id}
                topN={TOP_N}
                title={cfgMap[id]?.title || id}
                subsetIdx={activeSubsetIdx}
                chartParams={cfgMap[id]?.params || []}
                chartSql={cfgMap[id]?.sql || null}
                onOpenRecords={openChartRecords}
                isPinned={false}
                onTogglePin={() => pinChart(id, cfgMap[id]?.title || id)} />
            </div>
          ))}
        </div>
      )}
    </div>
  );

  // ── Split pane layout (wide screens, viewer open) ──────────────────────────
  if (recordViewer && !isNarrow) {
    return (
      <div style={{ position: 'fixed', top: NAV_HEIGHT, left: 0, right: 0, bottom: 0, zIndex: 20 }}>
        <div ref={splitContainerRef} className="flex h-full">

          {/* Left pane: main content */}
          <div className="flex flex-col overflow-hidden bg-gray-50"
            style={{ width: `${splitRatio}%`, minWidth: 0 }}>
            <div className="flex-1 overflow-y-auto p-5">
              {renderMainContent(true)}
            </div>
          </div>

          {/* Drag divider */}
          <div
            onMouseDown={onDividerMouseDown}
            onTouchStart={onDividerTouchStart}
            className="relative flex-shrink-0 w-1.5 bg-gray-200 hover:bg-blue-400 active:bg-blue-500 cursor-col-resize transition-colors group select-none"
            title="Drag to resize">
            {/* Visible handle pip */}
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2
              w-4 h-10 rounded-full bg-gray-300 group-hover:bg-blue-400 transition-colors
              flex flex-col items-center justify-center gap-0.5">
              <div className="w-0.5 h-3 rounded bg-white/70"/>
              <div className="w-0.5 h-3 rounded bg-white/70"/>
            </div>
            {/* Ratio presets — appear on hover */}
            <div className="absolute top-4 left-1/2 -translate-x-1/2 hidden group-hover:flex
              flex-col gap-1 bg-white border border-gray-200 rounded-lg shadow-lg p-1.5 z-10 w-14">
              {[[33,67],[50,50],[60,40],[67,33]].map(([l]) => (
                <button key={l}
                  onMouseDown={e => { e.stopPropagation(); setSplitRatio(l); }}
                  className={`text-xs py-0.5 rounded hover:bg-blue-50 hover:text-blue-600 transition text-center
                    ${splitRatio === l ? 'bg-blue-100 text-blue-700 font-semibold' : 'text-gray-500'}`}>
                  {l}/{100-l}
                </button>
              ))}
            </div>
          </div>

          {/* Right pane: pinned charts + record viewer (embedded) */}
          <div className="flex flex-col overflow-hidden border-l border-gray-200"
            style={{ flex: 1, minWidth: 0 }}>
            {/* Pinned charts strip */}
            {(cooccPinned || pinnedCharts.length > 0) && (
              <div className="flex-shrink-0 border-b border-gray-200 overflow-y-auto" style={{ maxHeight: '40%' }}>
                <div className="p-2 space-y-2 bg-gray-50">
                  {cooccPinned && (
                    <TermsCooccurrencePanel pipeline={pipeline} pipelineId={pipeline.id} isLLM={isLLM}
                      onOpenRecords={openChartRecords} isPinned={true} onTogglePin={() => setCooccPinned(false)} />
                  )}
                  {pinnedCharts.map(pin => (
                    <ChartPanel key={pin.key} chartId={pin.chartId} pipelineId={pipeline.id}
                      topN={TOP_N} title={`📌 ${pin.title}`}
                      subsetIdx={pin.subsetIdx} chartParams={cfgMap[pin.chartId]?.params || []}
                      chartSql={cfgMap[pin.chartId]?.sql || null} onOpenRecords={openChartRecords}
                      isPinned={true} onTogglePin={() => unpinChart(pin.key)} />
                  ))}
                </div>
              </div>
            )}
            <RecordViewerPanel
              key={recordViewer.sql + (recordViewer.initialFuelSubgroup || '') + (recordViewer.initialImpactSubgroup || '')}
              pipeline={pipeline}
              initialSql={recordViewer.sql}
              title={recordViewer.title}
              initialFuelSubgroup={recordViewer.initialFuelSubgroup}
              initialImpactSubgroup={recordViewer.initialImpactSubgroup}
              onSubsetSaved={loadSubsets}
              onClose={() => setRecordViewer(null)}
              embedded={true}
            />
          </div>
        </div>
      </div>
    );
  }

  // ── Narrow screen (mobile/tablet): tab layout ──────────────────────────────
  if (recordViewer && isNarrow) {
    return (
      <div style={{ position: 'fixed', top: NAV_HEIGHT, left: 0, right: 0, bottom: 0, zIndex: 20 }}
        className="flex flex-col bg-gray-50">
        {/* Tab bar */}
        <div className="flex-shrink-0 flex border-b border-gray-200 bg-white">
          {[['charts','Charts'],['records','Records']].map(([id, label]) => (
            <button key={id}
              onClick={() => setNarrowTab(id)}
              className={`flex-1 py-2.5 text-sm font-medium transition border-b-2 ${
                narrowTab === id
                  ? 'border-blue-500 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}>
              {label}
            </button>
          ))}
          <button onClick={() => setRecordViewer(null)}
            className="px-4 text-gray-400 hover:text-gray-700 transition border-b-2 border-transparent">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
        {/* Content */}
        <div className="flex-1 overflow-hidden">
          {narrowTab === 'charts' ? (
            <div className="h-full overflow-y-auto p-4">
              {renderMainContent(false)}
            </div>
          ) : (
            <RecordViewerPanel
              key={recordViewer.sql}
              pipeline={pipeline}
              initialSql={recordViewer.sql}
              title={recordViewer.title}
              initialFuelSubgroup={recordViewer.initialFuelSubgroup}
              initialImpactSubgroup={recordViewer.initialImpactSubgroup}
              onSubsetSaved={loadSubsets}
              onClose={() => setRecordViewer(null)}
              embedded={true}
            />
          )}
        </div>
      </div>
    );
  }

  // ── Default: single column (no viewer open) ────────────────────────────────
  return (
    <div className="mb-6">
      {renderMainContent(false)}
    </div>
  );
}
