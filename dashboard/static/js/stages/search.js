// ── Stage: Search ─────────────────────────────────────────────────────────────

const _SKIP_LABELS = {
  not_oa: 'not OA', not_english: 'not English', no_doi: 'no DOI',
  no_abstract: 'no abstract', no_keywords: 'no keywords',
  empty_abstract: 'empty abstract', json_error: 'JSON error',
  empty_line: 'empty line', no_id: 'no ID', processing_error: 'error',
};

function SearchStage({ pipeline, pipelineStatus, onJobStart, onJobDone }) {
  const [maxFiles, setMaxFiles] = useState('');
  const [totalFiles, setTotalFiles] = useState(null);
  // localDir stored in localStorage but not exposed in UI (fixed per deployment)
  const localDir = localStorage.getItem('oa_local_dir') || '';
  const [activeJobId, setActiveJobId] = useState(null);
  const [speed, setSpeed] = useState(null); // { filesPerMin, dbMBps }
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
    // Backfill subgroups for any existing records that are missing them
    apiFetch(`/api/pipelines/${pipeline.id}/backfill-subgroups`, { method: 'POST' })
      .catch(() => {});
  }, [pipeline.id]);

  const startSearch = async () => {
    const args = {
      '--output-db': pipeline.output_db_path,
      '--state-file': pipeline.state_file_path,
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
            <div className="mb-4 w-48">
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max files{' '}
                {totalFiles && <span className="text-gray-400 font-normal text-xs">({totalFiles.toLocaleString()} total)</span>}
              </label>
              <input type="number" placeholder="blank = all" min="1" value={maxFiles}
                onChange={e => setMaxFiles(e.target.value)}
                className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
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
            {(prog.file_errors > 0 || prog.record_errors > 0 || prog.failed > 0) && (
              <span className="text-red-500 ml-auto">
                {prog.failed > 0 && `${prog.failed} failed file${prog.failed !== 1 ? 's' : ''}`}
                {prog.failed > 0 && (prog.file_errors > 0 || prog.record_errors > 0) && ' · '}
                {prog.file_errors > 0 && `${prog.file_errors} file errors`}
                {prog.file_errors > 0 && prog.record_errors > 0 && ' · '}
                {prog.record_errors > 0 && `${prog.record_errors} record errors`}
              </span>
            )}
          </div>

          {/* Screening funnel */}
          {prog.seen > 0 && (() => {
            const sk       = prog.skip_stats || {};
            const seen     = prog.seen;
            const inserted = prog.inserted || 0;
            // All tracked skip reasons, sorted by count descending
            const allSkipRows = Object.entries(sk).sort((a, b) => b[1] - a[1]);
            const tracked = allSkipRows.reduce((s, [, v]) => s + v, 0);
            const unaccounted = seen - tracked - inserted;

            const FunnelRow = ({ indent, label, count, note, color }) => (
              <div className={`flex items-baseline gap-2 ${indent ? 'pl-4 border-l-2 border-gray-100 ml-2' : ''}`}>
                <span className={`text-xs font-mono font-semibold w-28 text-right flex-shrink-0 ${color || 'text-gray-700'}`}>
                  {count.toLocaleString()}
                </span>
                <span className={`text-xs ${color ? 'text-gray-500' : 'text-gray-600'}`}>{label}</span>
                {note && <span className="text-xs text-gray-400 italic">{note}</span>}
              </div>
            );

            return (
              <div className="pt-2 border-t border-gray-100 space-y-1">
                <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Screening funnel</div>
                <FunnelRow label="records scanned" count={seen} />
                {allSkipRows.map(([key, count]) => (
                  <FunnelRow key={key} indent
                    label={_SKIP_LABELS[key] || key}
                    count={count}
                    color="text-gray-400"
                    note={key === 'not_english' ? '(raw line check)' : undefined}
                  />
                ))}
                {unaccounted > 0 && (
                  <FunnelRow indent
                    label="unaccounted (pipeline restart or tracking gap)"
                    count={unaccounted}
                    color="text-orange-500"
                  />
                )}
                <div className="flex items-baseline gap-2 mt-1 pt-1 border-t border-gray-100">
                  <span className="text-sm font-bold w-28 text-right flex-shrink-0 text-green-700">{inserted.toLocaleString()}</span>
                  <span className="text-xs font-semibold text-green-700">records saved</span>
                  {seen > 0 && <span className="text-xs text-gray-400">({(inserted / seen * 100).toFixed(3)}% match rate)</span>}
                </div>
                {(prog.failed > 0 || prog.file_errors > 0 || prog.record_errors > 0) && (
                  <div className="mt-1.5 pt-1.5 border-t border-gray-100 text-xs text-red-500 space-y-0.5">
                    {prog.failed > 0 && (
                      <div>{prog.failed} file{prog.failed !== 1 ? 's' : ''} failed to process — see pipeline.log for details</div>
                    )}
                    {prog.record_errors > 0 && (
                      <div>{prog.record_errors.toLocaleString()} record-level errors — see pipeline.log</div>
                    )}
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
        const inserted    = sp?.inserted || 0;
        const seen        = sp?.seen     || 0;
        const allSkipRows = Object.entries(sk).sort((a, b) => b[1] - a[1]);
        const tracked     = allSkipRows.reduce((s, [, v]) => s + v, 0);
        const unaccounted = seen > 0 ? seen - tracked - inserted : 0;

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
              {(sp?.failed > 0 || sp?.file_errors > 0 || sp?.record_errors > 0) && (
                <div>
                  <div className="text-xs text-red-400 mb-0.5">Errors</div>
                  <div className="text-sm font-semibold text-red-600">
                    {sp.failed > 0 && `${sp.failed} failed file${sp.failed !== 1 ? 's' : ''}`}
                    {sp.failed > 0 && sp.file_errors > 0 && ' · '}
                    {sp.file_errors > 0 && `${sp.file_errors} file err`}
                    {(sp.failed > 0 || sp.file_errors > 0) && sp.record_errors > 0 && ' · '}
                    {sp.record_errors > 0 && `${sp.record_errors} record err`}
                  </div>
                  <div className="text-xs text-gray-400">see pipeline.log in ZIP</div>
                </div>
              )}
            </div>

            {/* Screening funnel — all skip reasons from state, plus unaccounted */}
            {seen > 0 && (
              <div className="border-t border-gray-100 pt-3">
                <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Screening funnel</div>
                <div className="space-y-1 font-mono text-xs">
                  <div className="flex gap-3">
                    <span className="w-28 text-right flex-shrink-0 text-gray-700 font-semibold">{seen.toLocaleString()}</span>
                    <span>records scanned</span>
                  </div>
                  {allSkipRows.map(([key, count]) => (
                    <div key={key} className="flex gap-3 pl-4">
                      <span className="w-28 text-right flex-shrink-0 text-gray-400">{count.toLocaleString()}</span>
                      <span className="text-gray-400">
                        ↳ {_SKIP_LABELS[key] || key}
                        {key === 'not_english' && <span className="text-gray-300 ml-1">(raw line check)</span>}
                      </span>
                    </div>
                  ))}
                  {unaccounted > 0 && (
                    <div className="flex gap-3 pl-4">
                      <span className="w-28 text-right flex-shrink-0 text-orange-500">{unaccounted.toLocaleString()}</span>
                      <span className="text-orange-500">↳ unaccounted (pipeline restart or tracking gap)</span>
                    </div>
                  )}
                  <div className="flex gap-3 mt-1 pt-1 border-t border-gray-100">
                    <span className="w-28 text-right flex-shrink-0 text-green-700 font-bold">{inserted.toLocaleString()}</span>
                    <span className="text-green-700 font-semibold">✓ saved</span>
                  </div>
                </div>
                {(sp?.failed > 0 || sp?.record_errors > 0) && (
                  <div className="mt-2 text-xs text-red-500 space-y-0.5">
                    {sp.failed > 0 && (
                      <div>{sp.failed} file{sp.failed !== 1 ? 's' : ''} failed to process — check pipeline.log for details</div>
                    )}
                    {sp.record_errors > 0 && (
                      <div>{sp.record_errors.toLocaleString()} record-level errors — check pipeline.log for details</div>
                    )}
                  </div>
                )}
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

