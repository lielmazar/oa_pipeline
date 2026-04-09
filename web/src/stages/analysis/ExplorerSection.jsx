import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../api/client';
import { ChartCard, ChartResult } from './chartPrimitives.jsx';

function OrthogonalityPanel({ pipelineId, subsetId }) {
  const [schema, setSchema] = useState([]);
  const [columnX, setColumnX] = useState('');
  const [columnY, setColumnY] = useState('');
  const [jsonX, setJsonX] = useState(true);
  const [jsonY, setJsonY] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipelineId}/schema`)
      .then(r => r.ok ? r.json() : [])
      .then(s => {
        setSchema(s);
        if (s.length >= 2) {
          setColumnX(prev => prev || s[0].name);
          setColumnY(prev => prev || s[1].name);
        }
      })
      .catch(() => {});
  }, [pipelineId]);

  const run = async () => {
    if (!subsetId || !columnX || !columnY) return;
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const res = await apiFetch(`/api/pipeline/${pipelineId}/orthogonality/pair`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          column_x: columnX,
          column_y: columnY,
          json_x: jsonX,
          json_y: jsonY,
          subset_id: subsetId,
        }),
      });
      const d = await res.json();
      if (!res.ok) { setError(d.detail || 'Request failed'); return; }
      setResult(d);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const colOpts = schema.map(c => c.name);

  return (
    <div className="space-y-4 text-xs">
      <p className="text-gray-600">
        Chi-square independence test on the same cell counts as a heatmap (exploded JSON columns optional).
        Low p-value suggests association (dimensions are not independent in this subset).
      </p>
      <div className="grid sm:grid-cols-2 gap-3">
        <label className="block">
          <span className="text-gray-500 font-medium">Dimension A</span>
          <select value={columnX} onChange={e => setColumnX(e.target.value)} className="mt-1 w-full border rounded-lg px-2 py-2">
            {colOpts.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="text-gray-500 font-medium">Dimension B</span>
          <select value={columnY} onChange={e => setColumnY(e.target.value)} className="mt-1 w-full border rounded-lg px-2 py-2">
            {colOpts.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={jsonX} onChange={e => setJsonX(e.target.checked)} />
          <span className="text-gray-600">A is JSON array (json_each)</span>
        </label>
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={jsonY} onChange={e => setJsonY(e.target.checked)} />
          <span className="text-gray-600">B is JSON array</span>
        </label>
      </div>
      <button type="button" onClick={run} disabled={loading || !subsetId}
        className="text-sm px-4 py-2 bg-slate-700 text-white rounded-lg hover:bg-slate-800 disabled:opacity-40 font-medium">
        {loading ? 'Computing…' : 'Run test'}
      </button>
      {error && <p className="text-red-600">{error}</p>}
      {result && (
        <div className="space-y-3 border border-gray-200 rounded-xl p-4 bg-gray-50/50">
          <p className="text-sm text-gray-800 font-medium">{result.interpretation}</p>
          {result.stats && !result.stats.error && (
            <div className="flex flex-wrap gap-4 text-gray-700">
              <span>χ² = <strong>{result.stats.chi2}</strong></span>
              <span>df = {result.stats.df}</span>
              <span>p ≈ <strong>{result.stats.p_value ?? '—'}</strong></span>
              <span>Cramér&apos;s V ≈ <strong>{result.stats.cramers_v ?? '—'}</strong></span>
              <span>n = {result.stats.n}</span>
            </div>
          )}
          {result.stats?.error && <p className="text-amber-800">{result.stats.error}</p>}
          {result.row_labels?.length > 0 && result.matrix && (
            <div className="overflow-x-auto max-h-64 overflow-y-auto border border-gray-200 rounded-lg bg-white">
              <table className="w-full text-[11px]">
                <thead>
                  <tr>
                    <th className="px-2 py-1 text-left bg-gray-100"> </th>
                    {result.col_labels.map(cl => (
                      <th key={cl} className="px-2 py-1 text-left bg-gray-100 truncate max-w-[120px]">{cl}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.row_labels.map((rl, i) => (
                    <tr key={rl} className="border-t border-gray-100">
                      <td className="px-2 py-1 font-medium text-gray-600 truncate max-w-[120px]">{rl}</td>
                      {result.matrix[i]?.map((cell, j) => (
                        <td key={j} className="px-2 py-1">{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
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

export default function ExplorerSection({ pipelineId, subsetId, llmOnly }) {
  const [tab, setTab] = useState('default');
  const [charts, setCharts] = useState([]);
  const [saved, setSaved] = useState([]);
  const [topN, setTopN] = useState(20);
  const [cutoffYear, setCutoffYear] = useState(2000);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipelineId}/charts`)
      .then(r => r.ok ? r.json() : [])
      .then(c => setCharts(Array.isArray(c) ? c : []));
  }, [pipelineId, llmOnly]);

  const loadSaved = useCallback(() => {
    apiFetch(`/api/pipeline/${pipelineId}/saved-charts`)
      .then(r => r.ok ? r.json() : [])
      .then(setSaved);
  }, [pipelineId]);

  useEffect(() => { if (tab === 'saved') loadSaved(); }, [tab, loadSaved]);

  const visibleCharts = llmOnly ? charts.filter(c => c.llm_only) : charts;
  const metaCharts = visibleCharts.filter(c => !c.llm_only);
  const llmCharts = visibleCharts.filter(c => c.llm_only);

  const Tab = ({ id, label }) => (
    <button type="button" onClick={() => setTab(id)}
      className={`text-sm px-4 py-2 border-b-2 transition font-medium ${
        tab === id ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-700'
      }`}>
      {label}
    </button>
  );

  return (
    <section className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <div className="px-4 pt-3 border-b border-gray-100">
        <h2 className="text-sm font-bold text-gray-900 mb-2">Explorer</h2>
        {(tab === 'dynamic' || tab === 'orthogonality') && !subsetId && (
          <p className="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 mb-2">
            Select a subset under Dataset access to run charts and orthogonality tests on that view.
          </p>
        )}
        <div className="flex flex-wrap border-b border-gray-100 -mb-px gap-x-1">
          <Tab id="dynamic" label="Dynamic charting" />
          <Tab id="default" label="Default charts" />
          <Tab id="orthogonality" label="Orthogonality" />
          <Tab id="saved" label="Saved charts" />
        </div>
      </div>
      <div className="p-4">
        {tab === 'dynamic' && (
          <DynamicChartPanel pipelineId={pipelineId} subsetId={subsetId} />
        )}
        {tab === 'default' && (
          <div className="space-y-6">
            {!subsetId && (
              <p className="text-xs text-gray-500 bg-gray-50 border border-gray-100 rounded-lg px-3 py-2">
                Select a subset to scope default charts to that slice (full dataset charts use all filtered records when no subset is selected — pick &quot;All&quot; or a saved subset).
              </p>
            )}
            {subsetId && (
              <div className="flex flex-wrap gap-3 text-xs items-end">
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
            {visibleCharts.length === 0 && (
              <p className="text-sm text-gray-400 text-center py-8">
                {llmOnly ? 'No LLM charts — run classification first.' : 'No charts.'}
              </p>
            )}
            {metaCharts.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Metadata & corpus</h3>
                <div className="space-y-2">
                  {metaCharts.map(c => (
                    <ChartCard key={c.id} chart={c} pipelineId={pipelineId} subsetId={subsetId}
                      topN={topN} cutoffYear={cutoffYear} />
                  ))}
                </div>
              </div>
            )}
            {llmCharts.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">LLM classification</h3>
                <div className="space-y-2">
                  {llmCharts.map(c => (
                    <ChartCard key={c.id} chart={c} pipelineId={pipelineId} subsetId={subsetId}
                      topN={topN} cutoffYear={cutoffYear} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
        {tab === 'orthogonality' && (
          <OrthogonalityPanel pipelineId={pipelineId} subsetId={subsetId} />
        )}
        {tab === 'saved' && (
          <div className="space-y-2">
            {saved.length === 0 && <p className="text-xs text-gray-400 text-center py-8">No saved charts yet.</p>}
            {saved.map(s => (
              <SavedChartCard key={s.id} pipelineId={pipelineId} item={s} onDeleted={loadSaved} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
