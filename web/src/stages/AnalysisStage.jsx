import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';

// ── Simple chart renderers ─────────────────────────────────────────────────

function HBarChart({ columns, rows, xKey = 0, yKey = 1 }) {
  if (!rows?.length) return <p className="text-xs text-gray-400 text-center py-4">No data</p>;
  const maxVal = Math.max(...rows.map(r => Number(r[yKey]) || 0));
  return (
    <div className="space-y-1.5">
      {rows.slice(0, 20).map((r, i) => (
        <div key={i} className="flex items-center gap-2 text-xs">
          <span className="w-32 truncate text-gray-600 text-right shrink-0" title={String(r[xKey])}>{r[xKey]}</span>
          <div className="flex-1 bg-gray-100 rounded h-5 relative">
            <div
              className="bg-blue-500 h-5 rounded transition-all"
              style={{ width: `${maxVal > 0 ? (Number(r[yKey]) / maxVal * 100) : 0}%` }}
            />
          </div>
          <span className="w-12 text-gray-500 shrink-0">{Number(r[yKey]).toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

function PieTable({ columns, rows, xKey = 0, yKey = 1 }) {
  if (!rows?.length) return <p className="text-xs text-gray-400 text-center py-4">No data</p>;
  const total = rows.reduce((s, r) => s + (Number(r[yKey]) || 0), 0);
  const COLORS = ['bg-blue-500','bg-green-500','bg-yellow-500','bg-purple-500','bg-red-500',
                  'bg-indigo-500','bg-pink-500','bg-orange-500','bg-teal-500','bg-cyan-500'];
  return (
    <div className="space-y-1.5">
      {rows.map((r, i) => (
        <div key={i} className="flex items-center gap-2 text-xs">
          <span className={`w-3 h-3 rounded-full shrink-0 ${COLORS[i % COLORS.length]}`} />
          <span className="flex-1 text-gray-700 truncate">{r[xKey]}</span>
          <span className="text-gray-500">{total > 0 ? `${(Number(r[yKey]) / total * 100).toFixed(1)}%` : ''}</span>
          <span className="text-gray-400 w-14 text-right">{Number(r[yKey]).toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

function HeatmapTable({ columns, rows }) {
  if (!rows?.length) return <p className="text-xs text-gray-400 text-center py-4">No data</p>;
  const xVals = [...new Set(rows.map(r => r[0]))];
  const yVals = [...new Set(rows.map(r => r[1]))];
  const cell  = {};
  rows.forEach(r => { cell[`${r[0]}|${r[1]}`] = Number(r[2]) || 0; });
  const maxVal = Math.max(...rows.map(r => Number(r[2]) || 0));

  return (
    <div className="overflow-auto">
      <table className="text-xs border-collapse">
        <thead>
          <tr>
            <th className="p-1 bg-gray-50" />
            {yVals.map(y => <th key={y} className="p-1 text-gray-500 font-normal whitespace-nowrap max-w-20 truncate">{y}</th>)}
          </tr>
        </thead>
        <tbody>
          {xVals.map(x => (
            <tr key={x}>
              <td className="p-1 text-gray-600 whitespace-nowrap pr-2 font-medium">{x}</td>
              {yVals.map(y => {
                const v = cell[`${x}|${y}`] || 0;
                const alpha = maxVal > 0 ? v / maxVal : 0;
                return (
                  <td key={y} title={`${x} × ${y}: ${v}`}
                    className="p-1 text-center min-w-8"
                    style={{ backgroundColor: `rgba(59, 130, 246, ${alpha * 0.8 + 0.05})` }}>
                    {v > 0 ? v : ''}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChartCard({ chart, pipelineId }) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen]       = useState(false);
  const [error, setError]     = useState('');

  const load = useCallback(async () => {
    if (data || loading) return;
    setLoading(true);
    try {
      const res = await apiFetch(`/api/pipeline/${pipelineId}/query`, {
        method: 'POST',
        body: JSON.stringify({ sql: chart.sql }),
      });
      const d = await res.json();
      if (!res.ok) { setError(d.detail || 'Query failed'); return; }
      setData(d);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [pipelineId, chart.sql, data, loading]);

  const toggle = () => {
    if (!open) load();
    setOpen(o => !o);
  };

  const rows = data?.rows || [];
  const renderer = chart.renderer;

  return (
    <div className="border border-gray-200 rounded-xl overflow-hidden">
      <button
        onClick={toggle}
        className="w-full flex items-center justify-between px-4 py-3 bg-white hover:bg-gray-50 transition text-left">
        <div>
          <span className="font-medium text-gray-900 text-sm">{chart.title}</span>
          {chart.llm_only && (
            <span className="ml-2 text-xs bg-purple-100 text-purple-700 px-1.5 py-0.5 rounded-full">LLM</span>
          )}
        </div>
        <span className="text-gray-400 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 pt-1 border-t border-gray-100 bg-white">
          {loading && <p className="text-xs text-gray-400 py-4 text-center">Loading…</p>}
          {error   && <p className="text-xs text-red-500 py-2">{error}</p>}
          {data && !loading && (
            <>
              {(renderer === 'bar' || renderer === 'hbar') &&
                <HBarChart columns={data.columns} rows={rows} />}
              {renderer === 'pie' &&
                <PieTable columns={data.columns} rows={rows} />}
              {(renderer === 'heatmap' || renderer === 'cooccurrence_3d' || renderer === 'cooccurrence_3d_bar') &&
                <HeatmapTable columns={data.columns} rows={rows} />}
              <p className="text-xs text-gray-400 mt-2 text-right">{rows.length} rows</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Record Browser ─────────────────────────────────────────────────────────

const DEFAULT_COLS = ['openalex_id', 'title', 'publication_year', 'type', 'open_access_oa_status'];
const LLM_COLS     = ['llm_fuel_classification', 'llm_ml_role', 'llm_confidence'];

function RecordBrowser({ pipelineId, llmOnly }) {
  const [page, setPage]     = useState(1);
  const [search, setSearch] = useState('');
  const [data, setData]     = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [schema, setSchema]   = useState([]);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipelineId}/schema`)
      .then(r => r.ok ? r.json() : [])
      .then(s => setSchema(s))
      .catch(() => {});
  }, [pipelineId]);

  const availCols = schema.map(c => c.name);
  const visibleCols = [...DEFAULT_COLS, ...(llmOnly ? LLM_COLS : [])].filter(c => availCols.includes(c));

  const load = useCallback(async (pg = 1, q = '') => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: pg, q, visible_cols: visibleCols.join(','), page_size: 50,
      });
      const res = await apiFetch(`/api/pipeline/${pipelineId}/records?${params}`);
      if (res.ok) setData(await res.json());
    } finally { setLoading(false); }
  }, [pipelineId, visibleCols.join(',')]); // eslint-disable-line

  useEffect(() => { if (schema.length) load(1, search); }, [schema.length, pipelineId]); // eslint-disable-line

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
    <div className="space-y-3">
      <form onSubmit={doSearch} className="flex gap-2">
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search title, abstract, terms…"
          className="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
        />
        <button type="submit"
          className="text-sm px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition">
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
        <div className="overflow-x-auto border border-gray-200 rounded-xl">
          <table className="w-full text-xs">
            <thead className="bg-gray-50">
              <tr>
                {cols.map(c => (
                  <th key={c} className="px-3 py-2 text-left font-medium text-gray-600 whitespace-nowrap">{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, i) => (
                <tr key={i}
                  className="border-t border-gray-100 hover:bg-blue-50 cursor-pointer"
                  onClick={() => openDetail(row[cols.indexOf('openalex_id')])}>
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

      {/* Pagination */}
      {data && data.total > data.per_page && (
        <div className="flex justify-center gap-2">
          <button
            disabled={page <= 1}
            onClick={() => { const p = page - 1; setPage(p); load(p, search); }}
            className="text-xs px-3 py-1.5 border rounded-lg disabled:opacity-30 hover:bg-gray-50 transition">
            ← Prev
          </button>
          <span className="text-xs text-gray-500 py-1.5">Page {data.page} / {Math.ceil(data.total / data.per_page)}</span>
          <button
            disabled={page >= Math.ceil(data.total / data.per_page)}
            onClick={() => { const p = page + 1; setPage(p); load(p, search); }}
            className="text-xs px-3 py-1.5 border rounded-lg disabled:opacity-30 hover:bg-gray-50 transition">
            Next →
          </button>
        </div>
      )}

      {/* Record detail modal */}
      {detail && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto p-6">
            <div className="flex justify-between items-start mb-4">
              <h3 className="font-bold text-gray-900 text-sm flex-1 pr-4">{detail.title}</h3>
              <button onClick={() => setDetail(null)} className="text-gray-400 hover:text-gray-700 text-xl leading-none">×</button>
            </div>
            <div className="space-y-1.5">
              {Object.entries(detail).map(([k, v]) => v != null && v !== '' && (
                <div key={k} className="flex gap-2 text-xs">
                  <span className="text-gray-500 w-36 shrink-0 font-medium">{k}</span>
                  <span className="text-gray-800 break-words">{
                    typeof v === 'string' && v.startsWith('[') ? (
                      <span className="font-mono">{v}</span>
                    ) : String(v)
                  }</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Subsets ────────────────────────────────────────────────────────────────

function SubsetsPanel({ pipelineId }) {
  const [subsets, setSubsets] = useState([]);
  const [name, setName]       = useState('');
  const [sql, setSql]         = useState('');
  const [adding, setAdding]   = useState(false);
  const [showAdd, setShowAdd] = useState(false);

  const load = () => {
    apiFetch(`/api/pipeline/${pipelineId}/subsets`)
      .then(r => r.ok ? r.json() : [])
      .then(setSubsets);
  };

  useEffect(load, [pipelineId]); // eslint-disable-line

  const add = async e => {
    e.preventDefault();
    if (!name.trim() || !sql.trim()) return;
    setAdding(true);
    await apiFetch(`/api/pipeline/${pipelineId}/subsets`, {
      method: 'POST',
      body: JSON.stringify({ name: name.trim(), sql_query: sql.trim() }),
    });
    setAdding(false); setShowAdd(false); setName(''); setSql('');
    load();
  };

  const del = async id => {
    await apiFetch(`/api/pipeline/${pipelineId}/subsets/${id}`, { method: 'DELETE' });
    load();
  };

  return (
    <div className="space-y-3">
      <div className="flex justify-between items-center">
        <p className="text-xs text-gray-500">{subsets.length} saved subsets</p>
        <button onClick={() => setShowAdd(true)}
          className="text-xs px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition">
          + Add Subset
        </button>
      </div>
      {subsets.map(s => (
        <div key={s.id} className="border border-gray-200 rounded-lg p-3 flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-gray-800">{s.name}</p>
            <p className="text-xs font-mono text-gray-500 mt-0.5 truncate">{s.sql_query}</p>
          </div>
          <button onClick={() => del(s.id)} className="text-gray-300 hover:text-red-500 transition text-lg leading-none">×</button>
        </div>
      ))}
      {subsets.length === 0 && (
        <p className="text-xs text-gray-400 text-center py-4">
          No subsets yet. Add SQL-defined data views for focused analysis.
        </p>
      )}

      {showAdd && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <form onSubmit={add}
            className="bg-white rounded-xl shadow-2xl w-full max-w-lg mx-4 p-6 space-y-3">
            <h3 className="font-bold text-gray-900">New Subset</h3>
            <label className="block">
              <span className="text-xs text-gray-500 font-medium">Name</span>
              <input value={name} onChange={e => setName(e.target.value)} autoFocus
                className="mt-1 w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400" />
            </label>
            <label className="block">
              <span className="text-xs text-gray-500 font-medium">SQL (WHERE clause or full SELECT)</span>
              <textarea value={sql} onChange={e => setSql(e.target.value)} rows={4}
                placeholder="SELECT * FROM filtered_records WHERE open_access_oa_status = 'gold'"
                className="mt-1 w-full border rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-blue-400 resize-none" />
            </label>
            <div className="flex gap-2">
              <button type="button" onClick={() => setShowAdd(false)}
                className="flex-1 border text-sm py-2 rounded-lg hover:bg-gray-50 transition">Cancel</button>
              <button type="submit" disabled={adding}
                className="flex-1 bg-blue-600 text-white text-sm py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50 transition font-medium">
                {adding ? 'Saving…' : 'Save'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

// ── Main ─────────────────────────────────────────────────────────────────────

export default function AnalysisStage({ pipeline, llmOnly }) {
  const [tab, setTab]       = useState('charts');
  const [charts, setCharts] = useState([]);
  const [stats, setStats]   = useState(null);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipeline.id}/charts`)
      .then(r => r.ok ? r.json() : [])
      .then(setCharts);
    apiFetch(`/api/pipeline/${pipeline.id}/stats`)
      .then(r => r.ok ? r.json() : null)
      .then(setStats);
  }, [pipeline.id]);

  const visibleCharts = llmOnly
    ? charts.filter(c => c.llm_only)
    : charts;

  const Tab = ({ id, label }) => (
    <button onClick={() => setTab(id)}
      className={`text-sm px-4 py-2 border-b-2 transition font-medium ${
        tab === id ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-700'
      }`}>
      {label}
    </button>
  );

  return (
    <div className="space-y-4">
      {/* Stats bar */}
      {stats?.exists && (
        <div className="bg-white border border-gray-200 rounded-xl p-4 flex gap-6 text-sm">
          <div>
            <span className="text-gray-500">Records: </span>
            <span className="font-semibold">{stats.record_count?.toLocaleString()}</span>
          </div>
          {stats.llm_processed_count > 0 && (
            <div>
              <span className="text-gray-500">LLM processed: </span>
              <span className="font-semibold">{stats.llm_processed_count?.toLocaleString()}</span>
            </div>
          )}
          <div>
            <span className="text-gray-500">DB size: </span>
            <span className="font-semibold">{stats.size_mb} MB</span>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="flex border-b border-gray-100 px-4">
          <Tab id="charts"  label="Charts" />
          <Tab id="records" label="Records" />
          <Tab id="subsets" label="Subsets" />
        </div>

        <div className="p-4">
          {tab === 'charts' && (
            <div className="space-y-2">
              {visibleCharts.length === 0 && (
                <p className="text-sm text-gray-400 text-center py-8">
                  {llmOnly ? 'No LLM charts available — run LLM classification first.' : 'No charts available.'}
                </p>
              )}
              {visibleCharts.map(c => (
                <ChartCard key={c.id} chart={c} pipelineId={pipeline.id} />
              ))}
            </div>
          )}
          {tab === 'records' && (
            <RecordBrowser pipelineId={pipeline.id} llmOnly={llmOnly} />
          )}
          {tab === 'subsets' && (
            <SubsetsPanel pipelineId={pipeline.id} />
          )}
        </div>
      </div>
    </div>
  );
}
