/**
 * OpenAlex Pipeline Dashboard — React SPA
 */

const { useState, useEffect, useRef, useCallback } = React;

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
    setStatus('running');
    let lastSeq = -1;

    function connect() {
      const es = new EventSource(`/api/jobs/${jobId}/stream?after=${lastSeq}`);
      esRef.current = es;

      es.addEventListener('line', e => {
        try {
          const d = JSON.parse(e.data.replace(/'/g, '"'));
          if (d.seq > lastSeq) {
            setLines(prev => [...prev, stripAnsi(d.text)]);
            lastSeq = d.seq;
          }
        } catch {
          setLines(prev => [...prev, e.data]);
        }
      });

      es.addEventListener('done', e => {
        es.close(); esRef.current = null;
        try {
          const d = JSON.parse(e.data.replace(/'/g, '"'));
          setStatus(d.status);
          if (onDoneRef.current) onDoneRef.current(d);
        } catch { setStatus('done'); }
      });

      es.onerror = () => {
        es.close(); esRef.current = null;
        setTimeout(async () => {
          try {
            const r = await fetch(`/api/jobs/${jobId}`);
            if (r.ok) {
              const j = await r.json();
              if (j.status === 'running') connect();
            }
          } catch {}
        }, 3000);
      };
    }

    connect();
    return () => { if (esRef.current) { esRef.current.close(); esRef.current = null; } };
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
      className={`terminal bg-gray-900 text-green-400 p-4 h-80 overflow-y-auto whitespace-pre-wrap ${className}`}>
      {lines.join('\n')}
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

function StageStepper({ currentStage }) {
  const currentIdx = STAGE_IDS.indexOf(currentStage);
  return (
    <div className="flex items-center w-full overflow-x-auto pb-1">
      {STAGE_IDS.map((sid, i) => {
        let dotCls = 'w-7 h-7 rounded-full border-2 flex items-center justify-center text-xs font-bold flex-shrink-0 ';
        if (i < currentIdx)        dotCls += 'bg-green-500 border-green-500 text-white';
        else if (i === currentIdx) dotCls += 'bg-blue-600 border-blue-600 text-white';
        else                       dotCls += 'bg-white border-gray-300 text-gray-400';
        return (
          <div key={sid} className={`flex items-center ${i < STAGE_IDS.length - 1 ? 'flex-1' : ''}`}>
            <div className="flex flex-col items-center">
              <div className={dotCls}>{i + 1}</div>
              <span className={`mt-1 whitespace-nowrap ${i === currentIdx ? 'text-blue-700 font-semibold' : 'text-gray-400'}`}
                style={{ fontSize: '0.65rem' }}>
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

  const handleAdvance = async () => {
    const label = pipeline.next_stage_label || pipeline.next_stage;
    if (!confirm(`Advance to "${label}"?`)) return;
    const res = await apiFetch(`/api/pipelines/${pipeline.id}/advance`, { method: 'POST' });
    const updated = await res.json();
    if (!res.ok) { alert(updated.error); return; }
    onUpdated(updated);
  };

  const handleGoBack = async () => {
    const label = pipeline.prev_stage_label || pipeline.prev_stage;
    if (!confirm(`Go back to "${label}"?`)) return;
    const res = await apiFetch(`/api/pipelines/${pipeline.id}/go-back`, { method: 'POST' });
    const updated = await res.json();
    if (!res.ok) { alert(updated.error); return; }
    onUpdated(updated);
  };

  const handleDelete = async () => {
    if (!confirm(`Delete pipeline "${pipeline.name}"?`)) return;
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
          <div className="ml-auto flex gap-2 flex-wrap">
            {pipeline.is_owner && pipeline.prev_stage && (
              <button onClick={handleGoBack}
                className="text-sm text-gray-600 border border-gray-200 px-3 py-1 rounded-lg hover:bg-gray-50 transition">
                ← {pipeline.prev_stage_label}
              </button>
            )}
            <button onClick={() => setShowFork(true)}
              className="text-sm text-purple-700 border border-purple-300 px-3 py-1 rounded-lg hover:bg-purple-50 transition">
              Fork
            </button>
            {pipeline.is_owner && (
              <button onClick={handleDelete}
                className="text-sm text-red-600 border border-red-300 px-3 py-1 rounded-lg hover:bg-red-50 transition">
                Delete
              </button>
            )}
            {pipeline.is_owner && pipeline.next_stage && (
              <button onClick={handleAdvance}
                className="text-sm bg-green-600 text-white px-4 py-1 rounded-lg hover:bg-green-700 transition font-medium">
                Advance to {pipeline.next_stage_label} →
              </button>
            )}
          </div>
        </div>
        <StageStepper currentStage={pipeline.current_stage} />
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

function KeywordStage({ pipeline, onUpdate }) {
  const config = pipeline.config || {};
  const [fuel, setFuel] = useState([...(config.fuel_terms || [])]);
  const [impact, setImpact] = useState([...(config.impact_terms || [])]);
  const [fuelInput, setFuelInput] = useState('');
  const [impactInput, setImpactInput] = useState('');
  const [saved, setSaved] = useState(false);

  const addFuel = () => {
    const t = fuelInput.trim();
    if (!t || fuel.includes(t)) { setFuelInput(''); return; }
    setFuel(p => [...p, t]); setFuelInput('');
  };
  const addImpact = () => {
    const t = impactInput.trim();
    if (!t || impact.includes(t)) { setImpactInput(''); return; }
    setImpact(p => [...p, t]); setImpactInput('');
  };

  const save = async () => {
    const res = await apiFetch(`/api/pipelines/${pipeline.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ config: { ...config, fuel_terms: fuel, impact_terms: impact } }),
    });
    const updated = await res.json();
    if (!res.ok) { alert(updated.error); return; }
    setSaved(true); setTimeout(() => setSaved(false), 3000);
    if (onUpdate) onUpdate(updated);
  };

  const TagList = ({ terms, onRemove }) => (
    <div className="flex flex-wrap gap-1.5 p-2 border rounded-lg min-h-[2.5rem] bg-gray-50">
      {terms.length === 0
        ? <span className="text-gray-400 text-xs">No terms yet — add below</span>
        : terms.map(t => (
          <span key={t} className="tag-pill">
            {t}
            {onRemove && <button onClick={() => onRemove(t)}>✕</button>}
          </span>
        ))}
    </div>
  );

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h3 className="font-semibold text-lg mb-1">Stage 1: Keyword Selection</h3>
        <p className="text-sm text-gray-500 mb-4">Configure fuel terms and impact terms for filtering OpenAlex records.</p>
        <div className="space-y-5">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Fuel Terms ({fuel.length})</label>
            <TagList terms={fuel} onRemove={pipeline.is_owner ? t => setFuel(f => f.filter(x => x !== t)) : null} />
            {pipeline.is_owner && (
              <div className="flex gap-2 mt-2">
                <input type="text" placeholder="Add fuel term..." value={fuelInput}
                  onChange={e => setFuelInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addFuel())}
                  className="flex-1 border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
                <button onClick={addFuel}
                  className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 transition">Add</button>
              </div>
            )}
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Impact Terms ({impact.length})</label>
            <TagList terms={impact} onRemove={pipeline.is_owner ? t => setImpact(i => i.filter(x => x !== t)) : null} />
            {pipeline.is_owner && (
              <div className="flex gap-2 mt-2">
                <input type="text" placeholder="Add impact term..." value={impactInput}
                  onChange={e => setImpactInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addImpact())}
                  className="flex-1 border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
                <button onClick={addImpact}
                  className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 transition">Add</button>
              </div>
            )}
          </div>
          {saved && <p className="text-sm text-green-600">Keywords saved.</p>}
          {pipeline.is_owner && (
            <button onClick={save}
              className="w-full py-2 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700 transition">
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

  const { lines, status: jobStatus } = useJobStream(activeJobId, onJobDone);
  const isRunning = activeJobId && (!jobStatus || jobStatus === 'running');

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
          <div className="flex justify-between text-sm text-gray-600 mb-2">
            <span>{(prog.processed || 0).toLocaleString()} / {(prog.total || 0).toLocaleString()} files processed</span>
            <span>{prog.percent}%</span>
          </div>
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div className="bg-blue-500 h-2 rounded-full transition-all" style={{ width: `${prog.percent}%` }} />
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

function RecordModal({ record, onClose }) {
  if (!record) return null;
  const oaId = record.openalex_id || '';
  return (
    <div className="fixed inset-0 bg-gray-900/60 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col">
        <div className="flex justify-between items-start p-6 border-b flex-shrink-0">
          <h3 className="text-base font-bold pr-4 flex-1">{record.title || oaId || 'Record'}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 flex-shrink-0">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div className="overflow-y-auto p-6 flex-1 text-sm">
          <div className="flex items-center gap-3 mb-3 pb-2 border-b">
            <span className="text-xs font-mono text-gray-400">{oaId}</span>
            {oaId && <a href={`https://openalex.org/${oaId}`} target="_blank" className="text-blue-600 hover:underline text-xs">OpenAlex ↗</a>}
            {record.doi && <a href={`https://doi.org/${record.doi}`} target="_blank" className="text-blue-600 hover:underline text-xs">DOI ↗</a>}
          </div>
          {Object.entries(record).filter(([k]) => !k.startsWith('_')).map(([k, v]) => {
            let display;
            if (v === null || v === undefined || v === '') {
              display = <span className="text-gray-300 italic">—</span>;
            } else if (JSON_DISPLAY_COLS.has(k) && typeof v === 'string' && (v.startsWith('[') || v.startsWith('{'))) {
              try {
                const parsed = JSON.parse(v);
                if (Array.isArray(parsed)) {
                  display = <span>{parsed.map((x, i) => (
                    <span key={i} className="inline-block bg-blue-50 text-blue-800 text-xs px-1.5 py-0.5 rounded mr-1 mb-1">{String(x)}</span>
                  ))}</span>;
                } else {
                  display = <pre className="text-xs bg-gray-50 p-2 rounded overflow-x-auto">{JSON.stringify(parsed, null, 2)}</pre>;
                }
              } catch { display = <span className="break-all">{String(v)}</span>; }
            } else if (typeof v === 'string' && v.length > 300) {
              display = <span className="break-all leading-relaxed">{v}</span>;
            } else {
              display = <span className="break-all">{String(v)}</span>;
            }
            return (
              <div key={k} className="flex gap-2 py-1.5 border-b border-gray-50">
                <span className="text-gray-500 font-medium w-44 flex-shrink-0 text-xs pt-0.5">{k}</span>
                <div className="flex-1 min-w-0">{display}</div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function ReviewStage({ pipeline, isLLM }) {
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState('publication_year');
  const [search, setSearch] = useState('');
  const [records, setRecords] = useState(null);
  const [sqlText, setSqlText] = useState('');
  const [sqlResult, setSqlResult] = useState(null);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const searchInputRef = useRef(null);

  const loadRecords = useCallback(async (p, s, q) => {
    const params = new URLSearchParams({ pipeline_id: pipeline.id, page: p, sort: s, dir: 'desc', q: q || '' });
    try {
      const res = await apiFetch(`/api/db/records?${params}`);
      const data = await res.json();
      if (!data.error) setRecords(data);
    } catch {}
  }, [pipeline.id]);

  useEffect(() => { loadRecords(1, 'publication_year', ''); }, [loadRecords]);

  const doSearch = () => {
    const q = searchInputRef.current?.value || '';
    setSearch(q); setPage(1);
    loadRecords(1, sort, q);
  };
  const doSort = s => { setSort(s); setPage(1); loadRecords(1, s, search); };
  const doPage = p => { setPage(p); loadRecords(p, sort, search); };

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
        <div className="flex gap-3 mb-4">
          <input ref={searchInputRef} type="text" placeholder="Search title, abstract, keywords..."
            defaultValue={search}
            onKeyDown={e => e.key === 'Enter' && doSearch()}
            className="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
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

        <div className="min-h-[12rem] overflow-x-auto rounded-lg border border-gray-200">
          {!records ? (
            <div className="text-gray-400 text-center py-8">Loading records...</div>
          ) : !records.rows?.length ? (
            <div className="text-gray-400 text-center py-8">No records found</div>
          ) : (
            <table className="w-full text-xs text-left">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>{records.columns.map(c => (
                  <th key={c} className="px-3 py-2 font-medium text-gray-600 whitespace-nowrap">{c}</th>
                ))}</tr>
              </thead>
              <tbody>
                {records.rows.map((row, ri) => {
                  const rowId = row[records.columns.indexOf('openalex_id')] || ri;
                  return (
                    <tr key={ri} onClick={() => handleRowClick(rowId)}
                      className="border-b border-gray-100 hover:bg-blue-50 cursor-pointer">
                      {row.map((cell, ci) => (
                        <td key={ci} className="px-3 py-2 max-w-[200px] truncate"
                          title={String(cell ?? '')}>
                          {String(cell ?? '')}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
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
                <p className="text-xs text-gray-500 mb-1">{sqlResult.rows.length} row(s)</p>
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

      {selectedRecord && <RecordModal record={selectedRecord} onClose={() => setSelectedRecord(null)} />}
    </div>
  );
}

// ── Stage: Report ─────────────────────────────────────────────────────────────

function ReportStage({ pipeline, isLLM }) {
  const [activeJobId, setActiveJobId] = useState(null);
  const [hasReport, setHasReport] = useState(false);
  const reportKey = isLLM ? 'llm_report_path' : 'report_path';

  const { lines, status: jobStatus } = useJobStream(activeJobId, () => setHasReport(true));
  const reportUrl = `/api/report?pipeline_id=${pipeline.id}${isLLM ? '&llm=1' : ''}`;

  useEffect(() => {
    if (pipeline[reportKey]) setHasReport(true);
  }, [pipeline, reportKey]);

  const genReport = async () => {
    const args = {
      '--db': pipeline.output_db_path,
      '--output': isLLM ? pipeline.llm_report_path : pipeline.report_path,
      '--state': pipeline.state_file_path,
    };
    const res = await apiFetch('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ script: 'generate_report', pipeline_id: pipeline.id, args }),
    });
    const job = await res.json();
    if (!res.ok) { alert(job.error); return; }
    setActiveJobId(job.id);
  };

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h3 className="font-semibold text-lg mb-1">Stage {isLLM ? 7 : 4}: {isLLM ? 'Final Report' : 'Report Generation'}</h3>
        <p className="text-sm text-gray-500 mb-4">
          {isLLM ? 'Generate the final enriched report with LLM classification results.' : 'Generate an HTML report with charts and statistics.'}
        </p>
        {pipeline.is_owner && (
          <button onClick={genReport}
            className="px-4 py-2 bg-emerald-600 text-white rounded-lg font-semibold hover:bg-emerald-700 transition">
            ▶ Generate {isLLM ? 'Final Report' : 'Report'}
          </button>
        )}
      </div>
      {activeJobId && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="bg-gray-800 px-4 py-2 flex justify-between items-center">
            <span className="text-gray-300 text-sm font-mono">job {activeJobId}</span>
            <span className={`text-xs px-2 py-0.5 rounded ${jobStatus === 'done' ? 'bg-green-700 text-green-100' : 'bg-gray-700 text-gray-300'}`}>
              {jobStatus || 'running'}
            </span>
          </div>
          <Terminal lines={lines} className="h-40" />
        </div>
      )}
      {hasReport && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <div className="flex justify-between items-center mb-3">
            <h4 className="font-semibold">Report Preview</h4>
            <a href={reportUrl} target="_blank" className="text-sm text-blue-600 hover:underline">Open in new tab ↗</a>
          </div>
          <iframe src={reportUrl} className="w-full h-96 rounded border border-gray-200" />
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
  const { lines, status: jobStatus } = useJobStream(activeJobId);

  const launchPhase = async phase => {
    const args = {
      '--db': pipeline.output_db_path,
      '--batch-dir': pipeline.batch_state_path
        ? pipeline.batch_state_path.replace('/state.json', '')
        : undefined,
    };
    if (limit) args['--limit'] = limit;
    if (['poll', 'run'].includes(phase)) args['--interval'] = pollInterval;
    const res = await apiFetch('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({
        script: 'llm_classify',
        pipeline_id: pipeline.id,
        args: { [phase]: true, ...args },
      }),
    });
    const job = await res.json();
    if (!res.ok) { alert(job.error); return; }
    setActiveJobId(job.id);
  };

  const PHASES = [
    { id: 'estimate', label: '💰 Estimate Cost' },
    { id: 'prepare',  label: '📄 Prepare' },
    { id: 'submit',   label: '🚀 Submit' },
    { id: 'poll',     label: '🔄 Poll' },
    { id: 'save',     label: '💾 Save Results' },
    { id: 'run',      label: '▶ Run All', primary: true },
  ];

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h3 className="font-semibold text-lg mb-1">Stage 5: LLM Classification</h3>
        <p className="text-sm text-gray-500 mb-4">Run LLM classification on filtered records via OpenAI Batch API.</p>
        {pipelineStatus && (
          <div className="text-sm text-gray-600 mb-4">
            <strong>{(pipelineStatus.record_count || 0).toLocaleString()}</strong> total records ·{' '}
            <strong>{(pipelineStatus.llm_done || 0).toLocaleString()}</strong> LLM classified
          </div>
        )}
        {pipeline.is_owner ? (
          <>
            <div className="flex flex-wrap gap-2 mb-4">
              {PHASES.map(({ id, label, primary }) => (
                <button key={id} onClick={() => launchPhase(id)}
                  className={`px-4 py-2 rounded-lg text-sm transition ${primary
                    ? 'bg-purple-600 text-white hover:bg-purple-700'
                    : 'border border-gray-300 hover:bg-gray-50'}`}>
                  {label}
                </button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Limit (blank = all)</label>
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
          </>
        ) : (
          <p className="text-gray-400 text-sm italic">View only — you are not the owner.</p>
        )}
      </div>
      {activeJobId && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="bg-gray-800 px-4 py-2 flex justify-between items-center">
            <span className="text-gray-300 text-sm font-mono">job {activeJobId}</span>
            <span className={`text-xs px-2 py-0.5 rounded ${jobStatus === 'done' ? 'bg-green-700 text-green-100' : 'bg-gray-700 text-gray-300'}`}>
              {jobStatus || 'running'}
            </span>
          </div>
          <Terminal lines={lines} />
        </div>
      )}
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
    apiFetch(`/api/jobs/${jobId}`).then(r => r.json()).then(setJobInfo).catch(() => {});
  }, [jobId]);

  const statusCls = status === 'done' ? 'bg-green-700 text-green-100'
    : status === 'error' ? 'bg-red-700 text-red-100'
    : 'bg-yellow-700 text-yellow-100';

  return (
    <div className="mt-4 bg-gray-900 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-gray-800">
        <span className="text-gray-300 text-sm font-mono">
          {jobInfo ? `${jobInfo.label || jobInfo.script} (${jobId})` : jobId}
        </span>
        <div className="flex items-center gap-3">
          <span className={`text-xs px-2 py-0.5 rounded ${statusCls}`}>{status || jobInfo?.status || '…'}</span>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-sm">✕</button>
        </div>
      </div>
      <Terminal lines={lines} className="h-96" />
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
                    </td>
                    <td className="px-4 py-3 text-sm text-center">
                      {job.workers > 1
                        ? <span className="text-xs font-medium text-blue-700">{job.workers} cores</span>
                        : <span className="text-xs text-gray-400">1</span>}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-500">{job.pid || '—'}</td>
                    <td className="px-4 py-3 text-sm text-gray-500">{started}</td>
                    <td className="px-4 py-3 text-sm text-gray-500">{elapsed}</td>
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
