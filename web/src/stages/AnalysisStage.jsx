import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';
import DatasetAccessSection from './analysis/DatasetAccessSection.jsx';
import ExplorerSection from './analysis/ExplorerSection.jsx';
import ResearchDbViewer from './analysis/ResearchDbViewer.jsx';

export default function AnalysisStage({ pipeline, llmOnly, onOpenQA }) {
  const [stats, setStats] = useState(null);
  const [selectedSubsetId, setSelectedSubsetId] = useState(null);
  const [selectedSubsetMeta, setSelectedSubsetMeta] = useState(null);
  const [viewerSubset, setViewerSubset] = useState(null);
  const [subsetsKey, setSubsetsKey] = useState(0);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipeline.id}/stats`)
      .then(r => r.ok ? r.json() : null)
      .then(setStats);
  }, [pipeline.id]);

  useEffect(() => {
    if (!selectedSubsetId) {
      setSelectedSubsetMeta(null);
      return;
    }
    apiFetch(`/api/pipeline/${pipeline.id}/subsets/summary`)
      .then(r => r.ok ? r.json() : null)
      .then(s => {
        const sub = (s?.subsets || []).find(x => x.id === selectedSubsetId);
        setSelectedSubsetMeta(sub || null);
      })
      .catch(() => setSelectedSubsetMeta(null));
  }, [pipeline.id, selectedSubsetId, subsetsKey]);

  const refreshSubsets = useCallback(() => setSubsetsKey(k => k + 1), []);

  const exportSubsetIds = async () => {
    if (!selectedSubsetId) return;
    const res = await fetch(
      `/api/pipeline/${pipeline.id}/subsets/${selectedSubsetId}/export-ids`,
      { credentials: 'same-origin' },
    );
    if (!res.ok) {
      const t = await res.text();
      alert(t || 'Export failed');
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `subset_${selectedSubsetId}_openalex_ids.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const openQA = () => {
    onOpenQA?.({ pipelineId: pipeline.id, subsetId: selectedSubsetId });
  };

  return (
    <div className="space-y-4">
      {stats?.exists && (
        <div className="bg-white border border-gray-200 rounded-xl p-4 flex gap-6 text-sm flex-wrap">
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

      {selectedSubsetMeta && (
        <div className="bg-blue-50 border border-blue-200 rounded-xl px-4 py-3 flex flex-wrap items-center gap-3 text-sm">
          <div className="min-w-0 flex-1">
            <span className="text-blue-800 font-medium">Active subset: </span>
            <span className="text-gray-900 font-semibold">{selectedSubsetMeta.name}</span>
            {selectedSubsetMeta.row_count != null && (
              <span className="text-blue-900/80 ml-2">
                · {selectedSubsetMeta.row_count.toLocaleString()} rows
              </span>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={exportSubsetIds}
              className="text-xs px-3 py-1.5 bg-white border border-blue-300 text-blue-800 rounded-lg hover:bg-blue-100 font-medium"
            >
              Export openalex IDs (CSV)
            </button>
            <button
              type="button"
              onClick={openQA}
              className="text-xs px-3 py-1.5 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 font-medium"
            >
              Open QA / IRR
            </button>
          </div>
        </div>
      )}

      {!llmOnly && (
        <p className="text-xs text-gray-500 bg-gray-50 border border-gray-100 rounded-lg px-3 py-2">
          Charts that use LLM columns (fuel, ML role, etc.) appear after you run LLM classification and have processed records.
        </p>
      )}
      {llmOnly && stats?.llm_processed_count === 0 && (
        <p className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
          No LLM-processed records yet. Run classification from the LLM stage, then return here for LLM-only charts.
        </p>
      )}

      <DatasetAccessSection
        key={subsetsKey}
        pipelineId={pipeline.id}
        selectedSubsetId={selectedSubsetId}
        onSelectSubset={setSelectedSubsetId}
        onOpenViewer={setViewerSubset}
      />

      <ExplorerSection
        pipelineId={pipeline.id}
        subsetId={selectedSubsetId}
        llmOnly={llmOnly}
      />

      {viewerSubset && (
        <ResearchDbViewer
          pipelineId={pipeline.id}
          subset={viewerSubset}
          llmOnly={llmOnly}
          onClose={() => setViewerSubset(null)}
          onSubsetSaved={refreshSubsets}
        />
      )}
    </div>
  );
}
