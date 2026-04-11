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
      a.download = `${pipeline.name || 'pipeline'}_${fmt}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(e.message);
    } finally {
      setDownloading(null);
    }
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
          Both exports include: research_schema.json, schema.csv, QA ratings, IRR statistics, and starred records.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <ExportCard
            title="Full Export"
            items={[
              'SQLite database (research.sqlite)',
              'Single records.csv (all records)',
              'research_schema.json + schema.csv',
              'QA ratings + IRR statistics',
              'Starred records CSV',
            ]}
            loading={downloading === 'full'}
            onClick={() => download('full')}
          />
          <ExportCard
            title="For Claude"
            items={[
              'Records as 25 MB chunked CSVs',
              'research_schema.json + schema.csv',
              'QA ratings + IRR statistics',
              'Starred records CSV',
            ]}
            note="Chunked for Claude Desktop project upload (no SQLite)"
            loading={downloading === 'claude'}
            onClick={() => download('claude')}
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

function ExportCard({ title, items, note, loading, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      className="text-left border border-gray-200 rounded-xl p-4 hover:border-teal-400 hover:shadow-sm disabled:opacity-50 transition w-full"
    >
      <span className="text-sm font-bold text-gray-900 block mb-2">
        {loading ? 'Preparing…' : title}
      </span>
      <ul className="space-y-0.5 mb-2">
        {items.map((item, i) => (
          <li key={i} className="text-[11px] text-gray-600 flex items-start gap-1.5">
            <span className="text-teal-500 mt-0.5 shrink-0">✓</span>
            {item}
          </li>
        ))}
      </ul>
      {note && <p className="text-[10px] text-gray-400 italic">{note}</p>}
    </button>
  );
}
