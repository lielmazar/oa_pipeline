// ── Stage: Keywords ───────────────────────────────────────────────────────────

// Migrate any config format → canonical {name, subgroups:[{name,terms,is_others?}]}
function _migrateGroups(config) {
  // Brand-new pipeline: no groups, no terms at all → start with named groups + empty Default
  const hasGroups = config.groups && config.groups.length > 0;
  const hasFlatTerms = (config.fuel_terms && config.fuel_terms.length > 0) ||
                       (config.impact_terms && config.impact_terms.length > 0);

  if (!hasGroups && !hasFlatTerms) {
    // First-time / empty config — one empty Default subgroup per group
    return [
      { name: 'Fuel Terms',   subgroups: [{ name: 'Default', is_others: true, terms: [] }] },
      { name: 'Impact Terms', subgroups: [{ name: 'Default', is_others: true, terms: [] }] },
    ];
  }

  const raw = hasGroups ? config.groups : [
    { name: 'Fuel Terms',   terms: [...(config.fuel_terms   || [])] },
    { name: 'Impact Terms', terms: [...(config.impact_terms || [])] },
  ];
  return raw.map(g => {
    if (g.subgroups && g.subgroups.length > 0) {
      return {
        name: g.name,
        subgroups: g.subgroups.map(sg => ({
          ...sg,
          terms: [...(sg.terms || [])],
          // Migrate legacy 'Others' name to 'Default'
          name: (sg.is_others && sg.name === 'Others') ? 'Default' : sg.name,
        })),
      };
    }
    // Flat terms → wrap in one "Default" catch-all subgroup
    return {
      name: g.name,
      subgroups: [{ name: 'Default', is_others: true, terms: [...(g.terms || [])] }],
    };
  });
}

// Count total terms across all subgroups of a group
function _totalTerms(group) {
  return (group.subgroups || []).reduce((s, sg) => s + (sg.terms || []).length, 0);
}

// Get/ensure the "Others" subgroup index within a group's subgroups
function _othersIdx(subgroups) {
  const idx = subgroups.findIndex(sg => sg.is_others);
  return idx >= 0 ? idx : subgroups.length - 1;
}

function KeywordStage({ pipeline, onUpdate }) {
  const config = pipeline.config || {};

  const [groups, setGroups] = useState(() => _migrateGroups(config));
  // newTermInput[gi][si] = current text for the add-term input in group gi, subgroup si
  const [newTermInput, setNewTermInput] = useState(
    () => _migrateGroups(config).map(g => g.subgroups.map(() => ''))
  );
  // newSgName[gi] = text in the "add subgroup" input for group gi
  const [newSgName, setNewSgName] = useState(() => _migrateGroups(config).map(() => ''));
  const [editingSg, setEditingSg] = useState(null); // {gi, si}
  const [dragSrc, setDragSrc] = useState(null);     // {gi, si, term}
  const [dropTarget, setDropTarget] = useState(null);// {gi, si}
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(false);
  const [jsonMsg, setJsonMsg] = useState(null); // { ok, text }
  const jsonUploadRef = useRef(null);

  // ── Helpers ──────────────────────────────────────────────────────────────

  const setSubgroups = (gi, fn) =>
    setGroups(prev => prev.map((g, i) => i !== gi ? g : { ...g, subgroups: fn(g.subgroups) }));

  const addTerm = (gi, si) => {
    const val = (newTermInput[gi]?.[si] || '').trim();
    if (!val) return;
    const alreadyExists = groups[gi].subgroups.some(sg => (sg.terms || []).includes(val));
    if (alreadyExists) {
      setNewTermInput(prev => prev.map((row, i) => i !== gi ? row : row.map((v, j) => j === si ? '' : v)));
      return;
    }
    setSubgroups(gi, sgs => sgs.map((sg, j) =>
      j !== si ? sg : { ...sg, terms: [...(sg.terms || []), val] }
    ));
    setNewTermInput(prev => prev.map((row, i) => i !== gi ? row : row.map((v, j) => j === si ? '' : v)));
  };

  const removeTerm = (gi, si, term) =>
    setSubgroups(gi, sgs => sgs.map((sg, j) =>
      j !== si ? sg : { ...sg, terms: (sg.terms || []).filter(t => t !== term) }
    ));

  const moveTerm = (fromGi, fromSi, toGi, toSi, term) => {
    if (fromGi === toGi && fromSi === toSi) return;
    setGroups(prev => prev.map((g, gi) => {
      if (gi !== fromGi && gi !== toGi) return g;
      return {
        ...g,
        subgroups: g.subgroups.map((sg, si) => {
          if (gi === fromGi && si === fromSi)
            return { ...sg, terms: (sg.terms || []).filter(t => t !== term) };
          if (gi === toGi && si === toSi && !(sg.terms || []).includes(term))
            return { ...sg, terms: [...(sg.terms || []), term] };
          return sg;
        }),
      };
    }));
  };

  const addSubgroup = gi => {
    const name = (newSgName[gi] || '').trim();
    if (!name) return;
    const othersI = _othersIdx(groups[gi].subgroups);
    setSubgroups(gi, sgs => [
      ...sgs.slice(0, othersI),
      { name, terms: [] },
      ...sgs.slice(othersI),
    ]);
    setNewSgName(prev => prev.map((v, i) => i === gi ? '' : v));
    // Expand inputs array
    setNewTermInput(prev => prev.map((row, i) =>
      i !== gi ? row : [...row.slice(0, othersI), '', ...row.slice(othersI)]
    ));
  };

  const removeSubgroup = (gi, si) => {
    const sg = groups[gi].subgroups[si];
    if (sg.is_others) return;
    const termsToMove = sg.terms || [];
    setSubgroups(gi, sgs => {
      const othersI = _othersIdx(sgs.filter((_, j) => j !== si));
      const remaining = sgs.filter((_, j) => j !== si);
      if (termsToMove.length > 0 && remaining[othersI]) {
        remaining[othersI] = {
          ...remaining[othersI],
          terms: [...(remaining[othersI].terms || []), ...termsToMove],
        };
      }
      return remaining;
    });
    setNewTermInput(prev => prev.map((row, i) => i !== gi ? row : row.filter((_, j) => j !== si)));
  };

  const renameSg = (gi, si, name) =>
    setSubgroups(gi, sgs => sgs.map((sg, j) => j !== si ? sg : { ...sg, name }));

  const loadDefaults = async () => {
    setLoading(true);
    try {
      const res = await apiFetch('/api/default-keywords');
      if (!res.ok) throw new Error('fetch failed');
      const data = await res.json();
      const migrated = _migrateGroups(data);
      setGroups(migrated);
      setNewTermInput(migrated.map(g => g.subgroups.map(() => '')));
      setNewSgName(migrated.map(() => ''));
    } catch { alert('Could not load defaults.'); }
    finally { setLoading(false); }
  };

  const downloadJson = () => {
    const a = document.createElement('a');
    a.href = `/api/pipelines/${pipeline.id}/keywords/json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const uploadJson = async (file) => {
    if (!file) return;
    let text;
    try { text = await file.text(); }
    catch (e) { setJsonMsg({ ok: false, text: 'Could not read file' }); return; }
    let res, data;
    try {
      res = await apiFetch(`/api/pipelines/${pipeline.id}/keywords/json`, {
        method: 'POST',
        body: JSON.stringify({ json: text }),
      });
      data = await res.json();
    } catch (e) {
      setJsonMsg({ ok: false, text: 'Upload failed: ' + (e.message || 'network error') });
      return;
    }
    if (!res.ok) { setJsonMsg({ ok: false, text: data.error || 'Upload failed' }); return; }
    const newGroups = _migrateGroups(data.config || {});
    setGroups(newGroups);
    setNewTermInput(newGroups.map(g => g.subgroups.map(() => '')));
    setNewSgName(newGroups.map(() => ''));
    setJsonMsg({ ok: true, text: `Loaded ${data.groups} group(s) from JSON` });
    setTimeout(() => setJsonMsg(null), 4000);
    if (onUpdate) onUpdate({ ...pipeline, config: data.config });
  };

  const save = async () => {
    const res = await apiFetch(`/api/pipelines/${pipeline.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ config: { ...config, groups } }),
    });
    const updated = await res.json();
    if (!res.ok) { alert(updated.error); return; }
    setSaved(true); setTimeout(() => setSaved(false), 3000);
    if (onUpdate) onUpdate(updated);
  };

  // ── Drag helpers ─────────────────────────────────────────────────────────

  const onDragStart = (e, gi, si, term) => {
    setDragSrc({ gi, si, term });
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', JSON.stringify({ gi, si, term }));
  };
  const onDragEnd = () => { setDragSrc(null); setDropTarget(null); };
  const onDragOver = (e, gi, si) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDropTarget({ gi, si });
  };
  const onDragLeave = () => setDropTarget(null);
  const onDrop = (e, gi, si) => {
    e.preventDefault();
    try {
      const src = JSON.parse(e.dataTransfer.getData('text/plain'));
      moveTerm(src.gi, src.si, gi, si, src.term);
    } catch {}
    setDragSrc(null); setDropTarget(null);
  };

  // ── Sub-group colors (cycle) ──────────────────────────────────────────────
  const SG_COLORS = [
    'bg-blue-50 border-blue-200',  'bg-green-50 border-green-200',
    'bg-amber-50 border-amber-200','bg-purple-50 border-purple-200',
    'bg-cyan-50 border-cyan-200',  'bg-rose-50 border-rose-200',
  ];

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <div className="flex items-start justify-between mb-1">
          <h3 className="font-semibold text-lg">Stage 1: Keyword Selection</h3>
          <div className="flex items-center gap-2 flex-wrap">
            {pipeline.is_owner && (
              <button onClick={loadDefaults} disabled={loading}
                className="text-xs px-3 py-1 border border-gray-300 rounded-lg text-gray-600 hover:bg-gray-50 transition">
                {loading ? 'Loading…' : '⟳ Load defaults'}
              </button>
            )}
            <button onClick={downloadJson}
              className="text-xs px-3 py-1 border border-gray-300 rounded-lg text-gray-600 hover:bg-gray-50 transition"
              title="Download keywords as JSON">
              ↓ JSON
            </button>
            {pipeline.is_owner && (
              <>
                <input ref={jsonUploadRef} type="file" accept=".json" className="hidden"
                  onChange={e => { uploadJson(e.target.files?.[0]); e.target.value = ''; }} />
                <button onClick={() => jsonUploadRef.current?.click()}
                  className="text-xs px-3 py-1 border border-blue-300 rounded-lg text-blue-600 hover:bg-blue-50 transition"
                  title="Upload keywords from JSON">
                  ↑ Upload JSON
                </button>
              </>
            )}
          </div>
        </div>
        {jsonMsg && (
          <div className={`text-xs px-3 py-1.5 rounded-lg mb-2 ${jsonMsg.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'}`}>
            {jsonMsg.text}
          </div>
        )}
        <p className="text-sm text-gray-500 mb-4">
          Each record must match ≥ 1 term from <em>each</em> top-level group.
          Within a group, sub-groups categorise terms for report analysis.
          <strong className="ml-1">Drag terms</strong> between sub-groups to reassign them.
        </p>

        <div className="space-y-8">
          {groups.map((group, gi) => (
            <div key={gi} className="border border-gray-200 rounded-xl overflow-hidden">
              {/* Group header */}
              <div className="bg-gray-800 text-white px-5 py-3 flex items-center gap-3">
                <span className="font-semibold text-sm">{group.name || `Group ${gi + 1}`}</span>
                <span className="text-gray-400 text-xs">{_totalTerms(group)} terms</span>
                <span className="text-gray-500 text-xs ml-auto">{(group.subgroups || []).filter(sg => !sg.is_others).length} sub-groups + Default</span>
              </div>

              {/* Sub-groups */}
              <div className="divide-y divide-gray-100">
                {(group.subgroups || []).map((sg, si) => {
                  const colorCls = sg.is_others
                    ? 'bg-gray-50 border-gray-200'
                    : SG_COLORS[si % SG_COLORS.length];
                  const isDropTarget = dropTarget && dropTarget.gi === gi && dropTarget.si === si;

                  return (
                    <div key={si}
                      className={`p-4 transition-colors ${isDropTarget ? 'ring-2 ring-inset ring-blue-400 bg-blue-50' : ''}`}
                      onDragOver={e => onDragOver(e, gi, si)}
                      onDragLeave={onDragLeave}
                      onDrop={e => onDrop(e, gi, si)}
                    >
                      {/* Sub-group header */}
                      <div className="flex items-center gap-2 mb-2">
                        {/* Name (editable) */}
                        {editingSg && editingSg.gi === gi && editingSg.si === si ? (
                          <input autoFocus type="text" value={sg.name}
                            onChange={e => renameSg(gi, si, e.target.value)}
                            onBlur={() => setEditingSg(null)}
                            onKeyDown={e => e.key === 'Enter' && setEditingSg(null)}
                            className="font-medium text-xs border-b border-blue-400 bg-transparent focus:outline-none w-36"
                          />
                        ) : (
                          <button
                            disabled={!pipeline.is_owner}
                            onClick={() => pipeline.is_owner && setEditingSg({ gi, si })}
                            className={`font-medium text-xs flex items-center gap-1 ${sg.is_others ? 'text-gray-500 italic' : 'text-gray-700'} hover:text-blue-600`}
                            title={sg.is_others ? 'Default sub-group — click to rename, cannot be deleted' : 'Click to rename'}
                          >
                            <span className={`inline-block w-2 h-2 rounded-full mr-1 ${colorCls.split(' ')[0].replace('bg-', 'bg-').replace('-50', '-400')}`} />
                            {sg.name || `Sub-group ${si + 1}`}
                            {pipeline.is_owner && <span className="text-gray-400 text-xs">✎</span>}
                          </button>
                        )}
                        <span className="text-xs text-gray-400">({(sg.terms || []).length} terms)</span>
                        {/* Delete sub-group / Default label */}
                        {sg.is_others
                          ? <span className="ml-auto text-xs text-gray-300 italic select-none">catch-all</span>
                          : pipeline.is_owner && (
                            <button onClick={() => removeSubgroup(gi, si)}
                              className="ml-auto text-xs text-red-400 hover:text-red-600 transition px-1"
                              title="Delete sub-group (terms move to Default)">✕</button>
                          )
                        }
                      </div>

                      {/* Terms drop zone */}
                      <div className={`flex flex-wrap gap-1.5 p-2 min-h-[2.5rem] rounded-lg border transition-colors ${colorCls} ${isDropTarget ? 'border-blue-400' : ''}`}>
                        {(sg.terms || []).length === 0
                          ? <span className="text-gray-400 text-xs italic">{sg.is_others ? 'Unassigned terms appear here' : 'Drop terms here or add below'}</span>
                          : (sg.terms || []).map(term => {
                            const isDragging = dragSrc && dragSrc.gi === gi && dragSrc.si === si && dragSrc.term === term;
                            return (
                              <span key={term}
                                draggable={pipeline.is_owner}
                                onDragStart={e => onDragStart(e, gi, si, term)}
                                onDragEnd={onDragEnd}
                                className={`tag-pill cursor-grab active:cursor-grabbing select-none ${isDragging ? 'opacity-40' : ''}`}
                                title="Drag to move to another sub-group"
                              >
                                {term}
                                {pipeline.is_owner && (
                                  <button onClick={() => removeTerm(gi, si, term)}>✕</button>
                                )}
                              </span>
                            );
                          })}
                      </div>

                      {/* Add term input */}
                      {pipeline.is_owner && (
                        <div className="flex gap-2 mt-2">
                          <input type="text"
                            placeholder={`Add to ${sg.name || 'sub-group'}…`}
                            value={newTermInput[gi]?.[si] || ''}
                            onChange={e => setNewTermInput(prev =>
                              prev.map((row, i) => i !== gi ? row : row.map((v, j) => j === si ? e.target.value : v))
                            )}
                            onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addTerm(gi, si))}
                            className="flex-1 border rounded px-3 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-blue-400"
                          />
                          <button onClick={() => addTerm(gi, si)}
                            className="px-3 py-1 bg-blue-600 text-white text-xs rounded hover:bg-blue-700 transition">
                            Add
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Add new sub-group */}
              {pipeline.is_owner && (
                <div className="bg-gray-50 border-t border-gray-200 px-4 py-3 flex gap-2">
                  <input type="text"
                    placeholder="New sub-group name…"
                    value={newSgName[gi] || ''}
                    onChange={e => setNewSgName(prev => prev.map((v, i) => i === gi ? e.target.value : v))}
                    onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addSubgroup(gi))}
                    className="flex-1 border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                  />
                  <button onClick={() => addSubgroup(gi)}
                    className="px-3 py-1.5 text-sm bg-gray-700 text-white rounded hover:bg-gray-900 transition">
                    + Sub-group
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="mt-6 flex items-center gap-4">
          {saved && <span className="text-sm text-green-600 font-medium">✓ Keywords saved</span>}
          {pipeline.is_owner && (
            <button onClick={save}
              className="flex-1 py-2 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700 transition">
              Save Keywords
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

