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

