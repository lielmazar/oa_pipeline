// ── Pipeline header ───────────────────────────────────────────────────────────

function StageStepper({ currentStage, onNavigate, isOwner, navigating }) {
  const currentIdx = STAGE_IDS.indexOf(currentStage);
  return (
    <div className="flex items-center w-full overflow-x-auto pb-1">
      {STAGE_IDS.map((sid, i) => {
        const isCompleted = i < currentIdx;
        const isCurrent = i === currentIdx;
        const isClickable = isOwner && !isCurrent && !navigating;
        let dotCls = 'w-7 h-7 rounded-full border-2 flex items-center justify-center text-xs font-bold flex-shrink-0 transition-all duration-150 ';
        if (isCompleted)      dotCls += 'bg-green-500 border-green-500 text-white';
        else if (isCurrent)   dotCls += 'bg-blue-600 border-blue-600 text-white';
        else                  dotCls += 'bg-white border-gray-300 text-gray-400';
        if (isClickable)      dotCls += ' cursor-pointer hover:scale-110 hover:shadow-md';
        else if (!isCurrent)  dotCls += ' cursor-default';
        return (
          <div key={sid} className={`flex items-center ${i < STAGE_IDS.length - 1 ? 'flex-1' : ''}`}>
            <div className="flex flex-col items-center">
              <div className={dotCls}
                onClick={isClickable ? () => onNavigate(sid) : undefined}
                title={isClickable ? `Jump to ${STAGE_LABELS_MAP[sid]}` : undefined}>
                {navigating && isCurrent
                  ? <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin inline-block" />
                  : i + 1}
              </div>
              <span
                className={`mt-1 whitespace-nowrap ${isCurrent ? 'text-blue-700 font-semibold' : isClickable ? 'text-gray-500 hover:text-gray-700 cursor-pointer' : 'text-gray-400'}`}
                style={{ fontSize: '0.65rem' }}
                onClick={isClickable ? () => onNavigate(sid) : undefined}>
                {STAGE_LABELS_MAP[sid]}
              </span>
            </div>
            {i < STAGE_IDS.length - 1 && (
              <div className={`flex-1 h-0.5 mx-1 ${i < currentIdx ? 'bg-green-400' : 'bg-gray-200'}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function PipelineHeader({ pipeline, onUpdated }) {
  const [showFork, setShowFork] = useState(false);
  const [navigating, setNavigating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const navigate = async (stageId) => {
    if (navigating) return;
    setNavigating(true);
    try {
      const res = await apiFetch(`/api/pipelines/${pipeline.id}/set-stage`, {
        method: 'POST',
        body: JSON.stringify({ stage: stageId }),
      });
      const updated = await res.json();
      if (!res.ok) { alert(updated.error); return; }
      onUpdated(updated);
    } finally {
      setNavigating(false);
    }
  };

  const handleAdvance = () => navigate(pipeline.next_stage);
  const handleGoBack = () => navigate(pipeline.prev_stage);

  const handleDelete = async () => {
    if (!confirmDelete) { setConfirmDelete(true); return; }
    await apiFetch(`/api/pipelines/${pipeline.id}`, { method: 'DELETE' });
    onUpdated(null);
  };

  return (
    <div className="bg-white border-b border-gray-200 px-6 py-4 shadow-sm">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center gap-3 mb-3">
          <h2 className="text-xl font-bold">{pipeline.name}</h2>
          <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded">
            {pipeline.is_owner ? 'owner' : `by ${pipeline.owner_username || '?'}`}
          </span>
          <div className="ml-auto flex gap-2 flex-wrap items-center">
            {pipeline.is_owner && pipeline.prev_stage && (
              <button onClick={handleGoBack} disabled={navigating}
                className="text-sm text-gray-600 border border-gray-200 px-3 py-1 rounded-lg hover:bg-gray-50 transition disabled:opacity-40">
                ← {pipeline.prev_stage_label}
              </button>
            )}
            <button onClick={() => setShowFork(true)}
              className="text-sm text-purple-700 border border-purple-300 px-3 py-1 rounded-lg hover:bg-purple-50 transition">
              Fork
            </button>
            {pipeline.is_owner && (
              confirmDelete
                ? <span className="flex items-center gap-1">
                    <span className="text-xs text-red-600">Sure?</span>
                    <button onClick={handleDelete}
                      className="text-xs text-white bg-red-600 border border-red-600 px-2 py-1 rounded-lg hover:bg-red-700 transition">
                      Yes, delete
                    </button>
                    <button onClick={() => setConfirmDelete(false)}
                      className="text-xs text-gray-600 border border-gray-200 px-2 py-1 rounded-lg hover:bg-gray-50 transition">
                      Cancel
                    </button>
                  </span>
                : <button onClick={handleDelete}
                    className="text-sm text-red-600 border border-red-300 px-3 py-1 rounded-lg hover:bg-red-50 transition">
                    Delete
                  </button>
            )}
            {pipeline.is_owner && pipeline.next_stage && (
              <button onClick={handleAdvance} disabled={navigating}
                className="text-sm bg-green-600 text-white px-4 py-1 rounded-lg hover:bg-green-700 transition font-medium disabled:opacity-40 flex items-center gap-1.5">
                {navigating && <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />}
                {pipeline.next_stage_label} →
              </button>
            )}
          </div>
        </div>
        <StageStepper
          currentStage={pipeline.current_stage}
          onNavigate={navigate}
          isOwner={pipeline.is_owner}
          navigating={navigating}
        />
      </div>
      {showFork && (
        <ForkPipelineModal
          pipeline={pipeline}
          onForked={p => { setShowFork(false); onUpdated(p, 'fork'); }}
          onClose={() => setShowFork(false)}
        />
      )}
    </div>
  );
}

