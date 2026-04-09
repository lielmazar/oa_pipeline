import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';

export default function SummaryStage({ pipeline }) {
  const [stats, setStats] = useState(null);
  const [subsetSummary, setSubsetSummary] = useState(null);
  const [events, setEvents] = useState([]);
  const [format, setFormat] = useState('csv');
  const [chunkMb, setChunkMb] = useState('');
  const [downloading, setDownloading] = useState(false);
  const [disagreements, setDisagreements] = useState([]);
  const [ratingsLog, setRatingsLog] = useState([]);

  const loadQaTables = useCallback(() => {
    apiFetch(`/api/pipeline/${pipeline.id}/qa/disagreements`)
      .then(r => (r.ok ? r.json() : []))
      .then(setDisagreements);
    apiFetch(`/api/pipeline/${pipeline.id}/qa/ratings`)
      .then(r => (r.ok ? r.json() : []))
      .then(setRatingsLog);
  }, [pipeline.id]);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipeline.id}/stats`)
      .then(r => (r.ok ? r.json() : null))
      .then(setStats);
    apiFetch(`/api/pipeline/${pipeline.id}/subsets/summary`)
      .then(r => (r.ok ? r.json() : null))
      .then(setSubsetSummary);
    apiFetch(`/api/pipeline/${pipeline.id}/events?limit=40`)
      .then(r => (r.ok ? r.json() : []))
      .then(setEvents);
    loadQaTables();
  }, [pipeline.id, loadQaTables]);

  const exportDisagreements = () => {
    const blob = new Blob([JSON.stringify(disagreements, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `qa_disagreements_${pipeline.id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const download = async () => {
    setDownloading(true);
    try {
      const params = new URLSearchParams();
      params.set('db_format', format);
      if (format === 'csv_chunks') {
        const n = chunkMb.trim() === '' ? null : Number(chunkMb);
        if (n != null && !Number.isNaN(n) && n > 0) {
          params.set('chunk_mb', String(Math.floor(n)));
        }
      }
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/export?${params.toString()}`);
      if (!res.ok) {
        alert('Export failed');
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `pipeline_${pipeline.id}_export.zip`;
      const cd = res.headers.get('Content-Disposition');
      if (cd) {
        const m = cd.match(/filename="([^"]+)"/);
        if (m) a.download = m[1];
      }
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  };

  const subsets = subsetSummary?.subsets ?? [];

  return (
    <div className="space-y-4">
      {/* Full pipeline archive */}
      <div className="bg-gradient-to-br from-slate-50 to-blue-50 border border-blue-100 rounded-xl p-5 shadow-sm">
        <h2 className="font-semibold text-gray-900 text-lg mb-1">Full pipeline archive</h2>
        <p className="text-xs text-gray-600 mb-4 max-w-3xl">
          Download one ZIP with research data (CSV and/or SQLite), logs, subset snapshot, QA ratings and
          annotations, IRR summary, chart data as CSV, matplotlib chart PNGs, co-occurrence exports, and
          README. Use this for archiving or offline analysis.
        </p>
        <div className="flex flex-col sm:flex-row sm:items-end gap-4 flex-wrap">
          <label className="block">
            <span className="text-xs text-gray-600 font-medium">Database format</span>
            <select
              value={format}
              onChange={e => setFormat(e.target.value)}
              className="mt-1 block border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white"
            >
              <option value="csv">CSV (one file per table)</option>
              <option value="sqlite">SQLite (output.sqlite)</option>
              <option value="both">CSV + SQLite</option>
              <option value="csv_chunks">CSV with optional size split</option>
            </select>
          </label>
          {format === 'csv_chunks' && (
            <label className="block">
              <span className="text-xs text-gray-600 font-medium">Chunk size (MB)</span>
              <input
                type="number"
                min={1}
                step={1}
                placeholder="Empty = one file per table"
                value={chunkMb}
                onChange={e => setChunkMb(e.target.value)}
                className="mt-1 block w-52 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white"
              />
              <span className="block text-[11px] text-gray-500 mt-1">Leave empty for a single CSV per table.</span>
            </label>
          )}
          <button
            type="button"
            onClick={download}
            disabled={downloading}
            className="bg-blue-600 text-white text-sm px-6 py-2.5 rounded-lg hover:bg-blue-700 disabled:opacity-50 transition font-medium sm:mb-0"
          >
            {downloading ? 'Building ZIP…' : '↓ Download full archive'}
          </button>
        </div>
      </div>

      {/* Overview */}
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <h2 className="font-semibold text-gray-900 mb-3">Overview</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
          {[
            ['Records', stats?.exists ? stats.record_count?.toLocaleString() : '—'],
            ['LLM processed', stats?.exists ? stats.llm_processed_count?.toLocaleString() || '—' : '—'],
            ['DB size', stats?.exists ? `${stats.size_mb} MB` : '—'],
            ['Stage', pipeline.stage_label || pipeline.current_stage],
            ['QA ratings', ratingsLog.length ? ratingsLog.length.toLocaleString() : '0'],
            ['Disagreements', disagreements.length ? disagreements.length.toLocaleString() : '0'],
            ['Subsets', subsets.length ? subsets.length.toLocaleString() : '—'],
            [
              'Dataset (subsets)',
              subsetSummary?.dataset_total != null
                ? subsetSummary.dataset_total.toLocaleString()
                : '—',
            ],
          ].map(([label, val]) => (
            <div key={label} className="bg-gray-50 rounded-lg p-3">
              <div className="text-xs text-gray-500 mb-1">{label}</div>
              <div className="font-semibold text-gray-900">{val}</div>
            </div>
          ))}
        </div>

        <h3 className="text-sm font-medium text-gray-800 mb-2">Subsets</h3>
        {subsets.length === 0 ? (
          <p className="text-sm text-gray-400">No subsets defined yet.</p>
        ) : (
          <div className="overflow-x-auto border border-gray-200 rounded-lg max-h-64 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  {['Name', 'Rows', 'Preset'].map(h => (
                    <th key={h} className="px-3 py-2 text-left font-medium text-gray-600">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {subsets.map(s => (
                  <tr key={s.id} className="border-t border-gray-100">
                    <td className="px-3 py-1.5 font-medium text-gray-800">{s.name}</td>
                    <td className="px-3 py-1.5 tabular-nums">
                      {s.row_count != null ? s.row_count.toLocaleString() : '—'}
                    </td>
                    <td className="px-3 py-1.5 text-gray-500">{s.preset_key || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* QA */}
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <div className="flex justify-between items-center gap-2 mb-2">
          <h2 className="font-semibold text-gray-900">QA disagreements</h2>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={loadQaTables}
              className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 transition"
            >
              ↻ Refresh
            </button>
            <button
              type="button"
              onClick={exportDisagreements}
              disabled={!disagreements.length}
              className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 disabled:opacity-40"
            >
              Export JSON
            </button>
          </div>
        </div>
        <p className="text-xs text-gray-500 mb-3">
          Human ratings that differ from the stored model output (model values are not shown here).
        </p>
        {disagreements.length === 0 ? (
          <p className="text-sm text-gray-400 py-4">No disagreements (or no ratings yet).</p>
        ) : (
          <div className="overflow-x-auto border border-gray-200 rounded-lg max-h-72 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  {['OpenAlex ID', 'Field', 'Your rating', 'Rater'].map(h => (
                    <th key={h} className="px-3 py-2 text-left font-medium text-gray-600">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {disagreements.map(r => (
                  <tr key={`${r.id}-${r.openalex_id}`} className="border-t border-gray-100">
                    <td className="px-3 py-1.5 font-mono truncate max-w-40">{r.openalex_id}</td>
                    <td className="px-3 py-1.5">{r.field}</td>
                    <td className="px-3 py-1.5 max-w-40 truncate font-medium text-amber-800">
                      {r.human_value}
                    </td>
                    <td className="px-3 py-1.5">{r.user_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <h2 className="font-semibold text-gray-900 mb-2">Ratings log</h2>
        <p className="text-xs text-gray-500 mb-3">All submitted QA ratings for this pipeline.</p>
        {ratingsLog.length === 0 ? (
          <p className="text-sm text-gray-400 py-4">No ratings submitted yet.</p>
        ) : (
          <div className="overflow-x-auto border border-gray-200 rounded-lg max-h-72 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  {['OpenAlex ID', 'Field', 'Your value', 'Rater', 'Date'].map(h => (
                    <th key={h} className="px-3 py-2 text-left font-medium text-gray-600">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ratingsLog.map(r => (
                  <tr key={r.id} className="border-t border-gray-100">
                    <td className="px-3 py-1.5 font-mono truncate max-w-32">{r.openalex_id}</td>
                    <td className="px-3 py-1.5">{r.field}</td>
                    <td className="px-3 py-1.5 max-w-32 truncate font-medium text-blue-700">
                      {r.human_value}
                    </td>
                    <td className="px-3 py-1.5">{r.user_id}</td>
                    <td className="px-3 py-1.5 text-gray-400">{r.created_at?.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {events.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <h3 className="font-medium text-gray-900 mb-3 text-sm">Pipeline history</h3>
          <div className="space-y-1">
            {events.map(e => (
              <div key={e.id} className="flex items-start gap-3 text-xs">
                <span className="text-gray-400 shrink-0 tabular-nums">{e.created_at?.slice(0, 19)}</span>
                <span
                  className={`shrink-0 font-medium ${
                    e.event?.includes('fail') || e.event?.includes('kill')
                      ? 'text-red-600'
                      : e.event?.includes('done') || e.event?.includes('collect')
                        ? 'text-green-700'
                        : 'text-gray-700'
                  }`}
                >
                  {e.event}
                </span>
                {e.detail && <span className="text-gray-500 truncate">{e.detail}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
