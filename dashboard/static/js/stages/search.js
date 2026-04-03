// ── Stage: Search ─────────────────────────────────────────────────────────────

const _SKIP_LABELS = {
  not_oa: 'not OA', not_english: 'not English', no_doi: 'no DOI',
  no_abstract: 'no abstract', no_keywords: 'no keywords',
  empty_abstract: 'empty abstract', json_error: 'JSON error',
  empty_line: 'empty line', no_id: 'no ID', processing_error: 'error',
};

function SearchStage({ pipeline, pipelineStatus, onJobStart, onJobDone }) {
  const [maxFiles, setMaxFiles] = useState('');
  const [workers, setWorkers] = useState(() => Number(localStorage.getItem('oa_workers') || '4'));
  const [localDir, setLocalDir] = useState(() => localStorage.getItem('oa_local_dir') || '');
  const [editingDir, setEditingDir] = useState(false);
  const [totalFiles, setTotalFiles] = useState(null);
  const [activeJobId, setActiveJobId] = useState(null);
  const [speed, setSpeed] = useState(null); // { filesPerMin, dbMBps }
  const [cpuCount, setCpuCount] = useState(null);
  const [skipDetailOpen, setSkipDetailOpen] = useState(false);
  const [recordViewer, setRecordViewer] = useState(null);
  const prevProgRef = useRef(null);

  const { lines, status: jobStatus } = useJobStream(activeJobId, onJobDone);
  const isRunning = activeJobId && (!jobStatus || jobStatus === 'running');

  // Track speed between consecutive status polls
  useEffect(() => {
    const prog = pipelineStatus?.search_progress;
    if (!prog) { setSpeed(null); prevProgRef.current = null; return; }
    const now = Date.now();
    const dbMB = pipelineStatus.db_size_mb || 0;
    if (prevProgRef.current && prog.processed > prevProgRef.current.processed) {
      const elapsedSec = (now - prevProgRef.current.ts) / 1000;
      if (elapsedSec > 1) {
        const filesDelta = prog.processed - prevProgRef.current.processed;
        const dbDelta = dbMB - prevProgRef.current.dbMB;
        setSpeed({
          filesPerMin: Math.round(filesDelta / elapsedSec * 60),
          dbMBps: dbDelta > 0 ? (dbDelta / elapsedSec).toFixed(2) : null,
        });
      }
    }
    prevProgRef.current = { ts: now, processed: prog.processed, dbMB };
  }, [pipelineStatus]);

  useEffect(() => {
    apiFetch(`/api/pipelines/${pipeline.id}/file-count`)
      .then(r => r.json()).then(d => { if (d.total_files) setTotalFiles(d.total_files); })
      .catch(() => {});
    apiFetch('/api/system/info')
      .then(r => r.json()).then(d => { if (d.cpu_count) setCpuCount(d.cpu_count); })
      .catch(() => {});
    // Backfill subgroups for any existing records that are missing them
    // (records inserted before subgroup config was present)
    apiFetch(`/api/pipelines/${pipeline.id}/backfill-subgroups`, { method: 'POST' })
      .catch(() => {});
  }, [pipeline.id]);

  const saveDir = val => {
    setLocalDir(val);
    localStorage.setItem('oa_local_dir', val);
    setEditingDir(false);
  };

  const saveWorkers = val => {
    const n = Math.max(1, Math.min(64, Number(val) || 16));
    setWorkers(n);
    localStorage.setItem('oa_workers', String(n));
  };

  const startSearch = async () => {
    const args = {
      '--output-db': pipeline.output_db_path,
      '--state-file': pipeline.state_file_path,
      '--workers': workers,
    };
    if (maxFiles) args['--max-files'] = maxFiles;
    if (localDir) args['--local-dir'] = localDir;
    const res = await apiFetch('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ script: 'run_pipeline', pipeline_id: pipeline.id, args }),
    });
    const job = await res.json();
    if (!res.ok) { alert(job.error); return; }
    setActiveJobId(job.id);
    if (onJobStart) onJobStart();
  };

  const prog = pipelineStatus?.search_progress;

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h3 className="font-semibold text-lg mb-1">Stage 2: OpenAlex Search</h3>
        <p className="text-sm text-gray-500 mb-4">Run the parallel pipeline to filter OpenAlex records using your keywords.</p>
        {pipeline.is_owner ? (
          <>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Max files{' '}
                  {totalFiles && <span className="text-gray-400 font-normal text-xs">({totalFiles.toLocaleString()} total)</span>}
                </label>
                <input type="number" placeholder="blank = all" min="1" value={maxFiles}
                  onChange={e => setMaxFiles(e.target.value)}
                  className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Workers{' '}
                  {cpuCount && <span className="text-gray-400 font-normal text-xs">({cpuCount} CPU cores available)</span>}
                </label>
                <input type="number" min="1" max="256" value={workers}
                  onChange={e => saveWorkers(e.target.value)}
                  className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
              </div>
            </div>
            <div className="mb-4">
              <label className="block text-xs font-medium text-gray-500 mb-1">
                OpenAlex snapshot dir (saved locally){' '}
                <button onClick={() => setEditingDir(!editingDir)}
                  className="text-blue-500 hover:underline text-xs ml-1">edit</button>
              </label>
              {editingDir ? (
                <input type="text" defaultValue={localDir} autoFocus
                  className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 mt-1"
                  onBlur={e => saveDir(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && saveDir(e.target.value)} />
              ) : (
                <div className="text-xs text-gray-600 font-mono bg-gray-50 px-3 py-1.5 rounded border">
                  {localDir || 'not set — click edit'}
                </div>
              )}
            </div>
            <div className="flex gap-3">
              {!isRunning ? (
                <button onClick={startSearch}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700 transition">
                  ▶ Start Search
                </button>
              ) : (
                <button onClick={() => activeJobId && apiFetch(`/api/jobs/${activeJobId}`, { method: 'DELETE' })}
                  className="px-4 py-2 bg-red-600 text-white rounded-lg font-semibold hover:bg-red-700 transition">
                  ■ Stop
                </button>
              )}
            </div>
          </>
        ) : (
          <p className="text-gray-400 text-sm italic">View only — you are not the owner.</p>
        )}
      </div>

      {prog && (
        <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
          {/* File progress bar */}
          <div>
            <div className="flex justify-between text-sm text-gray-600 mb-1">
              <span>{(prog.processed || 0).toLocaleString()} / {(prog.total || 0).toLocaleString()} partition files</span>
              <span className="font-semibold">{prog.percent}%</span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div className="bg-blue-500 h-2 rounded-full transition-all duration-500" style={{ width: `${prog.percent}%` }} />
            </div>
          </div>

          {/* Speed / DB / ETA row */}
          <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-gray-400">
            {pipelineStatus?.db_size_mb > 0 && (
              <span>DB: <strong className="text-gray-600">{pipelineStatus.db_size_mb} MB</strong></span>
            )}
            {speed?.filesPerMin > 0 && (
              <span>Speed: <strong className="text-blue-600">{speed.filesPerMin} files/min</strong></span>
            )}
            {speed?.dbMBps && (
              <span>Write: <strong className="text-blue-600">{speed.dbMBps} MB/s</strong></span>
            )}
            {speed?.filesPerMin > 0 && prog.total > prog.processed && (
              <span>ETA: <strong className="text-gray-600">
                {(() => {
                  const remaining = prog.total - prog.processed;
                  const mins = remaining / speed.filesPerMin;
                  return mins > 60
                    ? `${(mins / 60).toFixed(1)}h`
                    : `${Math.ceil(mins)}m`;
                })()}
              </strong></span>
            )}
            {(prog.file_errors > 0 || prog.record_errors > 0) && (
              <span className="text-red-500 ml-auto">
                {prog.file_errors > 0 && `${prog.file_errors} file errors`}
                {prog.file_errors > 0 && prog.record_errors > 0 && ' · '}
                {prog.record_errors > 0 && `${prog.record_errors} record errors`}
              </span>
            )}
          </div>

          {/* Screening funnel */}
          {prog.seen > 0 && (() => {
            const sk = prog.skip_stats || {};
            const seen = prog.seen;
            const notEn = sk['not_english'] || 0;
            const noDoi = sk['no_doi'] || 0;
            const noAbs = sk['no_abstract'] || 0;
            const noKw  = sk['no_keywords'] || 0;
            const emAbs = sk['empty_abstract'] || 0;
            const other = seen - (notEn + noDoi + noAbs + noKw + emAbs + (prog.inserted || 0));
            const inserted = prog.inserted || 0;

            // Detailed "other" reasons: everything except the 5 main labeled ones and inserted
            const MAIN_KEYS = new Set(['not_english','no_doi','no_abstract','no_keywords','empty_abstract']);
            const detailRows = Object.entries(sk)
              .filter(([k]) => !MAIN_KEYS.has(k))
              .sort((a, b) => b[1] - a[1]);

            const FunnelRow = ({ indent, label, count, note, color, onClick }) => (
              <div className={`flex items-baseline gap-2 ${indent ? 'pl-4 border-l-2 border-gray-100 ml-2' : ''}`}>
                <span className={`text-xs font-mono font-semibold w-28 text-right flex-shrink-0 ${color || 'text-gray-700'}`}>
                  {count.toLocaleString()}
                </span>
                {onClick ? (
                  <button onClick={onClick}
                    className="text-xs text-gray-600 hover:text-blue-600 underline decoration-dotted decoration-gray-400 hover:decoration-blue-400 text-left">
                    {label}
                  </button>
                ) : (
                  <span className="text-xs text-gray-600">{label}</span>
                )}
                {note && <span className="text-xs text-gray-400 italic">{note}</span>}
              </div>
            );

            return (
              <div className="pt-2 border-t border-gray-100 space-y-1">
                <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Screening funnel</div>
                <FunnelRow label="records scanned" count={seen} />
                {notEn > 0 && <FunnelRow indent label="non-English pre-screened out" count={notEn} color="text-gray-400" note="(raw line check)" />}
                {noDoi > 0 && <FunnelRow indent label="no DOI" count={noDoi} color="text-gray-400" />}
                {noAbs > 0 && <FunnelRow indent label="no abstract" count={noAbs} color="text-gray-400" />}
                {noKw > 0  && <FunnelRow indent label="no matching keywords" count={noKw} color="text-gray-400" />}
                {(emAbs + other) > 0 && (
                  <FunnelRow indent
                    label={`other skip reasons${detailRows.length > 0 ? ' ⓘ' : ''}`}
                    count={emAbs + Math.max(0, other)}
                    color="text-gray-400"
                    onClick={detailRows.length > 0 ? () => setSkipDetailOpen(true) : null}
                  />
                )}
                <div className="flex items-baseline gap-2 mt-1 pt-1 border-t border-gray-100">
                  <span className="text-sm font-bold w-28 text-right flex-shrink-0 text-green-700">{inserted.toLocaleString()}</span>
                  <span className="text-xs font-semibold text-green-700">records saved</span>
                  {seen > 0 && <span className="text-xs text-gray-400">({(inserted / seen * 100).toFixed(3)}% match rate)</span>}
                </div>

                {/* Skip detail popup */}
                {skipDetailOpen && (
                  <div className="fixed inset-0 bg-gray-900/60 flex items-center justify-center z-50 p-4"
                    onClick={e => e.target === e.currentTarget && setSkipDetailOpen(false)}>
                    <div className="bg-white rounded-xl shadow-2xl w-full max-w-sm">
                      <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
                        <h3 className="font-semibold text-sm text-gray-800">Other skip reasons — full breakdown</h3>
                        <button onClick={() => setSkipDetailOpen(false)}
                          className="text-gray-400 hover:text-gray-700 p-1 rounded hover:bg-gray-100 transition">
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12"/>
                          </svg>
                        </button>
                      </div>
                      <div className="px-5 py-4 space-y-1.5">
                        {emAbs > 0 && (
                          <div className="flex justify-between text-xs">
                            <span className="text-gray-600">empty abstract</span>
                            <span className="font-mono font-semibold text-gray-700">{emAbs.toLocaleString()}</span>
                          </div>
                        )}
                        {detailRows.map(([key, count]) => (
                          <div key={key} className="flex justify-between text-xs">
                            <span className="text-gray-600">{_SKIP_LABELS[key] || key}</span>
                            <span className="font-mono font-semibold text-gray-700">{count.toLocaleString()}</span>
                          </div>
                        ))}
                        {emAbs === 0 && detailRows.length === 0 && (
                          <p className="text-xs text-gray-400 italic">No detailed breakdown available.</p>
                        )}
                      </div>
                      <div className="px-5 py-3 border-t border-gray-100 bg-gray-50/50 rounded-b-xl text-right">
                        <button onClick={() => setSkipDetailOpen(false)}
                          className="text-xs px-3 py-1.5 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 transition">
                          Close
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      )}

      {activeJobId && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="bg-gray-800 px-4 py-2 flex justify-between items-center">
            <span className="text-gray-300 text-sm font-mono">job {activeJobId}</span>
            <span className={`text-xs px-2 py-0.5 rounded ${jobStatus === 'done' ? 'bg-green-700 text-green-100' : jobStatus === 'error' ? 'bg-red-700 text-red-100' : 'bg-gray-700 text-gray-300'}`}>
              {jobStatus || 'running'}
            </span>
          </div>
          <Terminal lines={lines} />
        </div>
      )}

      {/* ── Search results summary (shown once data is available) ── */}
      {(pipelineStatus?.record_count > 0 || pipelineStatus?.search_progress?.seen > 0) && (() => {
        const recordCount = pipelineStatus.record_count;
        const dbSizeMb    = pipelineStatus.db_size_mb;
        const sp          = pipelineStatus.search_progress;
        const sk          = sp?.skip_stats || {};
        const notEn   = sk['not_english']   || 0;
        const noDoi   = sk['no_doi']        || 0;
        const noAbs   = sk['no_abstract']   || 0;
        const noKw    = sk['no_keywords']   || 0;
        const emAbs   = sk['empty_abstract']|| 0;
        const inserted = sp?.inserted || 0;
        const seen     = sp?.seen     || 0;
        const otherSkip = seen > 0
          ? Math.max(0, seen - notEn - noDoi - noAbs - noKw - emAbs - inserted)
          : 0;

        return (
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Search Results</h3>

            {/* Stats row */}
            <div className="flex flex-wrap gap-8">
              {recordCount != null && (
                <div>
                  <div className="text-xs text-gray-400 mb-0.5">Saved records</div>
                  <div className="text-2xl font-bold text-gray-900">
                    <button onClick={() => setRecordViewer({ sql: 'SELECT * FROM filtered_records', title: 'All Search Results' })}
                      className="underline decoration-blue-400 hover:text-blue-600 transition-colors">
                      {recordCount.toLocaleString()}
                    </button>
                  </div>
                  <div className="text-xs text-gray-400">publications matched keywords</div>
                </div>
              )}
              {seen > 0 && (
                <div>
                  <div className="text-xs text-gray-400 mb-0.5">Records scanned</div>
                  <div className="text-base font-semibold text-gray-700">{seen.toLocaleString()}</div>
                  {inserted > 0 && (
                    <div className="text-xs text-gray-400">{(inserted / seen * 100).toFixed(3)}% match rate</div>
                  )}
                </div>
              )}
              {dbSizeMb != null && dbSizeMb > 0 && (
                <div>
                  <div className="text-xs text-gray-400 mb-0.5">Database size</div>
                  <div className="text-base font-semibold text-gray-700">{dbSizeMb} MB</div>
                </div>
              )}
            </div>

            {/* Screening funnel */}
            {seen > 0 && (
              <div className="border-t border-gray-100 pt-3">
                <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Screening funnel</div>
                <div className="space-y-1 font-mono text-xs">
                  {[
                    { label: 'records scanned', count: seen },
                    notEn > 0 && { label: '↳ non-English (pre-screened)', count: notEn, dim: true },
                    noDoi > 0 && { label: '↳ no DOI', count: noDoi, dim: true },
                    noAbs > 0 && { label: '↳ no abstract', count: noAbs, dim: true },
                    noKw  > 0 && { label: '↳ no matching keywords', count: noKw, dim: true },
                    (emAbs + otherSkip) > 0 && { label: '↳ other reasons', count: emAbs + otherSkip, dim: true },
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
            )}
          </div>
        );
      })()}

      {/* ── Inline record viewer (opened from saved-records count) ── */}
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

