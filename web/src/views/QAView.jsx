import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';

function QualityBadge({ quality }) {
  const colors = {
    'good agreement':        'bg-green-100 text-green-700',
    'tentative agreement':   'bg-yellow-100 text-yellow-700',
    'needs improvement':     'bg-red-100 text-red-700',
    'insufficient data':     'bg-gray-100 text-gray-500',
  };
  return <span className={`text-xs px-2 py-0.5 rounded-full ${colors[quality] || 'bg-gray-100 text-gray-500'}`}>{quality}</span>;
}

function RatingCard({ record, field, onRate }) {
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!value.trim()) return;
    setSaving(true);
    await onRate(record.openalex_id, value.trim(), record.llm_value);
    setSaving(false);
  };

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900 line-clamp-2">{record.title || record.openalex_id}</p>
          {record.doi && (
            <a href={`https://doi.org/${record.doi}`} target="_blank" rel="noreferrer"
              className="text-xs text-blue-600 hover:underline">doi:{record.doi}</a>
          )}
        </div>
        {record.llm_confidence && (
          <span className={`text-xs px-2 py-0.5 rounded-full shrink-0 ${
            record.llm_confidence === 'Low'    ? 'bg-red-100 text-red-700' :
            record.llm_confidence === 'Medium' ? 'bg-yellow-100 text-yellow-700' :
                                                  'bg-green-100 text-green-700'
          }`}>{record.llm_confidence}</span>
        )}
      </div>
      <div className="text-xs text-gray-600 mb-3">
        <span className="font-medium">LLM value: </span>
        <code className="bg-gray-100 px-1 rounded">{record.llm_value || '—'}</code>
      </div>
      <div className="flex gap-2">
        <input
          value={value}
          onChange={e => setValue(e.target.value)}
          placeholder="Your assessment…"
          className="flex-1 border rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
        />
        <button type="button" onClick={save} disabled={saving || !value.trim()}
          className="text-sm px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition">
          {saving ? '…' : 'Rate'}
        </button>
      </div>
    </div>
  );
}

export default function QAView({ initialContext, onContextConsumed }) {
  const [pipelines, setPipelines]   = useState([]);
  const [pipelineId, setPipelineId] = useState('');
  const [subsetId, setSubsetId]     = useState('');
  const [field, setField]           = useState('llm_fuel_classification');
  const [qaFields, setQaFields]     = useState([]);
  const [annotationDims, setAnnotationDims] = useState([]);
  const [records, setRecords]       = useState([]);
  const [irr, setIrr]               = useState(null);
  const [rated, setRated]           = useState([]);
  const [disagreements, setDisagreements] = useState([]);
  const [annotations, setAnnotations] = useState([]);
  const [tab, setTab]               = useState('rate');
  const [loadingRec, setLoadingRec] = useState(false);
  const [annForm, setAnnForm]       = useState({ openalex_id: '', dimension: '', human_value: '' });
  const [annSaving, setAnnSaving]   = useState(false);

  useEffect(() => {
    apiFetch('/api/pipeline')
      .then(r => r.ok ? r.json() : [])
      .then(ps => {
        const relevant = ps.filter(p =>
          ['analysis','llm_classification','llm_analysis','summary'].includes(p.current_stage)
        );
        setPipelines(relevant);
        if (relevant.length === 1) setPipelineId(String(relevant[0].id));
      });
    apiFetch('/api/pipeline/qa/fields')
      .then(r => r.ok ? r.json() : {})
      .then(d => {
        setQaFields(d.qa_fields || []);
        setAnnotationDims(d.annotation_dimensions || []);
        if (d.qa_fields?.length && !field) setField(d.qa_fields[0].id);
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
      if (res.ok) { const d = await res.json(); setRecords(d.records || []); }
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

  const handleRate = async (oaId, humanVal, llmVal) => {
    await apiFetch(`/api/pipeline/${pipelineId}/qa/ratings`, {
      method: 'POST',
      body: JSON.stringify({
        openalex_id: oaId, field, human_value: humanVal, llm_value: llmVal,
      }),
    });
    setRecords(prev => prev.filter(r => r.openalex_id !== oaId));
    if (records.length <= 3) loadSample();
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

  return (
    <div className="max-w-4xl mx-auto p-6">
      <h1 className="text-xl font-bold text-gray-900 mb-6">QA / Inter-Rater Reliability</h1>

      <div className="flex flex-wrap gap-3 mb-6">
        <select
          value={pipelineId}
          onChange={e => setPipelineId(e.target.value)}
          className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400">
          <option value="">Select pipeline…</option>
          {pipelines.map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <select
          value={field}
          onChange={e => setField(e.target.value)}
          className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400">
          {qaFields.map(f => (
            <option key={f.id} value={f.id}>{f.label}</option>
          ))}
        </select>
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
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <p className="text-sm text-gray-600">
                  Rate the LLM&apos;s classification for <strong>{fieldLabel}</strong>.
                  {subsetId ? ' Records sampled from the selected subset only.' : ' Records prioritise low-confidence items.'}
                </p>
                <button type="button" onClick={loadSample} disabled={loadingRec}
                  className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 transition shrink-0">
                  {loadingRec ? 'Loading…' : '↻ New batch'}
                </button>
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
                  <RatingCard key={r.openalex_id} record={r} field={field} onRate={handleRate} />
                ))
              )}
            </div>
          )}

          {tab === 'irr' && (
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <p className="text-sm text-gray-600">
                  Krippendorff&apos;s alpha measures agreement between raters. Overlap units = records with ≥2 ratings.
                </p>
                <button type="button" onClick={loadIrr}
                  className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 transition">
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
                    <span>{f.units} units</span>
                    <span>{f.overlap_units ?? '—'} overlap units</span>
                  </div>
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
                <p className="text-sm text-gray-600">Human rating differs from stored LLM value.</p>
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
                        {['OpenAlex ID','Field','LLM','Human','Rater'].map(h => (
                          <th key={h} className="px-3 py-2 text-left font-medium text-gray-600">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {disagreements.map(r => (
                        <tr key={`${r.id}-${r.openalex_id}`} className="border-t border-gray-100">
                          <td className="px-3 py-1.5 font-mono truncate max-w-40">{r.openalex_id}</td>
                          <td className="px-3 py-1.5">{r.field}</td>
                          <td className="px-3 py-1.5 max-w-40 truncate">{r.llm_value || '—'}</td>
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
                        {['OpenAlex ID','Field','LLM value','Human value','Rater','Date'].map(h => (
                          <th key={h} className="px-3 py-2 text-left font-medium text-gray-600">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rated.map(r => (
                        <tr key={r.id} className="border-t border-gray-100">
                          <td className="px-3 py-1.5 font-mono truncate max-w-32">{r.openalex_id}</td>
                          <td className="px-3 py-1.5">{r.field}</td>
                          <td className="px-3 py-1.5 max-w-32 truncate">{r.llm_value || '—'}</td>
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
