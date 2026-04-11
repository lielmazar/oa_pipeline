import { useState, useEffect, useRef } from 'react';
import { apiFetch } from '../../api/client';
import {
  GroupedBarChart,
  MultiPieChart,
  HeatmapGrid,
} from './chartPrimitives.jsx';

export default function PresetCharts({ pipelineId, tab, subsets = [], onDrillDown }) {
  const [charts, setCharts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedChartId, setSelectedChartId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const url = tab
      ? `/api/pipeline/${pipelineId}/charts?tab=${tab}`
      : `/api/pipeline/${pipelineId}/charts`;
    apiFetch(url)
      .then(r => r.ok ? r.json() : [])
      .then(data => {
        if (cancelled) return;
        setCharts(data);
        if (data.length) setSelectedChartId(prev => data.find(c => c.id === prev) ? prev : data[0].id);
      })
      .catch(() => { if (!cancelled) setCharts([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [pipelineId, tab]);

  const selectedChart = charts.find(c => c.id === selectedChartId);

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-4">
        <div className="w-3.5 h-3.5 border-2 border-teal-500 border-t-transparent rounded-full animate-spin" />
        <span className="text-xs text-slate-400">Loading charts…</span>
      </div>
    );
  }
  if (!charts.length || !subsets.length) return null;

  return (
    <section className="space-y-2">
      <h3 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">
        {tab === 'llm' ? 'LLM Classification Charts' : 'Search Result Charts'}
      </h3>

      <div className="flex flex-wrap gap-1">
        {charts.map(c => {
          const active = c.id === selectedChartId;
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => setSelectedChartId(c.id)}
              className={`text-[11px] px-2.5 py-1 rounded-full border transition-all font-medium
                ${active
                  ? 'bg-teal-600 text-white border-teal-600'
                  : 'bg-white text-slate-500 border-slate-200 hover:border-teal-400 hover:text-teal-700'
                }`}
            >
              {c.title}
            </button>
          );
        })}
      </div>

      {selectedChart && (
        <UnifiedChartView
          key={selectedChart.id}
          chart={selectedChart}
          pipelineId={pipelineId}
          subsets={subsets}
          onDrillDown={onDrillDown}
        />
      )}
    </section>
  );
}

function UnifiedChartView({ chart, pipelineId, subsets, onDrillDown: parentDrillDown }) {
  const [subsetData, setSubsetData] = useState({});
  const [heatmapSubsetId, setHeatmapSubsetId] = useState(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    const results = {};
    subsets.forEach(s => { results[s.id] = { loading: true, rows: null, error: null }; });
    setSubsetData({ ...results });

    const controller = new AbortController();

    subsets.forEach(async (subset) => {
      try {
        const res = await apiFetch(`/api/pipeline/${pipelineId}/query`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sql: chart.sql, subset_id: subset.id }),
          signal: controller.signal,
        });
        const d = await res.json();
        results[subset.id] = res.ok
          ? { loading: false, rows: d.rows || [], error: null }
          : { loading: false, rows: null, error: d.detail || 'Query failed' };
      } catch (e) {
        if (e.name !== 'AbortError') {
          results[subset.id] = { loading: false, rows: null, error: e.message };
        }
      }
      if (mountedRef.current) setSubsetData({ ...results });
    });

    return () => { mountedRef.current = false; controller.abort(); };
  }, [chart.sql, pipelineId, subsets]);

  const anyLoading = Object.values(subsetData).some(d => d.loading);
  const subsetResults = subsets
    .filter(s => subsetData[s.id] && !subsetData[s.id].loading && subsetData[s.id].rows)
    .map(s => ({ subset: s, rows: subsetData[s.id].rows }));

  const isBar = !['pie', 'heatmap', 'cooccurrence_3d', 'cooccurrence_3d_bar', 'stacked'].includes(chart.renderer);
  const isPie = chart.renderer === 'pie';
  const isHeatmap = ['heatmap', 'cooccurrence_3d', 'cooccurrence_3d_bar', 'stacked'].includes(chart.renderer);

  const drillColumn = chart.drill_column;
  const drillColumns = chart.drill_columns;
  const canDrill = typeof parentDrillDown === 'function' && (drillColumn || drillColumns);

  const handleDrillDown = canDrill ? (clickedLabel) => {
    if (drillColumn) {
      const label = String(clickedLabel);
      const lteMatch = label.match(/^Unknown\/[≤<=]+(\d+)$/);
      if (lteMatch && drillColumn === 'publication_year') {
        parentDrillDown({ [drillColumn + '__lte']: lteMatch[1] });
      } else {
        parentDrillDown({ [drillColumn]: label });
      }
    }
  } : undefined;

  const handleHeatmapDrill = canDrill && drillColumns ? (xLabel, yLabel) => {
    const filters = {};
    if (drillColumns[0]) filters[drillColumns[0]] = String(xLabel);
    if (drillColumns[1]) filters[drillColumns[1]] = String(yLabel);
    parentDrillDown(filters);
  } : (canDrill && drillColumn ? (xLabel) => {
    parentDrillDown({ [drillColumn]: String(xLabel) });
  } : undefined);

  const errors = Object.entries(subsetData).filter(([, d]) => d.error);

  return (
    <div className="bg-white border border-slate-200/80 rounded-xl px-3 py-2.5 sm:px-4 sm:py-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <h4 className="text-sm font-semibold text-slate-800 truncate">
          {chart.title}
          {chart.llm_only && (
            <span className="ml-1.5 text-[9px] bg-violet-100 text-violet-700 px-1 py-px rounded-full font-medium align-middle">
              LLM
            </span>
          )}
        </h4>
        {!anyLoading && subsetResults.length > 0 && (
          <span className="flex items-center gap-2">
            {canDrill && (
              <span className="text-[9px] text-teal-500 font-medium">Click to view records</span>
            )}
            <span className="text-[10px] text-slate-400 shrink-0 tabular-nums">
              {subsetResults.reduce((s, r) => s + (r.rows?.length || 0), 0)} rows
            </span>
          </span>
        )}
      </div>

      {anyLoading && (
        <div className="flex items-center justify-center gap-2 py-6">
          <div className="w-3.5 h-3.5 border-2 border-teal-500 border-t-transparent rounded-full animate-spin" />
          <span className="text-xs text-slate-400">Loading…</span>
        </div>
      )}

      {!anyLoading && subsetResults.length === 0 && errors.length === 0 && (
        <p className="text-xs text-slate-400 py-6 text-center">No data</p>
      )}

      {!anyLoading && subsetResults.length > 0 && (
        <>
          {isBar && <GroupedBarChart subsetResults={subsetResults} onDrillDown={handleDrillDown} />}
          {isPie && <MultiPieChart subsetResults={subsetResults} onDrillDown={handleDrillDown} />}
          {isHeatmap && (
            <HeatmapGrid
              subsetResults={subsetResults}
              activeSubsetId={heatmapSubsetId ?? subsetResults[0]?.subset.id}
              onSubsetChange={setHeatmapSubsetId}
              onDrillDown={handleHeatmapDrill}
            />
          )}
        </>
      )}

      {errors.length > 0 && (
        <div className="mt-2 space-y-0.5">
          {errors.map(([id, d]) => (
            <p key={id} className="text-[10px] text-red-500">
              {subsets.find(s => String(s.id) === id)?.name || `Subset ${id}`}: {d.error}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
