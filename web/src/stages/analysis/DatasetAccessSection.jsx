import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../api/client';

export default function DatasetAccessSection({
  pipelineId,
  selectedSubsetId,
  onSelectSubset,
  onOpenViewer,
}) {
  const [summary, setSummary] = useState(null);

  const load = useCallback(() => {
    apiFetch(`/api/pipeline/${pipelineId}/subsets/summary`)
      .then(r => r.ok ? r.json() : null)
      .then(setSummary)
      .catch(() => setSummary(null));
  }, [pipelineId]);

  useEffect(() => { load(); }, [load]);

  const subsets = summary?.subsets ?? [];
  const total = summary?.dataset_total;

  const delSubset = async (e, id) => {
    e.stopPropagation();
    if (!window.confirm('Delete this subset?')) return;
    await apiFetch(`/api/pipeline/${pipelineId}/subsets/${id}`, { method: 'DELETE' });
    if (selectedSubsetId === id) onSelectSubset(null);
    load();
  };

  return (
    <section className="bg-white border border-gray-200 rounded-xl p-4 space-y-4">
      <h2 className="text-sm font-bold text-gray-900">Dataset access</h2>

      {total != null && (
        <p className="text-xs text-gray-600">
          Full dataset: <span className="font-semibold text-gray-900">{total.toLocaleString()}</span> records in this pipeline DB.
        </p>
      )}

      {subsets.length === 0 ? (
        <p className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
          No subsets yet. Use the Research DB Viewer to filter records and save them as a subset.
        </p>
      ) : (
        <div className="space-y-2">
          <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide">Subset</label>
          <div className="grid gap-2 sm:grid-cols-2">
            {subsets.map(s => {
              const active = s.id === selectedSubsetId;
              const rc = s.row_count;
              const pct = total && rc != null && total > 0 ? ((rc / total) * 100).toFixed(1) : null;
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => onSelectSubset(s.id)}
                  className={`text-left border rounded-xl p-3 transition relative group ${
                    active ? 'border-blue-500 ring-1 ring-blue-400 bg-blue-50/40' : 'border-gray-200 hover:border-gray-300'
                  }`}>
                  <div className="flex justify-between gap-2">
                    <span className="text-sm font-medium text-gray-900 truncate pr-6">{s.name}</span>
                    <button type="button" title="Delete subset" onClick={e => delSubset(e, s.id)}
                      className="absolute top-2 right-2 text-gray-300 hover:text-red-500 text-lg leading-none opacity-0 group-hover:opacity-100 transition">
                      ×
                    </button>
                  </div>
                  <div className="mt-1 text-xs text-gray-500 font-mono truncate" title={s.sql_query}>{s.sql_query}</div>
                  <div className="mt-2 flex flex-wrap gap-2 text-xs">
                    {rc != null && (
                      <span className="bg-white border border-gray-200 rounded px-2 py-0.5">
                        <span className="text-gray-500">Rows</span>{' '}
                        <span className="font-semibold text-gray-800">{rc.toLocaleString()}</span>
                        {pct != null && <span className="text-gray-400"> ({pct}% of dataset)</span>}
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2 pt-1 border-t border-gray-100">
        <button type="button" disabled={!selectedSubsetId}
          onClick={() => {
            const sel = subsets.find(s => s.id === selectedSubsetId);
            if (sel) onOpenViewer(sel);
          }}
          className="text-sm px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed font-medium transition">
          Open Research DB Viewer
        </button>
        {!selectedSubsetId && subsets.length > 0 && (
          <span className="text-xs text-gray-400 self-center">Select a subset first</span>
        )}
      </div>
    </section>
  );
}
