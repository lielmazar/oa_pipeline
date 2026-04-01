// ── Jobs View ─────────────────────────────────────────────────────────────────

function JobLogPanel({ jobId, onClose }) {
  const [jobInfo, setJobInfo] = useState(null);
  const { lines, status } = useJobStream(jobId);

  useEffect(() => {
    const load = () => apiFetch(`/api/jobs/${jobId}`).then(r => r.json()).then(setJobInfo).catch(() => {});
    load();
    // Re-fetch when job finishes to get final returncode
    if (status === 'done' || status === 'error') load();
  }, [jobId, status]);

  const effStatus = status || jobInfo?.status || '…';
  const statusCls = effStatus === 'done' ? 'bg-green-700 text-green-100'
    : effStatus === 'error' ? 'bg-red-700 text-red-100'
    : effStatus === 'killed' ? 'bg-gray-600 text-gray-200'
    : 'bg-yellow-700 text-yellow-100';

  const copyLogs = () => {
    navigator.clipboard?.writeText(lines.join('\n')).catch(() => {});
  };

  return (
    <div className="mt-4 bg-gray-900 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-gray-800">
        <div className="min-w-0 flex-1 mr-3">
          <div className="text-gray-300 text-sm font-medium truncate">
            {jobInfo ? (jobInfo.label || jobInfo.script) : jobId}
          </div>
          <div className="text-gray-500 text-xs font-mono truncate">
            {jobInfo?.cmd ? jobInfo.cmd.join(' ') : jobId}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {jobInfo?.returncode != null && (
            <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${jobInfo.returncode === 0 ? 'bg-green-800 text-green-200' : 'bg-red-800 text-red-200'}`}>
              exit {jobInfo.returncode}
            </span>
          )}
          <span className={`text-xs px-2 py-0.5 rounded ${statusCls}`}>{effStatus}</span>
          <span className="text-xs text-gray-500">{lines.length} lines</span>
          <button onClick={copyLogs} className="text-gray-400 hover:text-white text-xs px-1.5 py-0.5 border border-gray-700 rounded" title="Copy logs">
            Copy
          </button>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-sm ml-1">✕</button>
        </div>
      </div>
      <Terminal lines={lines} className="h-[32rem]" />
    </div>
  );
}

function JobsView({ jobs, pipelines, onRefresh }) {
  const [selectedJobId, setSelectedJobId] = useState(null);
  useInterval(onRefresh, 5000);

  const pipelineMap = Object.fromEntries(pipelines.map(p => [p.id, p.name]));

  const killJob = async id => {
    if (!confirm('Kill this job?')) return;
    const res = await apiFetch(`/api/jobs/${id}`, { method: 'DELETE' });
    if (!res.ok) { const d = await res.json(); alert(d.error); return; }
    onRefresh();
  };

  const STATUS_CLASSES = {
    running: 'bg-yellow-100 text-yellow-800',
    done:    'bg-green-100 text-green-800',
    error:   'bg-red-100 text-red-800',
    killed:  'bg-gray-100 text-gray-600',
  };

  const sorted = [...jobs].sort((a, b) => (b.started_at || 0) - (a.started_at || 0));

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">Job Monitor</h2>
        <button onClick={onRefresh}
          className="text-sm text-gray-500 border border-gray-200 px-3 py-1.5 rounded-lg hover:bg-gray-50 transition flex items-center gap-1.5">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
          Refresh
        </button>
      </div>
      {sorted.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center text-gray-400">
          <p className="text-lg mb-1">No jobs recorded</p>
          <p className="text-sm">Jobs appear here when pipeline scripts are launched.</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-left">
            <thead className="bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-600 uppercase tracking-wide">
              <tr>
                <th className="px-4 py-3">Job ID</th>
                <th className="px-4 py-3">Pipeline</th>
                <th className="px-4 py-3">Script</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-center">Cores</th>
                <th className="px-4 py-3">PID</th>
                <th className="px-4 py-3">Started</th>
                <th className="px-4 py-3">Duration</th>
                <th className="px-4 py-3 text-right">Log lines</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(job => {
                const pname = job.pipeline_id ? (pipelineMap[job.pipeline_id] || `#${job.pipeline_id}`) : '—';
                const statusCls = STATUS_CLASSES[job.status] || 'bg-gray-100 text-gray-600';
                const elapsed = job.ended_at
                  ? `${Math.round(job.ended_at - job.started_at)}s`
                  : job.started_at ? `${Math.round(Date.now() / 1000 - job.started_at)}s` : '—';
                const started = job.started_at ? new Date(job.started_at * 1000).toLocaleTimeString() : '—';
                const isSelected = selectedJobId === job.id;
                return (
                  <tr key={job.id}
                    onClick={() => setSelectedJobId(isSelected ? null : job.id)}
                    className={`border-b border-gray-100 hover:bg-blue-50 cursor-pointer ${isSelected ? 'bg-blue-50' : ''}`}>
                    <td className="px-4 py-3 text-sm font-mono text-gray-500">{job.id}</td>
                    <td className="px-4 py-3 text-sm">{pname}</td>
                    <td className="px-4 py-3 text-sm font-medium">{job.label || job.script}</td>
                    <td className="px-4 py-3">
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusCls}`}>{job.status}</span>
                      {job.returncode != null && job.returncode !== 0 && (
                        <span className="ml-1 text-xs text-red-500 font-mono">exit {job.returncode}</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-center">
                      {job.workers > 1
                        ? <span className="text-xs font-medium text-blue-700">{job.workers} cores</span>
                        : <span className="text-xs text-gray-400">1</span>}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-500">{job.pid || '—'}</td>
                    <td className="px-4 py-3 text-sm text-gray-500">{started}</td>
                    <td className="px-4 py-3 text-sm text-gray-500">{elapsed}</td>
                    <td className="px-4 py-3 text-right text-xs text-gray-400">{job.line_count > 0 ? job.line_count : '—'}</td>
                    <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                      {job.status === 'running' && (
                        <button onClick={() => killJob(job.id)}
                          className="text-xs text-red-600 border border-red-200 px-2 py-1 rounded hover:bg-red-50 transition">
                          Kill
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {selectedJobId && <JobLogPanel jobId={selectedJobId} onClose={() => setSelectedJobId(null)} />}
    </div>
  );
}

