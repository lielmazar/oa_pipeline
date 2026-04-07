import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';
import KeywordStage    from '../stages/KeywordStage';
import SearchStage     from '../stages/SearchStage';
import AnalysisStage   from '../stages/AnalysisStage';
import LlmStage        from '../stages/LlmStage';
import SummaryStage    from '../stages/SummaryStage';

const STAGE_ORDER = [
  'keyword_selection',
  'openalex_search',
  'analysis',
  'llm_classification',
  'llm_analysis',
  'summary',
];

const STAGE_LABELS = {
  keyword_selection:  'Keywords',
  openalex_search:    'Filter Run',
  analysis:           'Analysis',
  llm_classification: 'LLM',
  llm_analysis:       'LLM Analysis',
  summary:            'Summary',
};

function StageComponent({ stage, pipeline, onPipelineChange, onFilterRunChange }) {
  switch (stage) {
    case 'keyword_selection':  return <KeywordStage  pipeline={pipeline} onChange={onPipelineChange} />;
    case 'openalex_search':    return <SearchStage   pipeline={pipeline} onChange={onPipelineChange} onRunMetaChange={onFilterRunChange} />;
    case 'analysis':           return <AnalysisStage pipeline={pipeline} llmOnly={false} />;
    case 'llm_classification': return <LlmStage      pipeline={pipeline} onChange={onPipelineChange} />;
    case 'llm_analysis':       return <AnalysisStage pipeline={pipeline} llmOnly={true} />;
    case 'summary':            return <SummaryStage  pipeline={pipeline} />;
    default: return <div className="p-8 text-gray-400 text-sm">Unknown stage: {stage}</div>;
  }
}

export default function PipelineView({ initialPipeline, onBack, onFilterRunChange }) {
  const [pipeline, setPipeline] = useState(initialPipeline);
  const [advancing, setAdvancing] = useState(false);
  const [goingBack, setGoingBack] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}`);
      if (res.ok) setPipeline(await res.json());
    } catch {}
  }, [pipeline.id]);

  useEffect(() => { refresh(); }, [refresh]);

  const advance = async () => {
    setAdvancing(true);
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/advance`, { method: 'POST' });
      if (res.ok) setPipeline(await res.json());
    } finally { setAdvancing(false); }
  };

  const goBack = async () => {
    setGoingBack(true);
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/go-back`, { method: 'POST' });
      if (res.ok) setPipeline(await res.json());
    } finally { setGoingBack(false); }
  };

  const currentIdx = STAGE_ORDER.indexOf(pipeline.current_stage);

  return (
    <div className="max-w-6xl mx-auto px-4 py-4">
      {/* Header */}
      <div className="flex items-center gap-3 mb-4">
        <button onClick={onBack}
          className="text-sm text-gray-500 hover:text-gray-900 transition flex items-center gap-1">
          ← Pipelines
        </button>
        <span className="text-gray-300">/</span>
        <span className="text-xs text-gray-500 border border-gray-200 px-1.5 py-0.5 rounded">#{pipeline.id}</span>
        <h1 className="font-bold text-gray-900 text-lg truncate">{pipeline.name}</h1>
        {pipeline.forked_from_id && (
          <span className="text-xs text-gray-400 border border-gray-200 px-1.5 py-0.5 rounded">forked</span>
        )}
      </div>

      {/* Stage breadcrumb */}
      <div className="flex items-center gap-0 mb-6 bg-white border border-gray-200 rounded-xl overflow-hidden">
        {STAGE_ORDER.map((s, i) => {
          const isCurrent = s === pipeline.current_stage;
          const isDone    = i < currentIdx;
          const isLocked  = i > currentIdx;
          return (
            <div key={s}
              className={`flex-1 text-center text-xs py-3 border-r border-gray-200 last:border-r-0 transition
                ${isCurrent ? 'bg-blue-600 text-white font-semibold' :
                  isDone    ? 'bg-green-50 text-green-700' :
                              'text-gray-400'}`}>
              {isDone && <span className="mr-1">✓</span>}
              {STAGE_LABELS[s]}
            </div>
          );
        })}
      </div>

      {/* Stage content */}
      <StageComponent
        stage={pipeline.current_stage}
        pipeline={pipeline}
        onPipelineChange={setPipeline}
        onFilterRunChange={onFilterRunChange}
      />

      {/* Navigation buttons */}
      <div className="flex justify-between items-center mt-6 pt-4 border-t border-gray-200">
        <button
          onClick={goBack}
          disabled={goingBack || currentIdx === 0}
          className="text-sm px-4 py-2 border rounded-lg hover:bg-gray-50 disabled:opacity-30 disabled:cursor-not-allowed transition">
          {goingBack ? '…' : '← Back'}
        </button>
        <span className="text-xs text-gray-400">
          Stage {currentIdx + 1} of {STAGE_ORDER.length}
        </span>
        <button
          onClick={advance}
          disabled={advancing || currentIdx === STAGE_ORDER.length - 1}
          className="text-sm px-5 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-30 disabled:cursor-not-allowed transition font-medium">
          {advancing ? '…' : 'Next →'}
        </button>
      </div>
    </div>
  );
}
