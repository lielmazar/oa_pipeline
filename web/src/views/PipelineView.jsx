import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';
import KeywordStage from '../stages/KeywordStage';
import SearchStage from '../stages/SearchStage';
import LlmStage from '../stages/LlmStage';
import QaStage from '../stages/QaStage';
import ExportTab from '../stages/ExportTab';
import SubsetSelector from '../stages/analysis/SubsetSelector.jsx';
import PresetCharts from '../stages/analysis/PresetCharts.jsx';

const TABS = [
  { id: 'search', label: 'Search' },
  { id: 'llm', label: 'LLM' },
  { id: 'qa', label: 'QA' },
  { id: 'export', label: 'Export' },
];

export default function PipelineView({ initialPipeline, onBack, onFilterRunChange }) {
  const [pipeline, setPipeline] = useState(initialPipeline);
  const [activeTab, setActiveTab] = useState('search');
  const [subsetId, setSubsetId] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}`);
      if (res.ok) setPipeline(await res.json());
    } catch {}
  }, [pipeline.id]);

  useEffect(() => { refresh(); }, [refresh]);

  const switchTab = (tabId) => {
    setActiveTab(tabId);
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <button onClick={onBack}
          className="text-sm text-gray-500 hover:text-gray-900 transition flex items-center gap-1">
          ← Pipelines
        </button>
        <span className="text-gray-300">/</span>
        <span className="text-xs text-gray-500 border border-gray-200 px-1.5 py-0.5 rounded">#{pipeline.id}</span>
        <h1 className="font-bold text-gray-900 text-lg truncate flex-1 min-w-0">{pipeline.name}</h1>
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-0 mb-6 bg-white border border-gray-200 rounded-xl overflow-hidden">
        {TABS.map(t => (
          <button
            key={t.id}
            type="button"
            onClick={() => switchTab(t.id)}
            className={`flex-1 text-center text-sm py-3 border-r border-gray-200 last:border-r-0 transition cursor-pointer font-medium
              ${activeTab === t.id
                ? 'bg-blue-600 text-white'
                : 'bg-gray-50 text-gray-600 hover:bg-gray-100'}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'search' && (
        <div className="space-y-6">
          <KeywordStage pipeline={pipeline} onChange={setPipeline} />
          <SearchStage pipeline={pipeline} onChange={setPipeline} onRunMetaChange={onFilterRunChange} />
          <SubsetSelector pipelineId={pipeline.id} selectedSubsetId={subsetId} onSelectSubset={setSubsetId} />
          <PresetCharts pipelineId={pipeline.id} tab="search" subsetId={subsetId} />
        </div>
      )}

      {activeTab === 'llm' && (
        <div className="space-y-6">
          <LlmStage pipeline={pipeline} onChange={setPipeline} />
          <SubsetSelector pipelineId={pipeline.id} selectedSubsetId={subsetId} onSelectSubset={setSubsetId} />
          <PresetCharts pipelineId={pipeline.id} tab="llm" subsetId={subsetId} />
        </div>
      )}

      {activeTab === 'qa' && (
        <QaStage pipeline={pipeline} />
      )}

      {activeTab === 'export' && (
        <ExportTab pipeline={pipeline} />
      )}
    </div>
  );
}
