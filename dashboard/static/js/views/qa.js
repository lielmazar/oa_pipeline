// ── QA / Krippendorff's Alpha View ────────────────────────────────────────────

function QARatingCard({ article, field, fieldMeta, onRate }) {
  const [value, setValue] = useState('');
  const isMulti = fieldMeta?.type === 'multi';
  const isBinary = fieldMeta?.type === 'binary';

  const llmDisplay = (() => {
    if (!article.llm_value) return '—';
    try {
      const arr = JSON.parse(article.llm_value);
      return Array.isArray(arr) ? arr.join(', ') : String(article.llm_value);
    } catch { return String(article.llm_value); }
  })();

  const submit = () => {
    if (!value.trim()) return;
    onRate({ openalex_id: article.openalex_id, field, llm_value: article.llm_value, human_value: value.trim() });
    setValue('');
  };

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
      <div className="flex items-start justify-between gap-4 mb-3">
        <div className="min-w-0 flex-1">
          <h4 className="font-semibold text-gray-900 text-sm leading-snug">{article.title || 'Untitled'}</h4>
          {article.doi && (
            <a href={`https://doi.org/${article.doi}`} target="_blank" rel="noopener"
              className="text-xs text-blue-500 hover:underline mt-0.5 inline-block">
              {article.doi}
            </a>
          )}
        </div>
        {article.llm_confidence && (
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium flex-shrink-0
            ${article.llm_confidence === 'Low' ? 'bg-red-100 text-red-700' :
              article.llm_confidence === 'Medium' ? 'bg-yellow-100 text-yellow-700' :
              'bg-green-100 text-green-700'}`}>
            {article.llm_confidence}
          </span>
        )}
      </div>

      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs text-gray-500 font-medium">LLM value:</span>
        <span className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded font-mono">{llmDisplay}</span>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-500 font-medium whitespace-nowrap">Your rating:</span>
        {isBinary ? (
          <div className="flex gap-1">
            {['Yes', 'No'].map(opt => (
              <button key={opt} onClick={() => { setValue(opt); }}
                className={`text-xs px-3 py-1 rounded border transition
                  ${value === opt ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400'}`}>
                {opt}
              </button>
            ))}
          </div>
        ) : (
          <input type="text" value={value} onChange={e => setValue(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && submit()}
            placeholder={isMulti ? 'e.g. term1, term2' : 'Your classification'}
            className="flex-1 text-sm border rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-500" />
        )}
        <button onClick={submit} disabled={!value.trim()}
          className="text-xs bg-blue-600 text-white px-3 py-1.5 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-40 transition">
          Submit
        </button>
      </div>
    </div>
  );
}


function AlphaGauge({ alpha, label }) {
  const pct = alpha != null ? Math.max(0, Math.min(1, (alpha + 1) / 2)) * 100 : 0;
  const color = alpha == null ? 'gray' :
    alpha >= 0.8 ? 'green' : alpha >= 0.67 ? 'yellow' : alpha >= 0.4 ? 'orange' : 'red';
  const colorMap = {
    green: 'bg-green-500', yellow: 'bg-yellow-500', orange: 'bg-orange-500', red: 'bg-red-500', gray: 'bg-gray-300',
  };
  const textMap = {
    green: 'text-green-700', yellow: 'text-yellow-700', orange: 'text-orange-700', red: 'text-red-700', gray: 'text-gray-500',
  };
  const interpretation = alpha == null ? 'Not enough data' :
    alpha >= 0.8 ? 'Excellent agreement' : alpha >= 0.67 ? 'Good agreement' :
    alpha >= 0.4 ? 'Moderate agreement' : 'Poor agreement';

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
      <div className="text-xs text-gray-500 font-medium mb-1">{label}</div>
      <div className={`text-2xl font-bold ${textMap[color]}`}>
        {alpha != null ? alpha.toFixed(3) : '—'}
      </div>
      <div className="w-full bg-gray-200 rounded-full h-2 mt-2">
        <div className={`${colorMap[color]} h-2 rounded-full transition-all duration-500`}
          style={{ width: `${pct}%` }} />
      </div>
      <div className="text-xs text-gray-500 mt-1">{interpretation}</div>
    </div>
  );
}


function QAView({ pipelines, onRefresh }) {
  const [selectedPipelineId, setSelectedPipelineId] = useState(null);
  const [selectedField, setSelectedField] = useState('llm_fuel_classification');
  const [articles, setArticles] = useState([]);
  const [stats, setStats] = useState(null);
  const [alpha, setAlpha] = useState(null);
  const [loading, setLoading] = useState(false);
  const [ratedCount, setRatedCount] = useState(0);
  const [totalRated, setTotalRated] = useState(0);

  const qaFields = stats?.qa_fields || [
    { id: 'llm_fuel_classification', label: 'Fuel Classification', type: 'multi' },
    { id: 'llm_is_hydrocarbon', label: 'Is Hydrocarbon', type: 'binary' },
    { id: 'llm_ml_role', label: 'ML Role', type: 'multi' },
    { id: 'llm_ml_algorithm', label: 'ML Algorithm', type: 'multi' },
    { id: 'llm_confidence', label: 'LLM Confidence', type: 'ordinal' },
    { id: 'llm_scope', label: 'Value Chain', type: 'multi' },
  ];

  const fieldMeta = qaFields.find(f => f.id === selectedField);

  // Load stats + alpha when pipeline or field changes
  useEffect(() => {
    if (!selectedPipelineId) return;
    apiFetch(`/api/qa/stats/${selectedPipelineId}`).then(r => r.json()).then(setStats).catch(() => {});
    apiFetch(`/api/qa/alpha/${selectedPipelineId}`).then(r => r.json()).then(setAlpha).catch(() => {});
  }, [selectedPipelineId, ratedCount]);

  const loadSample = async () => {
    if (!selectedPipelineId) return;
    setLoading(true);
    try {
      const res = await apiFetch(`/api/qa/sample/${selectedPipelineId}?field=${selectedField}&size=10`);
      const data = await res.json();
      if (res.ok) {
        setArticles(data.articles || []);
        setTotalRated(data.total_rated || 0);
      }
    } catch {}
    setLoading(false);
  };

  // Auto-load sample when field/pipeline changes
  useEffect(() => { loadSample(); }, [selectedPipelineId, selectedField]);

  const handleRate = async (rating) => {
    if (!selectedPipelineId) return;
    const res = await apiFetch(`/api/qa/rate/${selectedPipelineId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ratings: [rating] }),
    });
    if (res.ok) {
      setArticles(prev => prev.filter(a => a.openalex_id !== rating.openalex_id));
      setRatedCount(c => c + 1);
      setTotalRated(c => c + 1);
    }
  };

  const liveAlpha = alpha?.fields?.[selectedField];

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-gray-900">QA — Krippendorff's Alpha</h2>
        <p className="text-sm text-gray-500 mt-1">
          Rate LLM classifications to compute inter-rater reliability. Articles are served low-confidence first.
        </p>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <select value={selectedPipelineId || ''}
          onChange={e => setSelectedPipelineId(Number(e.target.value) || null)}
          className="border rounded-lg px-3 py-2 text-sm min-w-[200px]">
          <option value="">Select pipeline...</option>
          {pipelines.map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>

        <select value={selectedField} onChange={e => setSelectedField(e.target.value)}
          className="border rounded-lg px-3 py-2 text-sm">
          {qaFields.map(f => (
            <option key={f.id} value={f.id}>{f.label}</option>
          ))}
        </select>

        <button onClick={loadSample} disabled={!selectedPipelineId || loading}
          className="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition">
          {loading ? 'Loading…' : 'Load Sample'}
        </button>

        <span className="text-xs text-gray-500">
          {totalRated} rated by you · {articles.length} pending
        </span>
      </div>

      {/* Alpha dashboard */}
      {selectedPipelineId && alpha?.fields && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
          {qaFields.map(f => {
            const a = alpha.fields[f.id];
            return <AlphaGauge key={f.id} label={f.label} alpha={a?.alpha ?? null} />;
          })}
        </div>
      )}

      {/* Stats summary */}
      {selectedPipelineId && stats && (
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 mb-6 flex flex-wrap gap-6 text-sm">
          <div><span className="text-gray-500">Total articles:</span> <strong>{stats.total_articles}</strong></div>
          {Object.entries(stats.per_field || {}).map(([fld, s]) => (
            <div key={fld}>
              <span className="text-gray-500">{fld}:</span>{' '}
              <strong>{s.articles}</strong> articles by <strong>{s.raters}</strong> raters
            </div>
          ))}
        </div>
      )}

      {/* Article cards */}
      {!selectedPipelineId && (
        <div className="text-center text-gray-400 py-16">Select a pipeline to start QA review.</div>
      )}

      {selectedPipelineId && articles.length === 0 && !loading && (
        <div className="text-center text-gray-400 py-16">
          <div className="text-4xl mb-2">✓</div>
          No more articles to rate for this field. Try another field or load more.
        </div>
      )}

      <div className="space-y-3">
        {articles.map(article => (
          <QARatingCard key={article.openalex_id} article={article}
            field={selectedField} fieldMeta={fieldMeta} onRate={handleRate} />
        ))}
      </div>

      {/* Load more */}
      {selectedPipelineId && articles.length > 0 && (
        <div className="text-center mt-6">
          <button onClick={loadSample} disabled={loading}
            className="text-sm text-blue-600 hover:text-blue-800 font-medium transition">
            {loading ? 'Loading…' : 'Load more articles'}
          </button>
        </div>
      )}
    </div>
  );
}


// ── Enhanced QA: Stratified Sample + Annotations ─────────────────────────────

function AnnotationRow({ article, dimensions, onSave }) {
  const [values, setValues] = useState({});

  const setVal = (dim, val) => setValues(v => ({ ...v, [dim]: val }));

  const llmVal = (col) => {
    const raw = article[col];
    if (!raw) return '—';
    try { const arr = JSON.parse(raw); return Array.isArray(arr) ? arr.join(', ') : String(raw); }
    catch { return String(raw); }
  };

  const LLM_COL_MAP = {
    fuel_relevant: 'llm_is_hydrocarbon',
    fuel_classification: 'llm_fuel_classification',
    ml_role: 'llm_ml_role',
    ml_algorithm: 'llm_ml_algorithm',
    ml_data_source: 'llm_ml_data_source',
    confidence: 'llm_confidence',
  };

  const handleSave = () => {
    const annotations = [];
    for (const [dim, hv] of Object.entries(values)) {
      if (!hv || !hv.trim()) continue;
      annotations.push({
        openalex_id: article.openalex_id,
        dimension: dim,
        human_value: hv.trim(),
        llm_value: article[LLM_COL_MAP[dim]] || null,
      });
    }
    if (annotations.length > 0) onSave(annotations);
  };

  const abstract = article.abstract_text || '';
  const snippet = abstract.length > 300 ? abstract.slice(0, 300) + '…' : abstract;

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm text-sm">
      <div className="flex items-start justify-between gap-4 mb-2">
        <div className="min-w-0 flex-1">
          <h4 className="font-semibold text-gray-900 text-sm leading-snug">{article.title || 'Untitled'}</h4>
          {article.doi && (
            <a href={`https://doi.org/${article.doi}`} target="_blank" rel="noopener"
              className="text-xs text-blue-500 hover:underline">{article.doi}</a>
          )}
        </div>
        {article.llm_confidence && (
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium flex-shrink-0
            ${article.llm_confidence.toLowerCase() === 'low' ? 'bg-red-100 text-red-700' :
              article.llm_confidence.toLowerCase() === 'medium' ? 'bg-yellow-100 text-yellow-700' :
              'bg-green-100 text-green-700'}`}>
            {article.llm_confidence}
          </span>
        )}
      </div>

      {snippet && <p className="text-xs text-gray-500 mb-3 leading-relaxed">{snippet}</p>}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 mb-3">
        {dimensions.map(d => (
          <div key={d.id} className="flex flex-col gap-1">
            <div className="flex items-center gap-1">
              <span className="text-xs font-medium text-gray-600">{d.label}</span>
              <span className="text-xs text-gray-400">LLM: {llmVal(LLM_COL_MAP[d.id])}</span>
            </div>
            {d.type === 'binary' ? (
              <div className="flex gap-1">
                {['true', 'false'].map(opt => (
                  <button key={opt} onClick={() => setVal(d.id, opt)}
                    className={`text-xs px-2 py-1 rounded border transition
                      ${values[d.id] === opt ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'}`}>
                    {opt}
                  </button>
                ))}
              </div>
            ) : (
              <input type="text" value={values[d.id] || ''} onChange={e => setVal(d.id, e.target.value)}
                placeholder={d.type === 'multi' ? 'val1, val2' : 'value'}
                className="text-xs border rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-blue-500" />
            )}
          </div>
        ))}
      </div>

      <button onClick={handleSave} disabled={Object.values(values).every(v => !v?.trim())}
        className="text-xs bg-green-600 text-white px-4 py-1.5 rounded-lg font-medium hover:bg-green-700 disabled:opacity-40 transition">
        Save Annotations
      </button>
    </div>
  );
}

function ConfusionPanel({ pipelineId }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    if (!pipelineId) return;
    apiFetch(`/api/qa/confusion/${pipelineId}`).then(r => r.json()).then(setData).catch(() => {});
  }, [pipelineId]);

  if (!data?.confusion) return null;

  return (
    <div className="space-y-4">
      {Object.entries(data.confusion).map(([dim, info]) => {
        if (!info.data) return null;
        if (info.type === 'binary') {
          const d = info.data;
          return (
            <div key={dim} className="bg-gray-50 rounded-lg p-4 border">
              <h4 className="text-sm font-semibold mb-2">{dim} (Binary)</h4>
              <div className="grid grid-cols-4 gap-2 text-center text-xs">
                <div className="bg-green-100 rounded p-2"><div className="font-bold text-green-700">{d.tp}</div>TP</div>
                <div className="bg-red-100 rounded p-2"><div className="font-bold text-red-700">{d.fp}</div>FP</div>
                <div className="bg-red-100 rounded p-2"><div className="font-bold text-red-700">{d.fn}</div>FN</div>
                <div className="bg-green-100 rounded p-2"><div className="font-bold text-green-700">{d.tn}</div>TN</div>
              </div>
              <div className="flex gap-4 mt-2 text-xs text-gray-600">
                <span>Precision: <strong>{d.precision.toFixed(3)}</strong></span>
                <span>Recall: <strong>{d.recall.toFixed(3)}</strong></span>
                <span>F1: <strong>{d.f1.toFixed(3)}</strong></span>
              </div>
            </div>
          );
        }
        return (
          <div key={dim} className="bg-gray-50 rounded-lg p-4 border">
            <h4 className="text-sm font-semibold mb-2">{dim} (Per-Code)</h4>
            <table className="text-xs border-collapse w-full">
              <thead><tr className="bg-gray-100">
                <th className="p-1 border text-left">Code</th>
                <th className="p-1 border text-right">TP</th>
                <th className="p-1 border text-right">FP</th>
                <th className="p-1 border text-right">FN</th>
                <th className="p-1 border text-right">Precision</th>
                <th className="p-1 border text-right">Recall</th>
              </tr></thead>
              <tbody>
                {info.data.map(c => (
                  <tr key={c.code}>
                    <td className="p-1 border">{c.code}</td>
                    <td className="p-1 border text-right">{c.tp}</td>
                    <td className="p-1 border text-right">{c.fp}</td>
                    <td className="p-1 border text-right">{c.fn}</td>
                    <td className="p-1 border text-right font-mono">{c.precision.toFixed(3)}</td>
                    <td className="p-1 border text-right font-mono">{c.recall.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}

function DisagreementPanel({ pipelineId }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    if (!pipelineId) return;
    apiFetch(`/api/qa/disagreements/${pipelineId}`).then(r => r.json()).then(setData).catch(() => {});
  }, [pipelineId]);

  if (!data?.disagreements?.length) return <p className="text-sm text-gray-400">No disagreements found.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse w-full">
        <thead><tr className="bg-gray-50">
          <th className="p-2 border text-left">Paper ID</th>
          <th className="p-2 border text-left">Dimension</th>
          <th className="p-2 border text-left">LLM Value</th>
          <th className="p-2 border text-left">Human Value</th>
        </tr></thead>
        <tbody>
          {data.disagreements.slice(0, 200).map((d, i) => (
            <tr key={i} className="bg-red-50">
              <td className="p-2 border font-mono">{d.openalex_id?.slice(0, 20)}</td>
              <td className="p-2 border">{d.dimension}</td>
              <td className="p-2 border">{d.llm_value}</td>
              <td className="p-2 border font-medium">{d.human_value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EnhancedQAView({ pipelines, onRefresh }) {
  const [pipelineId, setPipelineId] = useState(null);
  const [tab, setTab] = useState('annotate');
  const [articles, setArticles] = useState([]);
  const [dimensions, setDimensions] = useState([]);
  const [alphaDetailed, setAlphaDetailed] = useState(null);
  const [loading, setLoading] = useState(false);
  const [savedCount, setSavedCount] = useState(0);

  const generateSample = async (size = 100) => {
    if (!pipelineId) return;
    setLoading(true);
    try {
      const res = await apiFetch(`/api/qa/stratified-sample/${pipelineId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ size }),
      });
      const data = await res.json();
      if (res.ok) {
        setArticles(data.articles || []);
        setDimensions(data.dimensions || []);
      }
    } catch {}
    setLoading(false);
  };

  const loadAlpha = async () => {
    if (!pipelineId) return;
    apiFetch(`/api/qa/alpha-detailed/${pipelineId}`).then(r => r.json()).then(setAlphaDetailed).catch(() => {});
  };

  useEffect(() => { if (pipelineId) loadAlpha(); }, [pipelineId, savedCount]);

  const handleSaveAnnotations = async (annotations) => {
    const res = await apiFetch(`/api/qa/annotate/${pipelineId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ annotations }),
    });
    if (res.ok) {
      const oid = annotations[0]?.openalex_id;
      setArticles(prev => prev.filter(a => a.openalex_id !== oid));
      setSavedCount(c => c + 1);
    }
  };

  const tabs = [
    { id: 'annotate', label: 'Annotate' },
    { id: 'alpha', label: "Krippendorff's α" },
    { id: 'confusion', label: 'Confusion Matrix' },
    { id: 'disagreements', label: 'Disagreements' },
  ];

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-gray-900">Inter-Rater Reliability</h2>
        <p className="text-sm text-gray-500 mt-1">Stratified annotation, Krippendorff's alpha, confusion matrices, and disagreement analysis.</p>
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-6">
        <select value={pipelineId || ''} onChange={e => setPipelineId(Number(e.target.value) || null)}
          className="border rounded-lg px-3 py-2 text-sm min-w-[200px]">
          <option value="">Select pipeline...</option>
          {pipelines.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <span className="text-xs text-gray-500">{savedCount} annotations saved this session</span>
      </div>

      {pipelineId && (
        <>
          {/* Alpha overview */}
          {alphaDetailed?.dimensions && (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
              {Object.entries(alphaDetailed.dimensions).map(([dim, info]) => (
                <AlphaGauge key={dim} label={dim.replace(/_/g, ' ')} alpha={info.alpha} />
              ))}
            </div>
          )}

          <div className="flex gap-1 mb-4 border-b">
            {tabs.map(t => (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={`px-3 py-2 text-sm font-medium border-b-2 transition
                  ${tab === t.id ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
                {t.label}
              </button>
            ))}
          </div>

          {tab === 'annotate' && (
            <div>
              <div className="flex items-center gap-3 mb-4">
                <button onClick={() => generateSample(100)} disabled={loading}
                  className="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50">
                  {loading ? 'Generating…' : 'Generate QA Sample (100)'}
                </button>
                <button onClick={() => generateSample(50)} disabled={loading}
                  className="bg-gray-600 text-white text-sm px-4 py-2 rounded-lg font-medium hover:bg-gray-700 disabled:opacity-50">
                  Quick 50
                </button>
                <span className="text-xs text-gray-500">{articles.length} papers pending</span>
              </div>
              <div className="space-y-3">
                {articles.map(a => (
                  <AnnotationRow key={a.openalex_id} article={a} dimensions={dimensions} onSave={handleSaveAnnotations} />
                ))}
              </div>
              {articles.length === 0 && !loading && (
                <div className="text-center text-gray-400 py-12">
                  Click "Generate QA Sample" to load articles for annotation.
                </div>
              )}
            </div>
          )}

          {tab === 'alpha' && (
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              <h3 className="text-lg font-semibold mb-3">Krippendorff's Alpha per Dimension</h3>
              {alphaDetailed?.dimensions ? (
                <table className="text-sm border-collapse w-full">
                  <thead><tr className="bg-gray-50">
                    <th className="p-2 border text-left">Dimension</th>
                    <th className="p-2 border text-right">Alpha</th>
                    <th className="p-2 border text-right">N Items</th>
                    <th className="p-2 border text-right">N Coders</th>
                    <th className="p-2 border text-left">Interpretation</th>
                  </tr></thead>
                  <tbody>
                    {Object.entries(alphaDetailed.dimensions).map(([dim, info]) => (
                      <tr key={dim}>
                        <td className="p-2 border font-medium">{dim.replace(/_/g, ' ')}</td>
                        <td className="p-2 border text-right font-mono">
                          {info.alpha != null ? info.alpha.toFixed(4) : '—'}
                        </td>
                        <td className="p-2 border text-right">{info.n_items}</td>
                        <td className="p-2 border text-right">{info.n_coders}</td>
                        <td className="p-2 border">
                          <span className={`text-xs px-2 py-0.5 rounded
                            ${info.interpretation === 'good agreement' ? 'bg-green-100 text-green-700' :
                              info.interpretation === 'tentative agreement' ? 'bg-yellow-100 text-yellow-700' :
                              'bg-red-100 text-red-700'}`}>
                            {info.interpretation}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p className="text-gray-400">No annotation data available yet.</p>}
              <button onClick={loadAlpha} className="mt-3 text-sm text-blue-600 hover:text-blue-800 font-medium">
                Recalculate
              </button>
            </div>
          )}

          {tab === 'confusion' && (
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              <h3 className="text-lg font-semibold mb-3">Confusion Matrices</h3>
              <ConfusionPanel pipelineId={pipelineId} />
            </div>
          )}

          {tab === 'disagreements' && (
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              <h3 className="text-lg font-semibold mb-3">Per-Item Disagreements</h3>
              <DisagreementPanel pipelineId={pipelineId} />
            </div>
          )}
        </>
      )}

      {!pipelineId && (
        <div className="text-center text-gray-400 py-16">Select a pipeline to start inter-rater reliability analysis.</div>
      )}
    </div>
  );
}
