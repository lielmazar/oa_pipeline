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
