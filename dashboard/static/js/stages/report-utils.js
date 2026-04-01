// ── Stage: Report ─────────────────────────────────────────────────────────────

const JSON_DISPLAY_COLS = new Set([
  'matched_fuel_terms', 'matched_impact_terms', 'authors_countries',
  'authors_institutions', 'llm_fuel_classification', 'llm_ml_paradigm', 'llm_ml_method',
  'llm_ml_model_type', 'concepts', 'topics', 'llm_models',
]);

// Use browser Intl.DisplayNames to convert ISO-3166 alpha-2 codes to full names
const _regionNames = (() => {
  try { return new Intl.DisplayNames(['en'], { type: 'region' }); } catch { return null; }
})();
function countryDisplay(code) {
  if (!code) return code;
  try { return _regionNames ? (_regionNames.of(code.toUpperCase()) || code) : code; }
  catch { return code; }
}
function countryWikiUrl(code) {
  const name = countryDisplay(code);
  return `https://en.wikipedia.org/wiki/${encodeURIComponent(name.replace(/ /g, '_'))}`;
}

function RecordModal({ record, fuelSubgroups, onClose }) {
  if (!record) return null;

  useEffect(() => {
    const h = e => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, [onClose]);

  const oaId  = record.openalex_id || '';
  const rawDoi = (record.doi || '').replace(/^https?:\/\/(dx\.)?doi\.org\//, '');
  const doiUrl = rawDoi ? `https://doi.org/${rawDoi}` : null;
  const oaUrl  = oaId ? `https://openalex.org/${oaId}` : null;

  const parseJsonArr = v => {
    if (!v) return [];
    try { const p = JSON.parse(v); return Array.isArray(p) ? p : []; } catch { return []; }
  };
  const fuelTerms    = parseJsonArr(record.matched_fuel_terms);
  const impactTerms  = parseJsonArr(record.matched_impact_terms);
  const institutions = parseJsonArr(record.authors_institutions);
  const countries    = parseJsonArr(record.authors_countries);
  const llmScopes    = parseJsonArr(record.llm_fuel_classification).filter(v => v && v.toLowerCase() !== 'none');
  const llmParadigms = parseJsonArr(record.llm_ml_paradigm).filter(v => v && v.toLowerCase() !== 'none');
  const llmMethods   = parseJsonArr(record.llm_ml_method).filter(v => v && v.toLowerCase() !== 'none');
  const llmModels    = parseJsonArr(record.llm_models);
  const hasLLM       = record.llm_is_hydrocarbon !== undefined && record.llm_is_hydrocarbon !== null;

  const fwci = record.fwci != null ? Number(record.fwci) : null;
  const fwciColor = !fwci ? 'text-gray-400' : fwci >= 3 ? 'text-green-600 font-bold' : fwci >= 1 ? 'text-blue-600' : 'text-gray-500';

  const recSubgroups = useMemo(
    () => computeRowSubgroups(record.matched_fuel_terms, fuelSubgroups || []),
    [record.matched_fuel_terms, fuelSubgroups]
  );

  const OA_STYLE = {
    gold:     'bg-yellow-50 text-yellow-700 border-yellow-200',
    green:    'bg-green-50 text-green-700 border-green-200',
    hybrid:   'bg-purple-50 text-purple-700 border-purple-200',
    bronze:   'bg-orange-50 text-orange-700 border-orange-200',
    closed:   'bg-gray-50 text-gray-500 border-gray-200',
  };
  const oaStyle = OA_STYLE[record.open_access_oa_status] || OA_STYLE.closed;

  const Chip = ({ children, cls = 'bg-gray-100 text-gray-600 border-gray-200' }) => (
    <span className={`text-xs px-2 py-0.5 rounded-full border ${cls}`}>{children}</span>
  );

  const SectionHead = ({ children }) => (
    <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">{children}</h4>
  );

  return (
    <div className="fixed inset-0 bg-gray-900/70 flex items-start justify-center z-50 p-4 pt-10 overflow-y-auto"
      onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl mb-10">

        {/* ── Title + links ── */}
        <div className="p-6 pb-4 border-b border-gray-100">
          <div className="flex gap-3 items-start">
            <div className="flex-1 min-w-0">
              <h3 className="text-base font-bold text-gray-900 leading-snug mb-3">
                {record.title || oaId || 'Record'}
              </h3>
              <div className="flex flex-wrap gap-1.5">
                {doiUrl && <a href={doiUrl} target="_blank" rel="noopener"
                  className="text-xs px-2.5 py-1 bg-blue-600 text-white rounded-full hover:bg-blue-700 transition font-medium">
                  DOI ↗
                </a>}
                {oaUrl && <a href={oaUrl} target="_blank" rel="noopener"
                  className="text-xs px-2.5 py-1 bg-purple-100 text-purple-700 rounded-full hover:bg-purple-200 transition border border-purple-200">
                  OpenAlex ↗
                </a>}
                {record.open_access_oa_status && (
                  <Chip cls={oaStyle}>{record.open_access_oa_status} OA</Chip>
                )}
                {recSubgroups.map(sg => (
                  <Chip key={sg} cls="bg-blue-50 text-blue-700 border-blue-200">{sg}</Chip>
                ))}
              </div>
            </div>
            <button onClick={onClose} title="Close (Esc)"
              className="flex-shrink-0 p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12"/>
              </svg>
            </button>
          </div>
        </div>

        {/* ── Stats bar ── */}
        <div className="grid grid-cols-4 divide-x divide-gray-100 border-b border-gray-100 bg-gray-50/50">
          {[
            { label: 'Year',  value: record.publication_year || '—', cls: 'text-gray-800' },
            { label: 'FWCI',  value: fwci != null ? fwci.toFixed(2) : '—', cls: fwciColor },
            { label: 'Type',  value: record.type || '—', cls: 'text-gray-700' },
            { label: 'Language', value: (record.language || '—').toUpperCase(), cls: 'text-gray-700' },
          ].map(({ label, value, cls }) => (
            <div key={label} className="px-4 py-3 text-center">
              <div className="text-xs text-gray-400 mb-0.5 uppercase tracking-wide">{label}</div>
              <div className={`text-sm ${cls}`}>{value}</div>
            </div>
          ))}
        </div>

        {/* ── Body ── */}
        <div className="p-6 space-y-5 overflow-y-auto max-h-[55vh]">

          {/* Abstract */}
          {record.abstract_text && (
            <div>
              <SectionHead>Abstract</SectionHead>
              <p className="text-sm text-gray-700 leading-relaxed">{record.abstract_text}</p>
            </div>
          )}

          {/* Matched keywords */}
          {(fuelTerms.length > 0 || impactTerms.length > 0) && (
            <div>
              <SectionHead>Matched Keywords</SectionHead>
              <div className="space-y-2">
                {fuelTerms.length > 0 && (
                  <div className="flex flex-wrap gap-1 items-center">
                    <span className="text-xs text-gray-400 w-12 flex-shrink-0">Fuel</span>
                    {fuelTerms.map(t => <Chip key={t} cls="bg-orange-50 text-orange-700 border-orange-200">{t}</Chip>)}
                  </div>
                )}
                {impactTerms.length > 0 && (
                  <div className="flex flex-wrap gap-1 items-center">
                    <span className="text-xs text-gray-400 w-12 flex-shrink-0">Impact</span>
                    {impactTerms.map(t => <Chip key={t} cls="bg-teal-50 text-teal-700 border-teal-200">{t}</Chip>)}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Affiliations */}
          {(institutions.length > 0 || countries.length > 0) && (
            <div>
              <SectionHead>Affiliations</SectionHead>
              <div className="space-y-2">
                {institutions.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {institutions.map(inst => (
                      <Chip key={inst} cls="bg-purple-50 text-purple-700 border-purple-200">{inst}</Chip>
                    ))}
                  </div>
                )}
                {countries.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {countries.map(c => (
                      <a key={c} href={countryWikiUrl(c)} target="_blank" rel="noreferrer"
                        className="text-xs px-2 py-0.5 bg-gray-100 text-gray-600 rounded-full border border-gray-200 hover:bg-gray-200 transition">
                        {countryDisplay(c)}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* LLM classification */}
          {hasLLM && (
            <div className="bg-indigo-50 rounded-xl p-4 border border-indigo-100">
              <SectionHead>
                <span className="text-indigo-500">AI / LLM Classification</span>
              </SectionHead>
              <div className="space-y-2.5">
                <div className="flex flex-wrap gap-2">
                  {record.llm_is_hydrocarbon != null && (
                    <Chip cls={record.llm_is_hydrocarbon ? 'bg-green-100 text-green-700 border-green-200' : 'bg-gray-100 text-gray-500 border-gray-200'}>
                      {record.llm_is_hydrocarbon ? '✓ Hydrocarbon' : '✗ Non-hydrocarbon'}
                    </Chip>
                  )}
                  {record.llm_confidence && (
                    <Chip cls={record.llm_confidence === 'High' ? 'bg-green-100 text-green-700 border-green-200' : record.llm_confidence === 'Medium' ? 'bg-amber-100 text-amber-700 border-amber-200' : 'bg-red-100 text-red-600 border-red-200'}>
                      {record.llm_confidence} confidence
                    </Chip>
                  )}
                </div>
                {llmScopes.length > 0 && (
                  <div>
                    <span className="text-xs text-gray-500 block mb-1">Research scope</span>
                    <div className="flex flex-wrap gap-1">
                      {llmScopes.map(s => <Chip key={s} cls="bg-indigo-100 text-indigo-700 border-indigo-200">{s}</Chip>)}
                    </div>
                  </div>
                )}
                {(llmParadigms.length > 0 || llmMethods.length > 0) && (
                  <div className="flex gap-6">
                    {llmParadigms.length > 0 && (
                      <div>
                        <span className="text-xs text-gray-500 block mb-1">ML paradigm</span>
                        <div className="flex flex-wrap gap-1">
                          {llmParadigms.map(p => <Chip key={p} cls="bg-blue-100 text-blue-700 border-blue-200">{p}</Chip>)}
                        </div>
                      </div>
                    )}
                    {llmMethods.length > 0 && (
                      <div>
                        <span className="text-xs text-gray-500 block mb-1">ML method</span>
                        <div className="flex flex-wrap gap-1">
                          {llmMethods.map(m => <Chip key={m} cls="bg-cyan-100 text-cyan-700 border-cyan-200">{m}</Chip>)}
                        </div>
                      </div>
                    )}
                  </div>
                )}
                {llmModels.length > 0 && (
                  <div>
                    <span className="text-xs text-gray-500 block mb-1">Models mentioned</span>
                    <div className="flex flex-wrap gap-1">
                      {llmModels.map((m, i) => {
                        const name = typeof m === 'object' ? (m.name || JSON.stringify(m)) : String(m);
                        return <Chip key={i} cls="bg-gray-100 text-gray-600 border-gray-200">{name}</Chip>;
                      })}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* ── Footer ── */}
        <div className="px-6 py-3 border-t border-gray-100 bg-gray-50/50 rounded-b-2xl flex items-center justify-between">
          <span className="text-xs text-gray-400 font-mono">{oaId}</span>
          <button onClick={onClose}
            className="text-xs px-3 py-1.5 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function ColPickerDropdown({ label, allCols, selected, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const handler = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);
  const toggle = col => {
    if (selected.includes(col)) onChange(selected.filter(c => c !== col));
    else onChange([...selected, col]);
  };
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(o => !o)}
        className="border rounded-lg px-3 py-2 text-sm flex items-center gap-1.5 hover:bg-gray-50 whitespace-nowrap">
        {label}
        {selected.length > 0 && <span className="bg-blue-100 text-blue-700 text-xs px-1.5 py-0.5 rounded-full">{selected.length}</span>}
        <svg className="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7"/>
        </svg>
      </button>
      {open && (
        <div className="absolute z-20 top-full left-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg p-2 min-w-[220px] max-h-72 overflow-y-auto">
          <div className="flex gap-2 mb-2 border-b pb-2">
            <button onClick={() => onChange(allCols)} className="text-xs text-blue-600 hover:underline">All</button>
            <button onClick={() => onChange([])} className="text-xs text-gray-500 hover:underline">None</button>
          </div>
          {allCols.map(col => (
            <label key={col} className="flex items-center gap-2 px-1 py-0.5 rounded hover:bg-gray-50 cursor-pointer text-xs">
              <input type="checkbox" checked={selected.includes(col)} onChange={() => toggle(col)} className="accent-blue-600" />
              <span className="font-mono">{col}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// Compute which fuel sub-groups a record belongs to, given its matched_fuel_terms JSON string
function computeRowSubgroups(matchedJson, subgroupDefs) {
  if (!matchedJson || !subgroupDefs || !subgroupDefs.length) return [];
  let terms;
  try { terms = JSON.parse(matchedJson); } catch { return []; }
  if (!Array.isArray(terms)) return [];
  const termSet = new Set(terms.map(t => (t || '').toLowerCase()));
  const termToSg = {};
  for (const sg of subgroupDefs) {
    if (sg.is_others) continue;
    for (const t of (sg.terms || [])) termToSg[t.toLowerCase()] = sg.name;
  }
  const matched = new Set();
  for (const t of termSet) { if (termToSg[t]) matched.add(termToSg[t]); }
  if (matched.size === 0) {
    const othersName = (subgroupDefs.find(sg => sg.is_others) || {}).name || 'Others';
    return [othersName];
  }
  return [...matched];
}

// ── Chart panels (server-rendered images) ─────────────────────────────────────

function ChartPanel({ chartId, pipelineId, topN, title, sql, onViewQuery }) {
  const [showSql, setShowSql] = useState(false);
  const [imgErr, setImgErr]   = useState(false);
  const [imgLoaded, setImgLoaded] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const imgUrl = `/api/charts/image?pipeline_id=${pipelineId}&chart=${chartId}&top_n=${topN}&_r=${refreshKey}`;

  // Reset loaded state whenever the URL changes
  useEffect(() => { setImgLoaded(false); setImgErr(false); }, [chartId, pipelineId, topN, refreshKey]);

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
        <span className="text-sm font-semibold text-gray-800">{title || chartId}</span>
        <div className="flex items-center gap-2">
          <button onClick={() => setRefreshKey(k => k + 1)} title="Refresh chart"
            className="text-gray-300 hover:text-gray-500 transition text-xs px-1">
            ↻
          </button>
          {sql && (
            <button onClick={() => setShowSql(s => !s)}
              className={`text-xs px-2 py-0.5 rounded border transition
                ${showSql ? 'bg-gray-100 text-gray-700 border-gray-300'
                           : 'text-gray-400 border-gray-200 hover:text-gray-600'}`}>
              SQL
            </button>
          )}
          {sql && (
            <button onClick={() => onViewQuery(sql)}
              className="text-xs text-blue-500 hover:text-blue-700 hover:underline">
              Edit & Run →
            </button>
          )}
        </div>
      </div>
      {showSql && sql && (
        <div className="px-4 py-2 bg-gray-50 border-b border-gray-100">
          <pre className="text-xs font-mono text-gray-600 whitespace-pre-wrap overflow-x-auto">{sql}</pre>
        </div>
      )}
      <div className="p-3 relative">
        {!imgLoaded && !imgErr && (
          <div className="flex items-center justify-center h-48 text-gray-300">
            <span className="w-6 h-6 border-2 border-gray-200 border-t-blue-400 rounded-full animate-spin" />
          </div>
        )}
        {imgErr ? (
          <div className="flex items-center justify-center h-32 text-gray-400 text-sm">
            Could not render chart
          </div>
        ) : (
          <img src={imgUrl} alt={title}
            className={`w-full h-auto transition-opacity duration-300 ${imgLoaded ? 'opacity-100' : 'opacity-0 absolute inset-0'}`}
            onError={() => { setImgErr(true); setImgLoaded(false); }}
            onLoad={() => setImgLoaded(true)} />
        )}
      </div>
    </div>
  );
}

function ChartsPanel({ pipelineId, isLLM, topN, onTopNChange, onViewQuery }) {
  const [configs, setConfigs] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    apiFetch('/api/charts/configs')
      .then(r => r.json())
      .then(list => setConfigs(list))
      .catch(() => setConfigs([]));
  }, []);

  const visible = configs
    ? CHART_ORDER.filter(id => {
        const cfg = configs.find(c => c.id === id);
        return cfg && (!cfg.llm_only || isLLM);
      })
    : [];

  const cfgMap = useMemo(
    () => Object.fromEntries((configs || []).map(c => [c.id, c])),
    [configs]
  );

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl border border-gray-200 px-5 py-3 flex items-center gap-4">
        <span className="text-sm font-medium text-gray-600">Top N per chart</span>
        <input type="range" min="5" max="50" step="5" value={topN}
          onChange={e => onTopNChange(Number(e.target.value))}
          className="w-36 accent-emerald-600" />
        <span className="text-sm font-semibold text-emerald-700 w-6">{topN}</span>
        <button onClick={() => setRefreshKey(k => k + 1)} title="Refresh all charts"
          className="ml-auto text-sm text-gray-500 hover:text-gray-700 border border-gray-200 hover:border-gray-400 px-3 py-1 rounded-lg transition flex items-center gap-1.5">
          ↻ Refresh
        </button>
      </div>
      {configs === null ? (
        <div className="flex items-center justify-center py-20 text-gray-400">
          <span className="w-5 h-5 border-2 border-gray-300 border-t-blue-500 rounded-full animate-spin mr-2" />
          Loading…
        </div>
      ) : visible.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center text-gray-400">
          No chart data available yet — run the search stage first.
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {visible.map(id => (
            <ChartPanel key={`${id}-${topN}-${refreshKey}`} chartId={id} pipelineId={pipelineId}
              topN={topN}
              title={cfgMap[id]?.title || id}
              sql={cfgMap[id]?.sql || ''}
              onViewQuery={onViewQuery} />
          ))}
        </div>
      )}
    </div>
  );
}
