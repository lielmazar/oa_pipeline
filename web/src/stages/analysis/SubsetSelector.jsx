import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../api/client';

export default function SubsetSelector({ pipelineId, selectedSubsetId, onSelectSubset }) {
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

  useEffect(() => {
    if (selectedSubsetId != null || subsets.length === 0) return;
    const allSubset = subsets.find(s => s.preset_key === 'all');
    if (allSubset) onSelectSubset(allSubset.id);
    else if (subsets.length) onSelectSubset(subsets[0].id);
  }, [selectedSubsetId, subsets, onSelectSubset]);

  if (!subsets.length) return null;

  return (
    <section className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wide">Subset</h3>
        {total != null && (
          <span className="text-xs text-gray-500">
            Dataset: <span className="font-semibold text-gray-800">{total.toLocaleString()}</span> records
          </span>
        )}
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {subsets.map(s => {
          const active = s.id === selectedSubsetId;
          const rc = s.row_count;
          const pct = total && rc != null && total > 0 ? ((rc / total) * 100).toFixed(1) : null;
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => onSelectSubset(s.id)}
              className={`text-left border rounded-lg p-3 transition ${
                active ? 'border-blue-500 ring-1 ring-blue-400 bg-blue-50/40' : 'border-gray-200 hover:border-gray-300'
              }`}
            >
              <span className="text-sm font-medium text-gray-900 truncate block">{s.name}</span>
              {rc != null && (
                <span className="text-xs text-gray-500 mt-1 block">
                  {rc.toLocaleString()} rows
                  {pct != null && <span className="text-gray-400"> ({pct}%)</span>}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </section>
  );
}
