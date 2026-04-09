import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../api/client';

const DEFAULT_COLS = ['openalex_id', 'title', 'publication_year', 'type', 'open_access_oa_status'];
const LLM_COLS = ['llm_fuel_classification', 'llm_ml_role', 'llm_confidence'];

/** Paginated search over the full pipeline research DB (no subset). */
export default function FullDatasetRecordBrowser({ pipelineId, llmOnly }) {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [data, setData] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [schema, setSchema] = useState([]);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipelineId}/schema`)
      .then(r => (r.ok ? r.json() : []))
      .then(s => setSchema(s))
      .catch(() => {});
  }, [pipelineId]);

  const availCols = schema.map(c => c.name);
  const visibleCols = [...DEFAULT_COLS, ...(llmOnly ? LLM_COLS : [])].filter(c => availCols.includes(c));

  const load = useCallback(async (pg = 1, q = '') => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: pg,
        q,
        visible_cols: visibleCols.join(','),
        page_size: 50,
      });
      const res = await apiFetch(`/api/pipeline/${pipelineId}/records?${params}`);
      if (res.ok) setData(await res.json());
    } finally {
      setLoading(false);
    }
  }, [pipelineId, visibleCols.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (schema.length) load(1, search);
  }, [schema.length, pipelineId]); // eslint-disable-line react-hooks/exhaustive-deps

  const doSearch = e => {
    e.preventDefault();
    setPage(1);
    load(1, search);
  };

  const openDetail = async id => {
    const res = await apiFetch(`/api/pipeline/${pipelineId}/records/${encodeURIComponent(id)}`);
    if (res.ok) setDetail(await res.json());
  };

  const cols = data?.columns || visibleCols;

  return (
    <section className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
      <h2 className="text-sm font-bold text-gray-900">Full dataset records</h2>
      <p className="text-xs text-gray-500">
        Browse and search all rows in this pipeline&apos;s research database. For subset-scoped filters and SQL, use{' '}
        <strong>Research DB Viewer</strong> from Dataset access.
      </p>
      <form onSubmit={doSearch} className="flex gap-2">
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search title, abstract, terms…"
          className="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
        />
        <button
          type="submit"
          className="text-sm px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition"
        >
          Search
        </button>
      </form>

      {data && (
        <div className="text-xs text-gray-500">
          {data.total?.toLocaleString()} records · page {data.page}
        </div>
      )}

      {loading && <p className="text-xs text-gray-400 text-center py-4">Loading…</p>}

      {data?.rows && !loading && (
        <div className="overflow-x-auto border border-gray-200 rounded-xl max-h-[min(480px,50vh)] overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="bg-gray-50 sticky top-0">
              <tr>
                {cols.map(c => (
                  <th key={c} className="px-3 py-2 text-left font-medium text-gray-600 whitespace-nowrap">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, i) => (
                <tr
                  key={i}
                  className="border-t border-gray-100 hover:bg-blue-50 cursor-pointer"
                  onClick={() => openDetail(row[cols.indexOf('openalex_id')])}
                >
                  {row.map((cell, j) => (
                    <td key={j} className="px-3 py-1.5 max-w-xs truncate" title={String(cell ?? '')}>
                      {String(cell ?? '')}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && data.total > data.per_page && (
        <div className="flex justify-center gap-2">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => {
              const p = page - 1;
              setPage(p);
              load(p, search);
            }}
            className="text-xs px-3 py-1.5 border rounded-lg disabled:opacity-30 hover:bg-gray-50 transition"
          >
            ← Prev
          </button>
          <span className="text-xs text-gray-500 py-1.5">
            Page {data.page} / {Math.ceil(data.total / data.per_page)}
          </span>
          <button
            type="button"
            disabled={page >= Math.ceil(data.total / data.per_page)}
            onClick={() => {
              const p = page + 1;
              setPage(p);
              load(p, search);
            }}
            className="text-xs px-3 py-1.5 border rounded-lg disabled:opacity-30 hover:bg-gray-50 transition"
          >
            Next →
          </button>
        </div>
      )}

      {detail && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto p-6">
            <div className="flex justify-between items-start mb-4">
              <h3 className="font-bold text-gray-900 text-sm flex-1 pr-4">{detail.title}</h3>
              <button type="button" onClick={() => setDetail(null)} className="text-gray-400 hover:text-gray-700 text-xl leading-none">
                ×
              </button>
            </div>
            <div className="space-y-1.5">
              {Object.entries(detail).map(
                ([k, v]) =>
                  v != null &&
                  v !== '' && (
                    <div key={k} className="flex gap-2 text-xs">
                      <span className="text-gray-500 w-36 shrink-0 font-medium">{k}</span>
                      <span className="text-gray-800 break-words">
                        {typeof v === 'string' && v.startsWith('[') ? <span className="font-mono">{v}</span> : String(v)}
                      </span>
                    </div>
                  )
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
