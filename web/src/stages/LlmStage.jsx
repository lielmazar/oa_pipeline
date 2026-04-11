import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';
import { useInterval } from '../hooks/useInterval';

const TERMINAL = new Set(['completed', 'failed', 'expired', 'cancelled']);
/** Poll OpenAI batch status while a run is active (seconds). */
const POLL_ACTIVE_MS = 8000;

function statusHeadline(status) {
  const s = status || '';
  const map = {
    not_started: 'No batch submitted yet',
    validating: 'OpenAI is validating your batch…',
    in_progress: 'OpenAI is processing requests…',
    finalizing: 'Finalizing batch…',
    completed: 'Batch finished — collect results to write to your database',
    failed: 'Batch failed — check OpenAI dashboard or server logs',
    expired: 'Batch expired before completion',
    cancelled: 'Batch was cancelled',
  };
  return map[s] || `Status: ${s.replace(/_/g, ' ') || 'unknown'}`;
}

/** Empty → use server default (if any) or all pending rows; otherwise a positive cap. */
function parseBatchLimitInput(raw) {
  const t = String(raw ?? '').trim();
  if (!t) return { ok: true, value: null };
  const n = Number(t);
  if (!Number.isFinite(n) || n < 1) return { ok: false, value: null };
  return { ok: true, value: Math.min(Number.MAX_SAFE_INTEGER, Math.floor(n)) };
}

function batchProgressParts(counts) {
  const total = Number(counts?.total) || 0;
  const completed = Number(counts?.completed) || 0;
  const failed = Number(counts?.failed) || 0;
  const processing = Math.max(0, total - completed - failed);
  const pctDone = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  return { total, completed, failed, processing, pctDone };
}

function formatBytes(n) {
  if (n == null || !Number.isFinite(Number(n))) return '—';
  let v = Number(n);
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let u = 0;
  while (Math.abs(v) >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return u === 0 ? `${Math.round(v)} ${units[u]}` : `${v.toFixed(2)} ${units[u]}`;
}

function CollectProgressPanel({ progress }) {
  if (!progress || progress.status === 'idle') return null;
  const phase = progress.phase || '';
  const isQueued = phase === 'queued' || phase === 'starting';
  const isStream = phase === 'streaming' || phase === 'reading_file';
  const isWrite = phase === 'writing';
  const pct =
    progress.total > 0 && progress.processed != null
      ? Math.min(100, (100 * Number(progress.processed)) / Number(progress.total))
      : null;

  return (
    <div
      className="rounded-lg border border-emerald-200 bg-emerald-50/60 px-3 py-2.5 mt-3"
      role="status"
      aria-live="polite"
    >
      <div className="text-xs font-semibold text-emerald-900 mb-1">Collect progress</div>
      <p className="text-[11px] text-emerald-900/90 mb-2 leading-snug">
        {progress.message || phase || 'Working…'}
      </p>
      {typeof progress.bytes_received === 'number' && progress.bytes_received > 0 && (
        <p className="text-[11px] text-gray-600 mb-2 tabular-nums">
          {formatBytes(progress.bytes_received)} received
          {isStream ? ' (download / stream)' : ''}
        </p>
      )}
      {isWrite && pct != null && Number.isFinite(pct) && (
        <>
          <div
            className="h-2.5 w-full rounded-full overflow-hidden bg-emerald-200/80 border border-emerald-100"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(pct)}
          >
            <div
              className="h-full bg-emerald-600 transition-[width] duration-300 ease-out"
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="text-[11px] text-gray-700 mt-1.5 tabular-nums">
            {(progress.processed ?? 0).toLocaleString()} / {(progress.total ?? 0).toLocaleString()} rows
            {progress.pct != null && (
              <span className="text-gray-500"> · {Number(progress.pct).toFixed(1)}%</span>
            )}
          </p>
        </>
      )}
      {(isQueued || isStream || phase === 'done') && !(isWrite && pct != null) && (
        <div className="h-2.5 w-full rounded-full overflow-hidden bg-emerald-200/80 border border-emerald-100">
          <div
            className="h-full w-1/3 bg-emerald-500/80 animate-pulse rounded-full"
            style={{ animation: 'pulse 1.5s ease-in-out infinite' }}
          />
        </div>
      )}
      <p className="text-[10px] text-gray-500 mt-2">
        Large batch outputs (multi‑GB) can take many minutes. This panel updates while the server works; you can leave
        the page open.
      </p>
    </div>
  );
}

function SegmentedBatchBar({ completed, failed, processing, total }) {
  if (total <= 0) {
    return (
      <div className="h-3 rounded-full bg-gray-100 border border-gray-200" title="Request counts not available yet" />
    );
  }
  const c = (completed / total) * 100;
  const f = (failed / total) * 100;
  const p = (processing / total) * 100;
  return (
    <div
      className="h-3 w-full rounded-full overflow-hidden flex bg-gray-100 border border-gray-200"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(c)}
      aria-label={`Batch progress: ${Math.round(c)} percent completed`}
    >
      {c > 0 && (
        <div className="h-full bg-emerald-500 shrink-0 transition-all duration-500 ease-out" style={{ width: `${c}%` }} title={`${completed} completed`} />
      )}
      {f > 0 && (
        <div className="h-full bg-red-400 shrink-0 transition-all duration-500 ease-out" style={{ width: `${f}%` }} title={`${failed} failed`} />
      )}
      {p > 0 && (
        <div
          className="h-full bg-blue-400/80 shrink-0 animate-pulse transition-all duration-500 ease-out"
          style={{ width: `${p}%` }}
          title={`${processing} in queue or running`}
        />
      )}
    </div>
  );
}

function StatusBadge({ status }) {
  const colors = {
    completed:  'bg-green-100 text-green-700',
    failed:     'bg-red-100 text-red-700',
    expired:    'bg-orange-100 text-orange-700',
    cancelled:  'bg-gray-100 text-gray-600',
    in_progress:'bg-blue-100 text-blue-700',
    finalizing: 'bg-indigo-100 text-indigo-700',
    validating: 'bg-yellow-100 text-yellow-700',
    not_started:'bg-gray-100 text-gray-500',
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${colors[status] || 'bg-gray-100 text-gray-600'}`}>
      {status?.replace('_', ' ') || 'unknown'}
    </span>
  );
}

/** Short label for the model dropdown; full table comes from the API (same source as llm_classify.py MODEL_PRICING). */
function modelSelectLabel(id, row) {
  if (!row) return id;
  let s = `${id} — $${row.input}/$${row.output} per 1M in/out`;
  if (row.cached_input != null) s += ` · cached in $${row.cached_input}/1M`;
  if (row.reasoning != null) s += ` · reasoning $${row.reasoning}/1M`;
  return s;
}

export default function LlmStage({ pipeline }) {
  const [status, setStatus]       = useState(null);
  const [estimating, setEstimating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [collectProgress, setCollectProgress] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError]         = useState('');
  const [catalog, setCatalog]     = useState(null);
  const [model, setModel]         = useState(null);
  const [subsetSummary, setSubsetSummary] = useState(null);
  const [subsetId, setSubsetId]   = useState(null);
  const [batchLimit, setBatchLimit] = useState('');
  const [llmStats, setLlmStats]   = useState(null);
  const [output, setOutput]       = useState('');

  useEffect(() => {
    apiFetch('/api/pipeline/llm/model-catalog')
      .then(r => (r.ok ? r.json() : null))
      .then((c) => {
        if (c?.model_ids?.length && c.default_model) {
          setCatalog(c);
          setModel(c.default_model);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipeline.id}/subsets/summary`)
      .then(r => (r.ok ? r.json() : null))
      .then(setSubsetSummary)
      .catch(() => setSubsetSummary(null));
  }, [pipeline.id]);

  useEffect(() => {
    let cancelled = false;
    const q = subsetId == null ? '' : `?subset_id=${subsetId}`;
    apiFetch(`/api/pipeline/${pipeline.id}/llm/stats${q}`)
      .then(r => (r.ok ? r.json() : null))
      .then((d) => {
        if (!cancelled) setLlmStats(d);
      })
      .catch(() => {
        if (!cancelled) setLlmStats(null);
      });
    return () => {
      cancelled = true;
    };
  }, [pipeline.id, subsetId]);

  const subsets = subsetSummary?.subsets ?? [];

  const fetchStatus = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/llm/status`);
      if (res.ok) setStatus(await res.json());
    } catch {}
  }, [pipeline.id]);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const batchStatus = status?.status;
  const isActive    = batchStatus && !TERMINAL.has(batchStatus) && batchStatus !== 'not_started';
  const catalogOk   = catalog && model != null;
  useInterval(fetchStatus, isActive ? POLL_ACTIVE_MS : null);

  const runEstimate = async () => {
    if (!catalogOk) return;
    const lim = parseBatchLimitInput(batchLimit);
    if (!lim.ok) {
      setError('Batch limit must be a positive integer, or leave blank.');
      return;
    }
    setEstimating(true); setError(''); setOutput('');
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/llm/estimate`, {
        method: 'POST',
        body: JSON.stringify({ model, subset_id: subsetId, limit: lim.value }),
      });
      const d = await res.json();
      if (!res.ok) { setError(d.detail || 'Estimate failed'); return; }
      setOutput(d.output || '');
    } finally { setEstimating(false); }
  };

  const submit = async () => {
    if (!catalogOk) return;
    const lim = parseBatchLimitInput(batchLimit);
    if (!lim.ok) {
      setError('Batch limit must be a positive integer, or leave blank.');
      return;
    }
    const pending = llmStats?.llm_pending;
    const msg =
      lim.value != null
        ? `Submit LLM batch for up to ${lim.value.toLocaleString()} record(s)? This will incur API costs.`
        : catalog?.default_batch_limit != null
          ? `Submit LLM batch (server default: up to ${Number(catalog.default_batch_limit).toLocaleString()} unprocessed rows per run; clear cap in .env for unlimited)? This will incur API costs.`
          : `Submit LLM batch${pending != null ? ` (${pending.toLocaleString()} pending in this scope)` : ''}? This will incur API costs.`;
    if (!confirm(msg)) return;
    setSubmitting(true); setError(''); setOutput('');
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/llm/submit`, {
        method: 'POST',
        body: JSON.stringify({ model, subset_id: subsetId, limit: lim.value }),
      });
      const d = await res.json();
      if (!res.ok) { setError(d.detail || 'Submit failed'); return; }
      setOutput(d.output || '');
      await fetchStatus();
    } finally { setSubmitting(false); }
  };

  const collect = async () => {
    setCollecting(true); setError(''); setOutput('');
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/llm/collect`, { method: 'POST' });
      const d   = await res.json();
      if (!res.ok) { setError(d.detail || 'Collect failed'); return; }
      setOutput(d.output || '');
      await fetchStatus();
    } finally { setCollecting(false); }
  };

  const cancel = async () => {
    if (!confirm('Cancel the OpenAI batch?')) return;
    setCancelling(true);
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/llm/cancel`, { method: 'POST' });
      if (res.ok) await fetchStatus();
    } finally { setCancelling(false); }
  };

  const counts = status?.request_counts;
  const { total: rqTotal, completed: rqDone, failed: rqFail, processing: rqProc, pctDone } = batchProgressParts(counts);

  const copyBatchId = async () => {
    if (!status?.batch_id) return;
    try {
      await navigator.clipboard.writeText(status.batch_id);
    } catch {
      /* ignore */
    }
  };

  const batchSubsetLabel = (() => {
    if (!status?.batch_id) return null;
    const name = status.subset_name != null ? String(status.subset_name).trim() : '';
    if (name)
      return `${name}${status.subset_id != null ? ` · preset #${status.subset_id}` : ''}`;
    if (status.subset_id != null)
      return `Preset #${status.subset_id} (name unavailable)`;
    return 'Not recorded (batch started before scope metadata was saved)';
  })();

  const batchInputFileCount = status?.batch_input_file_count;
  const batchInputNames = Array.isArray(status?.batch_input_filenames) ? status.batch_input_filenames : [];
  const batchFileSummary =
    typeof batchInputFileCount === 'number'
      ? `${batchInputFileCount} JSONL file${batchInputFileCount !== 1 ? 's' : ''}`
      : status?.batch_id
        ? 'Not recorded'
        : null;

  const selectedSubsetName =
    subsetId == null
      ? 'Entire dataset (unprocessed records)'
      : subsets.find(s => s.id === subsetId)?.name ?? `Preset #${subsetId}`;

  return (
    <div className="space-y-4">
      {/* Status card */}
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-gray-900">LLM Classification</h2>
          {status && <StatusBadge status={batchStatus} />}
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Classify records via OpenAI Batch API. Requires{' '}
          <code className="bg-gray-100 px-1 rounded">OPENAI_API_KEY</code> on the server.
          Results are written back to the research database. Choose which subset to estimate and batch (same subsets as Dataset access).
          Already-classified rows are skipped on the next run; use a batch limit to send at most N pending rows per submit.
          Model list and rates come from{' '}
          <code className="bg-gray-100 px-1 rounded">pipeline/llm_pricing.py</code> (Batch API; same source as{' '}
          <code className="bg-gray-100 px-1 rounded">llm_classify.py</code>).
        </p>

        {llmStats && !llmStats.db_exists && (
          <p className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 mb-4">
            Research database not found yet — counts appear after the pipeline produces <code className="bg-amber-100/80 px-1 rounded">output.sqlite</code>.
          </p>
        )}
        {llmStats?.db_exists && (
          <div className="text-xs text-gray-700 bg-slate-50 border border-slate-100 rounded-lg px-3 py-2 mb-4 space-y-1">
            <div>
              <span className="font-medium text-gray-800">LLM progress in this scope: </span>
              <span className="tabular-nums">{llmStats.llm_done.toLocaleString()}</span>
              {' '}done ·{' '}
              <span className="tabular-nums">{llmStats.llm_pending.toLocaleString()}</span>
              {' '}still unprocessed
              {!llmStats.has_llm_column && (
                <span className="text-gray-500"> (no <code className="bg-white/80 px-1 rounded">llm_is_hydrocarbon</code> column yet — treating all rows as pending)</span>
              )}
            </div>
            {catalog?.default_batch_limit != null && (
              <div className="text-gray-600">
                Server default batch cap:{' '}
                <span className="font-mono font-medium">{Number(catalog.default_batch_limit).toLocaleString()}</span>
                {' '}rows per submit when the limit field below is left blank (set <code className="bg-white/80 px-1 rounded">OA_LLM_BATCH_DEFAULT_LIMIT</code> on the server).
              </div>
            )}
          </div>
        )}

        {/* Batch progress — always visible for orientation */}
        <div className="rounded-xl border border-gray-200 bg-gradient-to-b from-slate-50/80 to-white p-4 mb-4">
          <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
            <div>
              <h3 className="text-sm font-semibold text-gray-900">Batch progress</h3>
              <p className="text-xs text-gray-600 mt-0.5 max-w-xl">{statusHeadline(batchStatus)}</p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {isActive && (
                <span className="text-[11px] text-blue-700 bg-blue-50 border border-blue-100 rounded-full px-2.5 py-0.5 font-medium">
                  Auto-refresh · {POLL_ACTIVE_MS / 1000}s
                </span>
              )}
              {(status?.batch_id || isActive) && (
                <button
                  type="button"
                  onClick={() => fetchStatus()}
                  className="text-xs px-2.5 py-1 border border-gray-200 rounded-lg hover:bg-white bg-white/80 transition"
                >
                  ↻ Refresh now
                </button>
              )}
            </div>
          </div>

          {!status?.batch_id ? (
            <div className="text-xs text-gray-500 py-2 space-y-1">
              <p>Submit a batch below to track OpenAI processing here. Until then, there is nothing in flight.</p>
              <p className="text-gray-600">
                <span className="font-medium text-gray-700">Next submit will use: </span>
                {selectedSubsetName}
                <span className="text-gray-400"> (subset chosen in the form below)</span>
              </p>
            </div>
          ) : (
            <>
              <div className="mb-4 grid sm:grid-cols-2 gap-3 rounded-lg bg-white/70 border border-gray-100 px-3 py-2.5 text-xs">
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-500">Subset this batch came from</div>
                  <div className="text-gray-900 font-medium mt-1">{batchSubsetLabel}</div>
                </div>
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-500">Prepared batch input files</div>
                  <div
                    className="text-gray-900 font-medium mt-1"
                    title={batchInputNames.length ? batchInputNames.join('\n') : undefined}
                  >
                    {batchFileSummary}
                  </div>
                  {batchInputNames.length > 0 && (
                    <p className="text-[10px] text-gray-500 font-mono mt-1 break-all leading-snug">
                      {batchInputNames.join(' · ')}
                    </p>
                  )}
                </div>
                {(typeof status.record_count === 'number' || status.prepare_limit != null || status.effective_batch_limit != null) && (
                  <div className="sm:col-span-2 pt-1 border-t border-gray-100 text-gray-600 space-y-0.5">
                    {typeof status.record_count === 'number' && (
                      <div>
                        <span className="text-gray-500">Records in prepared batch: </span>
                        <span className="font-semibold text-gray-900 tabular-nums">{status.record_count.toLocaleString()}</span>
                      </div>
                    )}
                    {status.prepare_limit != null && (
                      <div className="text-[11px]">
                        <span className="text-gray-500">Prepare limit: </span>
                        <span className="font-mono">{Number(status.prepare_limit).toLocaleString()}</span>
                      </div>
                    )}
                    {status.effective_batch_limit != null && (
                      <div className="text-[11px]">
                        <span className="text-gray-500">Effective cap for this submit: </span>
                        <span className="font-mono">{Number(status.effective_batch_limit).toLocaleString()}</span>
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="flex flex-wrap items-end justify-between gap-4 mb-3">
                <div>
                  <div className="text-3xl font-bold tabular-nums text-gray-900 leading-none">
                    {rqTotal > 0 ? `${pctDone}%` : '—'}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">
                    {rqTotal > 0
                      ? `${rqDone.toLocaleString()} of ${rqTotal.toLocaleString()} requests completed`
                      : 'Waiting for request counts from OpenAI…'}
                  </div>
                </div>
                {batchStatus === 'completed' && status?.output_file_id && (
                  <p className="text-xs text-emerald-800 bg-emerald-50 border border-emerald-100 rounded-lg px-2.5 py-1.5 font-medium">
                    Output file ready — use Collect results
                  </p>
                )}
              </div>

              <SegmentedBatchBar completed={rqDone} failed={rqFail} processing={rqProc} total={rqTotal} />

              <div className="flex flex-wrap gap-3 mt-3 text-xs">
                <div className="flex items-center gap-1.5 rounded-lg bg-white border border-gray-100 px-2.5 py-1.5">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0" aria-hidden />
                  <span className="text-gray-600">Completed</span>
                  <span className="font-semibold text-gray-900 tabular-nums">{rqDone.toLocaleString()}</span>
                </div>
                <div className="flex items-center gap-1.5 rounded-lg bg-white border border-gray-100 px-2.5 py-1.5">
                  <span className="w-2 h-2 rounded-full bg-blue-400 shrink-0" aria-hidden />
                  <span className="text-gray-600">Queued / running</span>
                  <span className="font-semibold text-gray-900 tabular-nums">{rqProc.toLocaleString()}</span>
                </div>
                <div className="flex items-center gap-1.5 rounded-lg bg-white border border-gray-100 px-2.5 py-1.5">
                  <span className="w-2 h-2 rounded-full bg-red-400 shrink-0" aria-hidden />
                  <span className="text-gray-600">Failed</span>
                  <span className="font-semibold text-gray-900 tabular-nums">{rqFail.toLocaleString()}</span>
                </div>
                <div className="flex items-center gap-1.5 rounded-lg bg-white border border-gray-100 px-2.5 py-1.5">
                  <span className="text-gray-600">Total</span>
                  <span className="font-semibold text-gray-900 tabular-nums">{rqTotal.toLocaleString()}</span>
                </div>
              </div>

              <div className="mt-3 pt-3 border-t border-gray-100 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-gray-500">
                <span className="font-mono text-gray-700 break-all">
                  ID: {status.batch_id}
                  <button
                    type="button"
                    onClick={copyBatchId}
                    className="ml-2 text-blue-600 hover:underline font-sans"
                  >
                    Copy
                  </button>
                </span>
                {status.checked_at && (
                  <span title={status.checked_at}>
                    Last checked: {new Date(status.checked_at).toLocaleString()}
                  </span>
                )}
              </div>
            </>
          )}
        </div>

        {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2 mb-3">{error}</p>}

        {/* Subset scope — passed to llm_classify as --subset-sql */}
        <div className="mb-4">
          <label className="block text-xs text-gray-600 font-medium mb-1.5">Subset scope</label>
          {subsets.length === 0 ? (
            <p className="text-xs text-gray-500 bg-gray-50 border border-gray-100 rounded-lg px-3 py-2">
              No preset subsets loaded yet. Estimates and batches use all unprocessed rows in{' '}
              <code className="bg-gray-100 px-1 rounded">filtered_records</code> until subsets exist (they appear automatically in Analysis when the research DB is available).
            </p>
          ) : (
            <select
              value={subsetId == null ? '' : String(subsetId)}
              onChange={(e) => {
                const v = e.target.value;
                setSubsetId(v === '' ? null : Number(v));
              }}
              className="w-full max-w-xl border rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-teal-400"
            >
              <option value="">
                Entire dataset — all unprocessed records in filtered_records
              </option>
              {subsets.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {s.row_count != null ? ` · ${s.row_count.toLocaleString()} rows` : ''}
                </option>
              ))}
            </select>
          )}
          <p className="text-[11px] text-gray-500 mt-1.5">
            On submit, this scope is saved with the batch so the progress panel shows which preset the run used.
          </p>
        </div>

        <div className="mb-4">
          <label className="block text-xs text-gray-600 font-medium mb-1.5" htmlFor={`llm-batch-limit-${pipeline.id}`}>
            Max records this batch
          </label>
          <input
            id={`llm-batch-limit-${pipeline.id}`}
            type="text"
            inputMode="numeric"
            value={batchLimit}
            onChange={(e) => setBatchLimit(e.target.value)}
            placeholder={
              catalog?.default_batch_limit != null
                ? `Blank = server cap (${Number(catalog.default_batch_limit).toLocaleString()})`
                : 'Blank = all pending rows in scope'
            }
            className="w-full max-w-xs border rounded-lg px-2 py-1.5 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-teal-400"
          />
          <p className="text-[11px] text-gray-500 mt-1.5">
            Applies to Estimate and Submit. Rows already written by a previous successful Collect are not sent again.
          </p>
        </div>

        {/* Model selector — options from API (MODEL_PRICING in llm_classify.py) */}
        <div className="flex flex-col gap-2 mb-4">
          <div className="flex items-center gap-3 flex-wrap">
            <label className="text-xs text-gray-600 font-medium shrink-0">Model</label>
            <select
              value={model ?? ''}
              onChange={e => setModel(e.target.value)}
              disabled={!catalogOk}
              className="border rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-teal-400 min-w-[min(100%,28rem)] max-w-full disabled:opacity-50"
            >
              {!catalogOk && <option value="">Loading models…</option>}
              {catalog?.model_ids?.map((id) => (
                <option key={id} value={id}>
                  {modelSelectLabel(id, catalog.models?.[id])}
                </option>
              ))}
            </select>
          </div>
          {catalog?.models && (
            <details className="text-xs text-gray-600 border border-gray-100 rounded-lg px-3 py-2 bg-gray-50/80">
              <summary className="cursor-pointer font-medium text-gray-700">Full pricing table (per 1M tokens)</summary>
              <div className="overflow-x-auto mt-2">
                <table className="w-full text-[11px] border-collapse">
                  <thead>
                    <tr className="border-b border-gray-200 text-left text-gray-500">
                      <th className="py-1 pr-2 font-medium">Model</th>
                      <th className="py-1 pr-2 font-medium">Input</th>
                      <th className="py-1 pr-2 font-medium">Cached in</th>
                      <th className="py-1 pr-2 font-medium">Output</th>
                      <th className="py-1 pr-2 font-medium">Reasoning</th>
                      <th className="py-1 font-medium">Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {catalog.model_ids.map((id) => {
                      const r = catalog.models[id];
                      return (
                        <tr key={id} className="border-t border-gray-100">
                          <td className="py-1 pr-2 font-mono text-gray-800">{id}</td>
                          <td className="py-1 pr-2">${r?.input?.toFixed?.(4) ?? r?.input}</td>
                          <td className="py-1 pr-2">{r?.cached_input != null ? `$${r.cached_input}` : '—'}</td>
                          <td className="py-1 pr-2">${r?.output?.toFixed?.(4) ?? r?.output}</td>
                          <td className="py-1 pr-2">{r?.reasoning != null ? `$${r.reasoning}` : '—'}</td>
                          <td className="py-1 text-gray-500 max-w-xs">{r?.note || ''}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex flex-wrap gap-2">
          <button onClick={runEstimate} disabled={estimating || !catalogOk}
            className="text-sm px-4 py-2 border rounded-lg hover:bg-gray-50 disabled:opacity-50 transition">
            {estimating ? 'Estimating…' : '$ Estimate Cost'}
          </button>

          {(!batchStatus || batchStatus === 'not_started' || TERMINAL.has(batchStatus)) && (
            <button onClick={submit} disabled={submitting || !catalogOk}
              className="text-sm px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 transition font-medium">
              {submitting ? 'Submitting…' : 'Submit Batch'}
            </button>
          )}

          {batchStatus === 'completed' && (
            <button onClick={collect} disabled={collecting}
              className="text-sm px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition font-medium">
              {collecting ? 'Collecting…' : '↓ Collect Results'}
            </button>
          )}

          {isActive && (
            <>
              <button onClick={cancel} disabled={cancelling}
                className="text-sm px-3 py-2 border border-red-200 text-red-600 rounded-lg hover:bg-red-50 disabled:opacity-50 transition">
                {cancelling ? '…' : 'Cancel Batch'}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Estimate / command output */}
      {output && (
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-2">Output</h3>
          <pre className="bg-gray-900 text-gray-100 rounded-lg p-3 text-xs font-mono overflow-auto max-h-64 whitespace-pre-wrap">
            {output}
          </pre>
        </div>
      )}

      {/* Workflow steps guide */}
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <h3 className="text-sm font-medium text-gray-700 mb-3">Workflow</h3>
        <ol className="space-y-2 text-xs text-gray-600">
          {[
            ['1', 'Estimate cost', '→ preview token usage and USD cost before committing'],
            ['2', 'Submit Batch', '→ uploads JSONL to OpenAI and creates batch job (50% cheaper than real-time)'],
            ['3', 'Wait', '→ batches often finish within hours; progress above refreshes every few seconds while the batch is active'],
            ['4', 'Collect Results', '→ downloads output file and writes LLM fields into the research database'],
          ].map(([n, title, desc]) => (
            <li key={n} className="flex gap-2">
              <span className="w-5 h-5 bg-gray-100 rounded-full flex items-center justify-center font-bold text-gray-600 shrink-0">{n}</span>
              <span><strong>{title}</strong> {desc}</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
