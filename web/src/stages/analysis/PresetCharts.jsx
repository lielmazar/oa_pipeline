import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../api/client';
import {
  ChartCard,
  loadExplorerChartPrefs,
  mergeExplorerChartPrefs,
} from './chartPrimitives.jsx';

export default function PresetCharts({ pipelineId, tab, subsetId = null }) {
  const [charts, setCharts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [chartPrefs, setChartPrefs] = useState(() => loadExplorerChartPrefs(pipelineId));

  useEffect(() => {
    setChartPrefs(loadExplorerChartPrefs(pipelineId));
  }, [pipelineId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const url = tab
      ? `/api/pipeline/${pipelineId}/charts?tab=${tab}`
      : `/api/pipeline/${pipelineId}/charts`;
    apiFetch(url)
      .then(r => r.ok ? r.json() : [])
      .then(data => { if (!cancelled) setCharts(data); })
      .catch(() => { if (!cancelled) setCharts([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [pipelineId, tab]);

  const handlePrefsChange = useCallback((patch) => {
    const next = mergeExplorerChartPrefs(pipelineId, patch);
    setChartPrefs(next);
  }, [pipelineId]);

  if (loading) {
    return <p className="text-xs text-gray-400 py-4 text-center">Loading charts...</p>;
  }

  if (!charts.length) {
    return null;
  }

  return (
    <section className="space-y-2">
      <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wide px-1">
        {tab === 'llm' ? 'LLM Classification Charts' : 'Search Result Charts'}
      </h3>
      <div className="space-y-2">
        {charts.map(c => (
          <ChartCard
            key={c.id}
            chart={c}
            pipelineId={pipelineId}
            subsetId={subsetId}
            chartPrefs={chartPrefs}
            onChartPrefsChange={handlePrefsChange}
          />
        ))}
      </div>
    </section>
  );
}
