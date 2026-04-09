function formatDuration(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m > 0) return `${m}m ${String(sec).padStart(2, '0')}s`;
  return `${sec}s`;
}

export default function NavBar({ me, filterRunMeta, onShowHome, onRunPillClick, onLogout }) {
  const isRunning = !!filterRunMeta?.isRunning;
  const percent = Math.min(100, Math.max(0, filterRunMeta?.percent || 0));
  const pipelineId = filterRunMeta?.pipelineId;
  const etaText = filterRunMeta?.etaSec > 0 ? formatDuration(filterRunMeta.etaSec) : 'calculating…';
  const mbpsText = Number.isFinite(filterRunMeta?.mbPerSec) && filterRunMeta.mbPerSec > 0
    ? ` • ${filterRunMeta.mbPerSec.toFixed(2)} MB/s`
    : '';

  return (
    <nav className="h-12 bg-blue-700 text-white flex items-center px-4 gap-2 sticky top-0 z-30 shadow">
      <button
        type="button"
        onClick={onShowHome}
        className="font-bold text-sm mr-3 tracking-tight hover:text-blue-100 transition text-left">
        OA Pipelines
      </button>
      <div className="ml-auto flex items-center gap-3">
        {isRunning && (
          <button
            type="button"
            onClick={onRunPillClick}
            className="text-xs bg-green-500 text-white px-2 py-0.5 rounded-full animate-pulse whitespace-nowrap hover:bg-green-600 transition"
            title="Open running pipeline search stage"
          >
            #{pipelineId ?? '?'} Filtering {percent}% • ETA {etaText}{mbpsText}
          </button>
        )}
        <span className="text-xs text-blue-200">{me?.username}</span>
        <button onClick={onLogout}
          className="text-xs px-2 py-1 rounded border border-blue-500 hover:bg-blue-600 transition">
          Log out
        </button>
      </div>
    </nav>
  );
}
