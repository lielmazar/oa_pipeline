// ── Orthogonality Analysis View ───────────────────────────────────────────────

function CramersVHeatmap({ data }) {
  if (!data || !data.dimensions) return null;
  const { dimensions, matrix, pvalues } = data;
  const getColor = v => {
    if (v >= 0.3) return 'bg-red-200 text-red-900';
    if (v >= 0.15) return 'bg-yellow-100 text-yellow-900';
    return 'bg-green-50 text-green-900';
  };
  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse">
        <thead>
          <tr>
            <th className="p-2 border border-gray-200 bg-gray-50"></th>
            {dimensions.map(d => <th key={d} className="p-2 border border-gray-200 bg-gray-50 font-medium">{d}</th>)}
          </tr>
        </thead>
        <tbody>
          {dimensions.map((d, i) => (
            <tr key={d}>
              <th className="p-2 border border-gray-200 bg-gray-50 font-medium text-left">{d}</th>
              {dimensions.map((_, j) => {
                const v = matrix[i][j];
                const p = pvalues?.[i]?.[j];
                return (
                  <td key={j} className={`p-2 border border-gray-200 text-center font-mono ${getColor(v)}`}
                    title={p != null ? `p = ${p.toFixed(6)}` : ''}>
                    {v.toFixed(3)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EntropyPanel({ data }) {
  if (!data) return null;
  return (
    <div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <div className="bg-indigo-50 rounded-lg p-3 border border-indigo-200">
          <div className="text-2xl font-bold text-indigo-700">{data.h_role?.toFixed(2)}</div>
          <div className="text-xs text-gray-500">H(ML Role) bits</div>
        </div>
        <div className="bg-cyan-50 rounded-lg p-3 border border-cyan-200">
          <div className="text-2xl font-bold text-cyan-700">{data.h_role_given_fuel?.toFixed(2)}</div>
          <div className="text-xs text-gray-500">H(Role | Fuel) bits</div>
        </div>
        <div className="bg-green-50 rounded-lg p-3 border border-green-200">
          <div className="text-2xl font-bold text-green-700">{data.mi?.toFixed(2)}</div>
          <div className="text-xs text-gray-500">Mutual Information</div>
        </div>
        <div className="bg-amber-50 rounded-lg p-3 border border-amber-200">
          <div className="text-2xl font-bold text-amber-700">{data.uncertainty_reduction_pct?.toFixed(1)}%</div>
          <div className="text-xs text-gray-500">Uncertainty Reduction</div>
        </div>
      </div>
      <p className="text-sm text-gray-600 mb-3">
        Knowing the fuel domain reduces role uncertainty by <strong>{data.uncertainty_reduction_pct?.toFixed(1)}%</strong>.
      </p>
      {data.per_fuel?.length > 0 && (
        <div className="overflow-x-auto">
          <table className="text-xs border-collapse w-full">
            <thead>
              <tr className="bg-gray-50">
                <th className="p-2 border text-left">Fuel Class</th>
                <th className="p-2 border text-right">Papers</th>
                <th className="p-2 border text-right">H(Role) bits</th>
                <th className="p-2 border">Flag</th>
              </tr>
            </thead>
            <tbody>
              {data.per_fuel.slice(0, 20).map(pf => (
                <tr key={pf.fuel_class}>
                  <td className="p-2 border">{pf.fuel_class}</td>
                  <td className="p-2 border text-right">{pf.count.toLocaleString()}</td>
                  <td className="p-2 border text-right font-mono">{pf.h_role.toFixed(3)}</td>
                  <td className="p-2 border text-center">
                    {pf.h_role < 1.5 && <span className="text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">low diversity</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function JaccardHeatmap({ data, onCellClick }) {
  if (!data || !data.codes || data.codes.length === 0) return <p className="text-gray-400 text-sm">No data available.</p>;
  const { codes, matrix } = data;
  const getColor = v => {
    if (v > 0.1) return 'bg-red-200';
    if (v > 0.05) return 'bg-amber-100';
    return '';
  };
  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse">
        <thead>
          <tr>
            <th className="p-1 border bg-gray-50"></th>
            {codes.map(c => <th key={c} className="p-1 border bg-gray-50 font-medium" style={{writingMode:'vertical-lr',maxHeight:'120px'}}>{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {codes.map((c, i) => (
            <tr key={c}>
              <th className="p-1 border bg-gray-50 font-medium text-left whitespace-nowrap">{c}</th>
              {codes.map((_, j) => {
                const v = matrix[i][j];
                return (
                  <td key={j}
                    className={`p-1 border text-center font-mono cursor-pointer hover:ring-2 hover:ring-blue-400 ${getColor(v)}`}
                    onClick={() => i !== j && v > 0 && onCellClick?.(codes[i], codes[j])}
                    title={`${codes[i]} × ${codes[j]}: ${v.toFixed(4)}`}>
                    {i === j ? '—' : v > 0 ? v.toFixed(3) : ''}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TautologicalTable({ data }) {
  if (!data?.pairs?.length) return <p className="text-gray-400 text-sm">No tautological pairs detected.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse w-full">
        <thead>
          <tr className="bg-gray-50">
            <th className="p-2 border text-left">Fuel Class</th>
            <th className="p-2 border text-left">Top ML Role</th>
            <th className="p-2 border text-right">Overlap %</th>
            <th className="p-2 border text-right">Count</th>
            <th className="p-2 border text-right">Total</th>
            <th className="p-2 border">Flag</th>
          </tr>
        </thead>
        <tbody>
          {data.pairs.map((p, i) => (
            <tr key={i} className={p.flagged ? 'bg-red-50' : ''}>
              <td className="p-2 border">{p.fuel_class}</td>
              <td className="p-2 border">{p.top_ml_role}</td>
              <td className="p-2 border text-right font-mono">{p.overlap_pct}%</td>
              <td className="p-2 border text-right">{p.count.toLocaleString()}</td>
              <td className="p-2 border text-right">{p.total.toLocaleString()}</td>
              <td className="p-2 border text-center">
                {p.flagged && <span className="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded">⚠ tautological</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PMITable({ data }) {
  if (!data?.pairs?.length) return <p className="text-gray-400 text-sm">No PMI data available.</p>;
  const [sortKey, setSortKey] = useState('pmi');
  const [sortDir, setSortDir] = useState('desc');

  const sorted = useMemo(() => {
    const arr = [...data.pairs];
    arr.sort((a, b) => sortDir === 'desc' ? b[sortKey] - a[sortKey] : a[sortKey] - b[sortKey]);
    return arr;
  }, [data.pairs, sortKey, sortDir]);

  const toggleSort = key => {
    if (sortKey === key) setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    else { setSortKey(key); setSortDir('desc'); }
  };

  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse w-full">
        <thead>
          <tr className="bg-gray-50">
            <th className="p-2 border text-left">Role</th>
            <th className="p-2 border text-left">Algorithm</th>
            <th className="p-2 border text-right cursor-pointer hover:text-blue-600" onClick={() => toggleSort('pmi')}>
              PMI {sortKey === 'pmi' ? (sortDir === 'desc' ? '↓' : '↑') : ''}
            </th>
            <th className="p-2 border text-right cursor-pointer hover:text-blue-600" onClick={() => toggleSort('count')}>
              Count {sortKey === 'count' ? (sortDir === 'desc' ? '↓' : '↑') : ''}
            </th>
            <th className="p-2 border">Flag</th>
          </tr>
        </thead>
        <tbody>
          {sorted.slice(0, 100).map((p, i) => (
            <tr key={i} className={p.flag === 'near-deterministic' ? 'bg-red-50' : p.flag === 'anti-specific' ? 'bg-blue-50' : ''}>
              <td className="p-2 border">{p.role}</td>
              <td className="p-2 border">{p.algorithm}</td>
              <td className="p-2 border text-right font-mono">{p.pmi.toFixed(3)}</td>
              <td className="p-2 border text-right">{p.count}</td>
              <td className="p-2 border text-center">
                {p.flag === 'near-deterministic' && <span className="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded">near-deterministic</span>}
                {p.flag === 'anti-specific' && <span className="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">anti-specific</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DataQualityPanel({ data }) {
  if (!data) return null;
  return (
    <div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <div className="bg-blue-50 rounded-lg p-3 border border-blue-200">
          <div className="text-xl font-bold text-blue-700">{(data.total_papers || 0).toLocaleString()}</div>
          <div className="text-xs text-gray-500">Total Papers</div>
        </div>
        <div className="bg-green-50 rounded-lg p-3 border border-green-200">
          <div className="text-xl font-bold text-green-700">{(data.ml_positive_papers || 0).toLocaleString()}</div>
          <div className="text-xs text-gray-500">ML-Positive</div>
        </div>
        {data.role_without_algorithm > 0 && (
          <div className="bg-red-50 rounded-lg p-3 border border-red-200">
            <div className="text-xl font-bold text-red-700">{data.role_without_algorithm}</div>
            <div className="text-xs text-gray-500">Role w/o Algorithm</div>
          </div>
        )}
        {data.scope_leaks > 0 && (
          <div className="bg-red-50 rounded-lg p-3 border border-red-200">
            <div className="text-xl font-bold text-red-700">{data.scope_leaks}</div>
            <div className="text-xs text-gray-500">Scope Leaks</div>
          </div>
        )}
      </div>
      {data.confidence_distribution && (
        <div className="mb-4">
          <h4 className="text-sm font-semibold text-gray-700 mb-2">Confidence Distribution</h4>
          <div className="flex gap-3">
            {['High', 'Medium', 'Low'].map(level => {
              const all = data.confidence_distribution?.[level] || 0;
              const ml = data.ml_positive_confidence?.[level] || 0;
              return (
                <div key={level} className="bg-gray-50 rounded-lg p-3 border flex-1 text-center">
                  <div className="text-lg font-bold">{all.toLocaleString()}</div>
                  <div className="text-xs text-gray-500">{level}</div>
                  <div className="text-xs text-blue-500">({ml} ML+)</div>
                </div>
              );
            })}
          </div>
        </div>
      )}
      <div className="grid grid-cols-3 gap-3 text-center">
        {data.multi_role_papers > 0 && (
          <div className="bg-amber-50 rounded p-2 border border-amber-200">
            <div className="font-bold text-amber-700">{data.multi_role_papers}</div>
            <div className="text-xs text-gray-500">3+ ML roles</div>
          </div>
        )}
        {data.other_ml_count > 0 && (
          <div className="bg-gray-50 rounded p-2 border">
            <div className="font-bold">{data.other_ml_count}</div>
            <div className="text-xs text-gray-500">"other_ml"</div>
          </div>
        )}
        {data.other_fuel_count > 0 && (
          <div className="bg-gray-50 rounded p-2 border">
            <div className="font-bold">{data.other_fuel_count}</div>
            <div className="text-xs text-gray-500">"other" fuel</div>
          </div>
        )}
      </div>
    </div>
  );
}

function OrthogonalityView({ pipelines }) {
  const [pipelineId, setPipelineId] = useState(null);
  const [mlOnly, setMlOnly] = useState(true);
  const [tab, setTab] = useState('cramers');
  const [cramers, setCramers] = useState(null);
  const [entropy, setEntropy] = useState(null);
  const [jaccard, setJaccard] = useState(null);
  const [tautological, setTautological] = useState(null);
  const [pmi, setPmi] = useState(null);
  const [audit, setAudit] = useState(null);
  const [exemplars, setExemplars] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadData = useCallback(async () => {
    if (!pipelineId) return;
    setLoading(true);
    const ml = mlOnly ? 'true' : 'false';
    try {
      const [cr, en, jc, tp, pm, au, ex] = await Promise.all([
        apiFetch(`/api/orthogonality/cramers-v/${pipelineId}?ml_only=${ml}`).then(r => r.json()).catch(() => null),
        apiFetch(`/api/orthogonality/entropy/${pipelineId}?ml_only=${ml}`).then(r => r.json()).catch(() => null),
        apiFetch(`/api/orthogonality/jaccard/${pipelineId}?dimension=ml_role&ml_only=${ml}`).then(r => r.json()).catch(() => null),
        apiFetch(`/api/orthogonality/tautological/${pipelineId}`).then(r => r.json()).catch(() => null),
        apiFetch(`/api/orthogonality/pmi/${pipelineId}`).then(r => r.json()).catch(() => null),
        apiFetch(`/api/orthogonality/audit/${pipelineId}`).then(r => r.json()).catch(() => null),
        apiFetch(`/api/orthogonality/exemplars/${pipelineId}`).then(r => r.json()).catch(() => null),
      ]);
      setCramers(cr); setEntropy(en); setJaccard(jc);
      setTautological(tp); setPmi(pm); setAudit(au); setExemplars(ex);
    } catch {}
    setLoading(false);
  }, [pipelineId, mlOnly]);

  useEffect(() => { loadData(); }, [loadData]);

  const tabs = [
    { id: 'cramers', label: "Cramér's V" },
    { id: 'entropy', label: 'Entropy' },
    { id: 'jaccard', label: 'Jaccard' },
    { id: 'tautological', label: 'Tautological' },
    { id: 'pmi', label: 'PMI' },
    { id: 'audit', label: 'Data Quality' },
    { id: 'exemplars', label: 'Exemplars' },
  ];

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-gray-900">Taxonomy Orthogonality</h2>
        <p className="text-sm text-gray-500 mt-1">Analyse independence between classification dimensions.</p>
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-6">
        <select value={pipelineId || ''} onChange={e => setPipelineId(Number(e.target.value) || null)}
          className="border rounded-lg px-3 py-2 text-sm min-w-[200px]">
          <option value="">Select pipeline...</option>
          {pipelines.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-sm text-gray-600">
          <input type="checkbox" checked={mlOnly} onChange={e => setMlOnly(e.target.checked)} className="rounded" />
          ML-positive only
        </label>
        <button onClick={loadData} disabled={!pipelineId || loading}
          className="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50">
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {pipelineId && (
        <>
          <div className="flex gap-1 mb-4 border-b">
            {tabs.map(t => (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={`px-3 py-2 text-sm font-medium border-b-2 transition
                  ${tab === t.id ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
                {t.label}
              </button>
            ))}
          </div>

          <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
            {loading && <div className="text-center text-gray-400 py-8">Loading analysis...</div>}

            {!loading && tab === 'cramers' && (
              <div>
                <h3 className="text-lg font-semibold mb-3">Cramér's V Matrix</h3>
                <p className="text-sm text-gray-500 mb-3">Values near 0 indicate independence; values near 0.5+ suggest strong association.</p>
                <CramersVHeatmap data={cramers} />
              </div>
            )}

            {!loading && tab === 'entropy' && (
              <div>
                <h3 className="text-lg font-semibold mb-3">Conditional Entropy</h3>
                <EntropyPanel data={entropy} />
              </div>
            )}

            {!loading && tab === 'jaccard' && (
              <div>
                <h3 className="text-lg font-semibold mb-3">Within-Dimension Jaccard (ML Role)</h3>
                <p className="text-sm text-gray-500 mb-3">Amber &gt; 0.05, Red &gt; 0.1. Click cells to see shared papers.</p>
                <JaccardHeatmap data={jaccard} />
              </div>
            )}

            {!loading && tab === 'tautological' && (
              <div>
                <h3 className="text-lg font-semibold mb-3">Tautological Pair Detection</h3>
                <p className="text-sm text-gray-500 mb-3">Fuel×Role pairs where &gt;50% overlap may indicate taxonomy issues.</p>
                <TautologicalTable data={tautological} />
              </div>
            )}

            {!loading && tab === 'pmi' && (
              <div>
                <h3 className="text-lg font-semibold mb-3">PMI Analysis (Role × Algorithm)</h3>
                <p className="text-sm text-gray-500 mb-3">PMI &gt; 3.0 = near-deterministic, PMI &lt; -1.0 = anti-specific.</p>
                <PMITable data={pmi} />
              </div>
            )}

            {!loading && tab === 'audit' && (
              <div>
                <h3 className="text-lg font-semibold mb-3">Data Quality Audit</h3>
                <DataQualityPanel data={audit} />
              </div>
            )}

            {!loading && tab === 'exemplars' && (
              <div>
                <h3 className="text-lg font-semibold mb-3">Exemplar Papers</h3>
                <p className="text-sm text-gray-500 mb-3">Top paper by FWCI for each ML Role × Fuel Class cell with ≥3 papers.</p>
                {exemplars?.exemplars?.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="text-xs border-collapse w-full">
                      <thead>
                        <tr className="bg-gray-50">
                          <th className="p-2 border text-left">Fuel Class</th>
                          <th className="p-2 border text-left">ML Role</th>
                          <th className="p-2 border text-right">Cell Count</th>
                          <th className="p-2 border text-left">Title</th>
                          <th className="p-2 border text-right">FWCI</th>
                          <th className="p-2 border">DOI</th>
                        </tr>
                      </thead>
                      <tbody>
                        {exemplars.exemplars.slice(0, 100).map((ex, i) => (
                          <tr key={i}>
                            <td className="p-2 border">{ex.fuel_class}</td>
                            <td className="p-2 border">{ex.ml_role}</td>
                            <td className="p-2 border text-right">{ex.cell_count}</td>
                            <td className="p-2 border max-w-xs truncate" title={ex.title}>{ex.title}</td>
                            <td className="p-2 border text-right font-mono">{ex.fwci != null ? Number(ex.fwci).toFixed(2) : '—'}</td>
                            <td className="p-2 border">
                              {ex.doi && <a href={`https://doi.org/${ex.doi}`} target="_blank" rel="noopener" className="text-blue-500 hover:underline">{ex.doi}</a>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <p className="text-gray-400 text-sm">No exemplar papers found.</p>}
              </div>
            )}
          </div>
        </>
      )}

      {!pipelineId && (
        <div className="text-center text-gray-400 py-16">Select a pipeline to analyse taxonomy orthogonality.</div>
      )}
    </div>
  );
}
