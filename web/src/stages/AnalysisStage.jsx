import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';
import DatasetAccessSection from './analysis/DatasetAccessSection.jsx';
import ExplorerSection from './analysis/ExplorerSection.jsx';
import ResearchDbViewer from './analysis/ResearchDbViewer.jsx';

export default function AnalysisStage({ pipeline, llmOnly }) {
  const [selectedSubsetId, setSelectedSubsetId] = useState(null);
  const [selectedSubsetMeta, setSelectedSubsetMeta] = useState(null);
  const [viewerSubset, setViewerSubset] = useState(null);
  const [viewerCustomSql, setViewerCustomSql] = useState('');
  const [subsetsKey, setSubsetsKey] = useState(0);

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

  const openDrilldown = (sql) => {
    if (!selectedSubsetMeta) return;
    setViewerCustomSql(sql || '');
    setViewerSubset(selectedSubsetMeta);
  };

  return (
    <div className="space-y-4">
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
        metadataChartsOnly={!llmOnly}
        onDrilldown={openDrilldown}
      />

      {viewerSubset && (
        <ResearchDbViewer
          pipelineId={pipeline.id}
          subset={viewerSubset}
          llmOnly={llmOnly}
          onClose={() => setViewerSubset(null)}
          onSubsetSaved={refreshSubsets}
          initialCustomSql={viewerCustomSql}
        />
      )}
    </div>
  );
}
