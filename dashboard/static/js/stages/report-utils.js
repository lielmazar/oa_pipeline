// ── Stage: Report ─────────────────────────────────────────────────────────────

const JSON_DISPLAY_COLS = new Set([
  'matched_fuel_terms', 'matched_impact_terms', 'authors_countries',
  'authors_institutions', 'llm_fuel_classification',
  'llm_ml_role', 'llm_ml_algorithm', 'llm_scope',
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
  const llmScopes      = parseJsonArr(record.llm_fuel_classification).filter(v => v && v.toLowerCase() !== 'none');
  const llmValueChain  = parseJsonArr(record.llm_scope).filter(v => v && v.toLowerCase() !== 'none');
  const llmRoles       = parseJsonArr(record.llm_ml_role).filter(v => v && v.toLowerCase() !== 'none');
  const llmAlgorithms  = parseJsonArr(record.llm_ml_algorithm).filter(v => v && v.toLowerCase() !== 'none' && v.toLowerCase() !== 'other_ml');
  const llmDataSrc     = (record.llm_ml_data_source || '').trim().toLowerCase();
  // v2 compat
  const llmParadigms   = parseJsonArr(record.llm_ml_paradigm).filter(v => v && v.toLowerCase() !== 'none');
  const llmMethods     = parseJsonArr(record.llm_ml_method).filter(v => v && v.toLowerCase() !== 'none');
  const llmModels    = parseJsonArr(record.llm_models);
  const hasLLM       = record.llm_is_hydrocarbon !== undefined && record.llm_is_hydrocarbon !== null;

  const fwci = record.fwci != null ? Number(record.fwci) : null;
  const fwciColor = !fwci ? 'text-gray-400' : fwci >= 3 ? 'text-green-600 font-bold' : fwci >= 1 ? 'text-blue-600' : 'text-gray-500';

  // Prefer DB-stored subgroups (matched_fuel_subgroups) over client-side computation
  const recSubgroups = useMemo(() => {
    if (record.matched_fuel_subgroups) {
      try {
        const p = JSON.parse(record.matched_fuel_subgroups);
        if (Array.isArray(p) && p.length > 0) return p;
      } catch {}
    }
    return computeRowSubgroups(record.matched_fuel_terms, fuelSubgroups || []);
  }, [record.matched_fuel_subgroups, record.matched_fuel_terms, fuelSubgroups]);

  const recImpactSubgroups = useMemo(() => {
    if (record.matched_impact_subgroups) {
      try {
        const p = JSON.parse(record.matched_impact_subgroups);
        if (Array.isArray(p) && p.length > 0) return p;
      } catch {}
    }
    return [];
  }, [record.matched_impact_subgroups]);

  const citeCount = record.cited_by_count != null ? Number(record.cited_by_count) : null;

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
                {recImpactSubgroups.map(sg => (
                  <Chip key={sg} cls="bg-teal-50 text-teal-700 border-teal-200">{sg}</Chip>
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
            { label: 'Year',    value: record.publication_year || '—', cls: 'text-gray-800' },
            { label: 'FWCI',    value: fwci != null ? fwci.toFixed(2) : '—', cls: fwciColor },
            { label: 'Cited',   value: citeCount != null ? citeCount.toLocaleString() : '—', cls: citeCount > 0 ? 'text-blue-700' : 'text-gray-400' },
            { label: 'Type',    value: record.type || '—', cls: 'text-gray-700' },
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

          {/* Publisher / Source */}
          {(record.primary_location_source_display_name || record.primary_location_source_host_organization_name || record.primary_location_source_issn_l) && (
            <div>
              <SectionHead>Publication Venue</SectionHead>
              <div className="text-sm text-gray-700 space-y-1">
                {record.primary_location_source_display_name && (
                  <div><span className="text-xs text-gray-400">Source: </span>{record.primary_location_source_display_name}</div>
                )}
                {record.primary_location_source_host_organization_name && (
                  <div><span className="text-xs text-gray-400">Publisher: </span>{record.primary_location_source_host_organization_name}</div>
                )}
                {record.primary_location_source_issn_l && (
                  <div><span className="text-xs text-gray-400">ISSN-L: </span><code className="text-xs bg-gray-100 px-1 rounded">{record.primary_location_source_issn_l}</code></div>
                )}
                {record.primary_location_source_type && (
                  <div><span className="text-xs text-gray-400">Type: </span>{record.primary_location_source_type}</div>
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
                {llmValueChain.length > 0 && (
                  <div>
                    <span className="text-xs text-gray-500 block mb-1">Value chain</span>
                    <div className="flex flex-wrap gap-1">
                      {llmValueChain.map(s => <Chip key={s} cls="bg-sky-100 text-sky-700 border-sky-200">{s}</Chip>)}
                    </div>
                  </div>
                )}
                {(llmRoles.length > 0 || llmAlgorithms.length > 0 || llmDataSrc || llmParadigms.length > 0 || llmMethods.length > 0) && (
                  <div className="flex flex-wrap gap-6">
                    {llmRoles.length > 0 && (
                      <div>
                        <span className="text-xs text-gray-500 block mb-1">ML role</span>
                        <div className="flex flex-wrap gap-1">
                          {llmRoles.map(v => <Chip key={v} cls="bg-blue-100 text-blue-700 border-blue-200">{v}</Chip>)}
                        </div>
                      </div>
                    )}
                    {llmAlgorithms.length > 0 && (
                      <div>
                        <span className="text-xs text-gray-500 block mb-1">Algorithm</span>
                        <div className="flex flex-wrap gap-1">
                          {llmAlgorithms.map(v => <Chip key={v} cls="bg-cyan-100 text-cyan-700 border-cyan-200">{v}</Chip>)}
                        </div>
                      </div>
                    )}
                    {llmDataSrc && llmDataSrc !== 'none' && (
                      <div>
                        <span className="text-xs text-gray-500 block mb-1">Data source</span>
                        <Chip cls="bg-amber-100 text-amber-700 border-amber-200">{llmDataSrc}</Chip>
                      </div>
                    )}
                    {/* v2 compat */}
                    {llmRoles.length === 0 && llmParadigms.length > 0 && (
                      <div>
                        <span className="text-xs text-gray-500 block mb-1">ML paradigm</span>
                        <div className="flex flex-wrap gap-1">
                          {llmParadigms.map(p => <Chip key={p} cls="bg-blue-100 text-blue-700 border-blue-200">{p}</Chip>)}
                        </div>
                      </div>
                    )}
                    {llmAlgorithms.length === 0 && llmMethods.length > 0 && (
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

// ── Chart image concurrency queue + dedup cache ──────────────────────────────

const _chartQueue = [];
const _chartInFlight = new Map();   // url -> Promise<blobUrl>
const _chartBlobCache = new Map();  // url (without _r) -> blobUrl
const CHART_CONCURRENCY = 4;
let _chartRunning = 0;

function _drainChartQueue() {
  while (_chartRunning < CHART_CONCURRENCY && _chartQueue.length > 0) {
    const { url, resolve, reject } = _chartQueue.shift();
    _chartRunning++;
    fetch(url)
      .then(r => { if (!r.ok) throw new Error(r.status); return r.blob(); })
      .then(blob => {
        const blobUrl = URL.createObjectURL(blob);
        // Cache with the base URL (strip _r= param)
        const baseUrl = url.replace(/&_r=\d+/, '');
        _chartBlobCache.set(baseUrl, blobUrl);
        resolve(blobUrl);
      })
      .catch(reject)
      .finally(() => { _chartRunning--; _chartInFlight.delete(url); _drainChartQueue(); });
  }
}

function fetchChartImage(url) {
  // Check blob cache first (keyed without the _r= refresh param)
  const baseUrl = url.replace(/&_r=\d+/, '');
  const cached = _chartBlobCache.get(baseUrl);
  if (cached) return Promise.resolve(cached);

  // Deduplicate in-flight requests for the exact same URL
  if (_chartInFlight.has(url)) return _chartInFlight.get(url);

  const p = new Promise((resolve, reject) => {
    _chartQueue.push({ url, resolve, reject });
  });
  _chartInFlight.set(url, p);
  _drainChartQueue();
  return p;
}

// ── Lazy-load hook (IntersectionObserver) ─────────────────────────────────────

function useInView(rootMargin = '200px') {
  const ref = useRef(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) { setInView(true); obs.disconnect(); } },
      { rootMargin }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [rootMargin]);
  return [ref, inView];
}

// ── Chart panels (server-rendered images) ─────────────────────────────────────

function ChartPanel({ chartId, pipelineId, topN, title, subsetIdx, chartParams, onOpenRecords, chartSql }) {
  const [imgErr, setImgErr]     = useState(false);
  const [imgLoaded, setImgLoaded] = useState(false);
  const [blobUrl, setBlobUrl]   = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [showParams, setShowParams] = useState(false);
  const [localTopN, setLocalTopN]       = useState('');   // '' = use global topN
  const [cutoffYear, setCutoffYear]     = useState(2000);
  const [termsPerSg, setTermsPerSg]     = useState('');   // '' = show all terms

  // Lazy loading: only fetch when the card scrolls into view
  const [sentinelRef, inView] = useInView('300px');

  const hasCutoffYear  = (chartParams || []).includes('cutoff_year');
  const hasTopN        = (chartParams || []).includes('top_n');
  const hasTermsPerSg  = (chartParams || []).includes('terms_per_subgroup');
  const hasAnyParams   = hasCutoffYear || hasTopN || hasTermsPerSg;

  const effectiveTopN     = (localTopN && !isNaN(Number(localTopN))) ? Number(localTopN) : topN;
  const effectiveTermsPerSg = (termsPerSg && !isNaN(Number(termsPerSg)) && Number(termsPerSg) > 0)
    ? Number(termsPerSg) : null;

  const imgUrl = `/api/charts/image?pipeline_id=${pipelineId}&chart=${chartId}&top_n=${effectiveTopN}&_r=${refreshKey}` +
    (subsetIdx != null ? `&subset_idx=${subsetIdx}` : '') +
    (hasCutoffYear ? `&cutoff_year=${cutoffYear}` : '') +
    (hasTermsPerSg && effectiveTermsPerSg != null ? `&terms_per_subgroup=${effectiveTermsPerSg}` : '');

  // Fetch chart image through the concurrency-limited queue
  useEffect(() => {
    if (!inView) return;
    setImgLoaded(false); setImgErr(false); setBlobUrl(null);
    let cancelled = false;
    fetchChartImage(imgUrl).then(url => {
      if (!cancelled) { setBlobUrl(url); setImgLoaded(true); }
    }).catch(() => {
      if (!cancelled) setImgErr(true);
    });
    return () => { cancelled = true; };
  }, [imgUrl, inView]);

  // Force refresh: evict from blob cache
  const handleRefresh = useCallback(() => {
    const baseUrl = imgUrl.replace(/&_r=\d+/, '');
    _chartBlobCache.delete(baseUrl);
    setRefreshKey(k => k + 1);
  }, [imgUrl]);

  return (
    <div ref={sentinelRef} className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
        <span className="text-sm font-semibold text-gray-800">{title || chartId}</span>
        <div className="flex items-center gap-2">
          {onOpenRecords && chartSql && (
            <button onClick={() => onOpenRecords(chartSql, title || chartId)} title="View records for this chart"
              className="text-xs px-2 py-0.5 rounded border border-blue-200 text-blue-500 hover:bg-blue-50 hover:text-blue-700 transition flex items-center gap-1">
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 10h16M4 14h16M4 18h16"/>
              </svg>
              Records
            </button>
          )}
          {hasAnyParams && (
            <button onClick={() => setShowParams(s => !s)} title="Chart settings"
              className={`text-xs px-1.5 py-0.5 rounded border transition ${showParams ? 'bg-gray-100 text-gray-700 border-gray-300' : 'text-gray-400 border-gray-200 hover:text-gray-600'}`}>
              ⚙
            </button>
          )}
          <button onClick={handleRefresh} title="Refresh chart"
            className="text-gray-300 hover:text-gray-500 transition text-xs px-1">
            ↻
          </button>
        </div>
      </div>
      {showParams && hasAnyParams && (
        <div className="px-4 py-2 bg-gray-50 border-b border-gray-100 flex items-center gap-4">
          {hasCutoffYear && (
            <label className="flex items-center gap-1.5 text-xs text-gray-600">
              Cutoff year (≤)
              <input type="number" value={cutoffYear} min="1900" max="2020" step="5"
                onChange={e => setCutoffYear(Number(e.target.value) || 2000)}
                className="border rounded px-2 py-0.5 w-20 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400" />
            </label>
          )}
          {hasTopN && (
            <label className="flex items-center gap-1.5 text-xs text-gray-600">
              Show top
              <input type="number" value={localTopN} min="5" max="100" step="5" placeholder={String(topN)}
                onChange={e => setLocalTopN(e.target.value)}
                className="border rounded px-2 py-0.5 w-16 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400" />
            </label>
          )}
          {hasTermsPerSg && (
            <label className="flex items-center gap-1.5 text-xs text-gray-600">
              Terms per subgroup
              <input type="number" value={termsPerSg} min="1" max="50" step="1" placeholder="All"
                onChange={e => setTermsPerSg(e.target.value)}
                className="border rounded px-2 py-0.5 w-16 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400" />
            </label>
          )}
        </div>
      )}
      <div className="p-3 relative">
        {!inView ? (
          <div className="h-48" />
        ) : !imgLoaded && !imgErr ? (
          <div className="flex items-center justify-center h-48 text-gray-300">
            <span className="w-6 h-6 border-2 border-gray-200 border-t-blue-400 rounded-full animate-spin" />
          </div>
        ) : imgErr ? (
          <div className="flex items-center justify-center h-32 text-gray-400 text-sm">
            Could not render chart
          </div>
        ) : (
          <img src={blobUrl} alt={title}
            className="w-full h-auto transition-opacity duration-300 opacity-100" />
        )}
      </div>
    </div>
  );
}

// ── Co-occurrence heatmap shared helpers ──────────────────────────────────────

function _buildCooccSql(a, b) {
  const aIsArr = a.isArray !== false;
  const bIsArr = b.isArray !== false;
  const dimA   = aIsArr ? '_a.value' : a.col;
  const dimB   = bIsArr ? '_b.value' : b.col;
  const from   = ['filtered_records'];
  if (aIsArr) from.push(`json_each(${a.col}) _a`);
  if (bIsArr) from.push(`json_each(${b.col}) _b`);
  const where  = [];
  if (aIsArr) {
    if (a.terms && a.terms.length) {
      where.push(`_a.value IN (${a.terms.map(t => `'${t.replace(/'/g, "''")}'`).join(',')})`);
    } else {
      where.push(`_a.value IS NOT NULL AND _a.value != '' AND LOWER(_a.value) NOT IN ('none','null','[]')`);
    }
  } else {
    where.push(`${a.col} IS NOT NULL AND CAST(${a.col} AS TEXT) != ''`);
  }
  if (bIsArr) {
    if (b.terms && b.terms.length) {
      where.push(`_b.value IN (${b.terms.map(t => `'${t.replace(/'/g, "''")}'`).join(',')})`);
    } else {
      where.push(`_b.value IS NOT NULL AND _b.value != '' AND LOWER(_b.value) NOT IN ('none','null','[]')`);
    }
  } else {
    where.push(`${b.col} IS NOT NULL AND CAST(${b.col} AS TEXT) != ''`);
  }
  return (
    `SELECT ${dimA} AS dim_a, ${dimB} AS dim_b, COUNT(DISTINCT openalex_id) AS count ` +
    `FROM ${from.join(', ')} WHERE ${where.join(' AND ')} GROUP BY dim_a, dim_b`
  );
}

function _runCooccQuery(pipelineId, axisA, axisB, setData, setLoading, setError) {
  setLoading(true); setError(null); setData(null);
  const sql = _buildCooccSql(axisA, axisB);
  apiFetch('/api/db/query', { method: 'POST', body: JSON.stringify({ pipeline_id: pipelineId, sql }) })
    .then(r => r.json())
    .then(d => {
      if (d.error) { setError(d.error); return; }
      const rows = (d.rows || []).filter(r => r[0] && r[1] && Number(r[2]) > 0);
      const totA = {}, totB = {};
      rows.forEach(([a, b, c]) => { totA[a] = (totA[a] || 0) + c; totB[b] = (totB[b] || 0) + c; });
      const allAs  = Object.keys(totA).sort((a, b) => totA[b] - totA[a]);
      const allBs  = Object.keys(totB).sort((a, b) => totB[b] - totB[a]);
      const matrix = {};
      rows.forEach(([a, b, c]) => { matrix[`${a}\x00${b}`] = c; });
      const maxVal = Math.max(...rows.map(r => r[2]), 1);
      const rowTot = Object.fromEntries(allAs.map(a => [a, totA[a] || 0]));
      const colTot = Object.fromEntries(allBs.map(b => [b, totB[b] || 0]));
      setData({ allAs, allBs, matrix, maxVal, rowTot, colTot });
    })
    .catch(e => setError(String(e)))
    .finally(() => setLoading(false));
}

function _cellBg(count, maxVal) {
  if (!count) return null;
  const t = Math.pow(count / maxVal, 0.45);
  const r = Math.round(239 - (239 - 37)  * t);
  const g = Math.round(246 - (246 - 99)  * t);
  const b = Math.round(255 - (255 - 235) * t);
  return `rgb(${r},${g},${b})`;
}

function _exportCsv(data, labelA, labelB) {
  const { allAs, allBs, matrix } = data;
  const esc = v => `"${String(v).replace(/"/g, '""')}"`;
  const header = [esc(`${labelA} \\ ${labelB}`), ...allBs.map(esc)].join(',');
  const rows   = allAs.map(a => [esc(a), ...allBs.map(b => matrix[`${a}\x00${b}`] || 0)].join(','));
  const blob   = new Blob([[header, ...rows].join('\n')], { type: 'text/csv' });
  const url    = URL.createObjectURL(blob);
  const link   = document.createElement('a');
  link.download = `cooccurrence_${Date.now()}.csv`;
  link.href = url; link.click(); URL.revokeObjectURL(url);
}

function _exportPng(data, titleText) {
  const { allAs, allBs, matrix, maxVal } = data;
  const PAD   = 24, TITLE_H = 44, COL_HDR = 88;
  const ROW_W = Math.min(200, Math.max(100, 28 * Math.max(...allAs.map(s => s.length), 4)));
  const CELL_W = Math.max(44, Math.min(88, Math.floor(2600 / Math.max(allBs.length, 1))));
  const CELL_H = 30;
  const W = PAD * 2 + ROW_W + allBs.length * CELL_W;
  const H = PAD + TITLE_H + COL_HDR + allAs.length * CELL_H + PAD;
  const canvas = document.createElement('canvas');
  canvas.width = W * 2; canvas.height = H * 2;
  const ctx = canvas.getContext('2d');
  ctx.scale(2, 2);
  // bg
  ctx.fillStyle = '#F9FAFB'; ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = '#FFFFFF';
  ctx.fillRect(PAD, PAD + TITLE_H, ROW_W + allBs.length * CELL_W, COL_HDR + allAs.length * CELL_H);
  // title
  ctx.fillStyle = '#111827'; ctx.font = 'bold 13px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText(titleText, W / 2, PAD + 20);
  ctx.fillStyle = '#6B7280'; ctx.font = '10px sans-serif';
  ctx.fillText(`${allAs.length} rows × ${allBs.length} cols`, W / 2, PAD + 36);
  // col headers
  ctx.save();
  allBs.forEach((b, j) => {
    const x = PAD + ROW_W + j * CELL_W + CELL_W / 2;
    const y = PAD + TITLE_H + COL_HDR - 8;
    ctx.save(); ctx.translate(x, y); ctx.rotate(-Math.PI / 3.5);
    ctx.textAlign = 'right'; ctx.fillStyle = '#374151'; ctx.font = 'bold 8.5px sans-serif';
    const txt = b.length > 20 ? b.slice(0, 18) + '…' : b;
    ctx.fillText(txt, 0, 0); ctx.restore();
  });
  ctx.restore();
  // rows
  allAs.forEach((a, i) => {
    const y = PAD + TITLE_H + COL_HDR + i * CELL_H;
    ctx.fillStyle = i % 2 === 0 ? '#FFFFFF' : '#F9FAFB';
    ctx.fillRect(PAD, y, ROW_W, CELL_H);
    ctx.fillStyle = '#374151'; ctx.font = 'bold 8.5px sans-serif'; ctx.textAlign = 'left';
    const txt = a.length > 28 ? a.slice(0, 26) + '…' : a;
    ctx.fillText(txt, PAD + 6, y + CELL_H / 2 + 3);
    allBs.forEach((b, j) => {
      const x   = PAD + ROW_W + j * CELL_W;
      const cnt = matrix[`${a}\x00${b}`] || 0;
      ctx.fillStyle = _cellBg(cnt, maxVal) || (i % 2 === 0 ? '#FFFFFF' : '#F9FAFB');
      ctx.fillRect(x, y, CELL_W, CELL_H);
      ctx.strokeStyle = '#E5E7EB'; ctx.lineWidth = 0.5;
      ctx.strokeRect(x, y, CELL_W, CELL_H);
      if (cnt > 0) {
        const lum  = Math.pow(cnt / maxVal, 0.45);
        ctx.fillStyle = lum > 0.55 ? '#FFFFFF' : '#111827';
        ctx.font = 'bold 8.5px monospace'; ctx.textAlign = 'center';
        ctx.fillText(cnt >= 1000 ? `${(cnt/1000).toFixed(1)}k` : String(cnt), x + CELL_W / 2, y + CELL_H / 2 + 3);
      } else {
        ctx.fillStyle = '#D1D5DB'; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
        ctx.fillText('·', x + CELL_W / 2, y + CELL_H / 2 + 3);
      }
    });
  });
  // border lines
  ctx.strokeStyle = '#E5E7EB'; ctx.lineWidth = 0.5;
  for (let j = 0; j <= allBs.length; j++) {
    const x = PAD + ROW_W + j * CELL_W;
    ctx.beginPath(); ctx.moveTo(x, PAD + TITLE_H + COL_HDR);
    ctx.lineTo(x, PAD + TITLE_H + COL_HDR + allAs.length * CELL_H); ctx.stroke();
  }
  for (let i = 0; i <= allAs.length; i++) {
    const y = PAD + TITLE_H + COL_HDR + i * CELL_H;
    ctx.beginPath(); ctx.moveTo(PAD, y);
    ctx.lineTo(PAD + ROW_W + allBs.length * CELL_W, y); ctx.stroke();
  }
  // divider between row-header and cells
  ctx.strokeStyle = '#9CA3AF'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(PAD + ROW_W, PAD + TITLE_H);
  ctx.lineTo(PAD + ROW_W, PAD + TITLE_H + COL_HDR + allAs.length * CELL_H); ctx.stroke();
  canvas.toBlob(blob => {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.download = `cooccurrence_${Date.now()}.png`;
    link.href = url; link.click(); URL.revokeObjectURL(url);
  });
}

// ── Shared heatmap table component ────────────────────────────────────────────

function CooccurrenceTable({ data, axisA, axisB, onOpenRecords }) {
  const { allAs, allBs, matrix, maxVal, rowTot, colTot } = data;

  const openCell = (a, b, count) => {
    if (!onOpenRecords || !count) return;
    const safe = s => String(s).replace(/'/g, "''");
    const cond = (dim, val) => dim.isArray !== false
      ? `EXISTS (SELECT 1 FROM json_each(${dim.col}) WHERE LOWER(value) = LOWER('${safe(val)}'))`
      : `LOWER(CAST(${dim.col} AS TEXT)) = LOWER('${safe(val)}')`;
    onOpenRecords(
      `SELECT * FROM filtered_records WHERE ${cond(axisA, a)} AND ${cond(axisB, b)}`,
      `${a} × ${b} (${count.toLocaleString()} papers)`
    );
  };

  const maxRowTot = Math.max(...Object.values(rowTot || {}), 1);
  const maxColTot = Math.max(...Object.values(colTot || {}), 1);

  return (
    <div className="overflow-auto" style={{ maxHeight: '460px' }}>
      <table className="text-xs border-collapse" style={{ minWidth: 'max-content' }}>
        <thead className="sticky top-0 z-10 bg-white">
          <tr>
            <th className="bg-gray-50 px-3 py-2 text-left font-normal border-b border-r border-gray-200 min-w-[140px]">
              <span className="text-gray-400 text-xs">
                {allAs.length}r × {allBs.length}c
              </span>
            </th>
            {allBs.map(b => (
              <th key={b} className="bg-gray-50 border-b border-gray-200 min-w-[60px] max-w-[100px]">
                <div className="px-1.5 py-1.5 flex flex-col items-center gap-0.5">
                  <span className="block text-center text-gray-700 font-semibold leading-tight"
                    style={{ writingMode: 'vertical-lr', transform: 'rotate(180deg)', maxHeight: 80, overflow: 'hidden', fontSize: 10 }}
                    title={b}>{b}</span>
                  <span className="text-gray-400 font-normal tabular-nums" style={{ fontSize: 9 }}>
                    {(colTot[b] || 0).toLocaleString()}
                  </span>
                </div>
              </th>
            ))}
            <th className="bg-gray-50 border-b border-l border-gray-200 px-2 text-gray-400 font-normal text-xs">Total</th>
          </tr>
        </thead>
        <tbody>
          {allAs.map((a, ri) => (
            <tr key={a} className={ri % 2 === 0 ? 'bg-white' : 'bg-gray-50/60'}>
              <td className="sticky left-0 z-[5] bg-inherit px-3 py-1.5 font-medium text-gray-700 whitespace-nowrap border-r border-gray-200">
                <div className="flex items-center gap-1.5">
                  <span className="truncate block max-w-[140px]" title={a}>{a}</span>
                </div>
              </td>
              {allBs.map(b => {
                const count = matrix[`${a}\x00${b}`] || 0;
                const bg    = _cellBg(count, maxVal);
                return (
                  <td key={b} className="text-center px-0.5 py-0.5 transition-all"
                    style={{ background: bg || 'transparent' }}>
                    {count > 0 ? (
                      <button onClick={() => openCell(a, b, count)}
                        className="w-full px-1 py-0.5 rounded font-mono font-semibold hover:ring-2 hover:ring-blue-400 hover:ring-offset-1 transition-all"
                        style={{ color: Math.pow(count/maxVal, 0.45) > 0.55 ? '#fff' : '#111827', fontSize: 10 }}
                        title={`${a} × ${b}: ${count.toLocaleString()} papers — click to view`}>
                        {count >= 1000 ? `${(count/1000).toFixed(1)}k` : count}
                      </button>
                    ) : (
                      <span className="text-gray-200 select-none text-xs">·</span>
                    )}
                  </td>
                );
              })}
              <td className="border-l border-gray-200 px-2 py-1 text-right tabular-nums text-gray-500 font-medium" style={{ fontSize: 10 }}>
                <div className="flex items-center justify-end gap-1">
                  <div className="h-1.5 rounded-full bg-blue-200" style={{ width: Math.round((rowTot[a] || 0) / maxRowTot * 32) }} />
                  {(rowTot[a] || 0).toLocaleString()}
                </div>
              </td>
            </tr>
          ))}
          <tr className="bg-gray-50 border-t border-gray-200">
            <td className="sticky left-0 bg-gray-50 px-3 py-1 font-semibold text-gray-500 border-r border-gray-200 text-xs">Total</td>
            {allBs.map(b => (
              <td key={b} className="text-center px-1 py-1 tabular-nums text-gray-500 font-medium" style={{ fontSize: 9 }}>
                <div className="flex flex-col items-center gap-0.5">
                  <div className="h-1 rounded-full bg-teal-200" style={{ width: Math.round((colTot[b] || 0) / maxColTot * 36) }} />
                  {(colTot[b] || 0).toLocaleString()}
                </div>
              </td>
            ))}
            <td className="border-l border-gray-200 px-2 py-1" />
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ── Color scale legend ─────────────────────────────────────────────────────────

function ColorScaleLegend({ maxVal }) {
  const steps = 5;
  return (
    <div className="flex items-center gap-2 px-4 py-2 border-t border-gray-100">
      <span className="text-xs text-gray-400 flex-shrink-0">Papers:</span>
      <span className="text-xs text-gray-400">0</span>
      <div className="flex h-3 rounded overflow-hidden" style={{ width: 120 }}>
        {Array.from({ length: steps }, (_, i) => {
          const frac = i / (steps - 1);
          const cnt  = Math.round(frac * maxVal);
          return (
            <div key={i} style={{ flex: 1, background: _cellBg(cnt || 1, maxVal) || '#F3F4F6' }} />
          );
        })}
      </div>
      <span className="text-xs text-gray-500 font-semibold">{maxVal.toLocaleString()}</span>
    </div>
  );
}

// ── Export button with dropdown ────────────────────────────────────────────────

function ExportDropdown({ onCsv, onPng }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-lg border border-gray-200 text-gray-500 hover:bg-gray-50 hover:text-gray-700 transition">
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
        </svg>
        Export
        <svg className="w-2.5 h-2.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7"/>
        </svg>
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-20 bg-white border border-gray-200 rounded-xl shadow-lg py-1 min-w-[110px]">
          <button onClick={() => { onCsv(); setOpen(false); }}
            className="w-full text-left text-xs px-3 py-2 hover:bg-gray-50 text-gray-700 flex items-center gap-2">
            <span className="text-green-600 font-bold text-xs">CSV</span> Spreadsheet
          </button>
          <button onClick={() => { onPng(); setOpen(false); }}
            className="w-full text-left text-xs px-3 py-2 hover:bg-gray-50 text-gray-700 flex items-center gap-2">
            <span className="text-blue-600 font-bold text-xs">PNG</span> Image
          </button>
        </div>
      )}
    </div>
  );
}

// ── FuelImpactSubgroupCooccPanel ──────────────────────────────────────────────
// Fixed heatmap: matched_fuel_subgroups × matched_impact_subgroups

const _FUEL_SG_AXIS = { id: 'fuel_sg', label: 'Fuel Subgroup', col: 'matched_fuel_subgroups', isArray: true, terms: null };
const _IMPACT_SG_AXIS = { id: 'impact_sg', label: 'Impact Subgroup', col: 'matched_impact_subgroups', isArray: true, terms: null };

function FuelImpactSubgroupCooccPanel({ pipelineId, onOpenRecords }) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);

  useEffect(() => {
    _runCooccQuery(pipelineId, _FUEL_SG_AXIS, _IMPACT_SG_AXIS, setData, setLoading, setError);
  }, [pipelineId]);

  const refresh = () => _runCooccQuery(pipelineId, _FUEL_SG_AXIS, _IMPACT_SG_AXIS, setData, setLoading, setError);

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden shadow-sm">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
        <div>
          <span className="text-sm font-semibold text-gray-800">Fuel × Impact Subgroup Co-occurrence</span>
          <span className="ml-2 text-xs text-gray-400">Dynamic · click cells to view records</span>
        </div>
        <div className="flex items-center gap-2">
          {data && (
            <ExportDropdown
              onCsv={() => _exportCsv(data, 'Fuel Subgroup', 'Impact Subgroup')}
              onPng={() => _exportPng(data, 'Fuel × Impact Subgroup Co-occurrence')}
            />
          )}
          <button onClick={refresh} title="Refresh"
            className="text-gray-300 hover:text-gray-500 transition text-sm px-1">↻</button>
        </div>
      </div>
      {loading && (
        <div className="flex items-center justify-center h-28 text-gray-300">
          <span className="w-5 h-5 border-2 border-gray-200 border-t-blue-400 rounded-full animate-spin" />
        </div>
      )}
      {error && <div className="p-4 text-red-500 text-xs">Error: {error}</div>}
      {!loading && data && data.allAs.length > 0 && (
        <>
          <CooccurrenceTable data={data} axisA={_FUEL_SG_AXIS} axisB={_IMPACT_SG_AXIS} onOpenRecords={onOpenRecords} />
          <ColorScaleLegend maxVal={data.maxVal} />
        </>
      )}
      {!loading && !error && data && data.allAs.length === 0 && (
        <div className="p-8 text-center text-gray-400 text-sm">No subgroup co-occurrence data yet</div>
      )}
    </div>
  );
}

// ── TermsCooccurrencePanel ─────────────────────────────────────────────────────
// Dynamic heatmap: user picks two axes from a wide range of dimensions.

function TermsCooccurrencePanel({ pipeline, pipelineId, isLLM, onOpenRecords }) {
  const axisOptions = useMemo(() => {
    const groups = _migrateGroups(pipeline.config || {});
    const opts   = [];
    // Group 0 = fuel, Group 1 = impact — subgroup and term-level axes
    groups.forEach((g, gi) => {
      const termCol = gi === 0 ? 'matched_fuel_terms' : 'matched_impact_terms';
      const sgCol   = gi === 0 ? 'matched_fuel_subgroups' : 'matched_impact_subgroups';
      opts.push({ id: `${gi}_subgroups`, label: `${g.name} — subgroups`, col: sgCol, isArray: true, terms: null, group: g.name });
      (g.subgroups || []).forEach(sg => {
        if (!sg.is_others && sg.terms && sg.terms.length)
          opts.push({ id: `${gi}_${sg.name}`, label: sg.name, col: termCol, isArray: true, terms: sg.terms, group: g.name });
      });
    });
    // Bibliometric axes
    opts.push({ id: 'countries',     label: 'Countries',          col: 'authors_countries',       isArray: true,  terms: null, group: 'Bibliometric' });
    opts.push({ id: 'institutions',  label: 'Institutions',        col: 'authors_institutions',    isArray: true,  terms: null, group: 'Bibliometric' });
    opts.push({ id: 'work_type',     label: 'Work Type',           col: 'type',                    isArray: false, terms: null, group: 'Bibliometric' });
    opts.push({ id: 'oa_status',     label: 'OA Status',           col: 'open_access_oa_status',   isArray: false, terms: null, group: 'Bibliometric' });
    opts.push({ id: 'pub_year',      label: 'Publication Year',    col: 'publication_year',        isArray: false, terms: null, group: 'Bibliometric' });
    // LLM axes
    opts.push({ id: 'llm_fuel_cls',  label: 'Fuel Classification', col: 'llm_fuel_classification', isArray: true,  terms: null, group: 'LLM' });
    opts.push({ id: 'llm_ml_role',   label: 'ML Role',             col: 'llm_ml_role',             isArray: true,  terms: null, group: 'LLM' });
    opts.push({ id: 'llm_ml_algo',   label: 'ML Algorithm',        col: 'llm_ml_algorithm',        isArray: true,  terms: null, group: 'LLM' });
    opts.push({ id: 'llm_scope',     label: 'Value Chain',         col: 'llm_scope',               isArray: true,  terms: null, group: 'LLM' });
    return opts;
  }, [pipeline.config]);

  const defaultA = useMemo(() =>
    axisOptions.find(o => o.id.startsWith('0_') && !o.id.endsWith('_subgroups')) || axisOptions[0],
    [axisOptions]);
  const defaultB = useMemo(() =>
    axisOptions.find(o => o.id.startsWith('1_') && !o.id.endsWith('_subgroups')) ||
    axisOptions.find(o => o.id.startsWith('1_')) || axisOptions[1],
    [axisOptions]);

  const [axisAId, setAxisAId] = useState(null);
  const [axisBId, setAxisBId] = useState(null);

  useEffect(() => {
    if (axisOptions.length >= 2 && axisAId === null) {
      setAxisAId(defaultA?.id || axisOptions[0]?.id);
      setAxisBId(defaultB?.id || axisOptions[1]?.id);
    }
  }, [axisOptions]); // eslint-disable-line

  const axisA = axisOptions.find(o => o.id === axisAId);
  const axisB = axisOptions.find(o => o.id === axisBId);

  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);

  useEffect(() => {
    if (!axisA || !axisB) return;
    _runCooccQuery(pipelineId, axisA, axisB, setData, setLoading, setError);
  }, [axisAId, axisBId, pipelineId]); // eslint-disable-line

  const refresh = () => {
    if (axisA && axisB) _runCooccQuery(pipelineId, axisA, axisB, setData, setLoading, setError);
  };

  const swapAxes = () => { setAxisAId(axisBId); setAxisBId(axisAId); };

  // Group options by group for optgroup rendering
  const groups = useMemo(() => {
    const map = {};
    axisOptions.forEach(o => {
      if (!map[o.group]) map[o.group] = [];
      map[o.group].push(o);
    });
    return map;
  }, [axisOptions]);

  if (axisOptions.length < 2) return null;

  const AxisSelect = ({ value, onChange, label }) => (
    <div className="flex items-center gap-1.5">
      <span className="text-xs text-gray-500 font-medium flex-shrink-0">{label}</span>
      <select value={value || ''} onChange={e => onChange(e.target.value)}
        className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white text-gray-700 max-w-[200px] shadow-sm">
        {Object.entries(groups).map(([grp, opts]) => (
          <optgroup key={grp} label={grp}>
            {opts.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
          </optgroup>
        ))}
      </select>
    </div>
  );

  const chartTitle = axisA && axisB ? `${axisA.label} × ${axisB.label}` : 'Co-occurrence';

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden shadow-sm">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-100">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div>
            <span className="text-sm font-semibold text-gray-800">Terms Co-occurrence</span>
            <span className="ml-2 text-xs text-gray-400">Dynamic · click cells to view records</span>
          </div>
          <div className="flex items-center gap-2">
            {data && (
              <ExportDropdown
                onCsv={() => _exportCsv(data, axisA?.label || 'X', axisB?.label || 'Y')}
                onPng={() => _exportPng(data, chartTitle)}
              />
            )}
            <button onClick={refresh} title="Refresh data"
              className="text-gray-300 hover:text-gray-500 transition text-sm px-1">↻</button>
          </div>
        </div>
        {/* Axis selectors */}
        <div className="flex flex-wrap items-center gap-2 mt-2.5">
          <AxisSelect label="Rows" value={axisAId} onChange={setAxisAId} />
          <button onClick={swapAxes} title="Swap axes"
            className="text-gray-400 hover:text-gray-600 transition px-1 py-1 rounded hover:bg-gray-100 text-sm" aria-label="Swap axes">
            ⇄
          </button>
          <AxisSelect label="Cols" value={axisBId} onChange={setAxisBId} />
        </div>
      </div>

      {/* Body */}
      {loading && (
        <div className="flex items-center justify-center h-32 text-gray-300">
          <span className="w-5 h-5 border-2 border-gray-200 border-t-blue-400 rounded-full animate-spin" />
        </div>
      )}
      {error && <div className="p-4 text-red-500 text-xs">Error: {error}</div>}
      {!loading && data && data.allAs.length > 0 && (
        <>
          <CooccurrenceTable data={data} axisA={axisA} axisB={axisB} onOpenRecords={onOpenRecords} />
          <ColorScaleLegend maxVal={data.maxVal} />
        </>
      )}
      {!loading && !error && data && data.allAs.length === 0 && (
        <div className="p-8 text-center text-gray-400 text-sm">No co-occurrence data for selected axes</div>
      )}
    </div>
  );
}
