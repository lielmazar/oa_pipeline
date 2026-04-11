import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';

/* ── Dimension definitions (mirrored from ML Classification Prompt v8) ──────── */

const DIMENSION_DEFS = {
  llm_is_hydrocarbon: {
    title: 'Fuel Relevant',
    description:
      'true if the paper studies combustible liquid fuels for transportation (fossil, bio-derived, synthetic, alcohol blends) as the primary topic.',
    falseNote:
      'false if the paper studies: exhaust aftertreatment systems (SCR, DPF, DOC, TWC, catalytic converters) as the primary topic; ambient air quality, atmospheric dispersion, source apportionment, or health impacts of airborne pollutants; hydrogen-only or ammonia-only systems; fuel cells, batteries, solar, electric motors, ADAS; or any topic outside the fuel research fields.',
    scope:
      'Scope: Combustible liquid fuels for transportation — fossil, bio-derived, synthetic, and alcohol blends — from feedstock through combustion and tailpipe emissions.',
    outOfScope:
      'Out of scope: exhaust aftertreatment systems (SCR, DPF, DOC, TWC), ambient air quality / atmospheric dispersion / source apportionment, hydrogen-only, ammonia-only, fuel cells, batteries, solar, electric motors, ADAS.',
    codes: [
      { code: 'Yes (1)', desc: 'Paper studies combustible liquid fuels for transportation as primary topic.' },
      { code: 'No (0)', desc: 'Paper is out of scope (see above).' },
    ],
  },

  llm_research_objective: {
    title: 'Research Objective',
    description: "The paper's PRIMARY research goal. Pick the single most dominant.",
    codes: [
      {
        code: 'engine_emissions',
        desc: 'Measuring, reducing, or predicting engine-out or tailpipe emissions as the primary goal.',
        boundary:
          'vs engine_performance: assign engine_emissions unless emissions are clearly secondary/minor. When in doubt → engine_emissions. vs combustion_science: engine hardware or operating conditions to address real emissions → engine_emissions; fundamental emission-formation mechanisms without engine context → combustion_science.',
      },
      {
        code: 'engine_performance',
        desc: 'Improving or characterizing engine performance as the primary goal.',
        boundary:
          'vs engine_emissions: use ONLY when emissions are secondary/minor and the stated goal is efficiency or power output. vs combustion_science: optimizing or testing an engine system → engine_performance; fundamental heat release, flame, or ignition research → combustion_science.',
      },
      {
        code: 'combustion_science',
        desc: 'Fundamental combustion research — flame structure, burning velocity, ignition delay, chemical kinetics, reaction pathways, spray dynamics, soot formation mechanisms, surrogate fuel formulation.',
        boundary:
          'No engine optimization or hardware-specific testing. Shock tube ignition delay → combustion_science. Same fuel tested in a diesel engine → engine_emissions or engine_performance.',
      },
      {
        code: 'fuel_production',
        desc: 'Producing fuels from feedstocks via any conversion route. Includes process optimization and yield improvement.',
        boundary:
          'vs blend_optimization: converting raw feedstock into fuel → fuel_production; mixing existing fuels → blend_optimization. vs sustainability_assessment: if producing the fuel IS the contribution → fuel_production; if the LCA/TEA IS the contribution → sustainability_assessment. vs feedstock_assessment: if a conversion step is performed → fuel_production; if only feedstock is characterized → feedstock_assessment.',
      },
      {
        code: 'feedstock_assessment',
        desc: 'Evaluating feedstock potential, composition, cultivation, collection, or logistics. No fuel conversion step.',
        boundary:
          'vs fuel_production: conversion present → fuel_production. vs sustainability_assessment: lifecycle/economic assessment of supply chains → sustainability_assessment; feedstock characterization → feedstock_assessment.',
      },
      {
        code: 'fuel_properties',
        desc: 'Measuring, predicting, or characterizing physical/chemical fuel properties as the primary objective.',
        boundary:
          'vs fuel_production: if property measurement IS the contribution → fuel_properties; if it\'s validation of a production process → fuel_production.',
      },
      {
        code: 'blend_optimization',
        desc: 'Formulating fuel blends, designing additive packages, optimizing mixture ratios of existing fuels.',
        boundary:
          'vs fuel_production: input is already a usable fuel being combined → blend_optimization; input is a non-fuel feedstock being converted → fuel_production.',
      },
      {
        code: 'sustainability_assessment',
        desc: 'System-level evaluation: LCA, TEA, carbon footprint, well-to-wheel, policy, regulation, cost-benefit.',
        boundary:
          'vs fuel_production/feedstock_assessment: the assessment methodology or result IS the primary contribution → sustainability_assessment.',
      },
      {
        code: 'molecular_design',
        desc: 'Computational screening or inverse design of novel fuel molecules, additives, or catalysts.',
      },
      { code: 'none', desc: 'Not fuel-related, or out of scope.' },
    ],
  },

  llm_ml_role: {
    title: 'ML Role',
    description:
      'What the ML model DOES — its functional purpose. INDEPENDENT of research_objective.',
    notes: [
      'RSM, DOE, ANOVA, and metaheuristic optimization alone ≠ ML → set "none".',
      'GA/PSO used ONLY for parameter search without a trained predictive model → "none".',
      'GP/GEP producing trained symbolic models ARE ML.',
      'PCA/t-SNE/UMAP used only for preprocessing → not "pattern_discovery".',
      'ML-positive ONLY if the abstract describes a model trained, validated, or deployed in the paper itself.',
    ],
    codes: [
      {
        code: 'prediction',
        desc: 'Estimating output quantities from input variables. The goal is inference or forecasting.',
        boundary:
          'vs optimization: model produces estimates without an explicit search/optimization loop → prediction. vs surrogate_modeling: replaces a known expensive simulation → surrogate_modeling; learns directly from data → prediction.',
      },
      {
        code: 'optimization',
        desc: 'Tuning parameters to maximize or minimize an objective via explicit search (GA, PSO, Bayesian opt, Pareto).',
        boundary:
          'Requires an explicit optimization loop, not just prediction. Prediction + manual interpretation ≠ optimization.',
      },
      {
        code: 'surrogate_modeling',
        desc: 'ML trained to replace or approximate expensive simulations (CFD, kinetics, process models) for faster evaluation.',
        boundary:
          'vs prediction: surrogate replaces an identified simulation; prediction learns from empirical or operational data directly.',
      },
      {
        code: 'soft_sensor',
        desc: 'Inferring hard-to-measure quantities in real time from available sensor signals.',
        boundary:
          'vs prediction: soft_sensor operates online in a running system; prediction operates offline on datasets.',
      },
      {
        code: 'pattern_discovery',
        desc: 'Unsupervised exploration as the PRIMARY contribution — clustering, latent structure identification.',
        boundary:
          'Not routine preprocessing. PCA for dimensionality reduction before regression ≠ pattern_discovery.',
      },
      {
        code: 'image_analysis',
        desc: 'Extracting information from visual data — flame images, spray morphology, microscopy.',
      },
      {
        code: 'design',
        desc: 'Generating, screening, or inverse-designing NEW molecular structures or compositions using ML.',
        boundary:
          'vs prediction: design creates new candidates; prediction estimates properties of known candidates.',
      },
      {
        code: 'automation',
        desc: 'Closed-loop control using ML — adaptive calibration, model-predictive control, RL-based controllers.',
        boundary:
          'vs optimization: automation operates in closed-loop real-time control; optimization is offline parameter tuning.',
      },
      {
        code: 'anomaly_detection',
        desc: 'Identifying faults, outliers, or degradation in fuel systems or processes.',
        boundary:
          'vs prediction: anomaly_detection flags abnormal states; prediction estimates continuous quantities.',
      },
      {
        code: 'text_mining',
        desc: 'NLP on fuel-related patents, literature, or regulatory text.',
      },
      { code: 'none', desc: 'No ML in this paper.' },
    ],
  },

  llm_ml_algorithm: {
    title: 'ML Algorithm',
    description:
      'Specific ML model names or algorithm families mentioned in the abstract. Include ONLY machine learning models — not statistical tests, DOE, RSM, or physics-based simulations.',
    notes: [
      'Use ["none"] if no ML algorithm is present.',
      'Never mix "none" with real values.',
      'If ml_role ≠ "none" then ml_algorithm MUST contain at least one ML model name.',
      'Examples: ["Random Forest", "XGBoost"], ["ANN", "Genetic Algorithm"], ["LSTM"], ["PLS"], ["SVM", "Random Forest", "Gradient Boosting"].',
    ],
  },

  llm_ml_data_source: {
    title: 'ML Data Source',
    description: 'Primary origin of data used to train or feed the ML model.',
    codes: [
      {
        code: 'experimental',
        desc: 'Data generated by the authors through physical tests — laboratory, engine dynamometer, bench-scale measurements.',
        boundary:
          'vs operational: controlled laboratory/test-bench conditions → experimental; real-world uncontrolled conditions → operational.',
      },
      {
        code: 'simulation',
        desc: 'Data generated from computational models — CFD, chemical kinetics, process simulation, molecular dynamics.',
        boundary:
          'vs experimental: ML trains on simulation outputs → simulation; trains on physical measurements → experimental.',
      },
      {
        code: 'database',
        desc: 'Data retrieved from published repositories or standardized collections — NIST, PubChem, DIPPR, curated datasets.',
        boundary:
          'vs literature: structured database with API or download → database; data manually collected from papers → literature.',
      },
      {
        code: 'literature',
        desc: 'Data manually or systematically extracted from published papers.',
        boundary: 'vs database: unstructured extraction from papers → literature.',
      },
      {
        code: 'operational',
        desc: 'Real-world data from deployed systems — fleet vehicles, refineries, power plants, onboard sensors, GPS logs, satellite/aerial monitoring.',
        boundary:
          'vs experimental: operational data comes from in-use or deployed systems, not controlled test setups.',
      },
      {
        code: 'mixed',
        desc: 'Two or more of the above contribute substantially.',
        boundary: 'Use only when sources are genuinely co-equal, not when one dominates.',
      },
      { code: 'none', desc: 'No ML, or data source not identifiable from abstract.' },
    ],
  },

  llm_confidence: {
    title: 'Confidence',
    description: 'How clearly the abstract describes the ML method, target, and data.',
    codes: [
      { code: 'high', desc: 'ML method, target, and data clearly described.' },
      { code: 'medium', desc: 'ML mentioned but vague, or fuel relevance ambiguous.' },
      { code: 'low', desc: 'Ambiguous, title-abstract mismatch, or ML only implied.' },
    ],
  },
};

/* ── Collapsible dimension guide ──────────────────────────────────────────────── */

function DimensionGuide({ fieldId }) {
  const [open, setOpen] = useState(false);
  const def = DIMENSION_DEFS[fieldId];
  if (!def) return null;

  return (
    <div className="mb-4 border border-indigo-100 rounded-xl bg-indigo-50/40 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-2.5 text-left text-sm font-medium text-indigo-800 hover:bg-indigo-50 transition"
      >
        <span>Coding guide — {def.title}</span>
        <svg
          className={`w-4 h-4 text-indigo-500 transform transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="px-4 pb-4 text-xs text-gray-700 leading-relaxed space-y-3">
          <p>{def.description}</p>

          {def.scope && (
            <p className="text-indigo-700 font-medium">{def.scope}</p>
          )}
          {def.outOfScope && (
            <p className="text-red-700/80">{def.outOfScope}</p>
          )}
          {def.falseNote && (
            <p className="text-gray-600 italic">{def.falseNote}</p>
          )}

          {def.notes && (
            <ul className="list-disc pl-4 space-y-1 text-gray-600">
              {def.notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          )}

          {def.codes && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="text-left text-gray-500 border-b border-indigo-100">
                    <th className="py-1.5 pr-3 font-semibold w-[140px]">Code</th>
                    <th className="py-1.5 pr-3 font-semibold">Description</th>
                    {def.codes.some(c => c.boundary) && (
                      <th className="py-1.5 font-semibold">Boundary rules</th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {def.codes.map(c => (
                    <tr key={c.code} className="border-b border-indigo-50 align-top">
                      <td className="py-1.5 pr-3 font-mono text-indigo-700 whitespace-nowrap">{c.code}</td>
                      <td className="py-1.5 pr-3">{c.desc}</td>
                      {def.codes.some(cc => cc.boundary) && (
                        <td className="py-1.5 text-gray-500 italic">{c.boundary || '—'}</td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


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
      active ? 'bg-teal-600 text-white border-teal-600' : 'bg-white text-gray-700 border-gray-200 hover:border-teal-300'
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
              className="text-sm px-4 py-1.5 bg-teal-600 text-white rounded-lg hover:bg-teal-700 disabled:opacity-50 ml-auto">
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
              className="text-xs px-3 py-1.5 rounded-full border border-gray-200 bg-white hover:bg-blue-50 hover:border-teal-300 font-medium">
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
  const [field, setField]           = useState('llm_is_hydrocarbon');
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
          className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-teal-400">
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
                    ? 'bg-teal-600 text-white border-teal-600'
                    : 'bg-white text-gray-700 border-gray-200 hover:border-gray-300'
                }`}>
                {f.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <DimensionGuide fieldId={field} />

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
