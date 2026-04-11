import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';


function QualityBadge({ quality }) {
  const colors = {
    'satisfactory agreement':    'bg-green-100 text-green-700',
    'good agreement':            'bg-green-100 text-green-700', // legacy label
    'tentative agreement':       'bg-yellow-100 text-yellow-700',
    'poor agreement':            'bg-red-100 text-red-700',
    'needs improvement':         'bg-red-100 text-red-700', // legacy label
    'systematic disagreement':   'bg-orange-100 text-orange-900',
    'insufficient data':         'bg-gray-100 text-gray-500',
    'no overlapping ratings':    'bg-slate-100 text-slate-700',
  };
  return <span className={`text-xs px-2 py-0.5 rounded-full ${colors[quality] || 'bg-gray-100 text-gray-500'}`}>{quality}</span>;
}

function pillLabel(fieldId, raw) {
  if (fieldId === 'llm_is_hydrocarbon') {
    if (raw === '0' || raw === 0) return 'No';
    if (raw === '1' || raw === 1) return 'Yes';
  }
  return String(raw);
}

function RatingCard({ pipelineId, record, fieldId, fieldType, labels, onRate }) {
  const [selected, setSelected] = useState(() => new Set());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setSelected(new Set());
  }, [record.openalex_id, fieldId]);

  const submit = async (humanVal) => {
    setSaving(true);
    try {
      await onRate(record.openalex_id, humanVal);
    } finally {
      setSaving(false);
    }
  };

  const toggle = (raw) => {
    setSelected((prev) => {
      const next = new Set(prev);
      const key = String(raw);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const submitMulti = () => {
    if (!selected.size) return;
    const arr = [...selected].sort();
    submit(JSON.stringify(arr));
  };

  const pillCls = (active) =>
    `text-xs px-3 py-1.5 rounded-full border transition font-medium ${
      active ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-200 hover:border-blue-300'
    }`;

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4">
      <div className="mb-3 flex items-start gap-2">
        <h3 className="text-base font-semibold text-gray-900 leading-snug flex-1 min-w-0">
          {record.title || record.openalex_id}
        </h3>
        {record.doi && (
          <a href={`https://doi.org/${record.doi}`} target="_blank" rel="noreferrer"
            className="text-xs text-blue-600 hover:underline mt-1 inline-block">doi:{record.doi}</a>
        )}
      </div>

      <div className="mb-4">
        <p className="text-[11px] font-medium text-gray-500 uppercase tracking-wide mb-1">Abstract</p>
        {record.abstract?.trim() ? (
          <div className="max-h-52 overflow-y-auto rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-sm text-gray-800 leading-relaxed whitespace-pre-wrap">
            {record.abstract}
          </div>
        ) : (
          <p className="text-sm text-gray-400 italic">No abstract stored for this record.</p>
        )}
      </div>

      {fieldType === 'multi' && (
        <p className="text-[11px] text-gray-500 mb-2">Multiple selections are allowed. Choose all that apply, then submit.</p>
      )}

      <p className="text-[11px] font-medium text-gray-500 uppercase tracking-wide mb-2">Your rating</p>
      <div className="flex flex-wrap gap-2">
        {(labels || []).length === 0 ? (
          <p className="text-xs text-amber-700">No label options discovered for this field in the current scope.</p>
        ) : fieldType === 'multi' ? (
          <>
            {labels.map((raw) => {
              const key = String(raw);
              const on = selected.has(key);
              return (
                <button key={key} type="button" onClick={() => toggle(raw)} className={pillCls(on)}>
                  {pillLabel(fieldId, raw)}
                </button>
              );
            })}
            <button type="button" onClick={submitMulti} disabled={saving || !selected.size}
              className="text-sm px-4 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 ml-auto">
              {saving ? '…' : 'Submit'}
            </button>
          </>
        ) : (
          labels.map((raw) => (
            <button
              key={String(raw)}
              type="button"
              onClick={() => submit(String(raw))}
              disabled={saving}
              className="text-xs px-3 py-1.5 rounded-full border border-gray-200 bg-white hover:bg-blue-50 hover:border-blue-300 font-medium">
              {pillLabel(fieldId, raw)}
            </button>
          ))
        )}
      </div>
    </div>
  );
}

export default function QAView({ initialContext, onContextConsumed, lockedPipelineId }) {
  const [pipelines, setPipelines]   = useState([]);
  const [pipelineId, setPipelineId] = useState('');
  const [field, setField]           = useState('llm_fuel_classification');
  const [qaFields, setQaFields]     = useState([]);
  const [records, setRecords]       = useState([]);
  const [fieldLabels, setFieldLabels] = useState([]);
  const [fieldType, setFieldType]   = useState('');
  const [batchSize, setBatchSize]   = useState(0);
  const [ratedInBatch, setRatedInBatch] = useState(0);
  const [irr, setIrr]               = useState(null);
  const [loadingRec, setLoadingRec] = useState(false);

  useEffect(() => {
    if (lockedPipelineId != null) {
      setPipelineId(String(lockedPipelineId));
    } else {
      apiFetch('/api/pipeline')
        .then(r => r.ok ? r.json() : [])
        .then(ps => {
          const relevant = ps.filter(p =>
            ['analysis', 'llm_classification', 'llm_analysis', 'qa_irr', 'summary'].includes(p.current_stage)
          );
          setPipelines(relevant);
          if (relevant.length === 1) setPipelineId(String(relevant[0].id));
        });
    }
  }, [lockedPipelineId]);

  useEffect(() => {
    apiFetch('/api/pipeline/qa/fields')
      .then(r => r.ok ? r.json() : {})
      .then(d => { setQaFields(d.qa_fields || []); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (qaFields.length && !qaFields.some(f => f.id === field)) setField(qaFields[0].id);
  }, [qaFields, field]);

  useEffect(() => {
    if (!initialContext?.pipelineId) return;
    setPipelineId(String(initialContext.pipelineId));
    onContextConsumed?.();
  }, [initialContext, onContextConsumed]);

  const loadSample = useCallback(async () => {
    if (!pipelineId) return;
    setLoadingRec(true);
    try {
      const url = `/api/pipeline/${pipelineId}/qa/records?field=${encodeURIComponent(field)}&size=10`;
      const res = await apiFetch(url);
      if (res.ok) {
        const d = await res.json();
        setRecords(d.records || []);
        setFieldLabels(d.labels || []);
        setFieldType(d.field_type || '');
        const n = d.count ?? 0;
        setBatchSize(n);
        setRatedInBatch(0);
      }
    } finally { setLoadingRec(false); }
  }, [pipelineId, field]);

  const loadIrr = useCallback(async () => {
    if (!pipelineId) return;
    const irrRes = await apiFetch(`/api/pipeline/${pipelineId}/qa/irr`);
    if (irrRes.ok) setIrr(await irrRes.json());
  }, [pipelineId]);

  useEffect(() => {
    if (!pipelineId) return;
    loadSample();
    loadIrr();
  }, [pipelineId, field, loadSample, loadIrr]);

  const handleRate = async (oaId, humanVal) => {
    await apiFetch(`/api/pipeline/${pipelineId}/qa/ratings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        openalex_id: oaId,
        field,
        human_value: humanVal,
      }),
    });
    setRatedInBatch(c => c + 1);
    setRecords(prev => {
      const next = prev.filter(r => r.openalex_id !== oaId);
      if (next.length <= 3) loadSample();
      return next;
    });
    loadIrr();
  };

  const fieldLabel = qaFields.find(f => f.id === field)?.label || field;
  const pipelineLocked = lockedPipelineId != null;
  const progressPct = batchSize > 0 ? Math.round((ratedInBatch / batchSize) * 100) : 0;

  return (
    <div className="max-w-4xl mx-auto p-6">
      <h1 className="text-xl font-bold text-gray-900 mb-6">
        {pipelineLocked ? 'IRR' : 'QA / Inter-Rater Reliability'}
      </h1>

      {!pipelineLocked && (
      <div className="flex flex-wrap gap-3 mb-4 items-end">
        <select
          value={pipelineId}
          onChange={e => setPipelineId(e.target.value)}
          className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400">
          <option value="">Select pipeline…</option>
          {pipelines.map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      </div>
      )}

      <p className="text-xs text-blue-700 bg-blue-50 border border-blue-100 rounded-lg px-3 py-2 mb-4">
        IRR sample space: Open Access + LLM classified records.
      </p>

      {qaFields.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">Dimension</p>
          <div className="flex flex-wrap gap-2">
            {qaFields.map(f => (
              <button
                key={f.id}
                type="button"
                onClick={() => setField(f.id)}
                className={`text-xs px-3 py-1.5 rounded-full border font-medium transition ${
                  field === f.id
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-gray-700 border-gray-200 hover:border-gray-300'
                }`}>
                {f.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {!pipelineId ? (
        <div className="text-center py-20 text-gray-400 text-sm">
          Select a pipeline to begin QA annotation.
          {pipelines.length === 0 && ' (No pipelines in analysis or later stages yet.)'}
        </div>
      ) : (
        <div className="space-y-6">
          <section className="space-y-4">
            <div className="flex justify-between items-start gap-3 flex-wrap">
              <div className="text-sm text-gray-600 space-y-2 max-w-2xl">
                <p className="font-medium text-gray-800">Reliability (Krippendorff&apos;s α)</p>
                <p>
                  α measures agreement on the same papers (same field). Overlap includes human vs. stored model labels or multiple human raters.
                  When shown, <span className="text-gray-600">95% CI</span> is a cluster bootstrap (papers resampled with replacement).
                </p>
              </div>
              <button type="button" onClick={loadIrr}
                className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 transition shrink-0">
                ↻ Refresh α
              </button>
            </div>
            {irr?.fields?.length === 0 && (
              <p className="text-sm text-gray-400 text-center py-4">
                No ratings yet. Submit ratings below.
              </p>
            )}
            {irr?.fields?.map(f => (
              <div key={f.field} className="bg-white border border-gray-200 rounded-xl p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-medium text-gray-900 text-sm">{f.label}</span>
                  <QualityBadge quality={f.quality} />
                </div>
                <div className="flex flex-wrap gap-4 text-xs text-gray-600">
                  <span>
                    α = <strong className="text-gray-900">{f.alpha ?? '—'}</strong>
                    {f.alpha_ci_low != null && f.alpha_ci_high != null && (
                      <span className="text-gray-500 font-normal"> (95% CI {f.alpha_ci_low}–{f.alpha_ci_high})</span>
                    )}
                  </span>
                  <span>{f.raters} rater{f.raters !== 1 ? 's' : ''}</span>
                  <span>{f.units} rated papers</span>
                  <span title="Papers with ≥2 ratings for this field">{f.overlap_units ?? '—'} overlap papers</span>
                </div>
                {f.overlap_units === 0 && f.units > 0 && (
                  <p className="text-xs text-slate-600 mt-2">
                    No overlap yet: each paper needs a second rating on this field (e.g. model value stored with your rating, or another human on the same papers).
                  </p>
                )}
              </div>
            ))}
            {(irr?.fields?.length ?? 0) > 0 && (
              <div className="text-xs text-gray-600 bg-gray-50 rounded-lg p-3 leading-relaxed">
                <strong className="text-gray-700">Interpretation</strong> (aligned with{' '}
                <a
                  href="https://www.k-alpha.org/methodological-notes"
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-600 hover:underline"
                >
                  K-Alpha methodological notes
                </a>
                , Marzi et al. 2024; Krippendorff 2019): α = 1 perfect agreement; α ≥ 0.80 satisfactory;
                0.67–0.79 tentative (moderate); α &lt; 0.67 poor; α = 0 chance-like; α &lt; 0 systematic disagreement.
              </div>
            )}
          </section>

          <section className="space-y-3 border-t border-gray-200 pt-6">
            <div className="bg-gray-50 border border-gray-200 rounded-xl px-4 py-3 space-y-2">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <p className="text-sm text-gray-700">
                  Your assessment for <strong>{fieldLabel}</strong>. Model output is hidden to reduce bias.
                </p>
                <button type="button" onClick={loadSample} disabled={loadingRec}
                  className="text-xs px-3 py-1.5 border rounded-lg hover:bg-white transition shrink-0">
                  {loadingRec ? 'Loading…' : '↻ New batch'}
                </button>
              </div>
              {batchSize > 0 && (
                <div>
                  <div className="flex justify-between text-xs text-gray-600 mb-1">
                    <span>Progress in this sample</span>
                    <span>{ratedInBatch} / {batchSize} rated</span>
                  </div>
                  <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-500 transition-all"
                      style={{ width: `${progressPct}%` }}
                    />
                  </div>
                </div>
              )}
            </div>

            {loadingRec ? (
              <p className="text-sm text-gray-400 text-center py-8">Loading records…</p>
            ) : records.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-8">
                No unrated records found for this field and scope.{' '}
                <button type="button" onClick={loadSample} className="text-blue-600 hover:underline">Try again</button>
              </p>
            ) : (
              records.map(r => (
                <RatingCard
                  key={r.openalex_id}
                  pipelineId={pipelineId}
                  record={r}
                  fieldId={field}
                  fieldType={fieldType}
                  labels={fieldLabels}
                  onRate={handleRate}
                />
              ))
            )}
          </section>
        </div>
      )}
    </div>
  );
}
