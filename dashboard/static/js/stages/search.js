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
      '--keyword-config': null,
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

            const FunnelRow = ({ indent, label, count, note, color }) => (
              <div className={`flex items-baseline gap-2 ${indent ? 'pl-4 border-l-2 border-gray-100 ml-2' : ''}`}>
                <span className={`text-xs font-mono font-semibold w-28 text-right flex-shrink-0 ${color || 'text-gray-700'}`}>
                  {count.toLocaleString()}
                </span>
                <span className="text-xs text-gray-600">{label}</span>
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
                {(emAbs + other) > 0 && <FunnelRow indent label="other skip reasons" count={emAbs + Math.max(0, other)} color="text-gray-400" />}
                <div className="flex items-baseline gap-2 mt-1 pt-1 border-t border-gray-100">
                  <span className="text-sm font-bold w-28 text-right flex-shrink-0 text-green-700">{inserted.toLocaleString()}</span>
                  <span className="text-xs font-semibold text-green-700">records saved</span>
                  {seen > 0 && <span className="text-xs text-gray-400">({(inserted / seen * 100).toFixed(3)}% match rate)</span>}
                </div>
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
    </div>
  );
}

