import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';
import { useInterval } from '../hooks/useInterval';

const TERMINAL = new Set(['completed', 'failed', 'expired', 'cancelled']);

function StatusBadge({ status }) {
  const colors = {
    completed:  'bg-green-100 text-green-700',
    failed:     'bg-red-100 text-red-700',
    expired:    'bg-orange-100 text-orange-700',
    cancelled:  'bg-gray-100 text-gray-600',
    in_progress:'bg-blue-100 text-blue-700',
    validating: 'bg-yellow-100 text-yellow-700',
    not_started:'bg-gray-100 text-gray-500',
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${colors[status] || 'bg-gray-100 text-gray-600'}`}>
      {status?.replace('_', ' ') || 'unknown'}
    </span>
  );
}

export default function LlmStage({ pipeline, onChange }) {
  const [status, setStatus]       = useState(null);
  const [estimate, setEstimate]   = useState(null);
  const [estimating, setEstimating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError]         = useState('');
  const [model, setModel]         = useState('gpt-4o-mini');
  const [output, setOutput]       = useState('');

  const fetchStatus = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/llm/status`);
      if (res.ok) setStatus(await res.json());
    } catch {}
  }, [pipeline.id]);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const batchStatus = status?.status;
  const isActive    = batchStatus && !TERMINAL.has(batchStatus) && batchStatus !== 'not_started';
  // Poll every 2 min if non-terminal (backend lazily refreshes after 5 min, but we check more often)
  useInterval(fetchStatus, isActive ? 30000 : null);

  const runEstimate = async () => {
    setEstimating(true); setError(''); setEstimate(null); setOutput('');
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/llm/estimate`, {
        method: 'POST',
        body: JSON.stringify({ model }),
      });
      const d = await res.json();
      if (!res.ok) { setError(d.detail || 'Estimate failed'); return; }
      setEstimate(d);
      setOutput(d.output || '');
    } finally { setEstimating(false); }
  };

  const submit = async () => {
    if (!confirm('Submit LLM batch? This will incur API costs.')) return;
    setSubmitting(true); setError(''); setOutput('');
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}/llm/submit`, {
        method: 'POST',
        body: JSON.stringify({ model }),
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
          Results are written back to the research database.
        </p>

        {status?.batch_id && (
          <div className="bg-gray-50 rounded-lg p-3 text-xs space-y-1 mb-4">
            <div><span className="text-gray-500">Batch ID: </span><code>{status.batch_id}</code></div>
            {status.checked_at && (
              <div><span className="text-gray-500">Last checked: </span>{status.checked_at?.slice(0, 19)}</div>
            )}
            {counts && (
              <div className="flex gap-4 mt-1">
                <span className="text-green-700">✓ {counts.completed} completed</span>
                <span className="text-gray-600">/ {counts.total} total</span>
                {counts.failed > 0 && <span className="text-red-600">✗ {counts.failed} failed</span>}
              </div>
            )}
            {counts?.total > 0 && (
              <div className="w-full bg-gray-200 rounded-full h-1.5 mt-1">
                <div
                  className="bg-green-500 h-1.5 rounded-full"
                  style={{ width: `${counts.completed / counts.total * 100}%` }}
                />
              </div>
            )}
          </div>
        )}

        {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2 mb-3">{error}</p>}

        {/* Model selector */}
        <div className="flex items-center gap-3 mb-4">
          <label className="text-xs text-gray-600 font-medium shrink-0">Model</label>
          <select value={model} onChange={e => setModel(e.target.value)}
            className="border rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400">
            <option value="gpt-4o-mini">gpt-4o-mini (fast, cheap)</option>
            <option value="gpt-4o">gpt-4o (best quality)</option>
            <option value="gpt-4.1-mini">gpt-4.1-mini</option>
            <option value="gpt-4.1">gpt-4.1</option>
          </select>
        </div>

        {/* Action buttons */}
        <div className="flex flex-wrap gap-2">
          <button onClick={runEstimate} disabled={estimating}
            className="text-sm px-4 py-2 border rounded-lg hover:bg-gray-50 disabled:opacity-50 transition">
            {estimating ? 'Estimating…' : '$ Estimate Cost'}
          </button>

          {(!batchStatus || batchStatus === 'not_started' || TERMINAL.has(batchStatus)) && (
            <button onClick={submit} disabled={submitting}
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
              <button onClick={fetchStatus}
                className="text-sm px-3 py-2 border rounded-lg hover:bg-gray-50 transition">
                ↻ Refresh
              </button>
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
            ['3', 'Wait', '→ batches typically complete within 1–24 hours; status auto-refreshes every 30 s'],
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
