import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';
import { useInterval } from '../hooks/useInterval';

function ProgressBar({ percent }) {
  return (
    <div className="w-full bg-gray-100 rounded-full h-2">
      <div
        className="bg-blue-500 h-2 rounded-full transition-all duration-500"
        style={{ width: `${Math.min(100, percent || 0)}%` }}
      />
    </div>
  );
}

function formatDuration(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m ${String(sec).padStart(2, '0')}s`;
  if (m > 0) return `${m}m ${String(sec).padStart(2, '0')}s`;
  return `${sec}s`;
}

export default function SearchStage({ pipeline, onChange, onRunMetaChange }) {
  const [runState, setRunState] = useState(null);  // null = not loaded yet
  const [starting, setStarting] = useState(false);
  const [killing, setKilling]   = useState(false);
  const [error, setError]       = useState('');

  const poll = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/run`);
      if (res.ok) setRunState(await res.json());
    } catch {}
  }, [pipeline.id]);

  useEffect(() => { poll(); }, [poll]);

  const isRunning = runState?.run && !runState?.run?.ended_at;
  useInterval(poll, isRunning ? 3000 : null);  // fast-poll only while running

  const start = async () => {
    setStarting(true); setError('');
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/run`, { method: 'POST' });
      if (!res.ok) { const d = await res.json(); setError(d.detail || 'Failed to start'); return; }
      await poll();
    } finally { setStarting(false); }
  };

  const kill = async () => {
    if (!confirm('Stop the filter run?')) return;
    setKilling(true);
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/run`, { method: 'DELETE' });
      if (res.ok) setRunState(await res.json());
    } finally { setKilling(false); }
  };

  const config = pipeline?.config || {};
  const groups = config.groups || [];
  const totalTerms = groups.reduce((acc, g) =>
    acc + (g.subgroups || []).reduce((a, sg) => a + (sg.is_others ? 0 : (sg.terms?.length || 0)), 0), 0
  );

  const run     = runState?.run;
  const state   = runState?.state || {};
  const logTail = runState?.log_tail || [];
  const status  = runState?.status || 'idle';

  const seen     = state.total_records_seen || 0;
  const inserted = state.total_records_inserted || 0;
  const processed = state.files_processed ?? ((state.processed_files?.length || 0) + (state.failed_files?.length || 0));
  const total = state.total_files ?? (state.all_files?.length || 0);
  const percent  = total > 0 ? Math.round(processed / total * 100) : 0;
  const totalDataMb = Number(state.total_data_mb || 0);
  const totalSkipped = Object.values(state.skip_stats || {}).reduce((acc, val) => acc + Number(val || 0), 0);

  const startedAt = state.started_at || run?.started_at || null;
  const endedAt = run?.ended_at || null;
  const startedMs = startedAt ? Date.parse(startedAt) : NaN;
  const endedMs = endedAt ? Date.parse(endedAt) : NaN;
  const elapsedSec = state.elapsed_sec > 0
    ? Number(state.elapsed_sec)
    : (Number.isFinite(startedMs)
      ? ((Number.isFinite(endedMs) ? endedMs : Date.now()) - startedMs) / 1000
      : 0);
  const safeElapsedSec = elapsedSec > 0 ? elapsedSec : 0;
  const filesPerMin = state.files_per_min > 0 ? Number(state.files_per_min) : (processed > 0 && safeElapsedSec > 0 ? (processed / safeElapsedSec) * 60 : 0);
  const recordsPerSec = state.records_per_sec > 0 ? Number(state.records_per_sec) : (seen > 0 && safeElapsedSec > 0 ? seen / safeElapsedSec : 0);
  const mbPerSec = state.mb_per_sec > 0 ? Number(state.mb_per_sec) : (totalDataMb > 0 && safeElapsedSec > 0 ? totalDataMb / safeElapsedSec : 0);
  const remainingFiles = Math.max(0, total - processed);
  const etaSec = state.eta_sec > 0
    ? Number(state.eta_sec)
    : (isRunning && filesPerMin > 0 && remainingFiles > 0 ? remainingFiles / (filesPerMin / 60) : 0);

  useEffect(() => {
    onRunMetaChange?.({
      isRunning: !!isRunning,
      status,
      pipelineId: pipeline.id,
      pipelineName: pipeline.name,
      processed,
      total,
      percent,
      etaSec,
      mbPerSec,
    });
    return () => onRunMetaChange?.(null);
  }, [onRunMetaChange, isRunning, status, pipeline.id, pipeline.name, processed, total, percent, etaSec, mbPerSec]);

  return (
    <div className="space-y-4">
      {/* Config summary */}
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <h2 className="font-semibold text-gray-900 mb-3">OpenAlex Keyword Filter</h2>
        <div className="grid grid-cols-3 gap-4 text-sm">
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="text-xs text-gray-500 mb-1">Keyword groups</div>
            <div className="font-semibold text-gray-900">{groups.length}</div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="text-xs text-gray-500 mb-1">Total terms</div>
            <div className="font-semibold text-gray-900">{totalTerms}</div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="text-xs text-gray-500 mb-1">Workers</div>
            <div className="font-semibold text-gray-900">16 cores</div>
          </div>
        </div>
      </div>

      {/* Run control */}
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="font-medium text-gray-900">Filter Run</h3>
            <p className="text-xs text-gray-500 mt-0.5">
              Scans all ~700 OpenAlex .gz partitions for matching records.
            </p>
          </div>
          {!isRunning ? (
            <button onClick={start} disabled={starting}
              className="bg-blue-600 text-white text-sm px-5 py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50 transition font-medium">
              {starting ? 'Starting…' : run ? 'Re-run' : 'Start Filter Run'}
            </button>
          ) : (
            <button onClick={kill} disabled={killing}
              className="bg-red-600 text-white text-sm px-5 py-2 rounded-lg hover:bg-red-700 disabled:opacity-50 transition font-medium">
              {killing ? 'Stopping…' : 'Stop'}
            </button>
          )}
        </div>

        {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2 mb-3">{error}</p>}

        {/* Status badge */}
        {run && (
          <div className="flex items-center gap-2 mb-4">
            <span className="text-xs text-gray-500 border border-gray-200 px-1.5 py-0.5 rounded">#{pipeline.id}</span>
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
              status === 'running' ? 'bg-blue-100 text-blue-700 animate-pulse' :
              status === 'done'    ? 'bg-green-100 text-green-700' :
              status === 'failed'  ? 'bg-red-100 text-red-700' :
              status === 'killed'  ? 'bg-orange-100 text-orange-700' :
                                     'bg-gray-100 text-gray-600'
            }`}>
              {status}
            </span>
            {run.pid && isRunning && <span className="text-xs text-gray-400">PID {run.pid}</span>}
            {run.started_at && <span className="text-xs text-gray-400">started {run.started_at?.slice(11, 19)}</span>}
            {run.ended_at   && <span className="text-xs text-gray-400">ended {run.ended_at?.slice(11, 19)}</span>}
          </div>
        )}

        {/* Progress */}
        {(isRunning || (run && status === 'done')) && total > 0 && (
          <div className="space-y-2 mb-4">
            <div className="flex justify-between text-xs text-gray-500">
              <span>Files: {processed.toLocaleString()} / {total.toLocaleString()}</span>
              <span>{percent}%</span>
            </div>
            <ProgressBar percent={percent} />
            <div className="flex gap-4 text-xs text-gray-500">
              <span>Records scanned: {seen.toLocaleString()}</span>
              <span>Matched: {inserted.toLocaleString()}</span>
              <span>Skipped: {totalSkipped.toLocaleString()}</span>
              {seen > 0 && <span>Match rate: {(inserted / seen * 100).toFixed(3)}%</span>}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs">
              <div className="bg-gray-50 rounded-lg p-2 text-gray-600">Elapsed: <span className="font-medium text-gray-900">{formatDuration(safeElapsedSec)}</span></div>
              <div className="bg-gray-50 rounded-lg p-2 text-gray-600">ETA: <span className="font-medium text-gray-900">{etaSec > 0 ? formatDuration(etaSec) : 'calculating…'}</span></div>
              <div className="bg-gray-50 rounded-lg p-2 text-gray-600">Throughput: <span className="font-medium text-gray-900">{mbPerSec.toFixed(2)} MB/s</span></div>
              <div className="bg-gray-50 rounded-lg p-2 text-gray-600">Files/min: <span className="font-medium text-gray-900">{filesPerMin.toFixed(2)}</span></div>
              <div className="bg-gray-50 rounded-lg p-2 text-gray-600">Records/s: <span className="font-medium text-gray-900">{recordsPerSec.toFixed(0)}</span></div>
              <div className="bg-gray-50 rounded-lg p-2 text-gray-600">Data: <span className="font-medium text-gray-900">{totalDataMb.toFixed(0)} MB</span></div>
            </div>
          </div>
        )}

        {/* Done stats */}
        {!isRunning && run && status === 'done' && inserted > 0 && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 mb-3">
            <div className="text-sm font-medium text-green-800 mb-1">Filtering complete</div>
            <div className="text-xs text-green-700 mb-2">
              {inserted.toLocaleString()} records saved
              {seen > 0 && ` (${(inserted / seen * 100).toFixed(3)}% match rate from ${seen.toLocaleString()} scanned)`}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs text-green-800">
              <div>Files: <span className="font-semibold">{processed.toLocaleString()} / {total.toLocaleString()}</span></div>
              <div>Records seen: <span className="font-semibold">{seen.toLocaleString()}</span></div>
              <div>Matched: <span className="font-semibold">{inserted.toLocaleString()}</span></div>
              <div>Skipped: <span className="font-semibold">{totalSkipped.toLocaleString()}</span></div>
              <div>Data: <span className="font-semibold">{totalDataMb.toFixed(0)} MB</span></div>
              <div>Time: <span className="font-semibold">{formatDuration(safeElapsedSec)}</span></div>
            </div>
          </div>
        )}

        {/* Log tail */}
        {logTail.length > 0 && (
          <div>
            <p className="text-xs text-gray-500 mb-1 font-medium">#{pipeline.id} Log tail</p>
            <div className="bg-gray-900 text-gray-100 rounded-lg p-3 text-xs font-mono overflow-auto max-h-64">
              {logTail.map((line, i) => (
                <div key={i} className="leading-5 whitespace-pre-wrap break-all">{line}</div>
              ))}
            </div>
          </div>
        )}

        {!run && status === 'idle' && (
          <div className="text-sm text-gray-400 text-center py-4">
            No filter run yet. Click "Start Filter Run" to begin.
          </div>
        )}
      </div>
    </div>
  );
}
