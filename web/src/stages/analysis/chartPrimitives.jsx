import { useState, useEffect, useCallback, useRef } from 'react';
import { apiFetch } from '../../api/client';
import { buildChartDrilldownSql } from './chartDrilldown.js';

const CHART_LAYOUT_STORAGE_KEY = 'oa-pipeline-chart-bar-layout';

/** Charts that support a ⚙ bar orientation override (stored per pipeline + chart id). */
export const CHARTS_WITH_DISPLAY_CONFIG = new Set(['publications_by_year', 'work_types']);

function readBarLayout(pipelineId, chartId) {
  try {
    const raw = sessionStorage.getItem(CHART_LAYOUT_STORAGE_KEY);
    const o = raw ? JSON.parse(raw) : {};
    const v = o[`${pipelineId}:${chartId}`];
    return v === 'hbar' || v === 'bar' ? v : null;
  } catch {
    return null;
  }
}

function writeBarLayout(pipelineId, chartId, layout) {
  try {
    const raw = sessionStorage.getItem(CHART_LAYOUT_STORAGE_KEY);
    const o = raw ? JSON.parse(raw) : {};
    const k = `${pipelineId}:${chartId}`;
    if (layout == null) delete o[k];
    else o[k] = layout;
    sessionStorage.setItem(CHART_LAYOUT_STORAGE_KEY, JSON.stringify(o));
  } catch {
    /* ignore */
  }
}

const VBAR_TRACK_PX = 128;
const COL_W = 'clamp(2.75rem, 4vw, 4.5rem)';

export function HBarChart({ rows, xKey = 0, yKey = 1, maxRows = 40, onCountClick }) {
  if (!rows?.length) return <p className="text-xs text-gray-400 text-center py-4">No data</p>;
  const slice = rows.slice(0, maxRows);
  const maxVal = Math.max(...slice.map(r => Number(r[yKey]) || 0), 1);
  return (
    <div className="space-y-2 min-w-0">
      {slice.map((r, i) => {
        const pct = maxVal > 0 ? (Number(r[yKey]) / maxVal) * 100 : 0;
        const lab = String(r[xKey] ?? '');
        return (
          <div
            key={i}
            className="grid grid-cols-1 sm:grid-cols-[minmax(5rem,38%)_1fr_auto] gap-x-2 gap-y-1 items-center text-xs min-w-0"
          >
            <span
              className="text-gray-700 break-words sm:text-right line-clamp-2 sm:line-clamp-3 leading-snug"
              title={lab}>
              {lab}
            </span>
            <div className="min-w-[48px] h-6 sm:h-5 bg-gray-100 rounded-md overflow-hidden border border-gray-200/80">
              <div
                className="h-full bg-blue-500 rounded-md transition-all duration-300"
                style={{ width: `${pct}%` }}
              />
            </div>
            <button
              type="button"
              onClick={() => onCountClick?.(r)}
              className="text-blue-700 underline shrink-0 text-right tabular-nums sm:w-16 justify-self-end sm:justify-self-auto"
              title="Open matching records"
            >
              {Number(r[yKey]).toLocaleString()}
            </button>
          </div>
        );
      })}
    </div>
  );
}

export function VBarChart({ rows, xKey = 0, yKey = 1, maxRows = 48, onCountClick }) {
  if (!rows?.length) return <p className="text-xs text-gray-400 text-center py-4">No data</p>;
  const slice = rows.slice(0, maxRows);
  const maxVal = Math.max(...slice.map(r => Number(r[yKey]) || 0), 1);
  return (
    <div className="overflow-x-auto pb-1 pt-1 -mx-1 px-1">
      <div className="flex items-end gap-1.5" style={{ minHeight: VBAR_TRACK_PX + 40 }}>
        {slice.map((r, i) => {
          const v = Number(r[yKey]) || 0;
          const barH = maxVal > 0 ? Math.round((v / maxVal) * VBAR_TRACK_PX) : 0;
          const label = String(r[xKey] ?? '');
          return (
            <div
              key={i}
              className="flex flex-col items-center gap-1.5 shrink-0"
              style={{ width: COL_W, minWidth: '2.75rem', maxWidth: '5rem' }}
            >
              <div
                className="relative w-full rounded-t-md border border-gray-200/80 bg-gray-100 overflow-hidden"
                style={{ height: VBAR_TRACK_PX }}
              >
                <div
                  className="absolute bottom-0 left-0 right-0 bg-indigo-500 rounded-t-md pointer-events-none transition-all duration-300"
                  style={{ height: Math.max(barH, v > 0 ? 3 : 0) }}
                />
                <button
                  type="button"
                  title={`${label}: ${v} — click to drill down`}
                  onClick={() => onCountClick?.(r)}
                  className="absolute inset-0 w-full h-full bg-transparent hover:ring-2 hover:ring-indigo-400 ring-inset rounded-t-md cursor-pointer"
                />
              </div>
              <span
                className="text-[9px] leading-tight text-gray-600 text-center break-words w-full line-clamp-4 px-px"
                title={label}>
                {label}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const PIE_HEX = ['#3b82f6', '#22c55e', '#eab308', '#a855f7', '#ef4444', '#6366f1', '#ec4899', '#f97316', '#14b8a6', '#06b6d4'];

/** SVG pie + scrollable legend (used for Open Access and other pie renderer charts). */
export function PieSvgChart({ rows, xKey = 0, yKey = 1, onCountClick, size = 168 }) {
  if (!rows?.length) return <p className="text-xs text-gray-400 text-center py-4">No data</p>;
  const total = rows.reduce((s, r) => s + (Number(r[yKey]) || 0), 0);
  if (total <= 0) return <PieTable rows={rows} onCountClick={onCountClick} />;

  const slices = rows
    .map((row, idx) => ({ row, idx, v: Number(row[yKey]) || 0 }))
    .filter((s) => s.v > 0);

  const cx = size / 2;
  const cy = size / 2;
  const rad = size * 0.36;
  let angle = -Math.PI / 2;
  const arcs = [];
  slices.forEach((s, j) => {
    const sweep = (s.v / total) * 2 * Math.PI;
    const a0 = angle;
    const a1 = angle + sweep;
    angle = a1;
    const large = sweep > Math.PI ? 1 : 0;
    const x0 = cx + rad * Math.cos(a0);
    const y0 = cy + rad * Math.sin(a0);
    const x1 = cx + rad * Math.cos(a1);
    const y1 = cy + rad * Math.sin(a1);
    const d = `M ${cx} ${cy} L ${x0} ${y0} A ${rad} ${rad} 0 ${large} 1 ${x1} ${y1} Z`;
    const color = PIE_HEX[j % PIE_HEX.length];
    arcs.push({ d, color, row: s.row, key: s.idx });
  });

  return (
    <div className="flex flex-col sm:flex-row gap-4 items-start min-w-0">
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="shrink-0 mx-auto sm:mx-0"
        role="img"
        aria-label="Pie chart"
      >
        {arcs.map((a) => (
          <path
            key={a.key}
            d={a.d}
            fill={a.color}
            stroke="#fff"
            strokeWidth="1"
            className="cursor-pointer hover:opacity-85 transition-opacity"
            onClick={() => onCountClick?.(a.row)}
          />
        ))}
      </svg>
      <div className="flex-1 min-w-0 w-full max-h-56 overflow-y-auto space-y-1 pr-1 text-xs border border-gray-100 rounded-lg p-2 bg-gray-50/50">
        {slices.map((s, j) => {
          const pct = (s.v / total) * 100;
          return (
            <button
              key={s.idx}
              type="button"
              onClick={() => onCountClick?.(s.row)}
              className="w-full flex items-center gap-2 text-left rounded px-1 py-0.5 hover:bg-white border border-transparent hover:border-gray-200"
            >
              <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: PIE_HEX[j % PIE_HEX.length] }} />
              <span className="flex-1 truncate text-gray-800" title={String(s.row[xKey])}>{s.row[xKey]}</span>
              <span className="text-gray-500 tabular-nums shrink-0">{pct.toFixed(1)}%</span>
              <span className="text-blue-700 underline tabular-nums shrink-0">{s.v.toLocaleString()}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function PieTable({ rows, xKey = 0, yKey = 1, onCountClick }) {
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
          <button
            type="button"
            onClick={() => onCountClick?.(r)}
            className="text-blue-700 underline w-14 text-right"
            title="Open matching records"
          >
            {Number(r[yKey]).toLocaleString()}
          </button>
        </div>
      ))}
    </div>
  );
}

export function HeatmapTable({ rows, onCountClick }) {
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
                    {v > 0 ? (
                      <button type="button" className="underline text-blue-800" onClick={() => onCountClick?.([x, y, v])}>
                        {v}
                      </button>
                    ) : ''}
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

export function ChartResult({ renderer, rows, onCountClick }) {
  if (!rows?.length) return <p className="text-xs text-gray-400 text-center py-4">No data</p>;
  if (renderer === 'pie') return <PieSvgChart rows={rows} onCountClick={onCountClick} />;
  if (renderer === 'heatmap' || renderer === 'cooccurrence_3d' || renderer === 'cooccurrence_3d_bar') {
    return <HeatmapTable rows={rows} onCountClick={onCountClick} />;
  }
  if (renderer === 'stacked') return <StackedBarChart rows={rows} />;
  if (renderer === 'bar') return <VBarChart rows={rows} onCountClick={onCountClick} />;
  return <HBarChart rows={rows} onCountClick={onCountClick} />;
}

export function ChartCard({
  chart,
  pipelineId,
  subsetId = null,
  topN = 20,
  cutoffYear = 2000,
  onDrilldown,
}) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const [cfgOpen, setCfgOpen] = useState(false);
  const [barLayout, setBarLayout] = useState(() => readBarLayout(pipelineId, chart.id));
  const cfgRef = useRef(null);

  const showDisplayConfig = CHARTS_WITH_DISPLAY_CONFIG.has(chart.id);

  useEffect(() => {
    setBarLayout(readBarLayout(pipelineId, chart.id));
  }, [pipelineId, chart.id]);

  useEffect(() => {
    if (!cfgOpen) return;
    const close = (e) => {
      if (cfgRef.current && !cfgRef.current.contains(e.target)) setCfgOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [cfgOpen]);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
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
      if (!res.ok) { setError(d.detail || 'Query failed'); setData(null); return; }
      setData(d);
    } catch (e) {
      setError(e.message);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [pipelineId, chart.id, chart.sql, subsetId, topN, cutoffYear]);

  useEffect(() => {
    if (!open) return;
    load();
  }, [open, load]);

  const rows = data?.rows || [];
  const renderer = (() => {
    if (showDisplayConfig && barLayout) return barLayout;
    return chart.renderer;
  })();

  const handleDrill = (r) => {
    const sql = buildChartDrilldownSql({ chartId: chart.id, row: r, cutoffYear });
    if (sql) onDrilldown?.(sql);
  };

  return (
    <div id={`chart-${chart.id}`} className="border border-gray-200 rounded-xl overflow-hidden scroll-mt-4">
      <div className="flex items-stretch bg-white">
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          className="flex-1 flex items-center justify-between px-4 py-3 hover:bg-gray-50 transition text-left min-w-0">
          <div className="min-w-0">
            <span className="font-medium text-gray-900 text-sm">{chart.title}</span>
            {chart.llm_only && (
              <span className="ml-2 text-xs bg-purple-100 text-purple-700 px-1.5 py-0.5 rounded-full">LLM</span>
            )}
            {showDisplayConfig && barLayout && (
              <span className="ml-2 text-[10px] text-gray-400 font-normal">
                ({barLayout === 'hbar' ? 'horizontal' : 'vertical'} bars)
              </span>
            )}
          </div>
          <span className="text-gray-400 text-xs shrink-0 ml-2">{open ? '▲' : '▼'}</span>
        </button>
        {showDisplayConfig && (
          <div className="relative shrink-0 border-l border-gray-100" ref={cfgRef}>
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); setCfgOpen(c => !c); }}
              className="h-full px-3 text-gray-400 hover:text-gray-700 hover:bg-gray-50 text-sm leading-none"
              title="Chart display options"
              aria-label="Chart display options"
            >
              ⚙
            </button>
            {cfgOpen && (
              <div
                className="absolute right-0 top-full mt-1 z-30 w-52 bg-white border border-gray-200 rounded-lg shadow-lg p-3 text-xs"
                onMouseDown={e => e.stopPropagation()}
              >
                <div className="font-semibold text-gray-800 mb-2">Bar layout</div>
                <p className="text-[10px] text-gray-500 mb-2">Applies to this chart in this browser only.</p>
                <div className="space-y-1.5">
                  {[
                    { id: null, label: `Default (${chart.renderer === 'hbar' ? 'horizontal' : 'vertical'})` },
                    { id: 'bar', label: 'Vertical bars' },
                    { id: 'hbar', label: 'Horizontal bars' },
                  ].map((opt) => (
                    <label key={String(opt.id)} className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name={`layout-${chart.id}`}
                        checked={barLayout === opt.id}
                        onChange={() => {
                          setBarLayout(opt.id);
                          writeBarLayout(pipelineId, chart.id, opt.id);
                          setCfgOpen(false);
                        }}
                      />
                      <span>{opt.label}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {open && (
        <div className="px-3 pb-3 pt-1 border-t border-gray-100 bg-white">
          {loading && <p className="text-xs text-gray-400 py-4 text-center">Loading…</p>}
          {error && <p className="text-xs text-red-500 py-2">{error}</p>}
          {data && !loading && (
            <>
              <div className="max-w-xl mx-auto w-full max-h-64 overflow-auto">
                <ChartResult renderer={renderer} rows={rows} onCountClick={handleDrill} />
              </div>
              <p className="text-xs text-gray-400 mt-2 text-right">{rows.length} rows</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
