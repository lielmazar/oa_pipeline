import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';

export default function SummaryStage({ pipeline }) {
  const [stats, setStats]   = useState(null);
  const [events, setEvents] = useState([]);
  const [format, setFormat] = useState('csv');
  const [downloading, setDownloading] = useState(false);
  const [disagreements, setDisagreements] = useState([]);
  const [ratingsLog, setRatingsLog] = useState([]);

  const loadQaTables = useCallback(() => {
    apiFetch(`/api/pipeline/${pipeline.id}/qa/disagreements`)
      .then(r => r.ok ? r.json() : [])
      .then(setDisagreements);
    apiFetch(`/api/pipeline/${pipeline.id}/qa/ratings`)
      .then(r => r.ok ? r.json() : [])
      .then(setRatingsLog);
  }, [pipeline.id]);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipeline.id}/stats`)
      .then(r => r.ok ? r.json() : null)
      .then(setStats);
    apiFetch(`/api/pipeline/${pipeline.id}/events?limit=20`)
      .then(r => r.ok ? r.json() : [])
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
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/export?db_format=${format}`);
      if (!res.ok) { alert('Export failed'); return; }
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
    } finally { setDownloading(false); }
  };

  return (
    <div className="space-y-4">
      {/* Summary stats */}
      {stats?.exists && (
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <h2 className="font-semibold text-gray-900 mb-3">Pipeline Summary</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[
              ['Records', stats.record_count?.toLocaleString()],
              ['LLM processed', stats.llm_processed_count?.toLocaleString() || '—'],
              ['DB size', `${stats.size_mb} MB`],
              ['Stage', pipeline.stage_label || pipeline.current_stage],
            ].map(([label, val]) => (
              <div key={label} className="bg-gray-50 rounded-lg p-3">
                <div className="text-xs text-gray-500 mb-1">{label}</div>
                <div className="font-semibold text-gray-900">{val}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* QA review — disagreements & ratings (after IRR) */}
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <div className="flex justify-between items-center gap-2 mb-2">
          <h2 className="font-semibold text-gray-900">QA disagreements</h2>
          <div className="flex gap-2">
            <button type="button" onClick={loadQaTables}
              className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 transition">
              ↻ Refresh
            </button>
            <button type="button" onClick={exportDisagreements} disabled={!disagreements.length}
              className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 disabled:opacity-40">
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
                    <th key={h} className="px-3 py-2 text-left font-medium text-gray-600">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {disagreements.map(r => (
                  <tr key={`${r.id}-${r.openalex_id}`} className="border-t border-gray-100">
                    <td className="px-3 py-1.5 font-mono truncate max-w-40">{r.openalex_id}</td>
                    <td className="px-3 py-1.5">{r.field}</td>
                    <td className="px-3 py-1.5 max-w-40 truncate font-medium text-amber-800">{r.human_value}</td>
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
                    <th key={h} className="px-3 py-2 text-left font-medium text-gray-600">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ratingsLog.map(r => (
                  <tr key={r.id} className="border-t border-gray-100">
                    <td className="px-3 py-1.5 font-mono truncate max-w-32">{r.openalex_id}</td>
                    <td className="px-3 py-1.5">{r.field}</td>
                    <td className="px-3 py-1.5 max-w-32 truncate font-medium text-blue-700">{r.human_value}</td>
                    <td className="px-3 py-1.5">{r.user_id}</td>
                    <td className="px-3 py-1.5 text-gray-400">{r.created_at?.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Export */}
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <h2 className="font-semibold text-gray-900 mb-1">Export</h2>
        <p className="text-xs text-gray-500 mb-4">
          Download a ZIP archive containing the database, chart SQL queries, co-occurrence CSVs, and logs.
        </p>
        <div className="flex items-center gap-3 mb-4">
          <label className="text-xs text-gray-600 font-medium shrink-0">Database format</label>
          <select value={format} onChange={e => setFormat(e.target.value)}
            className="border rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400">
            <option value="csv">CSV</option>
            <option value="sqlite">SQLite</option>
            <option value="both">CSV + SQLite</option>
            <option value="csv_chunks_50">CSV chunks (50 MB)</option>
            <option value="csv_chunks_100">CSV chunks (100 MB)</option>
          </select>
        </div>
        <button onClick={download} disabled={downloading}
          className="bg-blue-600 text-white text-sm px-6 py-2.5 rounded-lg hover:bg-blue-700 disabled:opacity-50 transition font-medium">
          {downloading ? 'Building ZIP…' : '↓ Download Export'}
        </button>
        <p className="text-xs text-gray-400 mt-2">
          ZIP includes: data ({format.toUpperCase()}), chart_queries.sql, co-occurrence CSVs, pipeline logs, README.md
        </p>
      </div>

      {/* Event log */}
      {events.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <h3 className="font-medium text-gray-900 mb-3 text-sm">Pipeline History</h3>
          <div className="space-y-1">
            {events.map(e => (
              <div key={e.id} className="flex items-start gap-3 text-xs">
                <span className="text-gray-400 shrink-0 tabular-nums">{e.created_at?.slice(0, 19)}</span>
                <span className={`shrink-0 font-medium ${
                  e.event?.includes('fail') || e.event?.includes('kill') ? 'text-red-600' :
                  e.event?.includes('done') || e.event?.includes('collect') ? 'text-green-700' :
                  'text-gray-700'
                }`}>{e.event}</span>
                {e.detail && <span className="text-gray-500 truncate">{e.detail}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
