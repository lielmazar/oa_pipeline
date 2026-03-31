/**
 * OpenAlex Pipeline Dashboard — React SPA
 */

const { useState, useEffect, useRef, useCallback, useMemo } = React;

// ── Helpers ───────────────────────────────────────────────────────────────────

function timeAgo(ts) {
  if (!ts) return '';
  const secs = Math.floor(Date.now() / 1000 - ts);
  if (secs < 60)    return `${secs}s ago`;
  if (secs < 3600)  return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function stripAnsi(str) {
  return String(str ?? '')
    .replace(/\x1b\[[0-9;]*[A-Za-z]/g, '')
    .replace(/\x1b[()][A-Z]/g, '')
    .replace(/\r/g, '');
}

// ── API ───────────────────────────────────────────────────────────────────────

let _onUnauth = null;

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    ...opts,
  });
  if (res.status === 401) {
    if (_onUnauth) _onUnauth();
    throw new Error('Session expired');
  }
  return res;
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useJobStream(jobId, onDone) {
  const [lines, setLines] = useState([]);
  const [status, setStatus] = useState(null);
  const esRef = useRef(null);
  const onDoneRef = useRef(onDone);
  useEffect(() => { onDoneRef.current = onDone; }, [onDone]);

  useEffect(() => {
    if (!jobId) return;
    setLines([]);
    setStatus(null);
    let lastSeq = -1;
    let cancelled = false;

    // Always fetch buffered output first — shows existing lines immediately
    // and gives us the last seq number to resume SSE from.
    async function start() {
      try {
        const r = await fetch(`/api/jobs/${jobId}/output`);
        if (!r.ok || cancelled) return;
        const d = await r.json();
        if (cancelled) return;
        if (d.lines && d.lines.length > 0) {
          setLines(d.lines.map(stripAnsi));
          lastSeq = d.last_seq ?? (d.lines.length - 1);
        }
        setStatus(d.status || null);
        if (d.status !== 'running') {
          // Job already finished — buffered output is complete
          if (onDoneRef.current) onDoneRef.current(d);
          return;
        }
      } catch {}

      // Job is still running — open SSE from after the last buffered line
      connect();
    }

    function connect() {
      if (cancelled) return;
      const es = new EventSource(`/api/jobs/${jobId}/stream?after=${lastSeq}`);
      esRef.current = es;

      es.addEventListener('line', e => {
        try {
          const d = JSON.parse(e.data);
          if (d.seq > lastSeq) {
            setLines(prev => [...prev, stripAnsi(d.text)]);
            lastSeq = d.seq;
          }
        } catch {
          setLines(prev => [...prev, stripAnsi(e.data)]);
        }
      });

      es.addEventListener('done', e => {
        es.close(); esRef.current = null;
        try {
          const d = JSON.parse(e.data);
          setStatus(d.status);
          if (onDoneRef.current) onDoneRef.current(d);
        } catch { setStatus('done'); }
      });

      es.onerror = () => {
        es.close(); esRef.current = null;
        if (cancelled) return;
        setTimeout(async () => {
          if (cancelled) return;
          try {
            const r = await fetch(`/api/jobs/${jobId}/output`);
            if (!r.ok || cancelled) return;
            const d = await r.json();
            if (cancelled) return;
            // Catch up on any lines we missed while disconnected
            if (d.lines && d.lines.length > 0) {
              const newLastSeq = d.last_seq ?? (d.lines.length - 1);
              if (newLastSeq > lastSeq) {
                // Append only new lines
                const newLines = d.lines.slice(lastSeq + 1);
                if (newLines.length > 0) setLines(prev => [...prev, ...newLines.map(stripAnsi)]);
                lastSeq = newLastSeq;
              }
            }
            if (d.status === 'running') {
              connect();
            } else {
              setStatus(d.status);
              if (onDoneRef.current) onDoneRef.current(d);
            }
          } catch {}
        }, 2000);
      };
    }

    start();
    return () => {
      cancelled = true;
      if (esRef.current) { esRef.current.close(); esRef.current = null; }
    };
  }, [jobId]);

  return { lines, status };
}

function useInterval(cb, delay) {
  const cbRef = useRef(cb);
  useEffect(() => { cbRef.current = cb; }, [cb]);
  useEffect(() => {
    if (delay == null) return;
    const id = setInterval(() => cbRef.current(), delay);
    return () => clearInterval(id);
  }, [delay]);
}

// ── Shared components ─────────────────────────────────────────────────────────

function Terminal({ lines, className = '' }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [lines]);
  return (
    <div ref={ref}
      className={`terminal bg-gray-900 text-green-400 p-4 h-96 overflow-y-auto whitespace-pre-wrap ${className}`}>
      {lines.length === 0
        ? <span className="text-gray-600">Waiting for output...</span>
        : lines.join('\n')}
    </div>
  );
}

// ── Login ─────────────────────────────────────────────────────────────────────

function LoginOverlay({ visible, onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  if (!visible) return null;

  const submit = async e => {
    e.preventDefault();
    setError('');
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const d = await res.json();
      if (!res.ok) { setError(d.error || 'Login failed'); return; }
      onLogin(d);
    } catch (err) { setError(err.message); }
  };

  return (
    <div className="fixed inset-0 bg-gray-900/80 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl p-8 w-full max-w-sm">
        <h1 className="text-2xl font-bold mb-1">OpenAlex Pipeline</h1>
        <p className="text-gray-500 text-sm mb-6">Sign in to continue</p>
        <form onSubmit={submit} className="space-y-4">
          <input type="text" placeholder="Username" value={username}
            onChange={e => setUsername(e.target.value)} required autoFocus
            className="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" />
          <input type="password" placeholder="Password" value={password}
            onChange={e => setPassword(e.target.value)} required
            className="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" />
          {error && <p className="text-red-600 text-sm">{error}</p>}
          <button type="submit"
            className="w-full bg-blue-600 text-white py-2 rounded-lg font-semibold hover:bg-blue-700 transition">
            Sign in
          </button>
        </form>
      </div>
    </div>
  );
}

// ── Nav ───────────────────────────────────────────────────────────────────────

function NavBar({ me, view, runningCount, onShowHome, onShowJobs, onLogout }) {
  return (
    <nav className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between sticky top-0 z-40 shadow-sm">
      <button onClick={onShowHome}
        className="flex items-center gap-2 font-bold text-lg text-blue-700 hover:text-blue-900 transition">
        <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"
            d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18"/>
        </svg>
        OpenAlex Pipeline
      </button>
      <div className="flex items-center gap-3">
        <button onClick={onShowJobs}
          className={`text-sm transition px-3 py-1 rounded border flex items-center gap-1.5
            ${view === 'jobs' ? 'text-blue-700 border-blue-300 bg-blue-50' : 'text-gray-500 hover:text-blue-700 border-gray-200 hover:border-blue-300'}`}>
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"
              d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
          </svg>
          Jobs
          {runningCount > 0 && (
            <span className="text-xs bg-yellow-400 text-yellow-900 font-bold rounded-full px-1.5 animate-pulse">
              {runningCount}
            </span>
          )}
        </button>
        <span className="text-sm text-gray-500">{me?.username}</span>
        <button onClick={onLogout}
          className="text-sm text-gray-500 hover:text-red-600 transition px-3 py-1 rounded border border-gray-200 hover:border-red-300">
          Sign out
        </button>
      </div>
    </nav>
  );
}

// ── Stage constants ───────────────────────────────────────────────────────────

const STAGE_IDS = [
  'keyword_selection', 'openalex_search', 'results_review',
  'report_generation', 'llm_classification', 'llm_results_review', 'llm_report_generation',
];

const STAGE_LABELS_MAP = {
  keyword_selection:     '① Keywords',
  openalex_search:       '② Search',
  results_review:        '③ Review',
  report_generation:     '④ Report',
  llm_classification:    '⑤ LLM',
  llm_results_review:    '⑥ LLM Review',
  llm_report_generation: '⑦ Final Report',
};

const STAGE_COLORS = {
  keyword_selection:     'bg-gray-200 text-gray-700',
  openalex_search:       'bg-blue-100 text-blue-800',
  results_review:        'bg-cyan-100 text-cyan-800',
  report_generation:     'bg-emerald-100 text-emerald-800',
  llm_classification:    'bg-purple-100 text-purple-800',
  llm_results_review:    'bg-indigo-100 text-indigo-800',
  llm_report_generation: 'bg-green-100 text-green-800',
};

// ── Home ──────────────────────────────────────────────────────────────────────

function ForkPipelineModal({ pipeline, onForked, onClose }) {
  const [name, setName] = useState(`My Fork of ${pipeline.name}`);
  const [error, setError] = useState('');

  const submit = async e => {
    e.preventDefault();
    if (!name.trim()) return;
    setError('');
    try {
      const res = await apiFetch(`/api/pipelines/${pipeline.id}/fork`, {
        method: 'POST',
        body: JSON.stringify({ name: name.trim() }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.error || 'Fork failed'); return; }
      onForked(data);
    } catch (err) { setError(err.message); }
  };

  return (
    <div className="fixed inset-0 bg-gray-900/60 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl p-6 w-full max-w-md">
        <h3 className="text-lg font-bold mb-2">Fork Pipeline</h3>
        <p className="text-sm text-gray-500 mb-4">Creates a full copy of this pipeline's data in your account.</p>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">New Name *</label>
            <input type="text" value={name} onChange={e => setName(e.target.value)} required autoFocus
              className="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-500" />
          </div>
          {error && <p className="text-red-600 text-sm">{error}</p>}
          <div className="flex gap-3 justify-end">
            <button type="button" onClick={onClose}
              className="px-4 py-2 text-gray-600 border rounded-lg hover:bg-gray-50 transition">Cancel</button>
            <button type="submit"
              className="px-4 py-2 bg-purple-600 text-white rounded-lg font-semibold hover:bg-purple-700 transition">Fork</button>
          </div>
        </form>
      </div>
    </div>
  );
}

function NewPipelineModal({ onCreated, onClose }) {
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [error, setError] = useState('');

  const submit = async e => {
    e.preventDefault();
    if (!name.trim()) return;
    setError('');
    try {
      const res = await apiFetch('/api/pipelines', {
        method: 'POST',
        body: JSON.stringify({ name: name.trim(), description: desc.trim() }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.error || 'Failed'); return; }
      onCreated(data);
    } catch (err) { setError(err.message); }
  };

  return (
    <div className="fixed inset-0 bg-gray-900/60 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl p-6 w-full max-w-md">
        <h3 className="text-lg font-bold mb-4">Create New Pipeline</h3>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Name *</label>
            <input type="text" placeholder="e.g. SAF Research 2024" value={name}
              onChange={e => setName(e.target.value)} required autoFocus
              className="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <input type="text" placeholder="Optional" value={desc}
              onChange={e => setDesc(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          {error && <p className="text-red-600 text-sm">{error}</p>}
          <div className="flex gap-3 justify-end">
            <button type="button" onClick={onClose}
              className="px-4 py-2 text-gray-600 border rounded-lg hover:bg-gray-50 transition">Cancel</button>
            <button type="submit"
              className="px-4 py-2 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700 transition">Create</button>
          </div>
        </form>
      </div>
    </div>
  );
}

function PipelineCard({ pipeline, isSearching, onOpen, onFork, onDelete }) {
  const stageColor = STAGE_COLORS[pipeline.current_stage] || 'bg-gray-100 text-gray-700';
  const ownerLabel = pipeline.is_owner ? 'you' : (pipeline.owner_username || 'unknown');

  return (
    <div onClick={onOpen}
      className="bg-white rounded-xl border border-gray-200 p-5 flex flex-col gap-3 hover:shadow-md transition cursor-pointer">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="font-bold text-base leading-tight">
            {pipeline.name}
            {pipeline.forked_from_id && <span className="text-xs text-purple-500 ml-1">(fork)</span>}
          </h3>
          <p className="text-xs text-gray-400 mt-0.5">by {ownerLabel}</p>
        </div>
        <div className="flex flex-col items-end gap-1 flex-shrink-0">
          <span className={`text-xs font-medium px-2 py-1 rounded-full ${stageColor}`}>
            {pipeline.stage_label || pipeline.current_stage}
          </span>
          {isSearching && (
            <span className="flex items-center gap-1 text-xs text-blue-700 font-medium">
              <span className="inline-block w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
              searching…
            </span>
          )}
        </div>
      </div>
      {pipeline.description && (
        <p className="text-sm text-gray-500 line-clamp-2">{pipeline.description}</p>
      )}
      <div className="flex items-center justify-between mt-auto pt-2 border-t border-gray-100">
        <span className="text-xs text-gray-400">
          {pipeline.updated_at ? timeAgo(new Date(pipeline.updated_at).getTime() / 1000) : ''}
        </span>
        <div className="flex gap-2">
          {!pipeline.is_owner && (
            <button onClick={e => { e.stopPropagation(); onFork(); }}
              className="text-xs text-purple-700 border border-purple-200 px-2 py-1 rounded hover:bg-purple-50 transition">
              Fork
            </button>
          )}
          <button onClick={e => { e.stopPropagation(); onOpen(); }}
            className="text-xs text-blue-700 border border-blue-200 px-2 py-1 rounded hover:bg-blue-50 transition">
            Open
          </button>
          {pipeline.is_owner && (
            <button onClick={e => { e.stopPropagation(); onDelete(); }}
              className="text-xs text-red-600 border border-red-200 px-2 py-1 rounded hover:bg-red-50 transition">
              Delete
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function HomeView({ onOpenPipeline, runningJobs }) {
  const [pipelines, setPipelines] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [forkTarget, setForkTarget] = useState(null);

  const searchingPipelines = new Set(
    runningJobs.filter(j => j.script === 'run_pipeline' && j.status === 'running').map(j => j.pipeline_id)
  );

  const load = useCallback(async () => {
    try {
      const res = await apiFetch('/api/pipelines');
      setPipelines(await res.json());
    } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleDelete = async p => {
    if (!confirm(`Delete pipeline "${p.name}"?`)) return;
    await apiFetch(`/api/pipelines/${p.id}`, { method: 'DELETE' });
    load();
  };

  const openById = async id => {
    const res = await apiFetch(`/api/pipelines/${id}`);
    const p = await res.json();
    if (!res.ok) { alert(p.error); return; }
    onOpenPipeline(p);
  };

  if (loading) return <div className="flex items-center justify-center h-64 text-gray-400">Loading...</div>;

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">Pipelines</h2>
        <button onClick={() => setShowNew(true)}
          className="bg-blue-600 text-white px-4 py-2 rounded-lg font-semibold hover:bg-blue-700 transition flex items-center gap-2">
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4"/>
          </svg>
          New Pipeline
        </button>
      </div>
      {pipelines.length === 0 ? (
        <div className="text-center text-gray-400 py-16">
          <p className="text-lg mb-2">No pipelines yet</p>
          <p className="text-sm">Click "New Pipeline" to get started</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {pipelines.map(p => (
            <PipelineCard key={p.id} pipeline={p}
              isSearching={searchingPipelines.has(p.id)}
              onOpen={() => openById(p.id)}
              onFork={() => setForkTarget(p)}
              onDelete={() => handleDelete(p)}
            />
          ))}
        </div>
      )}
      {showNew && (
        <NewPipelineModal
          onCreated={p => { setShowNew(false); onOpenPipeline(p); }}
          onClose={() => setShowNew(false)}
        />
      )}
      {forkTarget && (
        <ForkPipelineModal
          pipeline={forkTarget}
          onForked={p => { setForkTarget(null); onOpenPipeline(p); }}
          onClose={() => setForkTarget(null)}
        />
      )}
    </div>
  );
}

// ── Pipeline header ───────────────────────────────────────────────────────────

function StageStepper({ currentStage, onNavigate, isOwner, navigating }) {
  const currentIdx = STAGE_IDS.indexOf(currentStage);
  return (
    <div className="flex items-center w-full overflow-x-auto pb-1">
      {STAGE_IDS.map((sid, i) => {
        const isCompleted = i < currentIdx;
        const isCurrent = i === currentIdx;
        const isClickable = isOwner && !isCurrent && !navigating;
        let dotCls = 'w-7 h-7 rounded-full border-2 flex items-center justify-center text-xs font-bold flex-shrink-0 transition-all duration-150 ';
        if (isCompleted)      dotCls += 'bg-green-500 border-green-500 text-white';
        else if (isCurrent)   dotCls += 'bg-blue-600 border-blue-600 text-white';
        else                  dotCls += 'bg-white border-gray-300 text-gray-400';
        if (isClickable)      dotCls += ' cursor-pointer hover:scale-110 hover:shadow-md';
        else if (!isCurrent)  dotCls += ' cursor-default';
        return (
          <div key={sid} className={`flex items-center ${i < STAGE_IDS.length - 1 ? 'flex-1' : ''}`}>
            <div className="flex flex-col items-center">
              <div className={dotCls}
                onClick={isClickable ? () => onNavigate(sid) : undefined}
                title={isClickable ? `Jump to ${STAGE_LABELS_MAP[sid]}` : undefined}>
                {navigating && isCurrent
                  ? <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin inline-block" />
                  : i + 1}
              </div>
              <span
                className={`mt-1 whitespace-nowrap ${isCurrent ? 'text-blue-700 font-semibold' : isClickable ? 'text-gray-500 hover:text-gray-700 cursor-pointer' : 'text-gray-400'}`}
                style={{ fontSize: '0.65rem' }}
                onClick={isClickable ? () => onNavigate(sid) : undefined}>
                {STAGE_LABELS_MAP[sid]}
              </span>
            </div>
            {i < STAGE_IDS.length - 1 && (
              <div className={`flex-1 h-0.5 mx-1 ${i < currentIdx ? 'bg-green-400' : 'bg-gray-200'}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function PipelineHeader({ pipeline, onUpdated }) {
  const [showFork, setShowFork] = useState(false);
  const [navigating, setNavigating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const navigate = async (stageId) => {
    if (navigating) return;
    setNavigating(true);
    try {
      const res = await apiFetch(`/api/pipelines/${pipeline.id}/set-stage`, {
        method: 'POST',
        body: JSON.stringify({ stage: stageId }),
      });
      const updated = await res.json();
      if (!res.ok) { alert(updated.error); return; }
      onUpdated(updated);
    } finally {
      setNavigating(false);
    }
  };

  const handleAdvance = () => navigate(pipeline.next_stage);
  const handleGoBack = () => navigate(pipeline.prev_stage);

  const handleDelete = async () => {
    if (!confirmDelete) { setConfirmDelete(true); return; }
    await apiFetch(`/api/pipelines/${pipeline.id}`, { method: 'DELETE' });
    onUpdated(null);
  };

  return (
    <div className="bg-white border-b border-gray-200 px-6 py-4 shadow-sm">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center gap-3 mb-3">
          <h2 className="text-xl font-bold">{pipeline.name}</h2>
          <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded">
            {pipeline.is_owner ? 'owner' : `by ${pipeline.owner_username || '?'}`}
          </span>
          <div className="ml-auto flex gap-2 flex-wrap items-center">
            {pipeline.is_owner && pipeline.prev_stage && (
              <button onClick={handleGoBack} disabled={navigating}
                className="text-sm text-gray-600 border border-gray-200 px-3 py-1 rounded-lg hover:bg-gray-50 transition disabled:opacity-40">
                ← {pipeline.prev_stage_label}
              </button>
            )}
            <button onClick={() => setShowFork(true)}
              className="text-sm text-purple-700 border border-purple-300 px-3 py-1 rounded-lg hover:bg-purple-50 transition">
              Fork
            </button>
            {pipeline.is_owner && (
              confirmDelete
                ? <span className="flex items-center gap-1">
                    <span className="text-xs text-red-600">Sure?</span>
                    <button onClick={handleDelete}
                      className="text-xs text-white bg-red-600 border border-red-600 px-2 py-1 rounded-lg hover:bg-red-700 transition">
                      Yes, delete
                    </button>
                    <button onClick={() => setConfirmDelete(false)}
                      className="text-xs text-gray-600 border border-gray-200 px-2 py-1 rounded-lg hover:bg-gray-50 transition">
                      Cancel
                    </button>
                  </span>
                : <button onClick={handleDelete}
                    className="text-sm text-red-600 border border-red-300 px-3 py-1 rounded-lg hover:bg-red-50 transition">
                    Delete
                  </button>
            )}
            {pipeline.is_owner && pipeline.next_stage && (
              <button onClick={handleAdvance} disabled={navigating}
                className="text-sm bg-green-600 text-white px-4 py-1 rounded-lg hover:bg-green-700 transition font-medium disabled:opacity-40 flex items-center gap-1.5">
                {navigating && <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />}
                {pipeline.next_stage_label} →
              </button>
            )}
          </div>
        </div>
        <StageStepper
          currentStage={pipeline.current_stage}
          onNavigate={navigate}
          isOwner={pipeline.is_owner}
          navigating={navigating}
        />
      </div>
      {showFork && (
        <ForkPipelineModal
          pipeline={pipeline}
          onForked={p => { setShowFork(false); onUpdated(p, 'fork'); }}
          onClose={() => setShowFork(false)}
        />
      )}
    </div>
  );
}

// ── Stage: Keywords ───────────────────────────────────────────────────────────

// Migrate any config format → canonical {name, subgroups:[{name,terms,is_others?}]}
function _migrateGroups(config) {
  const raw = config.groups && config.groups.length > 0 ? config.groups : [
    { name: 'Fuel Terms',   terms: [...(config.fuel_terms   || [])] },
    { name: 'Impact Terms', terms: [...(config.impact_terms || [])] },
  ];
  return raw.map(g => {
    if (g.subgroups && g.subgroups.length > 0) {
      return { name: g.name, subgroups: g.subgroups.map(sg => ({ ...sg, terms: [...(sg.terms || [])] })) };
    }
    // Flat terms → wrap in one "Others" catch-all subgroup
    return {
      name: g.name,
      subgroups: [{ name: 'Others', is_others: true, terms: [...(g.terms || [])] }],
    };
  });
}

// Count total terms across all subgroups of a group
function _totalTerms(group) {
  return (group.subgroups || []).reduce((s, sg) => s + (sg.terms || []).length, 0);
}

// Get/ensure the "Others" subgroup index within a group's subgroups
function _othersIdx(subgroups) {
  const idx = subgroups.findIndex(sg => sg.is_others);
  return idx >= 0 ? idx : subgroups.length - 1;
}

function KeywordStage({ pipeline, onUpdate }) {
  const config = pipeline.config || {};

  const [groups, setGroups] = useState(() => _migrateGroups(config));
  // newTermInput[gi][si] = current text for the add-term input in group gi, subgroup si
  const [newTermInput, setNewTermInput] = useState(
    () => _migrateGroups(config).map(g => g.subgroups.map(() => ''))
  );
  // newSgName[gi] = text in the "add subgroup" input for group gi
  const [newSgName, setNewSgName] = useState(() => _migrateGroups(config).map(() => ''));
  const [editingSg, setEditingSg] = useState(null); // {gi, si}
  const [dragSrc, setDragSrc] = useState(null);     // {gi, si, term}
  const [dropTarget, setDropTarget] = useState(null);// {gi, si}
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(false);

  // ── Helpers ──────────────────────────────────────────────────────────────

  const setSubgroups = (gi, fn) =>
    setGroups(prev => prev.map((g, i) => i !== gi ? g : { ...g, subgroups: fn(g.subgroups) }));

  const addTerm = (gi, si) => {
    const val = (newTermInput[gi]?.[si] || '').trim();
    if (!val) return;
    const alreadyExists = groups[gi].subgroups.some(sg => (sg.terms || []).includes(val));
    if (alreadyExists) {
      setNewTermInput(prev => prev.map((row, i) => i !== gi ? row : row.map((v, j) => j === si ? '' : v)));
      return;
    }
    setSubgroups(gi, sgs => sgs.map((sg, j) =>
      j !== si ? sg : { ...sg, terms: [...(sg.terms || []), val] }
    ));
    setNewTermInput(prev => prev.map((row, i) => i !== gi ? row : row.map((v, j) => j === si ? '' : v)));
  };

  const removeTerm = (gi, si, term) =>
    setSubgroups(gi, sgs => sgs.map((sg, j) =>
      j !== si ? sg : { ...sg, terms: (sg.terms || []).filter(t => t !== term) }
    ));

  const moveTerm = (fromGi, fromSi, toGi, toSi, term) => {
    if (fromGi === toGi && fromSi === toSi) return;
    setGroups(prev => prev.map((g, gi) => {
      if (gi !== fromGi && gi !== toGi) return g;
      return {
        ...g,
        subgroups: g.subgroups.map((sg, si) => {
          if (gi === fromGi && si === fromSi)
            return { ...sg, terms: (sg.terms || []).filter(t => t !== term) };
          if (gi === toGi && si === toSi && !(sg.terms || []).includes(term))
            return { ...sg, terms: [...(sg.terms || []), term] };
          return sg;
        }),
      };
    }));
  };

  const addSubgroup = gi => {
    const name = (newSgName[gi] || '').trim();
    if (!name) return;
    const othersI = _othersIdx(groups[gi].subgroups);
    setSubgroups(gi, sgs => [
      ...sgs.slice(0, othersI),
      { name, terms: [] },
      ...sgs.slice(othersI),
    ]);
    setNewSgName(prev => prev.map((v, i) => i === gi ? '' : v));
    // Expand inputs array
    setNewTermInput(prev => prev.map((row, i) =>
      i !== gi ? row : [...row.slice(0, othersI), '', ...row.slice(othersI)]
    ));
  };

  const removeSubgroup = (gi, si) => {
    const sg = groups[gi].subgroups[si];
    if (sg.is_others) return;
    const termsToMove = sg.terms || [];
    setSubgroups(gi, sgs => {
      const othersI = _othersIdx(sgs.filter((_, j) => j !== si));
      const remaining = sgs.filter((_, j) => j !== si);
      if (termsToMove.length > 0 && remaining[othersI]) {
        remaining[othersI] = {
          ...remaining[othersI],
          terms: [...(remaining[othersI].terms || []), ...termsToMove],
        };
      }
      return remaining;
    });
    setNewTermInput(prev => prev.map((row, i) => i !== gi ? row : row.filter((_, j) => j !== si)));
  };

  const renameSg = (gi, si, name) =>
    setSubgroups(gi, sgs => sgs.map((sg, j) => j !== si ? sg : { ...sg, name }));

  const loadDefaults = async () => {
    setLoading(true);
    try {
      const res = await apiFetch('/api/default-keywords');
      if (!res.ok) throw new Error('fetch failed');
      const data = await res.json();
      const migrated = _migrateGroups(data);
      setGroups(migrated);
      setNewTermInput(migrated.map(g => g.subgroups.map(() => '')));
      setNewSgName(migrated.map(() => ''));
    } catch { alert('Could not load defaults.'); }
    finally { setLoading(false); }
  };

  const save = async () => {
    const res = await apiFetch(`/api/pipelines/${pipeline.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ config: { ...config, groups } }),
    });
    const updated = await res.json();
    if (!res.ok) { alert(updated.error); return; }
    setSaved(true); setTimeout(() => setSaved(false), 3000);
    if (onUpdate) onUpdate(updated);
  };

  // ── Drag helpers ─────────────────────────────────────────────────────────

  const onDragStart = (e, gi, si, term) => {
    setDragSrc({ gi, si, term });
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', JSON.stringify({ gi, si, term }));
  };
  const onDragEnd = () => { setDragSrc(null); setDropTarget(null); };
  const onDragOver = (e, gi, si) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDropTarget({ gi, si });
  };
  const onDragLeave = () => setDropTarget(null);
  const onDrop = (e, gi, si) => {
    e.preventDefault();
    try {
      const src = JSON.parse(e.dataTransfer.getData('text/plain'));
      moveTerm(src.gi, src.si, gi, si, src.term);
    } catch {}
    setDragSrc(null); setDropTarget(null);
  };

  // ── Sub-group colors (cycle) ──────────────────────────────────────────────
  const SG_COLORS = [
    'bg-blue-50 border-blue-200',  'bg-green-50 border-green-200',
    'bg-amber-50 border-amber-200','bg-purple-50 border-purple-200',
    'bg-cyan-50 border-cyan-200',  'bg-rose-50 border-rose-200',
  ];

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <div className="flex items-start justify-between mb-1">
          <h3 className="font-semibold text-lg">Stage 1: Keyword Selection</h3>
          {pipeline.is_owner && (
            <button onClick={loadDefaults} disabled={loading}
              className="text-xs px-3 py-1 border border-gray-300 rounded-lg text-gray-600 hover:bg-gray-50 transition">
              {loading ? 'Loading…' : '⟳ Load defaults'}
            </button>
          )}
        </div>
        <p className="text-sm text-gray-500 mb-4">
          Each record must match ≥ 1 term from <em>each</em> top-level group.
          Within a group, sub-groups categorise terms for report analysis.
          <strong className="ml-1">Drag terms</strong> between sub-groups to reassign them.
        </p>

        <div className="space-y-8">
          {groups.map((group, gi) => (
            <div key={gi} className="border border-gray-200 rounded-xl overflow-hidden">
              {/* Group header */}
              <div className="bg-gray-800 text-white px-5 py-3 flex items-center gap-3">
                <span className="font-semibold text-sm">{group.name || `Group ${gi + 1}`}</span>
                <span className="text-gray-400 text-xs">{_totalTerms(group)} terms</span>
                <span className="text-gray-500 text-xs ml-auto">{(group.subgroups || []).filter(sg => !sg.is_others).length} sub-groups + Others</span>
              </div>

              {/* Sub-groups */}
              <div className="divide-y divide-gray-100">
                {(group.subgroups || []).map((sg, si) => {
                  const colorCls = sg.is_others
                    ? 'bg-gray-50 border-gray-200'
                    : SG_COLORS[si % SG_COLORS.length];
                  const isDropTarget = dropTarget && dropTarget.gi === gi && dropTarget.si === si;

                  return (
                    <div key={si}
                      className={`p-4 transition-colors ${isDropTarget ? 'ring-2 ring-inset ring-blue-400 bg-blue-50' : ''}`}
                      onDragOver={e => onDragOver(e, gi, si)}
                      onDragLeave={onDragLeave}
                      onDrop={e => onDrop(e, gi, si)}
                    >
                      {/* Sub-group header */}
                      <div className="flex items-center gap-2 mb-2">
                        {/* Name (editable) */}
                        {editingSg && editingSg.gi === gi && editingSg.si === si ? (
                          <input autoFocus type="text" value={sg.name}
                            onChange={e => renameSg(gi, si, e.target.value)}
                            onBlur={() => setEditingSg(null)}
                            onKeyDown={e => e.key === 'Enter' && setEditingSg(null)}
                            className="font-medium text-xs border-b border-blue-400 bg-transparent focus:outline-none w-36"
                          />
                        ) : (
                          <button
                            disabled={!pipeline.is_owner || sg.is_others}
                            onClick={() => pipeline.is_owner && !sg.is_others && setEditingSg({ gi, si })}
                            className={`font-medium text-xs flex items-center gap-1 ${sg.is_others ? 'text-gray-400 italic cursor-default' : 'text-gray-700 hover:text-blue-600'}`}
                            title={sg.is_others ? 'Catch-all for unassigned terms' : 'Click to rename'}
                          >
                            <span className={`inline-block w-2 h-2 rounded-full mr-1 ${colorCls.split(' ')[0].replace('bg-', 'bg-').replace('-50', '-400')}`} />
                            {sg.name || `Sub-group ${si + 1}`}
                            {pipeline.is_owner && !sg.is_others && <span className="text-gray-400 text-xs">✎</span>}
                          </button>
                        )}
                        <span className="text-xs text-gray-400">({(sg.terms || []).length} terms)</span>
                        {/* Delete sub-group */}
                        {pipeline.is_owner && !sg.is_others && (
                          <button onClick={() => removeSubgroup(gi, si)}
                            className="ml-auto text-xs text-red-400 hover:text-red-600 transition px-1"
                            title="Delete sub-group (terms move to Others)">✕</button>
                        )}
                      </div>

                      {/* Terms drop zone */}
                      <div className={`flex flex-wrap gap-1.5 p-2 min-h-[2.5rem] rounded-lg border transition-colors ${colorCls} ${isDropTarget ? 'border-blue-400' : ''}`}>
                        {(sg.terms || []).length === 0
                          ? <span className="text-gray-400 text-xs italic">Drop terms here or add below</span>
                          : (sg.terms || []).map(term => {
                            const isDragging = dragSrc && dragSrc.gi === gi && dragSrc.si === si && dragSrc.term === term;
                            return (
                              <span key={term}
                                draggable={pipeline.is_owner}
                                onDragStart={e => onDragStart(e, gi, si, term)}
                                onDragEnd={onDragEnd}
                                className={`tag-pill cursor-grab active:cursor-grabbing select-none ${isDragging ? 'opacity-40' : ''}`}
                                title="Drag to move to another sub-group"
                              >
                                {term}
                                {pipeline.is_owner && (
                                  <button onClick={() => removeTerm(gi, si, term)}>✕</button>
                                )}
                              </span>
                            );
                          })}
                      </div>

                      {/* Add term input */}
                      {pipeline.is_owner && (
                        <div className="flex gap-2 mt-2">
                          <input type="text"
                            placeholder={`Add to ${sg.name || 'sub-group'}…`}
                            value={newTermInput[gi]?.[si] || ''}
                            onChange={e => setNewTermInput(prev =>
                              prev.map((row, i) => i !== gi ? row : row.map((v, j) => j === si ? e.target.value : v))
                            )}
                            onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addTerm(gi, si))}
                            className="flex-1 border rounded px-3 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-blue-400"
                          />
                          <button onClick={() => addTerm(gi, si)}
                            className="px-3 py-1 bg-blue-600 text-white text-xs rounded hover:bg-blue-700 transition">
                            Add
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Add new sub-group */}
              {pipeline.is_owner && (
                <div className="bg-gray-50 border-t border-gray-200 px-4 py-3 flex gap-2">
                  <input type="text"
                    placeholder="New sub-group name…"
                    value={newSgName[gi] || ''}
                    onChange={e => setNewSgName(prev => prev.map((v, i) => i === gi ? e.target.value : v))}
                    onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addSubgroup(gi))}
                    className="flex-1 border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                  />
                  <button onClick={() => addSubgroup(gi)}
                    className="px-3 py-1.5 text-sm bg-gray-700 text-white rounded hover:bg-gray-900 transition">
                    + Sub-group
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="mt-6 flex items-center gap-4">
          {saved && <span className="text-sm text-green-600 font-medium">✓ Keywords saved</span>}
          {pipeline.is_owner && (
            <button onClick={save}
              className="flex-1 py-2 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700 transition">
              Save Keywords
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Stage: Search ─────────────────────────────────────────────────────────────

function SearchStage({ pipeline, pipelineStatus, onJobStart, onJobDone }) {
  const [maxFiles, setMaxFiles] = useState('');
  const [localDir, setLocalDir] = useState(() => localStorage.getItem('oa_local_dir') || '');
  const [editingDir, setEditingDir] = useState(false);
  const [totalFiles, setTotalFiles] = useState(null);
  const [activeJobId, setActiveJobId] = useState(null);
  const [speed, setSpeed] = useState(null); // { filesPerMin, dbMBps }
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
  }, [pipeline.id]);

  const saveDir = val => {
    setLocalDir(val);
    localStorage.setItem('oa_local_dir', val);
    setEditingDir(false);
  };

  const startSearch = async () => {
    const args = {
      '--output-db': pipeline.output_db_path,
      '--state-file': pipeline.state_file_path,
      '--workers': 16,
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
              <div className="flex items-end pb-0.5">
                <span className="text-xs text-gray-400">Workers: <strong className="text-blue-700">16 cores</strong> (fixed)</span>
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
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <div className="flex justify-between text-sm text-gray-600 mb-1">
            <span>{(prog.processed || 0).toLocaleString()} / {(prog.total || 0).toLocaleString()} files processed</span>
            <span className="font-semibold">{prog.percent}%</span>
          </div>
          <div className="w-full bg-gray-200 rounded-full h-2 mb-2">
            <div className="bg-blue-500 h-2 rounded-full transition-all duration-500" style={{ width: `${prog.percent}%` }} />
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-gray-400">
            {pipelineStatus?.db_size_mb > 0 && (
              <span>DB: <strong className="text-gray-600">{pipelineStatus.db_size_mb} MB</strong></span>
            )}
            {pipelineStatus?.record_count > 0 && (
              <span>Records: <strong className="text-gray-600">{(pipelineStatus.record_count).toLocaleString()}</strong></span>
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
          </div>
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

// ── Stage: Review ─────────────────────────────────────────────────────────────

const JSON_DISPLAY_COLS = new Set([
  'matched_fuel_terms', 'matched_impact_terms', 'authors_countries',
  'authors_institutions', 'llm_fuel_classification', 'llm_ml_paradigm', 'llm_ml_method',
  'llm_ml_model_type', 'concepts', 'topics', 'llm_models',
]);

// Use browser Intl.DisplayNames to convert ISO-3166 alpha-2 codes to full names
const _regionNames = (() => {
  try { return new Intl.DisplayNames(['en'], { type: 'region' }); } catch { return null; }
})();
function countryDisplay(code) {
  if (!code) return code;
  try { return _regionNames ? (_regionNames.of(code.toUpperCase()) || code) : code; }
  catch { return code; }
}
function countryWikiUrl(code) {
  const name = countryDisplay(code);
  return `https://en.wikipedia.org/wiki/${encodeURIComponent(name.replace(/ /g, '_'))}`;
}

function RecordModal({ record, fuelSubgroups, onClose }) {
  if (!record) return null;

  useEffect(() => {
    const h = e => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, [onClose]);

  const oaId  = record.openalex_id || '';
  const rawDoi = (record.doi || '').replace(/^https?:\/\/(dx\.)?doi\.org\//, '');
  const doiUrl = rawDoi ? `https://doi.org/${rawDoi}` : null;
  const oaUrl  = oaId ? `https://openalex.org/${oaId}` : null;

  const parseJsonArr = v => {
    if (!v) return [];
    try { const p = JSON.parse(v); return Array.isArray(p) ? p : []; } catch { return []; }
  };
  const fuelTerms    = parseJsonArr(record.matched_fuel_terms);
  const impactTerms  = parseJsonArr(record.matched_impact_terms);
  const institutions = parseJsonArr(record.authors_institutions);
  const countries    = parseJsonArr(record.authors_countries);
  const llmScopes    = parseJsonArr(record.llm_fuel_classification).filter(v => v && v.toLowerCase() !== 'none');
  const llmParadigms = parseJsonArr(record.llm_ml_paradigm).filter(v => v && v.toLowerCase() !== 'none');
  const llmMethods   = parseJsonArr(record.llm_ml_method).filter(v => v && v.toLowerCase() !== 'none');
  const llmModels    = parseJsonArr(record.llm_models);
  const hasLLM       = record.llm_is_hydrocarbon !== undefined && record.llm_is_hydrocarbon !== null;

  const fwci = record.fwci != null ? Number(record.fwci) : null;
  const fwciColor = !fwci ? 'text-gray-400' : fwci >= 3 ? 'text-green-600 font-bold' : fwci >= 1 ? 'text-blue-600' : 'text-gray-500';

  const recSubgroups = useMemo(
    () => computeRowSubgroups(record.matched_fuel_terms, fuelSubgroups || []),
    [record.matched_fuel_terms, fuelSubgroups]
  );

  const OA_STYLE = {
    gold:     'bg-yellow-50 text-yellow-700 border-yellow-200',
    green:    'bg-green-50 text-green-700 border-green-200',
    hybrid:   'bg-purple-50 text-purple-700 border-purple-200',
    bronze:   'bg-orange-50 text-orange-700 border-orange-200',
    closed:   'bg-gray-50 text-gray-500 border-gray-200',
  };
  const oaStyle = OA_STYLE[record.open_access_oa_status] || OA_STYLE.closed;

  const Chip = ({ children, cls = 'bg-gray-100 text-gray-600 border-gray-200' }) => (
    <span className={`text-xs px-2 py-0.5 rounded-full border ${cls}`}>{children}</span>
  );

  const SectionHead = ({ children }) => (
    <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">{children}</h4>
  );

  return (
    <div className="fixed inset-0 bg-gray-900/70 flex items-start justify-center z-50 p-4 pt-10 overflow-y-auto"
      onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl mb-10">

        {/* ── Title + links ── */}
        <div className="p-6 pb-4 border-b border-gray-100">
          <div className="flex gap-3 items-start">
            <div className="flex-1 min-w-0">
              <h3 className="text-base font-bold text-gray-900 leading-snug mb-3">
                {record.title || oaId || 'Record'}
              </h3>
              <div className="flex flex-wrap gap-1.5">
                {doiUrl && <a href={doiUrl} target="_blank" rel="noopener"
                  className="text-xs px-2.5 py-1 bg-blue-600 text-white rounded-full hover:bg-blue-700 transition font-medium">
                  DOI ↗
                </a>}
                {oaUrl && <a href={oaUrl} target="_blank" rel="noopener"
                  className="text-xs px-2.5 py-1 bg-purple-100 text-purple-700 rounded-full hover:bg-purple-200 transition border border-purple-200">
                  OpenAlex ↗
                </a>}
                {record.open_access_oa_status && (
                  <Chip cls={oaStyle}>{record.open_access_oa_status} OA</Chip>
                )}
                {recSubgroups.map(sg => (
                  <Chip key={sg} cls="bg-blue-50 text-blue-700 border-blue-200">{sg}</Chip>
                ))}
              </div>
            </div>
            <button onClick={onClose} title="Close (Esc)"
              className="flex-shrink-0 p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12"/>
              </svg>
            </button>
          </div>
        </div>

        {/* ── Stats bar ── */}
        <div className="grid grid-cols-4 divide-x divide-gray-100 border-b border-gray-100 bg-gray-50/50">
          {[
            { label: 'Year',  value: record.publication_year || '—', cls: 'text-gray-800' },
            { label: 'FWCI',  value: fwci != null ? fwci.toFixed(2) : '—', cls: fwciColor },
            { label: 'Type',  value: record.type || '—', cls: 'text-gray-700' },
            { label: 'Language', value: (record.language || '—').toUpperCase(), cls: 'text-gray-700' },
          ].map(({ label, value, cls }) => (
            <div key={label} className="px-4 py-3 text-center">
              <div className="text-xs text-gray-400 mb-0.5 uppercase tracking-wide">{label}</div>
              <div className={`text-sm ${cls}`}>{value}</div>
            </div>
          ))}
        </div>

        {/* ── Body ── */}
        <div className="p-6 space-y-5 overflow-y-auto max-h-[55vh]">

          {/* Abstract */}
          {record.abstract_text && (
            <div>
              <SectionHead>Abstract</SectionHead>
              <p className="text-sm text-gray-700 leading-relaxed">{record.abstract_text}</p>
            </div>
          )}

          {/* Matched keywords */}
          {(fuelTerms.length > 0 || impactTerms.length > 0) && (
            <div>
              <SectionHead>Matched Keywords</SectionHead>
              <div className="space-y-2">
                {fuelTerms.length > 0 && (
                  <div className="flex flex-wrap gap-1 items-center">
                    <span className="text-xs text-gray-400 w-12 flex-shrink-0">Fuel</span>
                    {fuelTerms.map(t => <Chip key={t} cls="bg-orange-50 text-orange-700 border-orange-200">{t}</Chip>)}
                  </div>
                )}
                {impactTerms.length > 0 && (
                  <div className="flex flex-wrap gap-1 items-center">
                    <span className="text-xs text-gray-400 w-12 flex-shrink-0">Impact</span>
                    {impactTerms.map(t => <Chip key={t} cls="bg-teal-50 text-teal-700 border-teal-200">{t}</Chip>)}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Affiliations */}
          {(institutions.length > 0 || countries.length > 0) && (
            <div>
              <SectionHead>Affiliations</SectionHead>
              <div className="space-y-2">
                {institutions.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {institutions.map(inst => (
                      <Chip key={inst} cls="bg-purple-50 text-purple-700 border-purple-200">{inst}</Chip>
                    ))}
                  </div>
                )}
                {countries.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {countries.map(c => (
                      <a key={c} href={countryWikiUrl(c)} target="_blank" rel="noreferrer"
                        className="text-xs px-2 py-0.5 bg-gray-100 text-gray-600 rounded-full border border-gray-200 hover:bg-gray-200 transition">
                        {countryDisplay(c)}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* LLM classification */}
          {hasLLM && (
            <div className="bg-indigo-50 rounded-xl p-4 border border-indigo-100">
              <SectionHead>
                <span className="text-indigo-500">AI / LLM Classification</span>
              </SectionHead>
              <div className="space-y-2.5">
                <div className="flex flex-wrap gap-2">
                  {record.llm_is_hydrocarbon != null && (
                    <Chip cls={record.llm_is_hydrocarbon ? 'bg-green-100 text-green-700 border-green-200' : 'bg-gray-100 text-gray-500 border-gray-200'}>
                      {record.llm_is_hydrocarbon ? '✓ Hydrocarbon' : '✗ Non-hydrocarbon'}
                    </Chip>
                  )}
                  {record.llm_confidence && (
                    <Chip cls={record.llm_confidence === 'High' ? 'bg-green-100 text-green-700 border-green-200' : record.llm_confidence === 'Medium' ? 'bg-amber-100 text-amber-700 border-amber-200' : 'bg-red-100 text-red-600 border-red-200'}>
                      {record.llm_confidence} confidence
                    </Chip>
                  )}
                </div>
                {llmScopes.length > 0 && (
                  <div>
                    <span className="text-xs text-gray-500 block mb-1">Research scope</span>
                    <div className="flex flex-wrap gap-1">
                      {llmScopes.map(s => <Chip key={s} cls="bg-indigo-100 text-indigo-700 border-indigo-200">{s}</Chip>)}
                    </div>
                  </div>
                )}
                {(llmParadigms.length > 0 || llmMethods.length > 0) && (
                  <div className="flex gap-6">
                    {llmParadigms.length > 0 && (
                      <div>
                        <span className="text-xs text-gray-500 block mb-1">ML paradigm</span>
                        <div className="flex flex-wrap gap-1">
                          {llmParadigms.map(p => <Chip key={p} cls="bg-blue-100 text-blue-700 border-blue-200">{p}</Chip>)}
                        </div>
                      </div>
                    )}
                    {llmMethods.length > 0 && (
                      <div>
                        <span className="text-xs text-gray-500 block mb-1">ML method</span>
                        <div className="flex flex-wrap gap-1">
                          {llmMethods.map(m => <Chip key={m} cls="bg-cyan-100 text-cyan-700 border-cyan-200">{m}</Chip>)}
                        </div>
                      </div>
                    )}
                  </div>
                )}
                {llmModels.length > 0 && (
                  <div>
                    <span className="text-xs text-gray-500 block mb-1">Models mentioned</span>
                    <div className="flex flex-wrap gap-1">
                      {llmModels.map((m, i) => {
                        const name = typeof m === 'object' ? (m.name || JSON.stringify(m)) : String(m);
                        return <Chip key={i} cls="bg-gray-100 text-gray-600 border-gray-200">{name}</Chip>;
                      })}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* ── Footer ── */}
        <div className="px-6 py-3 border-t border-gray-100 bg-gray-50/50 rounded-b-2xl flex items-center justify-between">
          <span className="text-xs text-gray-400 font-mono">{oaId}</span>
          <button onClick={onClose}
            className="text-xs px-3 py-1.5 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function ColPickerDropdown({ label, allCols, selected, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const handler = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);
  const toggle = col => {
    if (selected.includes(col)) onChange(selected.filter(c => c !== col));
    else onChange([...selected, col]);
  };
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(o => !o)}
        className="border rounded-lg px-3 py-2 text-sm flex items-center gap-1.5 hover:bg-gray-50 whitespace-nowrap">
        {label}
        {selected.length > 0 && <span className="bg-blue-100 text-blue-700 text-xs px-1.5 py-0.5 rounded-full">{selected.length}</span>}
        <svg className="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7"/>
        </svg>
      </button>
      {open && (
        <div className="absolute z-20 top-full left-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg p-2 min-w-[220px] max-h-72 overflow-y-auto">
          <div className="flex gap-2 mb-2 border-b pb-2">
            <button onClick={() => onChange(allCols)} className="text-xs text-blue-600 hover:underline">All</button>
            <button onClick={() => onChange([])} className="text-xs text-gray-500 hover:underline">None</button>
          </div>
          {allCols.map(col => (
            <label key={col} className="flex items-center gap-2 px-1 py-0.5 rounded hover:bg-gray-50 cursor-pointer text-xs">
              <input type="checkbox" checked={selected.includes(col)} onChange={() => toggle(col)} className="accent-blue-600" />
              <span className="font-mono">{col}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// Compute which fuel sub-groups a record belongs to, given its matched_fuel_terms JSON string
function computeRowSubgroups(matchedJson, subgroupDefs) {
  if (!matchedJson || !subgroupDefs || !subgroupDefs.length) return [];
  let terms;
  try { terms = JSON.parse(matchedJson); } catch { return []; }
  if (!Array.isArray(terms)) return [];
  const termSet = new Set(terms.map(t => (t || '').toLowerCase()));
  const termToSg = {};
  for (const sg of subgroupDefs) {
    if (sg.is_others) continue;
    for (const t of (sg.terms || [])) termToSg[t.toLowerCase()] = sg.name;
  }
  const matched = new Set();
  for (const t of termSet) { if (termToSg[t]) matched.add(termToSg[t]); }
  if (matched.size === 0) {
    const othersName = (subgroupDefs.find(sg => sg.is_others) || {}).name || 'Others';
    return [othersName];
  }
  return [...matched];
}

function ReviewStage({ pipeline, isLLM }) {
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState('publication_year');
  const [search, setSearch] = useState('');
  const [records, setRecords] = useState(null);
  const [schema, setSchema] = useState([]);
  const [visibleCols, setVisibleCols] = useState([]);   // [] = all
  const [searchInCols, setSearchInCols] = useState([]); // [] = default
  const [sqlText, setSqlText] = useState('');
  const [sqlResult, setSqlResult] = useState(null);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [subgroupFilter, setSubgroupFilter] = useState('');  // '' = all
  const searchInputRef = useRef(null);

  // Subgroup definitions from pipeline config (group 0 = fuel terms)
  const fuelSubgroups = useMemo(() => {
    const config = pipeline.config || {};
    const groups = _migrateGroups(config);
    return groups[0]?.subgroups || [];
  }, [pipeline.config]);

  // Load schema once to populate column pickers
  useEffect(() => {
    apiFetch(`/api/db/schema?pipeline_id=${pipeline.id}`)
      .then(r => r.json())
      .then(cols => {
        if (Array.isArray(cols)) setSchema(cols.map(c => c.name));
      })
      .catch(() => {});
  }, [pipeline.id]);

  const loadRecords = useCallback(async (p, s, q, vcols, scols) => {
    const params = new URLSearchParams({ pipeline_id: pipeline.id, page: p, sort: s, dir: 'desc', q: q || '' });
    if (vcols && vcols.length) params.set('visible_cols', vcols.join(','));
    if (scols && scols.length) params.set('search_cols', scols.join(','));
    try {
      const res = await apiFetch(`/api/db/records?${params}`);
      const data = await res.json();
      if (!data.error) setRecords(data);
    } catch {}
  }, [pipeline.id]);

  useEffect(() => { loadRecords(1, 'publication_year', '', [], []); }, [loadRecords]);

  const doSearch = () => {
    const q = searchInputRef.current?.value || '';
    setSearch(q); setPage(1);
    loadRecords(1, sort, q, visibleCols, searchInCols);
  };
  const doSort = s => { setSort(s); setPage(1); loadRecords(1, s, search, visibleCols, searchInCols); };
  const doPage = p => { setPage(p); loadRecords(p, sort, search, visibleCols, searchInCols); };
  const applyColChange = (vcols) => { setVisibleCols(vcols); setPage(1); loadRecords(1, sort, search, vcols, searchInCols); };
  const applySearchCols = (scols) => { setSearchInCols(scols); };

  const handleRowClick = async rowId => {
    const res = await apiFetch(`/api/db/records/${encodeURIComponent(rowId)}?pipeline_id=${pipeline.id}`);
    const rec = await res.json();
    if (!res.ok) return;
    setSelectedRecord(rec);
  };

  const runSql = async () => {
    const sql = sqlText.trim();
    if (!sql) return;
    const res = await apiFetch('/api/db/query', {
      method: 'POST',
      body: JSON.stringify({ pipeline_id: pipeline.id, sql }),
    });
    setSqlResult(await res.json());
  };

  const exportCsv = () => {
    if (!sqlResult?.rows) return;
    const csv = [
      sqlResult.columns.join(','),
      ...sqlResult.rows.map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))
    ].join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
    a.download = `pipeline_${pipeline.id}_query.csv`;
    a.click();
  };

  const exportTableCsv = () => {
    if (!records?.rows) return;
    const csv = [
      records.columns.join(','),
      ...records.rows.map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))
    ].join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
    a.download = `pipeline_${pipeline.id}_records_p${page}.csv`;
    a.click();
  };

  const perPage = records?.per_page || 100;
  const totalPages = records ? Math.ceil(records.total / perPage) : 0;

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h3 className="font-semibold text-lg mb-1">{isLLM ? 'Stage 6: LLM Results Review' : 'Stage 3: Results Review'}</h3>
        <p className="text-sm text-gray-500 mb-4">Browse and search the filtered records database. Click a row for full details.</p>
        {records && (
          <div className="flex gap-4 text-sm text-gray-600 mb-4">
            <span><strong>{(records.total || 0).toLocaleString()}</strong> records</span>
          </div>
        )}

        {/* Search + sort controls */}
        <div className="flex gap-3 mb-3 flex-wrap">
          <input ref={searchInputRef} type="text" placeholder="Search..."
            defaultValue={search}
            onKeyDown={e => e.key === 'Enter' && doSearch()}
            className="flex-1 min-w-[200px] border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
          {schema.length > 0 && (
            <ColPickerDropdown label="Search in" allCols={schema} selected={searchInCols} onChange={applySearchCols} />
          )}
          <select value={sort} onChange={e => doSort(e.target.value)}
            className="border rounded-lg px-3 py-2 text-sm">
            <option value="publication_year">Year ↓</option>
            <option value="title">Title ↑</option>
            <option value="fwci">FWCI ↓</option>
            {isLLM && <>
              <option value="llm_fuel_classification">LLM Fuel Type</option>
              <option value="llm_confidence">LLM Confidence</option>
              <option value="llm_is_hydrocarbon">Is Hydrocarbon</option>
            </>}
          </select>
          <button onClick={doSearch}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 transition">Search</button>
        </div>

        {/* Sub-group filter (only shown when subgroups are configured) */}
        {fuelSubgroups.length > 1 && (
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-medium text-gray-600">Fuel sub-group:</span>
            <div className="flex flex-wrap gap-1.5">
              <button onClick={() => setSubgroupFilter('')}
                className={`text-xs px-2.5 py-1 rounded-full border transition ${!subgroupFilter ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'}`}>
                All
              </button>
              {fuelSubgroups.map(sg => (
                <button key={sg.name} onClick={() => setSubgroupFilter(sg.name === subgroupFilter ? '' : sg.name)}
                  className={`text-xs px-2.5 py-1 rounded-full border transition ${subgroupFilter === sg.name ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'}`}>
                  {sg.name}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Export */}
        <div className="flex gap-3 mb-4 items-center flex-wrap">
          {records?.rows?.length > 0 && (
            <button onClick={exportTableCsv}
              className="border rounded-lg px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 transition">
              Export CSV (page)
            </button>
          )}
        </div>

        <div className="min-h-[12rem] rounded-xl border border-gray-200 overflow-hidden">
          {!records ? (
            <div className="text-gray-400 text-center py-12">Loading records…</div>
          ) : !records.rows?.length ? (
            <div className="text-gray-400 text-center py-12">No records found</div>
          ) : (() => {
            const cols = records.columns;
            const ci = name => cols.indexOf(name);
            const TITLE = ci('title'); const DOI = ci('doi'); const YEAR = ci('publication_year');
            const FWCI  = ci('fwci'); const TYPE = ci('type'); const OA = ci('open_access_oa_status');
            const ABS   = ci('abstract_text'); const MFUEL = ci('matched_fuel_terms');
            const MIMP  = ci('matched_impact_terms'); const INST = ci('authors_institutions');
            const SOURCE= ci('primary_location_source_display_name');
            const parseArr = v => { if (!v) return []; try { const p = JSON.parse(v); return Array.isArray(p) ? p : []; } catch { return []; } };
            const OA_CHIP = { gold: 'bg-yellow-50 text-yellow-600', green: 'bg-green-50 text-green-600', hybrid: 'bg-purple-50 text-purple-600', bronze: 'bg-orange-50 text-orange-600' };

            return (
              <div className="divide-y divide-gray-100">
                {records.rows.map((row, ri) => {
                  const matchedJson  = MFUEL >= 0 ? row[MFUEL] : null;
                  const rowSubgroups = computeRowSubgroups(matchedJson, fuelSubgroups);
                  if (subgroupFilter && !rowSubgroups.includes(subgroupFilter)) return null;

                  const rowId   = row[ci('openalex_id')] || ri;
                  const title   = TITLE >= 0 ? (row[TITLE] || '') : '';
                  const doi     = DOI  >= 0 ? (row[DOI]   || '') : '';
                  const year    = YEAR >= 0 ? row[YEAR]   : null;
                  const fwci    = FWCI >= 0 && row[FWCI] != null ? Number(row[FWCI]) : null;
                  const oaStat  = OA   >= 0 ? (row[OA]    || '') : '';
                  const abstr   = ABS  >= 0 ? (row[ABS]   || '') : '';
                  const insts   = parseArr(INST >= 0 ? row[INST] : null).slice(0, 3);
                  const source  = SOURCE >= 0 ? (row[SOURCE] || '') : '';
                  const fuelArr = parseArr(matchedJson);
                  const impArr  = parseArr(MIMP >= 0 ? row[MIMP] : null);
                  const doiUrl  = doi ? `https://doi.org/${doi.replace(/^https?:\/\/(dx\.)?doi\.org\//, '')}` : null;
                  const fwciCls = !fwci ? 'bg-gray-100 text-gray-400' : fwci >= 3 ? 'bg-green-100 text-green-700 font-bold' : fwci >= 1 ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500';

                  return (
                    <div key={ri} onClick={() => handleRowClick(rowId)}
                      className="px-5 py-4 hover:bg-blue-50 cursor-pointer transition-colors group">
                      {/* Row 1: title */}
                      <div className="flex items-start gap-3">
                        <div className="flex-1 min-w-0">
                          <span className="text-sm font-semibold text-gray-900 group-hover:text-blue-700 transition-colors leading-snug">
                            {doiUrl ? (
                              <a href={doiUrl} target="_blank" rel="noopener"
                                onClick={e => e.stopPropagation()}
                                className="hover:underline decoration-blue-400">{title || rowId}</a>
                            ) : (title || rowId)}
                          </span>
                        </div>
                        {/* FWCI badge */}
                        {fwci != null && (
                          <span className={`flex-shrink-0 text-xs px-2 py-0.5 rounded-full ${fwciCls}`} title="Field-Weighted Citation Impact">
                            FWCI {fwci.toFixed(1)}
                          </span>
                        )}
                      </div>

                      {/* Row 2: meta chips */}
                      <div className="flex flex-wrap gap-1.5 mt-1.5 items-center">
                        {year && <span className="text-xs text-gray-500 font-medium">{year}</span>}
                        {oaStat && <span className={`text-xs px-1.5 py-0.5 rounded ${OA_CHIP[oaStat] || 'bg-gray-100 text-gray-500'}`}>{oaStat}</span>}
                        {source && <span className="text-xs text-gray-400 italic truncate max-w-[220px]">{source}</span>}
                        {rowSubgroups.map(sg => (
                          <span key={sg} className="text-xs px-2 py-0.5 bg-blue-100 text-blue-700 rounded-full">{sg}</span>
                        ))}
                        {fuelArr.length > 0 && (
                          <span className="text-xs text-orange-500">
                            {fuelArr.slice(0, 3).join(' · ')}{fuelArr.length > 3 ? ` +${fuelArr.length - 3}` : ''}
                          </span>
                        )}
                        {impArr.length > 0 && (
                          <span className="text-xs text-teal-500">
                            {impArr.slice(0, 2).join(' · ')}{impArr.length > 2 ? ` +${impArr.length - 2}` : ''}
                          </span>
                        )}
                      </div>

                      {/* Row 3: abstract snippet + institutions */}
                      {(abstr || insts.length > 0) && (
                        <div className="flex gap-4 mt-1.5">
                          {abstr && (
                            <p className="text-xs text-gray-400 leading-relaxed flex-1 min-w-0 line-clamp-2">
                              {abstr.slice(0, 220)}{abstr.length > 220 ? '…' : ''}
                            </p>
                          )}
                          {insts.length > 0 && (
                            <div className="flex-shrink-0 text-xs text-gray-400 max-w-[220px] text-right">
                              {insts.map((inst, i) => <div key={i} className="truncate">{inst}</div>)}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })()}
        </div>

        {totalPages > 0 && (
          <div className="flex justify-between items-center mt-3 text-sm text-gray-500">
            <span>Page {page} of {totalPages} ({(records?.total || 0).toLocaleString()} records)</span>
            <div className="flex gap-2">
              <button disabled={page <= 1} onClick={() => doPage(page - 1)}
                className="px-3 py-1 border rounded hover:bg-gray-100 disabled:opacity-40">← Prev</button>
              <button disabled={page >= totalPages} onClick={() => doPage(page + 1)}
                className="px-3 py-1 border rounded hover:bg-gray-100 disabled:opacity-40">Next →</button>
            </div>
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h4 className="font-semibold mb-3">SQL Query</h4>
        <textarea value={sqlText} onChange={e => setSqlText(e.target.value)} rows={3}
          placeholder="SELECT * FROM filtered_records LIMIT 10"
          className="w-full border rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-400 mb-2" />
        <div className="flex gap-3">
          <button onClick={runSql}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 transition">Run Query</button>
          {sqlResult?.rows && (
            <button onClick={exportCsv}
              className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-sm hover:bg-gray-50 transition">Export CSV</button>
          )}
        </div>
        {sqlResult && (
          <div className="mt-4 overflow-x-auto">
            {sqlResult.error ? (
              <p className="text-red-600 text-sm">{sqlResult.error}</p>
            ) : !sqlResult.rows?.length ? (
              <p className="text-gray-400 text-sm">No results</p>
            ) : (
              <>
                <p className="text-xs text-gray-500 mb-1">{sqlResult.rows.length} row(s){sqlResult.truncated ? ' (truncated)' : ''}</p>
                <div className="rounded border border-gray-200">
                  <table className="text-xs">
                    <thead className="bg-gray-50 border-b">
                      <tr>{sqlResult.columns.map(c => <th key={c} className="px-2 py-1 text-left font-medium">{c}</th>)}</tr>
                    </thead>
                    <tbody>
                      {sqlResult.rows.map((r, i) => (
                        <tr key={i} className="border-b border-gray-100">
                          {r.map((v, j) => <td key={j} className="px-2 py-1 max-w-xs truncate">{String(v ?? '')}</td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {selectedRecord && <RecordModal record={selectedRecord} fuelSubgroups={fuelSubgroups} onClose={() => setSelectedRecord(null)} />}
    </div>
  );
}

// ── Stage: Report ─────────────────────────────────────────────────────────────

function ReportStage({ pipeline, isLLM }) {
  const [activeJobId, setActiveJobId] = useState(null);
  const [hasReport, setHasReport] = useState(false);
  const [topN, setTopN] = useState(20);
  const [tab, setTab] = useState('configure'); // 'configure' | 'preview'
  const [fullscreen, setFullscreen] = useState(false);
  const reportKey = isLLM ? 'llm_report_path' : 'report_path';

  const config = pipeline.config || {};
  const groups = useMemo(() => {
    const migrated = _migrateGroups(config);
    return migrated.map(g => ({
      name: g.name,
      terms: (g.subgroups || []).flatMap(sg => sg.terms || []),
    }));
  }, [pipeline.id]);

  const [selected, setSelected] = useState(() => groups.map(g => new Set(g.terms)));

  const toggleTerm = (gi, term) => setSelected(prev =>
    prev.map((s, i) => {
      if (i !== gi) return s;
      const ns = new Set(s);
      ns.has(term) ? ns.delete(term) : ns.add(term);
      return ns;
    })
  );
  const selectAll = gi => setSelected(prev => prev.map((s, i) => i === gi ? new Set(groups[i].terms) : s));
  const selectNone = gi => setSelected(prev => prev.map((s, i) => i === gi ? new Set() : s));

  const { lines, status: jobStatus } = useJobStream(activeJobId, () => {
    setHasReport(true);
    setTab('preview');
  });
  const reportUrl = `/api/report?pipeline_id=${pipeline.id}${isLLM ? '&llm=1' : ''}`;

  useEffect(() => {
    if (pipeline[reportKey]) { setHasReport(true); }
  }, [pipeline, reportKey]);

  const genReport = async () => {
    const args = {
      '--db': pipeline.output_db_path,
      '--output': isLLM ? pipeline.llm_report_path : pipeline.report_path,
      '--state': pipeline.state_file_path,
      '--top-n': String(topN),
    };
    if (groups[0]?.terms.length > 0 && selected[0]?.size < groups[0].terms.length && selected[0]?.size > 0)
      args['--include-group1'] = [...selected[0]].join(',');
    if (groups[1]?.terms.length > 0 && selected[1]?.size < groups[1].terms.length && selected[1]?.size > 0)
      args['--include-group2'] = [...selected[1]].join(',');
    const res = await apiFetch('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ script: 'generate_report', pipeline_id: pipeline.id, args }),
    });
    const job = await res.json();
    if (!res.ok) { alert(job.error); return; }
    setActiveJobId(job.id);
  };

  const hasTerms = groups.some(g => g.terms.length > 0);

  // Fullscreen overlay
  if (fullscreen) {
    return (
      <div className="fixed inset-0 z-50 bg-white flex flex-col">
        <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 bg-gray-50">
          <h4 className="font-semibold text-sm">{isLLM ? 'Final Report' : 'Report'} — {pipeline.name}</h4>
          <div className="flex items-center gap-2">
            <a href={reportUrl} target="_blank" className="text-xs text-blue-600 hover:underline px-2 py-1 border border-blue-200 rounded">
              Open in new tab ↗
            </a>
            <button onClick={() => setFullscreen(false)}
              className="text-xs text-gray-600 hover:text-gray-900 px-2 py-1 border border-gray-200 rounded hover:bg-gray-100 transition">
              ✕ Exit fullscreen
            </button>
          </div>
        </div>
        <iframe src={reportUrl} className="flex-1 w-full border-0" />
      </div>
    );
  }

  const TABS = [
    { id: 'configure', label: 'Configure' },
    { id: 'preview',   label: hasReport ? 'Preview ✓' : 'Preview' },
  ];

  return (
    <div className="space-y-4">
      {/* Tab bar */}
      <div className="flex border-b border-gray-200">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`px-5 py-2.5 text-sm font-medium transition border-b-2 -mb-px ${
              tab === t.id
                ? 'border-emerald-500 text-emerald-700'
                : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {t.label}
          </button>
        ))}
        {hasReport && (
          <div className="ml-auto flex items-center pr-1">
            <a href={reportUrl} target="_blank"
              className="text-xs text-blue-600 hover:underline px-3 py-1">
              Open in new tab ↗
            </a>
          </div>
        )}
      </div>

      {/* Configure tab */}
      {tab === 'configure' && (
        <div className="space-y-4">
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h3 className="font-semibold text-lg mb-1">
              Stage {isLLM ? 7 : 4}: {isLLM ? 'Final Report' : 'Report Generation'}
            </h3>
            <p className="text-sm text-gray-500 mb-5">
              {isLLM
                ? 'Generate the final enriched report with LLM classification results.'
                : 'Generate an HTML report with charts and statistics.'}
            </p>

            {/* Top N slider */}
            <div className="mb-5">
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs font-medium text-gray-600">Top N terms per chart</label>
                <span className="text-sm font-semibold text-emerald-700 w-8 text-right">{topN}</span>
              </div>
              <input type="range" min="5" max="50" step="5" value={topN}
                onChange={e => setTopN(Number(e.target.value))}
                className="w-full accent-emerald-600" />
              <div className="flex justify-between text-xs text-gray-400 mt-0.5">
                <span>5</span><span>50</span>
              </div>
            </div>

            {/* Term filter */}
            {hasTerms && (
              <details className="mb-5 border border-gray-100 rounded-lg">
                <summary className="px-4 py-2.5 text-sm font-medium text-gray-700 cursor-pointer hover:bg-gray-50 select-none flex items-center justify-between">
                  <span>Term filter</span>
                  <span className="text-xs text-gray-400 font-normal">
                    {groups.map((g, i) => `${selected[i]?.size ?? g.terms.length}/${g.terms.length} ${g.name}`).join(' · ')}
                  </span>
                </summary>
                <div className="p-4 grid grid-cols-1 gap-4 border-t border-gray-100">
                  {groups.map((group, gi) => group.terms.length === 0 ? null : (
                    <div key={gi}>
                      <div className="flex items-center gap-3 mb-2">
                        <span className="text-xs font-semibold text-gray-700">{group.name}</span>
                        <button onClick={() => selectAll(gi)} className="text-xs text-blue-500 hover:underline">All</button>
                        <button onClick={() => selectNone(gi)} className="text-xs text-gray-400 hover:underline">None</button>
                        <span className="text-xs text-gray-400 ml-auto">{selected[gi]?.size ?? group.terms.length} selected</span>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {group.terms.map(term => {
                          const on = selected[gi]?.has(term);
                          return (
                            <button key={term} onClick={() => toggleTerm(gi, term)}
                              className={`text-xs px-2 py-0.5 rounded-full border transition ${on
                                ? 'bg-emerald-50 border-emerald-300 text-emerald-800'
                                : 'bg-gray-50 border-gray-200 text-gray-400 line-through'}`}>
                              {term}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              </details>
            )}

            {pipeline.is_owner && (
              <button onClick={genReport}
                className="px-5 py-2 bg-emerald-600 text-white rounded-lg font-semibold hover:bg-emerald-700 transition flex items-center gap-2">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/>
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
                </svg>
                Generate {isLLM ? 'Final Report' : 'Report'}
              </button>
            )}
          </div>

          {/* Job terminal */}
          {activeJobId && (
            <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
              <div className="bg-gray-800 px-4 py-2 flex justify-between items-center">
                <span className="text-gray-300 text-sm font-mono">job {activeJobId}</span>
                <span className={`text-xs px-2 py-0.5 rounded ${
                  jobStatus === 'done'  ? 'bg-green-700 text-green-100' :
                  jobStatus === 'error' ? 'bg-red-700 text-red-100' :
                  'bg-gray-700 text-gray-300'}`}>
                  {jobStatus || 'running'}
                </span>
              </div>
              <Terminal lines={lines} className="h-40" />
            </div>
          )}
        </div>
      )}

      {/* Preview tab */}
      {tab === 'preview' && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          {hasReport ? (
            <>
              <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-100 bg-gray-50">
                <span className="text-sm font-medium text-gray-700">Report Preview</span>
                <button onClick={() => setFullscreen(true)}
                  className="text-xs text-gray-500 hover:text-gray-800 px-2 py-1 border border-gray-200 rounded hover:bg-white transition flex items-center gap-1">
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"
                      d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"/>
                  </svg>
                  Fullscreen
                </button>
              </div>
              <iframe src={reportUrl} className="w-full border-0"
                style={{ height: 'calc(100vh - 280px)', minHeight: '500px' }} />
            </>
          ) : (
            <div className="flex flex-col items-center justify-center py-16 text-gray-400">
              <svg className="w-12 h-12 mb-3 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5"
                  d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
              </svg>
              <p className="text-sm">No report generated yet.</p>
              <button onClick={() => setTab('configure')}
                className="mt-3 text-xs text-emerald-600 hover:underline">
                Go to Configure →
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Stage: LLM ────────────────────────────────────────────────────────────────

function LLMStage({ pipeline, pipelineStatus }) {
  const [activeJobId, setActiveJobId] = useState(null);
  const [limit, setLimit] = useState('');
  const [pollInterval, setPollInterval] = useState('60');
  const [launchError, setLaunchError] = useState('');
  const [liveBatchState, setLiveBatchState] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const { lines, status: jobStatus } = useJobStream(activeJobId);

  const isJobRunning = !!(activeJobId && (jobStatus === null || jobStatus === 'running'));

  // On mount, auto-connect to the most recent llm_classify job
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
    const subArgs = {};
    if (limit && ['prepare', 'run'].includes(phase)) subArgs['--limit'] = limit;
    if (['poll', 'run'].includes(phase)) subArgs['--interval'] = pollInterval;
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

            {/* Options */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Limit records (blank = all)</label>
                <input type="number" placeholder="e.g. 100" min="1" value={limit}
                  onChange={e => setLimit(e.target.value)}
                  className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-purple-400" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Poll interval (sec)</label>
                <input type="number" value={pollInterval} min="10"
                  onChange={e => setPollInterval(e.target.value)}
                  className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-purple-400" />
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

// ── Pipeline View ─────────────────────────────────────────────────────────────

function PipelineView({ initialPipeline, onBack, onNavigateTo }) {
  const [pipeline, setPipeline] = useState(initialPipeline);
  const [pipelineStatus, setPipelineStatus] = useState(null);
  const [jobRunning, setJobRunning] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/pipelines/${pipeline.id}/status`);
      setPipelineStatus(await res.json());
    } catch {}
  }, [pipeline.id]);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);
  useInterval(fetchStatus, jobRunning ? 3000 : 10000);

  const handleUpdated = (updated, action) => {
    if (!updated) { onBack(); return; }
    if (action === 'fork') { onNavigateTo(updated); return; }
    setPipeline(updated);
    fetchStatus();
  };

  const stage = pipeline.current_stage;
  const ALL_STAGES = ['keyword_selection','openalex_search','results_review','llm_results_review',
    'report_generation','llm_report_generation','llm_classification'];

  return (
    <div>
      <div className="bg-white border-b border-gray-100 px-6 py-2">
        <div className="max-w-5xl mx-auto">
          <button onClick={onBack}
            className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800 transition py-1">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7"/>
            </svg>
            Back
          </button>
        </div>
      </div>
      <PipelineHeader pipeline={pipeline} onUpdated={handleUpdated} />
      <div className="max-w-5xl mx-auto px-6 py-6">
        {stage === 'keyword_selection' && (
          <KeywordStage pipeline={pipeline} onUpdate={p => setPipeline(p)} />
        )}
        {stage === 'openalex_search' && (
          <SearchStage pipeline={pipeline} pipelineStatus={pipelineStatus}
            onJobStart={() => setJobRunning(true)}
            onJobDone={() => { setJobRunning(false); fetchStatus(); }} />
        )}
        {(stage === 'results_review' || stage === 'llm_results_review') && (
          <ReviewStage pipeline={pipeline} isLLM={stage === 'llm_results_review'} />
        )}
        {(stage === 'report_generation' || stage === 'llm_report_generation') && (
          <ReportStage pipeline={pipeline} isLLM={stage === 'llm_report_generation'} />
        )}
        {stage === 'llm_classification' && (
          <LLMStage pipeline={pipeline} pipelineStatus={pipelineStatus} />
        )}
        {!ALL_STAGES.includes(stage) && (
          <div className="text-gray-500">Unknown stage: {stage}</div>
        )}
      </div>
    </div>
  );
}

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

// ── App ───────────────────────────────────────────────────────────────────────

function App() {
  const [view, setView] = useState('loading');
  const [me, setMe] = useState(null);
  const [activePipeline, setActivePipeline] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [pipelines, setPipelines] = useState([]);

  const runningCount = jobs.filter(j => j.status === 'running').length;

  const refreshGlobal = useCallback(async () => {
    try {
      const [jobsRes, pipRes] = await Promise.all([
        apiFetch('/api/jobs'),
        apiFetch('/api/pipelines'),
      ]);
      setJobs(await jobsRes.json());
      setPipelines(await pipRes.json());
    } catch {}
  }, []);

  useEffect(() => {
    _onUnauth = () => { setMe(null); setView('login'); };
    fetch('/api/auth/me')
      .then(r => r.ok ? r.json() : null)
      .then(u => {
        if (u) { setMe(u); setView('home'); refreshGlobal(); }
        else   { setView('login'); }
      })
      .catch(() => setView('login'));
  }, []);

  // Refresh badge periodically when not on jobs page
  useInterval(refreshGlobal, me && view !== 'jobs' ? 10000 : null);

  const handleLogin = d => {
    setMe({ username: d.username });
    setView('home');
    refreshGlobal();
  };

  const handleLogout = async () => {
    await apiFetch('/api/auth/logout', { method: 'POST' });
    setMe(null); setView('login');
  };

  if (view === 'loading') {
    return <div className="flex items-center justify-center h-screen text-gray-400">Loading...</div>;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <LoginOverlay visible={!me} onLogin={handleLogin} />
      {me && (
        <>
          <NavBar me={me} view={view} runningCount={runningCount}
            onShowHome={() => { setView('home'); refreshGlobal(); }}
            onShowJobs={() => { setView('jobs'); refreshGlobal(); }}
            onLogout={handleLogout}
          />
          {view === 'home' && (
            <HomeView
              onOpenPipeline={p => { setActivePipeline(p); setView('pipeline'); }}
              runningJobs={jobs}
            />
          )}
          {view === 'pipeline' && activePipeline && (
            <PipelineView
              key={activePipeline.id}
              initialPipeline={activePipeline}
              onBack={() => { setView('home'); refreshGlobal(); }}
              onNavigateTo={p => setActivePipeline(p)}
            />
          )}
          {view === 'jobs' && (
            <JobsView jobs={jobs} pipelines={pipelines} onRefresh={refreshGlobal} />
          )}
        </>
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('app-root')).render(React.createElement(App));
