import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../api/client';
import { ChartCard, ChartResult } from './chartPrimitives.jsx';

const DEFAULT_CHART_ORDER = [
  'publications_by_year',
  'top_countries',
  'top_journals',
  'oa_status',
  'work_types',
  'top_institutions',
  'llm_scope',
  'llm_ml_role',
  'llm_ml_algorithm',
  'llm_ml_usage',
  'llm_ml_datasource',
  'llm_value_chain',
  'fuelclass_x_mlrole',
  'mlrole_x_algorithm',
  'fuelclass_x_algorithm',
  'fuelclass_x_datasource',
  'scope_x_mlrole',
  'scope_x_fuelclass',
  'fuel_subgroup_ml_cooccurrence',
  'fuel_impact_model_cooccurrence',
];

function formatStat(v) {
  if (v == null || Number.isNaN(v)) return '—';
  if (typeof v !== 'number') return String(v);
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 3 });
  if (Math.abs(v) >= 1) return v.toFixed(6).replace(/\.?0+$/, '');
  return v.toExponential(3);
}

function OrthogonalityPresetOverview({ pipelineId, subsetId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const goChart = (chartId) => {
    const el = document.getElementById(`chart-${chartId}`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  };

  useEffect(() => {
    if (!subsetId) {
      setData(null);
      return;
    }
    setLoading(true);
    const q = `?subset_id=${subsetId}`;
    apiFetch(`/api/pipeline/${pipelineId}/orthogonality/preset-overview${q}`)
      .then(r => r.ok ? r.json() : null)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [pipelineId, subsetId]);

  if (!subsetId) {
    return (
      <p className="text-xs text-gray-500">Select a subset to load the pairwise association overview.</p>
    );
  }
  if (loading) {
    return <p className="text-xs text-gray-400 py-4 text-center">Computing meaningful pairs…</p>;
  }
  const pairs = data?.pairs || [];
  return (
    <div className="space-y-3 text-xs">
      <p className="text-gray-600">
        Precomputed χ² / Cramér&apos;s V for curated dimension pairs (same cell counts as the co-occurrence heatmaps
        below). Use <em>View chart</em> to jump to the matching chart.
      </p>
      <div className="overflow-x-auto border border-gray-200 rounded-lg bg-white">
        <table className="w-full text-[11px]">
          <thead className="bg-gray-100">
            <tr>
              <th className="px-2 py-1 text-left">Pair</th>
              <th className="px-2 py-1 text-left">χ²</th>
              <th className="px-2 py-1 text-left">p</th>
              <th className="px-2 py-1 text-left">V</th>
              <th className="px-2 py-1 text-left">n</th>
              <th className="px-2 py-1 text-left">Heatmap</th>
            </tr>
          </thead>
          <tbody>
            {pairs.map((row) => (
              <tr key={row.id} className="border-t border-gray-100">
                <td className="px-2 py-1 align-top">{row.label}</td>
                <td className="px-2 py-1 font-mono">{row.error ? '—' : formatStat(row.stats?.chi2)}</td>
                <td className="px-2 py-1 font-mono">{row.error ? '—' : formatStat(row.stats?.p_value)}</td>
                <td className="px-2 py-1 font-mono">{row.error ? '—' : formatStat(row.stats?.cramers_v)}</td>
                <td className="px-2 py-1">{row.error ? '—' : row.stats?.n ?? '—'}</td>
                <td className="px-2 py-1">
                  {row.chart_id ? (
                    <button
                      type="button"
                      onClick={() => goChart(row.chart_id)}
                      className="text-blue-700 underline hover:text-blue-900"
                    >
                      View chart
                    </button>
                  ) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pairs.some((x) => x.error) && (
        <p className="text-[11px] text-amber-800 bg-amber-50 border border-amber-100 rounded px-2 py-1">
          Some pairs could not be computed (missing columns or insufficient categories). LLM charts require processed records.
        </p>
      )}
    </div>
  );
}


function DynamicChartPanel({ pipelineId, subsetId }) {
  const [schema, setSchema] = useState([]);
  const [chartType, setChartType] = useState('hbar');
  const [columnX, setColumnX] = useState('');
  const [columnY, setColumnY] = useState('');
  const [stackCol, setStackCol] = useState('');
  const [jsonX, setJsonX] = useState(false);
  const [jsonY, setJsonY] = useState(false);
  const [topN, setTopN] = useState(20);
  const [renderer, setRenderer] = useState('hbar');
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [saveName, setSaveName] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveOk, setSaveOk] = useState(false);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipelineId}/schema`)
      .then(r => r.ok ? r.json() : [])
      .then(s => {
        setSchema(s);
        if (s.length && !columnX) setColumnX(s[0].name);
      })
      .catch(() => {});
  }, [pipelineId, columnX]);

  useEffect(() => {
    if (chartType === 'heatmap' && schema.length >= 2 && (!columnY || columnY === columnX)) {
      const alt = schema.find(c => c.name !== columnX);
      if (alt) setColumnY(alt.name);
    }
    if (chartType === 'stacked' && schema.length >= 2 && (!stackCol || stackCol === columnX)) {
      const alt = schema.find(c => c.name !== columnX);
      if (alt) setStackCol(alt.name);
    }
  }, [chartType, schema, columnX, columnY, stackCol]);

  const run = async () => {
    if (!subsetId || !columnX) return;
    setLoading(true);
    setError('');
    setRows(null);
    try {
      const buildRes = await apiFetch(`/api/pipeline/${pipelineId}/dynamic-chart`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chart_type: chartType,
          column_x: columnX,
          column_y: chartType === 'heatmap' ? columnY : null,
          stack_column: chartType === 'stacked' ? stackCol : null,
          json_x: jsonX,
          json_y: chartType === 'heatmap' ? jsonY : false,
          top_n: topN,
        }),
      });
      const b = await buildRes.json();
      if (!buildRes.ok) { setError(b.detail || 'Build failed'); return; }
      setRenderer(b.renderer || 'hbar');
      const qRes = await apiFetch(`/api/pipeline/${pipelineId}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql: b.sql, subset_id: subsetId, max_rows: 5000 }),
      });
      const d = await qRes.json();
      if (!qRes.ok) { setError(d.detail || 'Query failed'); return; }
      setRows(d.rows || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const saveChart = async () => {
    if (!saveName.trim()) return;
    setSaving(true);
    setSaveOk(false);
    try {
      const res = await apiFetch(`/api/pipeline/${pipelineId}/saved-charts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: saveName.trim(),
          config: {
            kind: 'dynamic',
            subset_id: subsetId,
            chart_type: chartType,
            column_x: columnX,
            column_y: columnY || null,
            stack_column: stackCol || null,
            json_x: jsonX,
            json_y: chartType === 'heatmap' ? jsonY : false,
            top_n: topN,
          },
        }),
      });
      if (res.ok) {
        setSaveName('');
        setSaveOk(true);
        setTimeout(() => setSaveOk(false), 4000);
      }
    } finally {
      setSaving(false);
    }
  };

  const colOpts = schema.map(c => c.name);

  return (
    <div className="space-y-4">
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3 text-xs">
        <label className="block">
          <span className="text-gray-500 font-medium">Chart type</span>
          <select value={chartType} onChange={e => setChartType(e.target.value)}
            className="mt-1 w-full border rounded-lg px-2 py-2">
            <option value="bar">Vertical bars</option>
            <option value="hbar">Horizontal bars</option>
            <option value="pie">Pie / share</option>
            <option value="heatmap">Heatmap (2 columns)</option>
            <option value="stacked">Stacked (category × series)</option>
          </select>
        </label>
        <label className="block">
          <span className="text-gray-500 font-medium">Column (X / category)</span>
          <select value={columnX} onChange={e => setColumnX(e.target.value)} className="mt-1 w-full border rounded-lg px-2 py-2">
            {colOpts.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="text-gray-500 font-medium">Top N</span>
          <input type="number" min={1} max={500} value={topN} onChange={e => setTopN(Number(e.target.value) || 20)}
            className="mt-1 w-full border rounded-lg px-2 py-2" />
        </label>
        {chartType === 'heatmap' && (
          <label className="block">
            <span className="text-gray-500 font-medium">Column Y</span>
            <select value={columnY} onChange={e => setColumnY(e.target.value)} className="mt-1 w-full border rounded-lg px-2 py-2">
              {colOpts.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
        )}
        {chartType === 'stacked' && (
          <label className="block">
            <span className="text-gray-500 font-medium">Stack column</span>
            <select value={stackCol} onChange={e => setStackCol(e.target.value)} className="mt-1 w-full border rounded-lg px-2 py-2">
              {colOpts.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
        )}
        {(chartType === 'bar' || chartType === 'hbar' || chartType === 'pie') && (
          <label className="flex items-center gap-2 mt-6 cursor-pointer">
            <input type="checkbox" checked={jsonX} onChange={e => setJsonX(e.target.checked)} />
            <span className="text-gray-600">X is JSON array (json_each)</span>
          </label>
        )}
        {chartType === 'heatmap' && (
          <div className="sm:col-span-2 lg:col-span-3 flex flex-wrap gap-4 mt-1">
            <label className="flex items-center gap-2 cursor-pointer text-xs">
              <input type="checkbox" checked={jsonX} onChange={e => setJsonX(e.target.checked)} />
              <span className="text-gray-600">X is JSON array</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer text-xs">
              <input type="checkbox" checked={jsonY} onChange={e => setJsonY(e.target.checked)} />
              <span className="text-gray-600">Y is JSON array (e.g. llm_ml_role)</span>
            </label>
          </div>
        )}
      </div>
      <div className="flex flex-wrap gap-2 items-center">
        <button type="button" onClick={run} disabled={loading || !subsetId}
          className="text-sm px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-40 font-medium">
          {loading ? 'Running…' : 'Run chart'}
        </button>
        <input value={saveName} onChange={e => setSaveName(e.target.value)} placeholder="Save as…"
          className="text-sm border rounded-lg px-3 py-2 flex-1 min-w-[140px]" />
        <button type="button" onClick={saveChart} disabled={saving || !saveName.trim() || !rows?.length}
          className="text-sm px-3 py-2 border border-indigo-200 text-indigo-700 rounded-lg hover:bg-indigo-50 disabled:opacity-40">
          Save chart
        </button>
        {saveOk && <span className="text-xs text-green-600 self-center">Saved.</span>}
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
      {rows && (
        <div className="border border-gray-200 rounded-xl p-4 bg-white">
          <ChartResult renderer={renderer} rows={rows} />
          <p className="text-xs text-gray-400 mt-2 text-right">{rows.length} rows</p>
        </div>
      )}
    </div>
  );
}

function SavedChartCard({ pipelineId, item, onDeleted }) {
  const cfg = item.config || {};
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const [renderer, setRenderer] = useState('hbar');
  const [subsetMissing, setSubsetMissing] = useState(false);

  useEffect(() => {
    if (!cfg.subset_id) {
      setSubsetMissing(false);
      return;
    }
    apiFetch(`/api/pipeline/${pipelineId}/subsets/summary`)
      .then(r => r.ok ? r.json() : null)
      .then(s => {
        const ids = new Set((s?.subsets || []).map(x => x.id));
        setSubsetMissing(!ids.has(cfg.subset_id));
      })
      .catch(() => setSubsetMissing(false));
  }, [pipelineId, cfg.subset_id]);

  const run = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      if (cfg.kind === 'default' && cfg.chart_id) {
        const metaRes = await apiFetch(`/api/pipeline/${pipelineId}/charts/${encodeURIComponent(cfg.chart_id)}`);
        const meta = metaRes.ok ? await metaRes.json() : {};
        setRenderer(meta.renderer || 'hbar');
        const res = await apiFetch(`/api/pipeline/${pipelineId}/query`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            chart_id: cfg.chart_id,
            subset_id: cfg.subset_id,
            top_n: cfg.top_n ?? 20,
            cutoff_year: cfg.cutoff_year ?? 2000,
          }),
        });
        const d = await res.json();
        if (!res.ok) { setError(d.detail || 'Failed'); return; }
        setRows(d.rows || []);
      } else if (cfg.kind === 'dynamic') {
        const buildRes = await apiFetch(`/api/pipeline/${pipelineId}/dynamic-chart`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            chart_type: cfg.chart_type,
            column_x: cfg.column_x,
            column_y: cfg.column_y || null,
            stack_column: cfg.stack_column || null,
            json_x: !!cfg.json_x,
            json_y: !!cfg.json_y,
            top_n: cfg.top_n ?? 20,
          }),
        });
        const b = await buildRes.json();
        if (!buildRes.ok) { setError(b.detail || 'Build failed'); return; }
        setRenderer(b.renderer || 'hbar');
        const qRes = await apiFetch(`/api/pipeline/${pipelineId}/query`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sql: b.sql, subset_id: cfg.subset_id, max_rows: 5000 }),
        });
        const d = await qRes.json();
        if (!qRes.ok) { setError(d.detail || 'Query failed'); return; }
        setRows(d.rows || []);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [pipelineId, cfg]);

  const toggle = () => {
    if (!open) run();
    setOpen(o => !o);
  };

  const del = async () => {
    await apiFetch(`/api/pipeline/${pipelineId}/saved-charts/${item.id}`, { method: 'DELETE' });
    onDeleted();
  };

  return (
    <div className="border border-gray-200 rounded-xl overflow-hidden">
      <div className="flex items-stretch bg-white hover:bg-gray-50 border-b border-gray-100">
        <button type="button" onClick={toggle}
          className="flex-1 flex items-center justify-between px-4 py-3 text-left">
          <span className="font-medium text-gray-900 text-sm">{item.name}</span>
          <span className="text-gray-400 text-xs">{open ? '▲' : '▼'}</span>
        </button>
        <button type="button" onClick={del} className="px-3 text-xs text-red-500 hover:bg-red-50 shrink-0 border-l border-gray-100">
          Delete
        </button>
      </div>
      {open && (
        <div className="px-4 pb-4 border-t border-gray-100">
          {subsetMissing && cfg.subset_id && (
            <p className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 mb-2">
              Saved subset #{cfg.subset_id} no longer exists. Re-save the chart after selecting a valid subset.
            </p>
          )}
          {loading && <p className="text-xs text-gray-400 py-4 text-center">Loading…</p>}
          {error && <p className="text-xs text-red-500 py-2">{error}</p>}
          {rows && !loading && (
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

const CHART_CARD_WRAP = 'flex-1 min-w-[min(100%,340px)] max-w-full';

export default function ExplorerSection({ pipelineId, subsetId, llmOnly, metadataChartsOnly = false, onDrilldown }) {
  const [charts, setCharts] = useState([]);
  const [topN, setTopN] = useState(20);
  const [cutoffYear, setCutoffYear] = useState(2000);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipelineId}/charts`)
      .then(r => r.ok ? r.json() : [])
      .then(c => setCharts(Array.isArray(c) ? c : []));
  }, [pipelineId, llmOnly]);

  const visibleCharts = llmOnly ? charts.filter(c => c.llm_only) : charts;
  const chartRank = new Map(DEFAULT_CHART_ORDER.map((id, i) => [id, i]));
  const orderedCharts = [...visibleCharts].sort((a, b) => {
    const ra = chartRank.has(a.id) ? chartRank.get(a.id) : 999;
    const rb = chartRank.has(b.id) ? chartRank.get(b.id) : 999;
    return ra - rb;
  });
  const dependencyIds = new Set([
    'fuelclass_x_mlrole',
    'mlrole_x_algorithm',
    'fuelclass_x_algorithm',
    'fuelclass_x_datasource',
    'scope_x_mlrole',
    'scope_x_fuelclass',
    'fuel_subgroup_ml_cooccurrence',
    'fuel_impact_model_cooccurrence',
  ]);
  const metaCharts = orderedCharts.filter(c => !c.llm_only);
  const llmCharts = orderedCharts.filter(c => c.llm_only && !dependencyIds.has(c.id));
  const dependencyCharts = orderedCharts.filter(c => dependencyIds.has(c.id));

  return (
    <section className="bg-white border border-gray-200 rounded-xl p-4">
      <h2 className="text-sm font-bold text-gray-900 mb-3">
        {metadataChartsOnly ? 'Metadata & corpus charts' : 'Charts & orthogonality'}
      </h2>
      {metadataChartsOnly && (
        <p className="text-xs text-gray-600 bg-slate-50 border border-slate-100 rounded-lg px-3 py-2 mb-3">
          At this stage only corpus metadata views are shown. LLM cross-dimension charts and orthogonality tools appear in <strong>LLM Analysis</strong> after classification.
        </p>
      )}
      {!subsetId && (
        <p className="text-xs text-gray-500 bg-gray-50 border border-gray-100 rounded-lg px-3 py-2 mb-3">
          Select a subset to scope default charts to that slice (full dataset charts use all filtered records when no subset is selected — pick &quot;All&quot; or a saved subset).
        </p>
      )}
      {subsetId && (
        <div className="flex flex-wrap gap-3 text-xs items-end mb-4">
          <label className="block">
            <span className="text-gray-500">Top N (charts with top-N)</span>
            <input type="number" className="mt-1 border rounded px-2 py-1 w-24" value={topN} min={1} max={500}
              onChange={e => setTopN(Number(e.target.value) || 20)} />
          </label>
          <label className="block">
            <span className="text-gray-500">Year cutoff (publications by year)</span>
            <input type="number" className="mt-1 border rounded px-2 py-1 w-24" value={cutoffYear}
              onChange={e => setCutoffYear(Number(e.target.value) || 2000)} />
          </label>
        </div>
      )}

      <div className="flex flex-wrap gap-4 items-start">
        {visibleCharts.length === 0 && (
          <p className="text-sm text-gray-400 w-full text-center py-6 border border-dashed border-gray-200 rounded-xl bg-gray-50/50">
            {llmOnly ? 'No LLM charts — run classification first.' : 'No preset charts.'}
          </p>
        )}
        {metaCharts.length > 0 && (
          <h3 className="w-full basis-full text-xs font-semibold text-gray-500 uppercase tracking-wide">
            Metadata & corpus
          </h3>
        )}
        {metaCharts.map(c => (
          <div key={c.id} className={CHART_CARD_WRAP}>
            <ChartCard chart={c} pipelineId={pipelineId} subsetId={subsetId}
              topN={topN} cutoffYear={cutoffYear} onDrilldown={onDrilldown} />
          </div>
        ))}
        {!metadataChartsOnly && llmCharts.length > 0 && (
          <h3 className="w-full basis-full text-xs font-semibold text-gray-500 uppercase tracking-wide mt-1">
            LLM classification
          </h3>
        )}
        {!metadataChartsOnly && llmCharts.map(c => (
          <div key={c.id} className={CHART_CARD_WRAP}>
            <ChartCard chart={c} pipelineId={pipelineId} subsetId={subsetId}
              topN={topN} cutoffYear={cutoffYear} onDrilldown={onDrilldown} />
          </div>
        ))}
        {!metadataChartsOnly && dependencyCharts.length > 0 && (
          <>
            <h3 className="w-full basis-full text-xs font-semibold text-gray-500 uppercase tracking-wide mt-1">
              Cross-dimension dependency
            </h3>
            <p className="w-full basis-full text-[11px] text-gray-500 -mt-2 mb-0">
              Most meaningful for orthogonality context (association between dimensions).
            </p>
          </>
        )}
        {!metadataChartsOnly && dependencyCharts.map(c => (
          <div key={c.id} className={CHART_CARD_WRAP}>
            <ChartCard chart={c} pipelineId={pipelineId} subsetId={subsetId}
              topN={topN} cutoffYear={cutoffYear} onDrilldown={onDrilldown} />
          </div>
        ))}

        {!metadataChartsOnly && (
          <>
            <h3 className="w-full basis-full text-xs font-semibold text-gray-500 uppercase tracking-wide mt-2 border-t border-gray-100 pt-4">
              Orthogonality
            </h3>
            <div className={`${CHART_CARD_WRAP} border border-gray-200 rounded-xl p-3 bg-gray-50/60`}>
              <OrthogonalityPresetOverview pipelineId={pipelineId} subsetId={subsetId} />
            </div>
          </>
        )}
      </div>
    </section>
  );
}
