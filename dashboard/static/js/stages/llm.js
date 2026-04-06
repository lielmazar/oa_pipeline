// ── Stage: LLM ────────────────────────────────────────────────────────────────

const LLM_MODELS = [
  { id: 'gpt-5.4-nano',  label: 'GPT-5.4 Nano',  note: 'cheapest' },
  { id: 'gpt-5.4-mini',  label: 'GPT-5.4 Mini',  note: '' },
  { id: 'gpt-5.4',       label: 'GPT-5.4',        note: 'recommended' },
  { id: 'gpt-5.4-pro',   label: 'GPT-5.4 Pro',    note: 'most capable' },
  { id: 'gpt-4o-mini',   label: 'GPT-4o Mini',    note: '' },
  { id: 'gpt-4o',        label: 'GPT-4o',         note: '' },
];

function LLMStage({ pipeline, pipelineStatus }) {
  const [activeJobId, setActiveJobId] = useState(null);
  const [limit, setLimit] = useState('');
  const [selectedModel, setSelectedModel] = useState('gpt-5.4-nano');
  const [launchError, setLaunchError] = useState('');
  const [liveBatchState, setLiveBatchState] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const [subsets, setSubsets] = useState([]);
  const [activeSubsetIdx, setActiveSubsetIdx] = useState(null); // null = all records
  const { lines, status: jobStatus } = useJobStream(activeJobId);

  const isJobRunning = !!(activeJobId && (jobStatus === null || jobStatus === 'running'));

  // On mount, auto-connect to the most recent llm_classify job + load subsets
  useEffect(() => {
    apiFetch(`/api/jobs?pipeline_id=${pipeline.id}`)
      .then(r => r.json())
      .then(jobs => {
        const llmJobs = jobs
          .filter(j => j.script === 'llm_classify')
          .sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
        if (llmJobs.length > 0) setActiveJobId(llmJobs[0].id);
      })
      .catch(() => {});
    apiFetch(`/api/subsets?pipeline_id=${pipeline.id}`)
      .then(r => r.json())
      .then(data => setSubsets(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, [pipeline.id]);

  // Batch state: prefer freshly-queried live state
  const batchState = liveBatchState || pipelineStatus?.batch_state;
  const batchStatus = batchState?.status;
  const hasBatchId = !!(batchState?.batch_id);
  // cancelling is quasi-terminal: still polling but no action possible
  const TERMINAL = new Set(['completed', 'failed', 'expired', 'cancelled']);
  const NEAR_TERMINAL = new Set(['cancelling']);
  const batchTerminal = TERMINAL.has(batchStatus);
  const batchActive = hasBatchId && !batchTerminal; // includes cancelling

  const fetchBatchStatus = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/pipelines/${pipeline.id}/batch-status`);
      if (res.ok) {
        const d = await res.json();
        if (!d.error) setLiveBatchState(d);
      }
    } catch {}
  }, [pipeline.id]);

  // Immediate fetch when batch_id appears or becomes active
  useEffect(() => {
    if (hasBatchId) fetchBatchStatus();
  }, [hasBatchId, fetchBatchStatus]);

  // Auto-poll every 15s while non-terminal (slower for cancelling)
  const pollDelay = batchActive ? (NEAR_TERMINAL.has(batchStatus) ? 30000 : 15000) : null;
  useInterval(fetchBatchStatus, pollDelay);

  const cancelBatch = async () => {
    if (!confirm('Cancel this batch on OpenAI? Completed results so far will not be saved.')) return;
    setCancelling(true);
    try {
      const res = await apiFetch(`/api/pipelines/${pipeline.id}/batch-cancel`, { method: 'POST' });
      const d = await res.json();
      if (!res.ok) { alert(d.error || 'Cancel failed'); return; }
      setLiveBatchState(prev => prev ? { ...prev, status: d.status } : prev);
      fetchBatchStatus();
    } finally {
      setCancelling(false);
    }
  };

  const killJob = async () => {
    if (!activeJobId) return;
    await apiFetch(`/api/jobs/${activeJobId}`, { method: 'DELETE' });
    setActiveJobId(null);
  };

  const launchPhase = async phase => {
    setLaunchError('');
    const mainArgs = {
      '--db': pipeline.output_db_path,
      '--batch-dir': pipeline.batch_state_path
        ? pipeline.batch_state_path.replace('/state.json', '')
        : undefined,
    };
    if (activeSubsetIdx != null && subsets[activeSubsetIdx]) {
      mainArgs['--subset-sql'] = subsets[activeSubsetIdx].sql;
    }
    const subArgs = {};
    if (phase === 'estimate') subArgs['--model'] = selectedModel;
    if (limit && ['prepare', 'run', 'estimate'].includes(phase)) subArgs['--limit'] = limit;
    if (['prepare', 'run'].includes(phase)) subArgs['--model'] = selectedModel;
    if (['poll', 'run'].includes(phase)) subArgs['--interval'] = '30';
    const args = { ...mainArgs, [phase]: true, ...subArgs };
    const res = await apiFetch('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ script: 'llm_classify', pipeline_id: pipeline.id, args }),
    });
    const job = await res.json();
    if (!res.ok) { setLaunchError(job.error || 'Failed to launch'); return; }
    setActiveJobId(job.id);
    fetchBatchStatus();
  };

  // Detect whether results are already saved (llm_done >= record_count, both > 0)
  const alreadySaved = !!(
    pipelineStatus?.record_count > 0 &&
    pipelineStatus.llm_done != null &&
    pipelineStatus.llm_done >= pipelineStatus.record_count
  );

  // Context-aware suggested next action based on batch state
  // Note: "poll" (CLI job) is intentionally not suggested — the dashboard auto-polls every 15s.
  const suggestedAction = (() => {
    if (!batchState) return { phase: 'estimate', label: 'Estimate Cost', color: 'bg-purple-600 text-white hover:bg-purple-700' };
    if (!batchState.batch_id) {
      if (batchState.batch_file) return { phase: 'submit', label: 'Submit Batch', color: 'bg-blue-600 text-white hover:bg-blue-700' };
      return { phase: 'prepare', label: 'Prepare Batch', color: 'bg-blue-600 text-white hover:bg-blue-700' };
    }
    if (batchStatus === 'completed' && batchState.output_file_id && !alreadySaved)
      return { phase: 'save', label: 'Save Results to DB', color: 'bg-green-600 text-white hover:bg-green-700' };
    // validating / in_progress / cancelling — no CLI action needed, dashboard handles polling
    return null;
  })();

  const PHASES = [
    { id: 'estimate', label: 'Estimate' },
    { id: 'prepare',  label: 'Prepare' },
    { id: 'submit',   label: 'Submit' },
    { id: 'poll',     label: 'Poll (CLI wait loop)' },
    { id: 'save',     label: 'Save Results' },
    { id: 'run',      label: 'Run All' },
  ];

  const batchStatusColor = {
    completed:   'bg-green-100 text-green-800 border-green-200',
    in_progress: 'bg-blue-100 text-blue-800 border-blue-200',
    validating:  'bg-yellow-100 text-yellow-800 border-yellow-200',
    cancelling:  'bg-orange-100 text-orange-800 border-orange-200',
    failed:      'bg-red-100 text-red-800 border-red-200',
    cancelled:   'bg-gray-100 text-gray-600 border-gray-200',
    expired:     'bg-gray-100 text-gray-600 border-gray-200',
  }[batchStatus] || 'bg-gray-100 text-gray-600 border-gray-200';

  const jobStatusCls = jobStatus === 'done' ? 'bg-green-700 text-green-100'
    : jobStatus === 'error' ? 'bg-red-700 text-red-100'
    : jobStatus === 'killed' ? 'bg-gray-600 text-gray-200'
    : 'bg-yellow-700 text-yellow-100';

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="font-semibold text-lg">Stage 5: LLM Classification</h3>
            <p className="text-sm text-gray-500 mt-0.5">OpenAI Batch API classification of filtered records.</p>
          </div>
          {pipelineStatus && (
            <div className="text-right text-sm text-gray-600 flex-shrink-0 ml-4">
              <div><strong>{(pipelineStatus.record_count || 0).toLocaleString()}</strong> records</div>
              <div className="text-xs text-gray-400">{(pipelineStatus.llm_done || 0).toLocaleString()} classified</div>
              {alreadySaved && (
                <div className="text-xs text-green-600 font-medium mt-0.5">✓ Saved to DB</div>
              )}
            </div>
          )}
        </div>

        {/* Batch status card */}
        {batchState ? (
          <div className={`mb-5 p-4 rounded-lg border ${batchStatusColor}`}>
            <div className="flex items-center gap-2 mb-3">
              <span className="font-semibold text-sm capitalize">{batchStatus || 'unknown'}</span>
              {batchActive && !NEAR_TERMINAL.has(batchStatus) && (
                <span className="text-xs opacity-70 animate-pulse">live</span>
              )}
              <div className="ml-auto flex items-center gap-2">
                {!batchTerminal && !NEAR_TERMINAL.has(batchStatus) && pipeline.is_owner && (
                  <button onClick={cancelBatch} disabled={cancelling}
                    className="text-xs px-2 py-0.5 border border-current rounded opacity-70 hover:opacity-100 transition">
                    {cancelling ? 'Cancelling...' : 'Cancel Batch'}
                  </button>
                )}
                <button onClick={fetchBatchStatus} className="text-xs px-2 py-0.5 border border-current rounded opacity-70 hover:opacity-100 transition">
                  Refresh
                </button>
              </div>
            </div>

            {/* Progress bar */}
            {(() => {
              const completed = batchState.request_counts?.completed ?? 0;
              const failed = batchState.request_counts?.failed ?? 0;
              const total = batchState.request_counts?.total ?? batchState.record_count ?? 0;
              const pct = total > 0 ? (completed / total * 100) : 0;
              return total > 0 ? (
                <div className="mb-3">
                  <div className="flex justify-between text-xs opacity-80 mb-1">
                    <span>{completed.toLocaleString()} / {total.toLocaleString()} ({pct.toFixed(1)}%)</span>
                    {failed > 0 && <span>{failed} failed</span>}
                  </div>
                  <div className="h-2 bg-black/10 rounded-full overflow-hidden">
                    <div className="h-full bg-current opacity-50 rounded-full transition-all duration-500"
                      style={{ width: `${pct}%` }} />
                  </div>
                </div>
              ) : null;
            })()}

            <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs opacity-70">
              {batchState.batch_id && <span className="font-mono">{batchState.batch_id}</span>}
              {batchState.submitted_at && <span>Submitted {new Date(batchState.submitted_at).toLocaleTimeString()}</span>}
              {batchState.completed_at && <span>Finished {new Date(batchState.completed_at).toLocaleTimeString()}</span>}
              {batchState.record_count && <span>{batchState.record_count.toLocaleString()} records in batch</span>}
            </div>
          </div>
        ) : (
          <div className="mb-5 p-3 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-500">
            No batch submitted yet.
          </div>
        )}

        {pipeline.is_owner ? (
          <>
            {/* Suggested next action */}
            {suggestedAction && !isJobRunning && (
              <button onClick={() => launchPhase(suggestedAction.phase)}
                className={`w-full py-2.5 rounded-lg text-sm font-medium transition mb-3 ${suggestedAction.color}`}>
                {suggestedAction.label}
              </button>
            )}

            {/* Running job banner */}
            {isJobRunning && (
              <div className="mb-3 flex items-center gap-3 p-3 bg-yellow-50 border border-yellow-200 rounded-lg text-sm text-yellow-800">
                <span className="flex-1 font-mono text-xs">{activeJobId} running...</span>
                <button onClick={killJob}
                  className="px-3 py-1 text-xs bg-red-600 text-white rounded hover:bg-red-700 transition">
                  Kill job
                </button>
              </div>
            )}

            {/* All phase buttons (collapsed) */}
            <details className="mb-3">
              <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-700 select-none">
                All phases
              </summary>
              <div className="flex flex-wrap gap-2 mt-2">
                {PHASES.map(({ id, label }) => (
                  <button key={id} onClick={() => launchPhase(id)} disabled={isJobRunning}
                    className={`px-3 py-1.5 rounded text-sm border transition ${isJobRunning
                      ? 'opacity-40 cursor-not-allowed bg-gray-50 border-gray-200'
                      : 'border-gray-300 hover:bg-gray-50'}`}>
                    {label}
                  </button>
                ))}
              </div>
            </details>

            {/* Configuration card */}
            <div className="mb-3 p-3 bg-gray-50 border border-gray-200 rounded-lg space-y-3">
              {/* Dataset selector */}
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1.5">Dataset</label>
                <div className="flex flex-wrap gap-1.5">
                  <button
                    onClick={() => setActiveSubsetIdx(null)}
                    className={`text-xs px-2.5 py-1 rounded-full border transition ${activeSubsetIdx === null ? 'bg-purple-600 text-white border-purple-600' : 'bg-white text-gray-600 border-gray-300 hover:border-purple-400'}`}>
                    All records
                  </button>
                  {subsets.map((s, i) => (
                    <button key={i}
                      onClick={() => setActiveSubsetIdx(i === activeSubsetIdx ? null : i)}
                      className={`text-xs px-2.5 py-1 rounded-full border transition ${activeSubsetIdx === i ? 'bg-purple-600 text-white border-purple-600' : 'bg-white text-gray-600 border-gray-300 hover:border-purple-400'}`}>
                      {s.name}
                    </button>
                  ))}
                </div>
                {activeSubsetIdx != null && (
                  <p className="text-xs text-gray-400 mt-1">Processing only records in this subset.</p>
                )}
              </div>

              {/* Model + Limit row */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">Model</label>
                  <select value={selectedModel} onChange={e => setSelectedModel(e.target.value)}
                    className="w-full border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-purple-400 bg-white">
                    {LLM_MODELS.map(m => (
                      <option key={m.id} value={m.id}>
                        {m.label}{m.note ? ` (${m.note})` : ''}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">Record limit <span className="font-normal text-gray-400">(blank = all)</span></label>
                  <input type="number" placeholder="e.g. 500" min="1" value={limit}
                    onChange={e => setLimit(e.target.value)}
                    className="w-full border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-purple-400" />
                </div>
              </div>
            </div>

            {launchError && (
              <div className="mt-3 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                {launchError}
              </div>
            )}
          </>
        ) : (
          <p className="text-gray-400 text-sm italic">View only — you are not the owner.</p>
        )}
      </div>

      {/* Terminal */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="bg-gray-800 px-4 py-2 flex justify-between items-center">
          <span className="text-gray-300 text-sm font-mono">
            {activeJobId ? `job ${activeJobId}` : 'Terminal output'}
          </span>
          {activeJobId && (
            <span className={`text-xs px-2 py-0.5 rounded ${jobStatusCls}`}>
              {jobStatus || 'running'}
            </span>
          )}
        </div>
        <Terminal lines={lines} />
      </div>
    </div>
  );
}

