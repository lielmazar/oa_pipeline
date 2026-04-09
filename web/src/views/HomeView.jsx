import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';
import { useInterval } from '../hooks/useInterval';

const STAGE_LABELS = {
  keyword_selection:  'Keywords',
  openalex_search:    'Filter Run',
  analysis:           'Analysis',
  llm_classification: 'LLM',
  llm_analysis:       'LLM Analysis',
  qa_irr:             'IRR',
  summary:            'Summary',
};

const STAGE_COLORS = {
  keyword_selection:  'bg-gray-100 text-gray-600',
  openalex_search:    'bg-blue-100 text-blue-700',
  analysis:           'bg-green-100 text-green-700',
  llm_classification: 'bg-purple-100 text-purple-700',
  llm_analysis:       'bg-violet-100 text-violet-700',
  qa_irr:             'bg-emerald-100 text-emerald-800',
  summary:            'bg-amber-100 text-amber-700',
};

export default function HomeView({ onOpenPipeline }) {
  const [pipelines, setPipelines] = useState([]);
  const [stats, setStats]         = useState({});   // id → {record_count, llm_processed_count}
  const [loading, setLoading]     = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createDesc, setCreateDesc] = useState('');
  const [creating, setCreating]   = useState(false);
  const [error, setError]         = useState('');
  const [deleteId, setDeleteId]   = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch('/api/pipeline');
      if (res.ok) setPipelines(await res.json());
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Lazy-load stats for each pipeline after list loads
  useEffect(() => {
    pipelines.forEach(p => {
      if (stats[p.id] !== undefined) return;
      apiFetch(`/api/pipeline/${p.id}/stats`)
        .then(r => r.ok ? r.json() : null)
        .then(s => s && setStats(prev => ({ ...prev, [p.id]: s })))
        .catch(() => {});
    });
  }, [pipelines]); // eslint-disable-line react-hooks/exhaustive-deps

  useInterval(load, 60000); // refresh list every minute

  const submit = async e => {
    e.preventDefault();
    if (!createName.trim()) return;
    setCreating(true); setError('');
    try {
      const res = await apiFetch('/api/pipeline', {
        method: 'POST',
        body: JSON.stringify({ name: createName.trim(), description: createDesc }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'Failed to create pipeline'); return; }
      setShowCreate(false); setCreateName(''); setCreateDesc('');
      onOpenPipeline(data);
    } finally { setCreating(false); }
  };

  const confirmDelete = async () => {
    if (!deleteId) return;
    await apiFetch(`/api/pipeline/${deleteId}`, { method: 'DELETE' });
    setDeleteId(null);
    load();
  };

  return (
    <div className="max-w-4xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-gray-900">Pipelines</h1>
        <button onClick={() => { setShowCreate(true); setError(''); }}
          className="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-blue-700 transition font-medium">
          + New Pipeline
        </button>
      </div>

      {loading ? (
        <p className="text-gray-400 text-sm text-center py-20">Loading…</p>
      ) : pipelines.length === 0 ? (
        <div className="text-center py-24 text-gray-400">
          <div className="text-5xl mb-4">🔬</div>
          <p className="text-sm">No pipelines yet. Create your first one to get started.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {pipelines.map(p => {
            const s = stats[p.id];
            return (
              <div key={p.id}
                className="bg-white border border-gray-200 rounded-xl p-4 flex items-center gap-4
                           hover:border-blue-300 hover:shadow-sm transition cursor-pointer group"
                onClick={() => onOpenPipeline(p)}>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-xs text-gray-500 border border-gray-200 px-1.5 rounded">#{p.id}</span>
                    <span className="font-medium text-gray-900 truncate">{p.name}</span>
                    {p.forked_from_id && (
                      <span className="text-xs text-gray-400 border border-gray-200 px-1.5 rounded">forked</span>
                    )}
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      STAGE_COLORS[p.current_stage] || 'bg-gray-100 text-gray-600'
                    }`}>
                      {STAGE_LABELS[p.current_stage] || p.current_stage}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-gray-400">
                    {p.description && <span className="truncate">{p.description}</span>}
                    {s?.record_count != null && (
                      <span>{s.record_count.toLocaleString()} records
                        {s.llm_processed_count > 0 && ` · ${s.llm_processed_count.toLocaleString()} LLM`}
                      </span>
                    )}
                  </div>
                </div>
                <div className="text-right shrink-0 text-xs text-gray-400 space-y-0.5">
                  <div>{p.owner_username || 'you'}</div>
                  <div>{p.updated_at?.slice(0, 10)}</div>
                </div>
                <button
                  onClick={e => { e.stopPropagation(); setDeleteId(p.id); }}
                  className="text-gray-300 hover:text-red-500 text-xl leading-none transition opacity-0 group-hover:opacity-100 ml-1"
                  title="Delete pipeline">
                  ×
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <form onSubmit={submit}
            className="bg-white rounded-xl shadow-2xl w-full max-w-sm p-6 space-y-4 mx-4">
            <h2 className="font-bold text-gray-900">New Pipeline</h2>
            {error && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>}
            <label className="block">
              <span className="text-xs text-gray-500 uppercase tracking-wide font-medium">Name *</span>
              <input autoFocus value={createName} onChange={e => setCreateName(e.target.value)}
                placeholder="e.g. Sustainable Fuels 2025"
                className="mt-1 w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
            </label>
            <label className="block">
              <span className="text-xs text-gray-500 uppercase tracking-wide font-medium">Description</span>
              <input value={createDesc} onChange={e => setCreateDesc(e.target.value)}
                className="mt-1 w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
            </label>
            <p className="text-xs text-gray-400">
              Keywords will be pre-filled from the system default. You can edit them in the next step.
            </p>
            <div className="flex gap-2 pt-1">
              <button type="button" onClick={() => setShowCreate(false)}
                className="flex-1 border text-sm py-2 rounded-lg hover:bg-gray-50 transition">Cancel</button>
              <button type="submit" disabled={creating || !createName.trim()}
                className="flex-1 bg-blue-600 text-white text-sm py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50 transition font-medium">
                {creating ? 'Creating…' : 'Create'}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Delete confirmation */}
      {deleteId && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-xs p-6 mx-4 space-y-4">
            <h2 className="font-bold text-gray-900">Delete Pipeline?</h2>
            <p className="text-sm text-gray-600">
              This will permanently delete the pipeline and all its data.
            </p>
            <div className="flex gap-2">
              <button onClick={() => setDeleteId(null)}
                className="flex-1 border text-sm py-2 rounded-lg hover:bg-gray-50 transition">Cancel</button>
              <button onClick={confirmDelete}
                className="flex-1 bg-red-600 text-white text-sm py-2 rounded-lg hover:bg-red-700 transition font-medium">
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
