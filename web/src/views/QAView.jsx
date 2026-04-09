import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';

function QualityBadge({ quality }) {
  const colors = {
    'good agreement':            'bg-green-100 text-green-700',
    'tentative agreement':       'bg-yellow-100 text-yellow-700',
    'needs improvement':         'bg-red-100 text-red-700',
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

function RatingCard({ record, fieldId, fieldType, labels, onRate }) {
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
      <div className="mb-3">
        <h3 className="text-base font-semibold text-gray-900 leading-snug">
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

      <div className="mb-2">
        <p className="text-[11px] font-medium text-gray-500 uppercase tracking-wide mb-2">Available labels</p>
        {(labels || []).length === 0 ? (
          <p className="text-xs text-amber-700">No label options discovered for this field in the current scope.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {labels.map((raw) => (
              <span
                key={String(raw)}
                className="text-xs px-2.5 py-1 rounded-full bg-gray-100 text-gray-800 border border-gray-200">
                {pillLabel(fieldId, raw)}
              </span>
            ))}
          </div>
        )}
      </div>

      {fieldType === 'multi' && (
        <p className="text-[11px] text-gray-500 mb-2">Multiple selections are allowed. Choose all that apply, then submit.</p>
      )}

      <p className="text-[11px] font-medium text-gray-500 uppercase tracking-wide mb-2">Your rating</p>
      <div className="flex flex-wrap gap-2">
        {(labels || []).length === 0 ? null : fieldType === 'multi' ? (
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
  const [subsetId, setSubsetId]     = useState('');
  const [field, setField]           = useState('llm_fuel_classification');
  const [qaFields, setQaFields]     = useState([]);
  const [annotationDims, setAnnotationDims] = useState([]);
  const [records, setRecords]       = useState([]);
  const [fieldLabels, setFieldLabels] = useState([]);
  const [fieldType, setFieldType]   = useState('');
  const [batchSize, setBatchSize]   = useState(0);
  const [ratedInBatch, setRatedInBatch] = useState(0);
  const [irr, setIrr]               = useState(null);
  const [rated, setRated]           = useState([]);
  const [disagreements, setDisagreements] = useState([]);
  const [annotations, setAnnotations] = useState([]);
  const [tab, setTab]               = useState('rate');
  const [loadingRec, setLoadingRec] = useState(false);
  const [annForm, setAnnForm]       = useState({ openalex_id: '', dimension: '', human_value: '' });
  const [annSaving, setAnnSaving]   = useState(false);

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
      .then(d => {
        setQaFields(d.qa_fields || []);
        setAnnotationDims(d.annotation_dimensions || []);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (qaFields.length && !qaFields.some(f => f.id === field)) setField(qaFields[0].id);
  }, [qaFields, field]);

  useEffect(() => {
    if (!initialContext?.pipelineId) return;
    setPipelineId(String(initialContext.pipelineId));
    if (initialContext.subsetId != null) setSubsetId(String(initialContext.subsetId));
    onContextConsumed?.();
  }, [initialContext, onContextConsumed]);

  const loadSample = useCallback(async () => {
    if (!pipelineId) return;
    setLoadingRec(true);
    try {
      let url = `/api/pipeline/${pipelineId}/qa/records?field=${encodeURIComponent(field)}&size=10`;
      if (subsetId) url += `&subset_id=${encodeURIComponent(subsetId)}`;
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
  }, [pipelineId, field, subsetId]);

  const loadIrr = useCallback(async () => {
    if (!pipelineId) return;
    const [irrRes, ratingsRes] = await Promise.all([
      apiFetch(`/api/pipeline/${pipelineId}/qa/irr`),
      apiFetch(`/api/pipeline/${pipelineId}/qa/ratings`),
    ]);
    if (irrRes.ok)     setIrr(await irrRes.json());
    if (ratingsRes.ok) setRated(await ratingsRes.json());
  }, [pipelineId]);

  const loadDisagreements = useCallback(async () => {
    if (!pipelineId) return;
    const res = await apiFetch(`/api/pipeline/${pipelineId}/qa/disagreements`);
    if (res.ok) setDisagreements(await res.json());
  }, [pipelineId]);

  const loadAnnotations = useCallback(async () => {
    if (!pipelineId) return;
    const res = await apiFetch(`/api/pipeline/${pipelineId}/qa/annotations`);
    if (res.ok) setAnnotations(await res.json());
  }, [pipelineId]);

  useEffect(() => {
    if (!pipelineId) return;
    if (tab === 'rate') loadSample();
    else if (tab === 'irr') loadIrr();
    else if (tab === 'log') loadIrr();
    else if (tab === 'disagree') loadDisagreements();
    else if (tab === 'annotate') loadAnnotations();
  }, [pipelineId, field, tab, loadSample, loadIrr, loadDisagreements, loadAnnotations]);

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
  };

  const submitAnnotation = async () => {
    const { openalex_id, dimension, human_value } = annForm;
    if (!openalex_id.trim() || !dimension || !human_value.trim()) return;
    setAnnSaving(true);
    try {
      const res = await apiFetch(`/api/pipeline/${pipelineId}/qa/annotations`, {
        method: 'POST',
        body: JSON.stringify({
          openalex_id: openalex_id.trim(),
          dimension,
          human_value: human_value.trim(),
        }),
      });
      if (res.ok) {
        setAnnForm({ openalex_id: '', dimension: annForm.dimension, human_value: '' });
        loadAnnotations();
      }
    } finally { setAnnSaving(false); }
  };

  const exportDisagreements = () => {
    const blob = new Blob([JSON.stringify(disagreements, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `qa_disagreements_${pipelineId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const Tab = ({ id, label }) => (
    <button type="button" onClick={() => setTab(id)}
      className={`text-sm px-4 py-2 border-b-2 font-medium transition ${
        tab === id ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-700'
      }`}>
      {label}
    </button>
  );

  const fieldLabel = qaFields.find(f => f.id === field)?.label || field;
  const pipelineLocked = lockedPipelineId != null;
  const progressPct = batchSize > 0 ? Math.round((ratedInBatch / batchSize) * 100) : 0;

  return (
    <div className="max-w-4xl mx-auto p-6">
      <h1 className="text-xl font-bold text-gray-900 mb-6">
        {pipelineLocked ? 'IRR' : 'QA / Inter-Rater Reliability'}
      </h1>

      <div className="flex flex-wrap gap-3 mb-4">
        {!pipelineLocked && (
        <select
          value={pipelineId}
          onChange={e => setPipelineId(e.target.value)}
          className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400">
          <option value="">Select pipeline…</option>
          {pipelines.map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        )}
        <label className="flex items-center gap-2 text-sm text-gray-600">
          <span>Subset ID (optional)</span>
          <input
            value={subsetId}
            onChange={e => setSubsetId(e.target.value.replace(/\D/g, ''))}
            placeholder="e.g. 8"
            className="border rounded-lg px-2 py-1.5 w-24 text-sm"
          />
        </label>
      </div>

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

      {subsetId && (
        <p className="text-xs text-blue-700 bg-blue-50 border border-blue-100 rounded-lg px-3 py-2 mb-4">
          Sampling restricted to subset #{subsetId} (must exist on this pipeline).
        </p>
      )}

      {!pipelineId ? (
        <div className="text-center py-20 text-gray-400 text-sm">
          Select a pipeline to begin QA annotation.
          {pipelines.length === 0 && ' (No pipelines in analysis or later stages yet.)'}
        </div>
      ) : (
        <>
          <div className="flex flex-wrap border-b border-gray-200 mb-4">
            <Tab id="rate" label="Rate Records" />
            <Tab id="irr"  label={`IRR${irr?.fields?.length ? ` (${irr.fields.length})` : ''}`} />
            <Tab id="disagree" label={`Disagreements${disagreements.length ? ` (${disagreements.length})` : ''}`} />
            <Tab id="annotate" label="Annotations" />
            <Tab id="log"  label={`Ratings Log${rated.length ? ` (${rated.length})` : ''}`} />
          </div>

          {tab === 'rate' && (
            <div className="space-y-3">
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
                    record={r}
                    fieldId={field}
                    fieldType={fieldType}
                    labels={fieldLabels}
                    onRate={handleRate}
                  />
                ))
              )}
            </div>
          )}

          {tab === 'irr' && (
            <div className="space-y-4">
              <div className="flex justify-between items-start gap-3">
                <div className="text-sm text-gray-600 space-y-2 max-w-2xl">
                  <p>
                    Krippendorff&apos;s α measures <strong>agreement on the same papers</strong> (same field)—not merely how many rows you submitted.
                  </p>
                  <p>
                    <strong>Overlap papers</strong> are those with at least two ratings on that field: e.g. your label plus the stored model label (human vs. LLM), or two human raters on the same article. α is only shown when overlap &gt; 0.
                  </p>
                </div>
                <button type="button" onClick={loadIrr}
                  className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 transition shrink-0">
                  ↻ Refresh
                </button>
              </div>
              {irr?.fields?.length === 0 && (
                <p className="text-sm text-gray-400 text-center py-8">
                  No ratings yet. Rate some records first.
                </p>
              )}
              {irr?.fields?.map(f => (
                <div key={f.field} className="bg-white border border-gray-200 rounded-xl p-4">
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-medium text-gray-900 text-sm">{f.label}</span>
                    <QualityBadge quality={f.quality} />
                  </div>
                  <div className="flex flex-wrap gap-4 text-xs text-gray-600">
                    <span>α = <strong className="text-gray-900">{f.alpha ?? '—'}</strong></span>
                    <span>{f.raters} rater{f.raters !== 1 ? 's' : ''}</span>
                    <span>{f.units} rated papers</span>
                    <span title="Papers with ≥2 raters for this field">{f.overlap_units ?? '—'} overlap papers</span>
                  </div>
                  {f.overlap_units === 0 && f.units > 0 && (
                    <p className="text-xs text-slate-600 mt-2">
                      No overlap yet: each paper needs a second rating on this field (e.g. ensure model values are stored with your ratings, or add another human rater on the same papers).
                    </p>
                  )}
                </div>
              ))}
              <div className="text-xs text-gray-400 bg-gray-50 rounded-lg p-3">
                <strong>Interpretation:</strong> α ≥ 0.80 = good · α ≥ 0.67 = tentative · α &lt; 0.67 = needs improvement
              </div>
            </div>
          )}

          {tab === 'disagree' && (
            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <p className="text-sm text-gray-600">Where your rating differs from the stored model output (model values are not shown here).</p>
                <button type="button" onClick={exportDisagreements} disabled={!disagreements.length}
                  className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 disabled:opacity-40">
                  Export JSON
                </button>
              </div>
              {disagreements.length === 0 ? (
                <p className="text-sm text-gray-400 text-center py-8">No disagreements (or no ratings yet).</p>
              ) : (
                <div className="overflow-x-auto border border-gray-200 rounded-xl max-h-96 overflow-y-auto">
                  <table className="w-full text-xs">
                    <thead className="bg-gray-50 sticky top-0">
                      <tr>
                        {['OpenAlex ID','Field','Your rating','Rater'].map(h => (
                          <th key={h} className="px-3 py-2 text-left font-medium text-gray-600">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {disagreements.map(r => (
                        <tr key={`${r.id}-${r.openalex_id}`} className="border-t border-gray-100">
                          <td className="px-3 py-1.5 font-mono truncate max-w-40">{r.openalex_id}</td>
                          <td className="px-3 py-1.5">{r.field}</td>
                          <td className="px-3 py-1.5 max-w-40 truncate font-medium text-amber-800">{r.human_value}</td>
                          <td className="px-3 py-1.5">{r.user_id}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {tab === 'annotate' && (
            <div className="space-y-4">
              <p className="text-sm text-gray-600">
                Free-form dimensions (topic, methodology, quality ordinals). Stored per record and annotator.
              </p>
              <div className="grid sm:grid-cols-2 gap-3 bg-gray-50 border border-gray-200 rounded-xl p-4">
                <label className="block text-xs">
                  <span className="text-gray-500">openalex_id</span>
                  <input value={annForm.openalex_id} onChange={e => setAnnForm(f => ({ ...f, openalex_id: e.target.value }))}
                    className="mt-1 w-full border rounded-lg px-2 py-1.5 text-sm" placeholder="https://openalex.org/…" />
                </label>
                <label className="block text-xs">
                  <span className="text-gray-500">Dimension</span>
                  <select value={annForm.dimension} onChange={e => setAnnForm(f => ({ ...f, dimension: e.target.value }))}
                    className="mt-1 w-full border rounded-lg px-2 py-1.5 text-sm">
                    <option value="">Select…</option>
                    {annotationDims.map(d => (
                      <option key={d.id} value={d.id}>{d.label}</option>
                    ))}
                  </select>
                </label>
                <label className="block text-xs sm:col-span-2">
                  <span className="text-gray-500">Value</span>
                  <input value={annForm.human_value} onChange={e => setAnnForm(f => ({ ...f, human_value: e.target.value }))}
                    className="mt-1 w-full border rounded-lg px-2 py-1.5 text-sm" placeholder="Your annotation" />
                </label>
                <button type="button" onClick={submitAnnotation} disabled={annSaving}
                  className="sm:col-span-2 text-sm px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50">
                  {annSaving ? 'Saving…' : 'Save annotation'}
                </button>
              </div>
              <div className="border border-gray-200 rounded-xl overflow-hidden max-h-64 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="bg-gray-50">
                    <tr>
                      {['OpenAlex ID','Dimension','Value','Date'].map(h => (
                        <th key={h} className="px-3 py-2 text-left font-medium text-gray-600">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {annotations.length === 0 ? (
                      <tr><td colSpan={4} className="px-3 py-6 text-center text-gray-400">No annotations yet.</td></tr>
                    ) : (
                      annotations.map(a => (
                        <tr key={a.id} className="border-t border-gray-100">
                          <td className="px-3 py-1.5 font-mono truncate max-w-40">{a.openalex_id}</td>
                          <td className="px-3 py-1.5">{a.dimension}</td>
                          <td className="px-3 py-1.5 truncate max-w-48">{a.human_value}</td>
                          <td className="px-3 py-1.5 text-gray-400">{a.created_at?.slice(0, 10)}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {tab === 'log' && (
            <div>
              {rated.length === 0 ? (
                <p className="text-sm text-gray-400 text-center py-8">No ratings submitted yet.</p>
              ) : (
                <div className="overflow-x-auto border border-gray-200 rounded-xl">
                  <table className="w-full text-xs">
                    <thead className="bg-gray-50">
                      <tr>
                        {['OpenAlex ID','Field','Your value','Rater','Date'].map(h => (
                          <th key={h} className="px-3 py-2 text-left font-medium text-gray-600">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rated.map(r => (
                        <tr key={r.id} className="border-t border-gray-100">
                          <td className="px-3 py-1.5 font-mono truncate max-w-32">{r.openalex_id}</td>
                          <td className="px-3 py-1.5">{r.field}</td>
                          <td className="px-3 py-1.5 max-w-32 truncate font-medium text-blue-700">{r.human_value}</td>
                          <td className="px-3 py-1.5">{r.user_id}</td>
                          <td className="px-3 py-1.5 text-gray-400">{r.created_at?.slice(0, 10)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
