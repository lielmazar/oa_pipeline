// ── Stage: Summary & Export ───────────────────────────────────────────────────

const DB_FORMAT_OPTIONS = [
  { value: 'csv',           label: 'CSV files' },
  { value: 'sqlite',        label: 'SQLite database' },
  { value: 'both',          label: 'CSV + SQLite' },
  { value: 'csv_chunks',    label: 'CSV chunks' },
];

function SummaryStage({ pipeline, pipelineStatus }) {
  const [logLines, setLogLines]   = useState(null);   // null = not loaded
  const [logLoading, setLogLoading] = useState(false);
  const [dbFormat, setDbFormat]   = useState('csv');
  const [chunkMb, setChunkMb]     = useState('50');
  const [skipOpen, setSkipOpen]   = useState(false);

  // ── Stats derived from pipelineStatus + pipeline.config ───────────────────
  const rc   = pipelineStatus?.record_count ?? null;
  const llm  = pipelineStatus?.llm_done ?? null;
  const dbMB = pipelineStatus?.db_size_mb ?? null;
  const sp   = pipelineStatus?.search_progress ?? null;

  const { totalTerms, groupCount } = useMemo(() => {
    const groups = _migrateGroups(pipeline.config || {});
    const gt = groups.reduce(
      (s, g) => s + g.subgroups.reduce((ss, sg) => ss + (sg.terms || []).length, 0), 0
    );
    return { totalTerms: gt, groupCount: groups.length };
  }, [pipeline.config]);

  const stats = [
    { label: 'Records',        value: rc   != null ? rc.toLocaleString()  : '—', cls: 'text-blue-700',    sub: 'filtered' },
    { label: 'LLM classified', value: llm  != null ? llm.toLocaleString() : '—', cls: llm > 0 ? 'text-purple-700' : 'text-gray-400', sub: 'processed' },
    { label: 'DB size',        value: dbMB != null ? `${dbMB} MB`         : '—', cls: 'text-gray-700',    sub: 'on disk' },
    { label: 'Keywords',       value: totalTerms,                                 cls: 'text-emerald-700', sub: `${groupCount} group${groupCount !== 1 ? 's' : ''}` },
  ];

  // ── ZIP download — direct navigation so browser starts immediately ─────────
  const downloadZip = () => {
    const fmt = dbFormat === 'csv_chunks'
      ? `csv_chunks_${parseInt(chunkMb, 10) || 50}`
      : dbFormat;
    const a = document.createElement('a');
    a.href = `/api/pipelines/${pipeline.id}/export-zip?db_format=${fmt}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  // ── Log loader ────────────────────────────────────────────────────────────
  const toggleLog = () => {
    if (logLines !== null) { setLogLines(null); return; }
    setLogLoading(true);
    apiFetch(`/api/log?pipeline_id=${pipeline.id}&lines=300`)
      .then(r => r.json())
      .then(d => setLogLines(d.lines || []))
      .catch(() => setLogLines([]))
      .finally(() => setLogLoading(false));
  };

  // ── Screening funnel data ─────────────────────────────────────────────────
  const sk       = sp?.skip_stats || {};
  const seen     = sp?.seen     || 0;
  const inserted = sp?.inserted || 0;
  const notEn  = sk['not_english']    || 0;
  const noDoi  = sk['no_doi']         || 0;
  const noAbs  = sk['no_abstract']    || 0;
  const noKw   = sk['no_keywords']    || 0;
  const MAIN_KEYS = new Set(['not_english','no_doi','no_abstract','no_keywords']);
  const otherSkipRows = Object.entries(sk)
    .filter(([k]) => !MAIN_KEYS.has(k))
    .sort((a, b) => b[1] - a[1]);
  const otherTotal = otherSkipRows.reduce((s, [, v]) => s + v, 0);
  const fileErrors   = sp?.file_errors   || 0;
  const recordErrors = sp?.record_errors || 0;

  const _SKIP_LABEL = {
    empty_abstract: 'empty abstract', json_error: 'JSON parse error',
    empty_line: 'empty line', no_id: 'no OpenAlex ID', processing_error: 'processing error',
  };

  return (
    <div className="space-y-5">

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
            <button onClick={downloadZip}
              className="flex items-center gap-2 px-5 py-2.5 bg-teal-600 text-white rounded-lg font-semibold
                         hover:bg-teal-700 active:bg-teal-800 transition whitespace-nowrap">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
              </svg>
              Export ZIP
            </button>
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

      {/* ── Screening funnel ── */}
      {seen > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Screening Funnel</h4>
          <div className="space-y-0.5 font-mono text-xs">
            {[
              { label: 'records scanned', count: seen },
              notEn  > 0 && { label: '↳ non-English (pre-screened)', count: notEn,  dim: true },
              noDoi  > 0 && { label: '↳ no DOI',                     count: noDoi,  dim: true },
              noAbs  > 0 && { label: '↳ no abstract',                 count: noAbs,  dim: true },
              noKw   > 0 && { label: '↳ no matching keywords',        count: noKw,   dim: true },
              otherTotal > 0 && {
                label: (
                  <span className="flex items-center gap-1">
                    ↳ other skip reasons
                    {otherSkipRows.length > 0 && (
                      <button onClick={() => setSkipOpen(true)}
                        className="text-blue-500 hover:text-blue-700 underline decoration-dotted ml-0.5">ⓘ</button>
                    )}
                  </span>
                ),
                count: otherTotal, dim: true,
              },
              { label: '✓ saved', count: inserted, green: true },
            ].filter(Boolean).map((row, i) => (
              <div key={i} className={`flex gap-4 ${row.dim ? 'pl-4' : ''} ${row.green ? 'mt-1 pt-1 border-t border-gray-100' : ''}`}>
                <span className={`w-28 text-right flex-shrink-0 ${row.green ? 'text-green-700 font-bold' : row.dim ? 'text-gray-400' : 'text-gray-700 font-semibold'}`}>
                  {typeof row.count === 'number' ? row.count.toLocaleString() : row.count}
                </span>
                <span className={row.green ? 'text-green-700 font-semibold' : row.dim ? 'text-gray-400' : ''}>{row.label}</span>
              </div>
            ))}
          </div>
          {seen > 0 && inserted > 0 && (
            <div className="mt-2 text-xs text-gray-400">
              Match rate: <strong className="text-gray-600">{(inserted / seen * 100).toFixed(3)}%</strong>
            </div>
          )}

          {/* ── Other skip reasons popup ── */}
          {skipOpen && (
            <div className="fixed inset-0 bg-gray-900/60 flex items-center justify-center z-50 p-4"
              onClick={e => e.target === e.currentTarget && setSkipOpen(false)}>
              <div className="bg-white rounded-xl shadow-2xl w-full max-w-sm">
                <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
                  <h3 className="font-semibold text-sm text-gray-800">Other skip reasons — breakdown</h3>
                  <button onClick={() => setSkipOpen(false)}
                    className="text-gray-400 hover:text-gray-700 p-1 rounded hover:bg-gray-100 transition">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12"/>
                    </svg>
                  </button>
                </div>
                <div className="px-5 py-4 space-y-1.5">
                  {otherSkipRows.map(([key, count]) => (
                    <div key={key} className="flex justify-between text-xs">
                      <span className="text-gray-600">{_SKIP_LABEL[key] || key}</span>
                      <span className="font-mono font-semibold text-gray-700">{count.toLocaleString()}</span>
                    </div>
                  ))}
                  {otherSkipRows.length === 0 && (
                    <p className="text-xs text-gray-400 italic">No breakdown available.</p>
                  )}
                </div>
                <div className="px-5 py-3 border-t border-gray-100 bg-gray-50/50 rounded-b-xl text-right">
                  <button onClick={() => setSkipOpen(false)}
                    className="text-xs px-3 py-1.5 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 transition">
                    Close
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Processing stats ── */}
      {sp && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Search Statistics</h4>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {[
              { label: 'Files processed', value: sp.processed != null ? `${sp.processed.toLocaleString()} / ${sp.total.toLocaleString()}` : '—' },
              { label: 'Records scanned', value: seen > 0 ? seen.toLocaleString() : '—' },
              { label: 'Records saved',   value: inserted > 0 ? inserted.toLocaleString() : '—', highlight: true },
              { label: 'File errors',     value: fileErrors > 0 ? fileErrors.toLocaleString() : '0', err: fileErrors > 0 },
              { label: 'Record errors',   value: recordErrors > 0 ? recordErrors.toLocaleString() : '0', err: recordErrors > 0 },
              { label: 'Match rate',      value: seen > 0 && inserted > 0 ? `${(inserted/seen*100).toFixed(3)}%` : '—' },
            ].map(({ label, value, highlight, err }) => (
              <div key={label} className="bg-gray-50 rounded-lg p-3 border border-gray-100">
                <div className="text-xs text-gray-400 mb-0.5">{label}</div>
                <div className={`text-sm font-semibold ${err ? 'text-red-600' : highlight ? 'text-blue-700' : 'text-gray-800'}`}>{value}</div>
              </div>
            ))}
          </div>

          {/* Skip reasons mini-table */}
          {Object.keys(sk).length > 0 && (
            <div className="mt-4">
              <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Skip Reason Breakdown</div>
              <div className="space-y-1">
                {Object.entries(sk).sort((a,b) => b[1]-a[1]).map(([key, count]) => {
                  const total = seen || 1;
                  const pct = (count / total * 100).toFixed(2);
                  const LABELS = {
                    not_english: 'non-English', no_doi: 'no DOI', no_abstract: 'no abstract',
                    no_keywords: 'no keywords', empty_abstract: 'empty abstract',
                    json_error: 'JSON error', empty_line: 'empty line',
                    no_id: 'no OpenAlex ID', processing_error: 'processing error',
                  };
                  return (
                    <div key={key} className="flex items-center gap-2 text-xs">
                      <span className="w-40 text-gray-500 truncate">{LABELS[key] || key}</span>
                      <div className="flex-1 bg-gray-100 rounded-full h-1.5">
                        <div className="bg-gray-400 h-1.5 rounded-full" style={{ width: `${Math.min(100, pct)}%` }} />
                      </div>
                      <span className="w-20 text-right font-mono text-gray-600">{count.toLocaleString()}</span>
                      <span className="w-12 text-right text-gray-400">{pct}%</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── LLM stats ── */}
      {llm != null && llm > 0 && rc != null && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">LLM Classification</h4>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <div className="bg-purple-50 rounded-lg p-3 border border-purple-100">
              <div className="text-xs text-purple-400 mb-0.5">Classified</div>
              <div className="text-sm font-semibold text-purple-700">{llm.toLocaleString()}</div>
            </div>
            <div className="bg-gray-50 rounded-lg p-3 border border-gray-100">
              <div className="text-xs text-gray-400 mb-0.5">Pending</div>
              <div className="text-sm font-semibold text-gray-700">{Math.max(0, rc - llm).toLocaleString()}</div>
            </div>
            <div className="bg-gray-50 rounded-lg p-3 border border-gray-100">
              <div className="text-xs text-gray-400 mb-0.5">Coverage</div>
              <div className="text-sm font-semibold text-gray-700">{rc > 0 ? `${(llm/rc*100).toFixed(1)}%` : '—'}</div>
            </div>
          </div>
        </div>
      )}

      {/* ── Pipeline log ── */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
          <span className="text-sm font-semibold text-gray-800">Pipeline Log</span>
          <button onClick={toggleLog} disabled={logLoading}
            className="text-xs text-gray-500 hover:text-gray-700 border border-gray-200 px-3 py-1 rounded-lg transition disabled:opacity-50">
            {logLoading ? 'Loading…' : logLines !== null ? 'Hide' : 'Show last 300 lines'}
          </button>
        </div>
        {logLines !== null && (
          <div className="terminal bg-gray-900 text-green-400 p-4 h-80 overflow-y-auto whitespace-pre-wrap text-xs font-mono">
            {logLines.length === 0
              ? <span className="text-gray-600">No log entries found.</span>
              : logLines.join('\n')}
          </div>
        )}
      </div>

    </div>
  );
}
