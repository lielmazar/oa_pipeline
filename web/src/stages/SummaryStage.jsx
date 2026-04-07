import { useState, useEffect } from 'react';
import { apiFetch } from '../api/client';

export default function SummaryStage({ pipeline }) {
  const [stats, setStats]   = useState(null);
  const [events, setEvents] = useState([]);
  const [format, setFormat] = useState('csv');
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipeline.id}/stats`)
      .then(r => r.ok ? r.json() : null)
      .then(setStats);
    apiFetch(`/api/pipeline/${pipeline.id}/events?limit=20`)
      .then(r => r.ok ? r.json() : [])
      .then(setEvents);
  }, [pipeline.id]);

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
