import { useState, useEffect } from 'react';
import { apiFetch } from '../api/client';

export default function ExportTab({ pipeline }) {
  const [stats, setStats] = useState(null);
  const [schema, setSchema] = useState([]);
  const [downloading, setDownloading] = useState(null);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipeline.id}/stats`)
      .then(r => r.ok ? r.json() : null)
      .then(setStats)
      .catch(() => setStats(null));
    apiFetch(`/api/pipeline/${pipeline.id}/schema`)
      .then(r => r.ok ? r.json() : [])
      .then(setSchema)
      .catch(() => setSchema([]));
  }, [pipeline.id]);

  const download = async (fmt) => {
    setDownloading(fmt);
    try {
      const res = await fetch(`/api/pipeline/${pipeline.id}/export?fmt=${fmt}`, { credentials: 'same-origin' });
      if (!res.ok) {
        const t = await res.text();
        alert(t || 'Export failed');
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const ext = fmt === 'sqlite' ? '.sqlite' : '.zip';
      a.download = `${pipeline.name || 'pipeline'}${ext}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(e.message);
    } finally {
      setDownloading(null);
    }
  };

  const downloadSchema = async () => {
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/export/schema`);
      if (!res.ok) return;
      const data = await res.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'research_schema.json';
      a.click();
      URL.revokeObjectURL(url);
    } catch {}
  };

  return (
    <div className="space-y-6">
      {/* Stats */}
      <section className="bg-white border border-gray-200 rounded-xl p-5">
        <h2 className="text-sm font-bold text-gray-900 mb-3">Pipeline Stats</h2>
        {stats ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <Stat label="Database" value={stats.exists ? `${stats.size_mb} MB` : 'Not created'} />
            <Stat label="Total Records" value={stats.record_count?.toLocaleString() ?? '—'} />
            <Stat label="LLM Classified" value={stats.llm_processed_count?.toLocaleString() ?? '—'} />
            <Stat label="Columns" value={schema.length || '—'} />
          </div>
        ) : (
          <p className="text-sm text-gray-400">Loading...</p>
        )}
      </section>

      {/* Downloads */}
      <section className="bg-white border border-gray-200 rounded-xl p-5">
        <h2 className="text-sm font-bold text-gray-900 mb-1">Download</h2>
        <p className="text-xs text-gray-500 mb-4">
          CSVs are auto-chunked under 30 MB for Claude Desktop project upload. The ZIP includes
          research_schema.json and schema.csv for context.
        </p>
        <div className="flex flex-wrap gap-3">
          <DownloadButton
            label="Download CSVs for Claude"
            sub="Chunked CSV ZIP + schema"
            loading={downloading === 'csv'}
            onClick={() => download('csv')}
          />
          <DownloadButton
            label="Download SQLite"
            sub="Raw research database"
            loading={downloading === 'sqlite'}
            onClick={() => download('sqlite')}
          />
          <DownloadButton
            label="Download Schema"
            sub="research_schema.json"
            loading={false}
            onClick={downloadSchema}
          />
        </div>
      </section>

      {/* Column reference */}
      {schema.length > 0 && (
        <section className="bg-white border border-gray-200 rounded-xl p-5">
          <h2 className="text-sm font-bold text-gray-900 mb-3">Column Schema</h2>
          <div className="overflow-x-auto max-h-96">
            <table className="text-xs w-full">
              <thead className="sticky top-0 bg-gray-50">
                <tr>
                  <th className="text-left px-2 py-1.5 font-semibold text-gray-600">Column</th>
                  <th className="text-left px-2 py-1.5 font-semibold text-gray-600">Type</th>
                </tr>
              </thead>
              <tbody>
                {schema.map(c => (
                  <tr key={c.name} className="border-t border-gray-100">
                    <td className="px-2 py-1 font-mono text-gray-800">{c.name}</td>
                    <td className="px-2 py-1 text-gray-500">{c.type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-lg font-semibold text-gray-900">{value}</p>
    </div>
  );
}

function DownloadButton({ label, sub, loading, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      className="text-left border border-gray-300 rounded-lg px-4 py-3 hover:bg-gray-50 disabled:opacity-50 transition"
    >
      <span className="text-sm font-medium text-gray-900 block">{loading ? 'Preparing...' : label}</span>
      <span className="text-xs text-gray-500">{sub}</span>
    </button>
  );
}
