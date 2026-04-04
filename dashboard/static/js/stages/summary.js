// ── Stage: Summary & Export ───────────────────────────────────────────────────

// Key chart IDs shown in the summary view (non-LLM, representative)
const SUMMARY_CHART_IDS = [
  'publications_by_year',
  'top_fuel_terms',
  'top_impact_terms',
  'top_countries',
  'top_journals',
  'oa_status',
];

const DB_FORMAT_OPTIONS = [
  { value: 'csv',           label: 'CSV files' },
  { value: 'sqlite',        label: 'SQLite database' },
  { value: 'both',          label: 'CSV + SQLite' },
  { value: 'csv_chunks',    label: 'CSV chunks' },
];

function SummaryStage({ pipeline, pipelineStatus }) {
  const [downloading, setDownloading]   = useState(false);
  const [downloadErr, setDownloadErr]   = useState('');
  const [chartConfigs, setChartConfigs] = useState(null);
  const [logLines, setLogLines]         = useState(null);   // null = not loaded
  const [logLoading, setLogLoading]     = useState(false);
  const [dbFormat, setDbFormat]         = useState('csv');
  const [chunkMb, setChunkMb]           = useState('50');

  useEffect(() => {
    apiFetch('/api/charts/configs')
      .then(r => r.json())
      .then(list => setChartConfigs(Array.isArray(list) ? list : []))
      .catch(() => setChartConfigs([]));
  }, []);

  const cfgMap = useMemo(
    () => Object.fromEntries((chartConfigs || []).map(c => [c.id, c])),
    [chartConfigs]
  );

  const visibleCharts = useMemo(
    () => SUMMARY_CHART_IDS.filter(id => cfgMap[id]),
    [cfgMap]
  );

  // ── Stats derived from pipelineStatus + pipeline.config ───────────────────
  const rc   = pipelineStatus?.record_count ?? null;
  const llm  = pipelineStatus?.llm_done ?? null;
  const dbMB = pipelineStatus?.db_size_mb ?? null;

  const { totalTerms, groupCount } = useMemo(() => {
    const groups = _migrateGroups(pipeline.config || {});
    const gt = groups.reduce(
      (s, g) => s + g.subgroups.reduce((ss, sg) => ss + (sg.terms || []).length, 0), 0
    );
    return { totalTerms: gt, groupCount: groups.length };
  }, [pipeline.config]);

  const stats = [
    { label: 'Records',       value: rc   != null ? rc.toLocaleString()  : '—', cls: 'text-blue-700',    sub: 'filtered' },
    { label: 'LLM classified',value: llm  != null ? llm.toLocaleString() : '—', cls: llm > 0 ? 'text-purple-700' : 'text-gray-400', sub: 'processed' },
    { label: 'DB size',       value: dbMB != null ? `${dbMB} MB`         : '—', cls: 'text-gray-700',    sub: 'on disk' },
    { label: 'Keywords',      value: totalTerms,                                 cls: 'text-emerald-700', sub: `${groupCount} group${groupCount !== 1 ? 's' : ''}` },
  ];

  // ── ZIP download ──────────────────────────────────────────────────────────
  const downloadZip = async () => {
    setDownloadErr('');
    setDownloading(true);
    const fmt = dbFormat === 'csv_chunks'
      ? `csv_chunks_${parseInt(chunkMb, 10) || 50}`
      : dbFormat;
    try {
      const res = await apiFetch(`/api/pipelines/${pipeline.id}/export-zip?db_format=${fmt}`);
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setDownloadErr(d.error || `Export failed (${res.status})`);
        return;
      }
      const blob = await res.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href     = url;
      const cd   = res.headers.get('Content-Disposition') || '';
      const m    = cd.match(/filename="([^"]+)"/);
      a.download = m ? m[1] : `pipeline_${pipeline.id}_export.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setDownloadErr(e.message || 'Download failed');
    } finally {
      setDownloading(false);
    }
  };

  // ── Log loader ────────────────────────────────────────────────────────────
  const toggleLog = () => {
    if (logLines !== null) { setLogLines(null); return; }
    setLogLoading(true);
    apiFetch(`/api/log?pipeline_id=${pipeline.id}&lines=150`)
      .then(r => r.json())
      .then(d => setLogLines(d.lines || []))
      .catch(() => setLogLines([]))
      .finally(() => setLogLoading(false));
  };

  return (
    <div className="space-y-6">

      {/* ── Header: title + download ── */}
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <div className="flex items-start justify-between gap-4 flex-wrap mb-5">
          <div>
            <h3 className="font-semibold text-lg">Stage 6: Summary &amp; Export</h3>
            <p className="text-sm text-gray-500 mt-0.5">
              Pipeline results overview. Download all data, charts and logs as a single ZIP.
            </p>
          </div>
          <div className="flex flex-col items-end gap-2">
            {/* DB format selector */}
            <div className="flex items-center gap-2 flex-wrap justify-end">
              <span className="text-xs text-gray-500 whitespace-nowrap">DB export:</span>
              <select value={dbFormat} onChange={e => setDbFormat(e.target.value)}
                className="text-xs border border-gray-200 rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-teal-400">
                {DB_FORMAT_OPTIONS.map(o => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
              {dbFormat === 'csv_chunks' && (
                <div className="flex items-center gap-1">
                  <input type="number" min="10" max="500" value={chunkMb}
                    onChange={e => setChunkMb(e.target.value)}
                    className="w-16 text-xs border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-teal-400" />
                  <span className="text-xs text-gray-400">MB/chunk</span>
                </div>
              )}
            </div>
            <button onClick={downloadZip} disabled={downloading}
              className="flex items-center gap-2 px-5 py-2.5 bg-teal-600 text-white rounded-lg font-semibold
                         hover:bg-teal-700 transition disabled:opacity-50 whitespace-nowrap">
              {downloading
                ? <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                : <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                          d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
                  </svg>
              }
              {downloading ? 'Preparing ZIP…' : 'Export ZIP'}
            </button>
            {downloadErr && (
              <span className="text-xs text-red-600">{downloadErr}</span>
            )}
          </div>
        </div>

        {/* ── Stat cards ── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {stats.map(({ label, value, cls, sub }) => (
            <div key={label} className="bg-gray-50 rounded-xl p-4 text-center border border-gray-100">
              <div className="text-xs text-gray-400 mb-1 uppercase tracking-wide">{label}</div>
              <div className={`text-2xl font-bold ${cls}`}>{value}</div>
              <div className="text-xs text-gray-400 mt-0.5">{sub}</div>
            </div>
          ))}
        </div>

        {/* ── ZIP contents legend ── */}
        <div className="mt-5 px-4 py-3 bg-teal-50 rounded-xl border border-teal-100 text-xs text-teal-800">
          <span className="font-semibold mr-2">ZIP includes:</span>
          Per-stage HTML reports · Chart PNGs · SQL queries · Database export · Pipeline logs · Keywords config · Batch state · Pipeline metadata · Manifest
        </div>
      </div>

      {/* ── Key charts ── */}
      <div>
        <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3 px-1">
          Key Charts
        </h4>
        {chartConfigs === null ? (
          <div className="flex items-center justify-center py-16 text-gray-400">
            <span className="w-5 h-5 border-2 border-gray-300 border-t-teal-500 rounded-full animate-spin mr-2" />
            Loading…
          </div>
        ) : visibleCharts.length === 0 ? (
          <div className="bg-white rounded-xl border border-gray-200 p-10 text-center text-gray-400">
            No chart data yet — run the Search stage first.
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {visibleCharts.map(id => (
              <div key={id} className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                <div className="px-4 py-2.5 border-b border-gray-100">
                  <span className="text-sm font-semibold text-gray-800">{cfgMap[id]?.title || id}</span>
                </div>
                <div className="p-3">
                  <img
                    src={`/api/charts/image?pipeline_id=${pipeline.id}&chart=${id}&top_n=15`}
                    alt={cfgMap[id]?.title || id}
                    className="w-full h-auto"
                    loading="lazy"
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Pipeline log ── */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
          <span className="text-sm font-semibold text-gray-800">Pipeline Log</span>
          <button onClick={toggleLog} disabled={logLoading}
            className="text-xs text-gray-500 hover:text-gray-700 border border-gray-200 px-3 py-1 rounded-lg transition disabled:opacity-50">
            {logLoading ? 'Loading…' : logLines !== null ? 'Hide' : 'Show last 150 lines'}
          </button>
        </div>
        {logLines !== null && (
          <div className="terminal bg-gray-900 text-green-400 p-4 h-72 overflow-y-auto whitespace-pre-wrap text-xs font-mono">
            {logLines.length === 0
              ? <span className="text-gray-600">No log entries found.</span>
              : logLines.join('\n')}
          </div>
        )}
      </div>

    </div>
  );
}
