import { useState, useEffect } from 'react';
import { apiFetch } from '../api/client';

function TermChip({ term, onRemove }) {
  return (
    <span className="inline-flex items-center gap-1 bg-blue-50 text-blue-800 text-xs px-2 py-0.5 rounded-full">
      {term}
      {onRemove && (
        <button onClick={() => onRemove(term)} className="hover:text-red-500 transition leading-none">×</button>
      )}
    </span>
  );
}

function SubgroupEditor({ sg, groupIdx, sgIdx, onChange, readOnly }) {
  const [newTerm, setNewTerm] = useState('');

  const addTerm = () => {
    const t = newTerm.trim().toLowerCase();
    if (!t || sg.terms.includes(t)) { setNewTerm(''); return; }
    onChange({ ...sg, terms: [...sg.terms, t] });
    setNewTerm('');
  };

  const removeTerm = t => onChange({ ...sg, terms: sg.terms.filter(x => x !== t) });

  return (
    <div className="border border-gray-200 rounded-lg p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-gray-700">
          {sg.name} {sg.is_others && <span className="text-xs text-gray-400">(catch-all)</span>}
        </span>
        <span className="text-xs text-gray-400">{sg.terms?.length || 0} terms</span>
      </div>
      <div className="flex flex-wrap gap-1">
        {(sg.terms || []).map(t => (
          <TermChip key={t} term={t} onRemove={readOnly || sg.is_others ? null : removeTerm} />
        ))}
        {(sg.terms?.length === 0 && sg.is_others) && (
          <span className="text-xs text-gray-400 italic">matches anything not caught by other subgroups</span>
        )}
      </div>
      {!readOnly && !sg.is_others && (
        <div className="flex gap-2 mt-1">
          <input
            value={newTerm}
            onChange={e => setNewTerm(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addTerm())}
            placeholder="Add term…"
            className="flex-1 border rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
          />
          <button onClick={addTerm}
            className="text-xs px-2 py-1 bg-blue-600 text-white rounded hover:bg-blue-700 transition">
            Add
          </button>
        </div>
      )}
    </div>
  );
}

export default function KeywordStage({ pipeline, onChange }) {
  const [groups, setGroups]   = useState([]);
  const [saving, setSaving]   = useState(false);
  const [saved, setSaved]     = useState(false);
  const [error, setError]     = useState('');
  const [editMode, setEditMode] = useState(false);
  const [jsonText, setJsonText] = useState('');
  const [showJson, setShowJson] = useState(false);

  useEffect(() => {
    const g = pipeline?.config?.groups || [];
    setGroups(JSON.parse(JSON.stringify(g)));  // deep clone
  }, [pipeline?.id]);

  const save = async () => {
    setSaving(true); setError('');
    try {
      const res = await apiFetch(`/api/pipeline/${pipeline.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ config: { ...pipeline.config, groups } }),
      });
      if (!res.ok) { const d = await res.json(); setError(d.detail || 'Save failed'); return; }
      const updated = await res.json();
      onChange(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      setEditMode(false);
    } finally { setSaving(false); }
  };

  const loadDefault = async () => {
    try {
      const res = await apiFetch('/api/system/default-keywords');
      if (res.ok) { const d = await res.json(); setGroups(d.groups || []); setEditMode(true); }
    } catch {}
  };

  const downloadJson = async () => {
    const res = await apiFetch(`/api/pipeline/${pipeline.id}/keywords/json`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'keywords.json'; a.click();
    URL.revokeObjectURL(url);
  };

  const uploadJson = async e => {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    try {
      const data = JSON.parse(text);
      setGroups(data.groups || []);
      setEditMode(true);
    } catch { setError('Invalid JSON file'); }
    e.target.value = '';
  };

  const applyJsonText = () => {
    try {
      const data = JSON.parse(jsonText);
      setGroups(data.groups || []);
      setShowJson(false);
      setEditMode(true);
    } catch { setError('Invalid JSON'); }
  };

  const updateSubgroup = (gi, si, newSg) => {
    const next = groups.map((g, i) => {
      if (i !== gi) return g;
      return { ...g, subgroups: g.subgroups.map((s, j) => j === si ? newSg : s) };
    });
    setGroups(next);
  };

  const totalTerms = groups.reduce((acc, g) =>
    acc + (g.subgroups || []).reduce((a, sg) => a + (sg.is_others ? 0 : (sg.terms?.length || 0)), 0), 0
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <div className="flex items-center justify-between mb-2">
          <div>
            <h2 className="font-semibold text-gray-900">Keyword Configuration</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {groups.length} groups · {totalTerms} terms
            </p>
          </div>
          <div className="flex gap-2">
            <button onClick={loadDefault} className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 transition">
              Load defaults
            </button>
            <button onClick={downloadJson} className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 transition">
              ↓ JSON
            </button>
            <label className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 transition cursor-pointer">
              ↑ JSON
              <input type="file" accept=".json" onChange={uploadJson} className="hidden" />
            </label>
            <button onClick={() => { setJsonText(JSON.stringify({ groups }, null, 2)); setShowJson(true); }}
              className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 transition">
              Edit JSON
            </button>
            {!editMode ? (
              <button onClick={() => setEditMode(true)}
                className="text-xs px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition">
                Edit
              </button>
            ) : (
              <>
                <button onClick={() => { setGroups(pipeline?.config?.groups || []); setEditMode(false); }}
                  className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50 transition">
                  Cancel
                </button>
                <button onClick={save} disabled={saving}
                  className="text-xs px-3 py-1.5 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition">
                  {saving ? 'Saving…' : saved ? '✓ Saved' : 'Save'}
                </button>
              </>
            )}
          </div>
        </div>
        {error && <p className="text-xs text-red-600 bg-red-50 rounded px-2 py-1">{error}</p>}
      </div>

      {/* Groups */}
      {groups.map((group, gi) => (
        <div key={gi} className="bg-white border border-gray-200 rounded-xl p-4">
          <h3 className="font-medium text-gray-800 mb-3 flex items-center gap-2">
            <span className="w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs flex items-center justify-center font-bold">
              {gi + 1}
            </span>
            {group.name}
          </h3>
          <div className="space-y-2">
            {(group.subgroups || []).map((sg, si) => (
              <SubgroupEditor
                key={si}
                sg={sg}
                groupIdx={gi}
                sgIdx={si}
                readOnly={!editMode}
                onChange={newSg => updateSubgroup(gi, si, newSg)}
              />
            ))}
          </div>
        </div>
      ))}

      {groups.length === 0 && (
        <div className="bg-white border border-dashed border-gray-300 rounded-xl p-10 text-center text-gray-400 text-sm">
          No keyword groups defined.
          <button onClick={loadDefault} className="ml-2 text-blue-600 hover:underline">
            Load system defaults
          </button>
        </div>
      )}

      {/* JSON editor modal */}
      {showJson && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl mx-4 p-6 space-y-3">
            <h3 className="font-bold text-gray-900">Edit as JSON</h3>
            <textarea
              value={jsonText}
              onChange={e => setJsonText(e.target.value)}
              rows={20}
              className="w-full border rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-blue-400 resize-none"
            />
            {error && <p className="text-xs text-red-600">{error}</p>}
            <div className="flex gap-2">
              <button onClick={() => setShowJson(false)}
                className="flex-1 border text-sm py-2 rounded-lg hover:bg-gray-50 transition">Cancel</button>
              <button onClick={applyJsonText}
                className="flex-1 bg-blue-600 text-white text-sm py-2 rounded-lg hover:bg-blue-700 transition font-medium">
                Apply
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
