import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';
import DatasetAccessSection from './analysis/DatasetAccessSection.jsx';
import ExplorerSection from './analysis/ExplorerSection.jsx';
import ResearchDbViewer from './analysis/ResearchDbViewer.jsx';
import { loadAnalysisStagePrefs, saveAnalysisStagePrefs } from './analysis/analysisStageStorage.js';

export default function AnalysisStage({ pipeline, llmOnly }) {
  const [selectedSubsetId, setSelectedSubsetIdState] = useState(() => loadAnalysisStagePrefs(pipeline.id).subsetId);
  const [datasetSectionOpen, setDatasetSectionOpenState] = useState(() => loadAnalysisStagePrefs(pipeline.id).datasetOpen);
  const [selectedSubsetMeta, setSelectedSubsetMeta] = useState(null);
  const [viewerSubset, setViewerSubset] = useState(null);
  const [viewerCustomSql, setViewerCustomSql] = useState('');
  const [subsetsKey, setSubsetsKey] = useState(0);

  useEffect(() => {
    const p = loadAnalysisStagePrefs(pipeline.id);
    setSelectedSubsetIdState(p.subsetId);
    setDatasetSectionOpenState(p.datasetOpen);
  }, [pipeline.id]);

  const setSelectedSubsetId = useCallback(
    (id) => {
      setSelectedSubsetIdState(id);
      saveAnalysisStagePrefs(pipeline.id, { subsetId: id });
    },
    [pipeline.id],
  );

  const setDatasetSectionOpen = useCallback(
    (open) => {
      setDatasetSectionOpenState(open);
      saveAnalysisStagePrefs(pipeline.id, { datasetOpen: open });
    },
    [pipeline.id],
  );

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
        sectionOpen={datasetSectionOpen}
        onSectionOpenChange={setDatasetSectionOpen}
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
