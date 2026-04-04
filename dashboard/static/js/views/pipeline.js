// ── Pipeline View ─────────────────────────────────────────────────────────────

function PipelineView({ initialPipeline, onBack, onNavigateTo }) {
  const [pipeline, setPipeline] = useState(initialPipeline);
  const [pipelineStatus, setPipelineStatus] = useState(null);
  const [jobRunning, setJobRunning] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/pipelines/${pipeline.id}/status`);
      setPipelineStatus(await res.json());
    } catch {}
  }, [pipeline.id]);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);
  useInterval(fetchStatus, jobRunning ? 3000 : 10000);

  const handleUpdated = (updated, action) => {
    if (!updated) { onBack(); return; }
    if (action === 'fork') { onNavigateTo(updated); return; }
    setPipeline(updated);
    fetchStatus();
  };

  const stage = pipeline.current_stage;

  return (
    <div>
      <div className="bg-white border-b border-gray-100 px-6 py-2">
        <div className="max-w-5xl mx-auto">
          <button onClick={onBack}
            className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800 transition py-1">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7"/>
            </svg>
            Back
          </button>
        </div>
      </div>
      <PipelineHeader pipeline={pipeline} onUpdated={handleUpdated} />
      <div className="max-w-5xl mx-auto px-6 py-6">
        {stage === 'keyword_selection' && (
          <KeywordStage pipeline={pipeline} onUpdate={p => setPipeline(p)} />
        )}
        {stage === 'openalex_search' && (
          <SearchStage pipeline={pipeline} pipelineStatus={pipelineStatus}
            onJobStart={() => setJobRunning(true)}
            onJobDone={() => { setJobRunning(false); fetchStatus(); }} />
        )}
        {stage === 'analysis' && (
          <ReportStage pipeline={pipeline} isLLM={false} pipelineStatus={pipelineStatus} />
        )}
        {stage === 'llm_classification' && (
          <LLMStage pipeline={pipeline} pipelineStatus={pipelineStatus} />
        )}
        {stage === 'llm_analysis' && (
          <ReportStage pipeline={pipeline} isLLM={true} pipelineStatus={pipelineStatus} />
        )}
        {stage === 'summary' && (
          <SummaryStage pipeline={pipeline} pipelineStatus={pipelineStatus} />
        )}
        {!STAGE_IDS.includes(stage) && (
          <div className="text-gray-500 text-sm">Unknown stage: {stage}</div>
        )}
      </div>
    </div>
  );
}

