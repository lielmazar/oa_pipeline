import { useState, useCallback } from 'react';
import { apiFetch } from '../../api/client';

export function HBarChart({ rows, xKey = 0, yKey = 1, maxRows = 40 }) {
  if (!rows?.length) return <p className="text-xs text-gray-400 text-center py-4">No data</p>;
  const slice = rows.slice(0, maxRows);
  const maxVal = Math.max(...slice.map(r => Number(r[yKey]) || 0));
  return (
    <div className="space-y-1.5">
      {slice.map((r, i) => (
        <div key={i} className="flex items-center gap-2 text-xs">
          <span className="w-36 truncate text-gray-600 text-right shrink-0" title={String(r[xKey])}>{r[xKey]}</span>
          <div className="flex-1 bg-gray-100 rounded h-5 relative min-w-0">
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

export function VBarChart({ rows, xKey = 0, yKey = 1, maxRows = 24 }) {
  if (!rows?.length) return <p className="text-xs text-gray-400 text-center py-4">No data</p>;
  const slice = rows.slice(0, maxRows);
  const maxVal = Math.max(...slice.map(r => Number(r[yKey]) || 0));
  return (
    <div className="flex items-end gap-1 h-40 overflow-x-auto pb-1">
      {slice.map((r, i) => {
        const h = maxVal > 0 ? (Number(r[yKey]) / maxVal) * 100 : 0;
        return (
          <div key={i} className="flex flex-col items-center gap-0.5 shrink-0 w-10">
            <div className="w-full bg-gray-100 rounded-t flex-1 flex items-end min-h-[4px]" style={{ minHeight: '80px' }}>
              <div
                className="w-full bg-indigo-500 rounded-t transition-all"
                style={{ height: `${h}%`, minHeight: h > 0 ? '4px' : 0 }}
              />
            </div>
            <span className="text-[10px] text-gray-500 truncate max-w-full" title={String(r[xKey])}>
              {String(r[xKey]).slice(0, 6)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function PieTable({ rows, xKey = 0, yKey = 1 }) {
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

export function HeatmapTable({ rows }) {
  if (!rows?.length) return <p className="text-xs text-gray-400 text-center py-4">No data</p>;
  const xVals = [...new Set(rows.map(r => r[0]))];
  const yVals = [...new Set(rows.map(r => r[1]))];
  const cell = {};
  rows.forEach(r => { cell[`${r[0]}|${r[1]}`] = Number(r[2]) || 0; });
  const maxVal = Math.max(...rows.map(r => Number(r[2]) || 0));

  return (
    <div className="overflow-auto max-h-96">
      <table className="text-xs border-collapse">
        <thead>
          <tr>
            <th className="p-1 bg-gray-50" />
            {yVals.map(y => <th key={y} className="p-1 text-gray-500 font-normal whitespace-nowrap max-w-24 truncate">{y}</th>)}
          </tr>
        </thead>
        <tbody>
          {xVals.map(x => (
            <tr key={x}>
              <td className="p-1 text-gray-600 whitespace-nowrap pr-2 font-medium max-w-32 truncate">{x}</td>
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

const STACK_COLORS = ['bg-blue-500','bg-emerald-500','bg-amber-500','bg-violet-500','bg-rose-500','bg-cyan-500','bg-orange-500'];

export function StackedBarChart({ rows }) {
  if (!rows?.length) return <p className="text-xs text-gray-400 text-center py-4">No data</p>;
  const byX = {};
  rows.forEach(r => {
    const x = String(r[0]);
    const series = String(r[1]);
    const c = Number(r[2]) || 0;
    if (!byX[x]) byX[x] = [];
    byX[x].push({ series, count: c });
  });
  const keys = Object.keys(byX).slice(0, 30);
  return (
    <div className="space-y-2">
      {keys.map(x => {
        const segments = byX[x];
        const total = segments.reduce((s, g) => s + g.count, 0) || 1;
        return (
          <div key={x} className="text-xs">
            <div className="text-gray-600 truncate mb-0.5" title={x}>{x}</div>
            <div className="flex h-5 rounded overflow-hidden bg-gray-100">
              {segments.map((g, i) => (
                <div
                  key={g.series}
                  className={`${STACK_COLORS[i % STACK_COLORS.length]} h-5 transition-all`}
                  style={{ width: `${(g.count / total) * 100}%` }}
                  title={`${g.series}: ${g.count}`}
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function ChartResult({ renderer, rows }) {
  if (!rows?.length) return <p className="text-xs text-gray-400 text-center py-4">No data</p>;
  if (renderer === 'pie') return <PieTable rows={rows} />;
  if (renderer === 'heatmap' || renderer === 'cooccurrence_3d' || renderer === 'cooccurrence_3d_bar') {
    return <HeatmapTable rows={rows} />;
  }
  if (renderer === 'stacked') return <StackedBarChart rows={rows} />;
  if (renderer === 'bar') return <VBarChart rows={rows} />;
  return <HBarChart rows={rows} />;
}

export function ChartCard({ chart, pipelineId, subsetId = null, topN = 20, cutoffYear = 2000 }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (data || loading) return;
    setLoading(true);
    try {
      const body =
        subsetId != null
          ? { chart_id: chart.id, subset_id: subsetId, top_n: topN, cutoff_year: cutoffYear }
          : { sql: chart.sql };
      const res = await apiFetch(`/api/pipeline/${pipelineId}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await res.json();
      if (!res.ok) { setError(d.detail || 'Query failed'); return; }
      setData(d);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [pipelineId, chart.id, chart.sql, data, loading, subsetId, topN, cutoffYear]);

  const toggle = () => {
    if (!open) load();
    setOpen(o => !o);
  };

  const rows = data?.rows || [];
  const renderer = chart.renderer;

  return (
    <div className="border border-gray-200 rounded-xl overflow-hidden">
      <button
        type="button"
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
          {error && <p className="text-xs text-red-500 py-2">{error}</p>}
          {data && !loading && (
            <>
              <ChartResult renderer={renderer} rows={rows} />
              <p className="text-xs text-gray-400 mt-2 text-right">{rows.length} rows</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
