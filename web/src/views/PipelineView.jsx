import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';
import KeywordStage from '../stages/KeywordStage';
import SearchStage from '../stages/SearchStage';
import LlmStage from '../stages/LlmStage';
import QaStage from '../stages/QaStage';
import ExportTab from '../stages/ExportTab';
import PresetCharts from '../stages/analysis/PresetCharts.jsx';
import RecordsViewer from '../stages/RecordsViewer.jsx';

const TABS = [
  { id: 'search', label: 'Search' },
  { id: 'llm', label: 'LLM' },
  { id: 'data', label: 'Data' },
  { id: 'qa', label: 'QA' },
  { id: 'export', label: 'Export' },
];

export default function PipelineView({ initialPipeline, onBack, onFilterRunChange }) {
  const [pipeline, setPipeline] = useState(initialPipeline);
  const [activeTab, setActiveTab] = useState('search');
  const [subsets, setSubsets] = useState([]);
  const [dataFilters, setDataFilters] = useState(null);

  const handleDrillDown = useCallback((filters) => {
    setDataFilters(filters);
    setActiveTab('data');
  }, []);

  const refresh = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}`);
      if (res.ok) setPipeline(await res.json());
    } catch {}
  }, [pipeline.id]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    let cancelled = false;
    apiFetch(`/api/pipeline/${pipeline.id}/subsets/summary`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!cancelled && data?.subsets) setSubsets(data.subsets);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [pipeline.id]);

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button onClick={onBack}
          className="text-sm text-slate-500 hover:text-slate-900 transition flex items-center gap-1">
          ← Pipelines
        </button>
        <span className="text-slate-300">/</span>
        <span className="text-xs text-slate-500 border border-slate-200 px-1.5 py-0.5 rounded">#{pipeline.id}</span>
        <h1 className="font-bold text-slate-900 text-base truncate flex-1 min-w-0">{pipeline.name}</h1>
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-0 mb-4 bg-white border border-slate-200 rounded-lg overflow-hidden max-w-lg">
        {TABS.map(t => (
          <button
            key={t.id}
            type="button"
            onClick={() => setActiveTab(t.id)}
            className={`flex-1 text-center text-xs py-2 border-r border-slate-200 last:border-r-0 transition cursor-pointer font-medium
              ${activeTab === t.id
                ? 'bg-teal-600 text-white'
                : 'bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-700'}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'search' && (
        <div className="space-y-4">
          <KeywordStage pipeline={pipeline} onChange={setPipeline} />
          <SearchStage pipeline={pipeline} onChange={setPipeline} onRunMetaChange={onFilterRunChange} />
          <PresetCharts pipelineId={pipeline.id} tab="search" subsets={subsets} onDrillDown={handleDrillDown} />
        </div>
      )}

      {activeTab === 'llm' && (
        <div className="space-y-4">
          <LlmStage pipeline={pipeline} onChange={setPipeline} />
          <PresetCharts pipelineId={pipeline.id} tab="llm" subsets={subsets} onDrillDown={handleDrillDown} />
        </div>
      )}

      {activeTab === 'data' && (
        <RecordsViewer pipelineId={pipeline.id} subsets={subsets} initialFilters={dataFilters} />
      )}

      {activeTab === 'qa' && <QaStage pipeline={pipeline} />}
      {activeTab === 'export' && <ExportTab pipeline={pipeline} />}
    </div>
  );
}
