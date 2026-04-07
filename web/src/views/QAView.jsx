import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';

const QA_FIELDS = [
  { id: 'llm_fuel_classification', label: 'Fuel Classification', type: 'multi' },
  { id: 'llm_is_hydrocarbon',      label: 'Is Hydrocarbon',      type: 'binary' },
  { id: 'llm_ml_role',             label: 'ML Role',             type: 'multi' },
  { id: 'llm_ml_algorithm',        label: 'ML Algorithm',        type: 'multi' },
  { id: 'llm_confidence',          label: 'LLM Confidence',      type: 'ordinal' },
  { id: 'llm_scope',               label: 'Value Chain',         type: 'multi' },
];

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
        <button onClick={save} disabled={saving || !value.trim()}
          className="text-sm px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition">
          {saving ? '…' : 'Rate'}
        </button>
      </div>
    </div>
  );
}

export default function QAView() {
  const [pipelines, setPipelines]   = useState([]);
  const [pipelineId, setPipelineId] = useState('');
  const [field, setField]           = useState('llm_fuel_classification');
  const [records, setRecords]       = useState([]);
  const [irr, setIrr]               = useState(null);
  const [rated, setRated]           = useState([]);
  const [tab, setTab]               = useState('rate');
  const [loadingRec, setLoadingRec] = useState(false);

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
  }, []);

  const loadSample = useCallback(async () => {
    if (!pipelineId) return;
    setLoadingRec(true);
    try {
      const res = await apiFetch(`/api/pipeline/${pipelineId}/qa/records?field=${field}&size=10`);
      if (res.ok) { const d = await res.json(); setRecords(d.records || []); }
    } finally { setLoadingRec(false); }
  }, [pipelineId, field]);

  const loadIrr = useCallback(async () => {
    if (!pipelineId) return;
    const [irrRes, ratingsRes] = await Promise.all([
      apiFetch(`/api/pipeline/${pipelineId}/qa/irr`),
      apiFetch(`/api/pipeline/${pipelineId}/qa/ratings`),
    ]);
    if (irrRes.ok)     setIrr(await irrRes.json());
    if (ratingsRes.ok) setRated(await ratingsRes.json());
  }, [pipelineId]);

  useEffect(() => {
    if (!pipelineId) return;
    if (tab === 'rate') loadSample();
    else loadIrr();
  }, [pipelineId, field, tab]); // eslint-disable-line

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

  const Tab = ({ id, label }) => (
    <button onClick={() => setTab(id)}
      className={`text-sm px-4 py-2 border-b-2 font-medium transition ${
        tab === id ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-700'
      }`}>
      {label}
    </button>
  );

  return (
    <div className="max-w-4xl mx-auto p-6">
      <h1 className="text-xl font-bold text-gray-900 mb-6">QA / Inter-Rater Reliability</h1>

      {/* Pipeline + field selector */}
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
          {QA_FIELDS.map(f => (
            <option key={f.id} value={f.id}>{f.label}</option>
          ))}
        </select>
      </div>

      {!pipelineId ? (
        <div className="text-center py-20 text-gray-400 text-sm">
          Select a pipeline to begin QA annotation.
          {pipelines.length === 0 && ' (No pipelines with LLM data yet.)'}
        </div>
      ) : (
        <>
          <div className="flex border-b border-gray-200 mb-4">
            <Tab id="rate" label="Rate Records" />
            <Tab id="irr"  label={`IRR${irr ? ` (${irr.fields?.length} fields)` : ''}`} />
            <Tab id="log"  label={`Ratings Log${rated.length ? ` (${rated.length})` : ''}`} />
          </div>

          {/* Rate tab */}
          {tab === 'rate' && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm text-gray-600">
                  Rate the LLM's classification for{' '}
                  <strong>{QA_FIELDS.find(f => f.id === field)?.label}</strong>.
                  Records are sampled prioritising low-confidence items.
                </p>
                <button onClick={loadSample} disabled={loadingRec}
                  className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 transition shrink-0">
                  {loadingRec ? 'Loading…' : '↻ New batch'}
                </button>
              </div>
              {loadingRec ? (
                <p className="text-sm text-gray-400 text-center py-8">Loading records…</p>
              ) : records.length === 0 ? (
                <p className="text-sm text-gray-400 text-center py-8">
                  No unrated records found for this field.{' '}
                  <button onClick={loadSample} className="text-blue-600 hover:underline">Try again</button>
                </p>
              ) : (
                records.map(r => (
                  <RatingCard key={r.openalex_id} record={r} field={field} onRate={handleRate} />
                ))
              )}
            </div>
          )}

          {/* IRR tab */}
          {tab === 'irr' && (
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <p className="text-sm text-gray-600">
                  Krippendorff's alpha measures agreement between raters.
                </p>
                <button onClick={loadIrr}
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
                  <div className="flex gap-6 text-xs text-gray-600">
                    <span>α = <strong className="text-gray-900">{f.alpha ?? '—'}</strong></span>
                    <span>{f.raters} rater{f.raters !== 1 ? 's' : ''}</span>
                    <span>{f.units} units</span>
                  </div>
                </div>
              ))}
              <div className="text-xs text-gray-400 bg-gray-50 rounded-lg p-3">
                <strong>Interpretation:</strong> α ≥ 0.80 = good · α ≥ 0.67 = tentative · α &lt; 0.67 = needs improvement
              </div>
            </div>
          )}

          {/* Log tab */}
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
