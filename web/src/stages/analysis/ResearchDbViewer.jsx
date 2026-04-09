import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { apiFetch } from '../../api/client';

/* ── ops by type ───────────────────────────────────────────────────────────── */
const TEXT_OPS  = [
  { id: 'contains',    label: 'contains' },
  { id: 'eq',          label: '=' },
  { id: 'neq',         label: '≠' },
  { id: 'starts_with', label: 'starts with' },
  { id: 'is_null',     label: 'is null' },
  { id: 'is_not_null', label: 'is not null' },
];
const NUM_OPS = [
  { id: 'eq',  label: '=' },
  { id: 'neq', label: '≠' },
  { id: 'gt',  label: '>' },
  { id: 'gte', label: '≥' },
  { id: 'lt',  label: '<' },
  { id: 'lte', label: '≤' },
  { id: 'is_null',     label: 'is null' },
  { id: 'is_not_null', label: 'is not null' },
];
function opsForType(t) {
  const u = (t || '').toUpperCase();
  if (['INT','INTEGER','REAL','FLOAT','NUMERIC','DOUBLE'].some(k => u.includes(k))) return NUM_OPS;
  return TEXT_OPS;
}

const DEFAULT_VISIBLE = ['openalex_id', 'title', 'publication_year', 'type', 'open_access_oa_status'];
const LLM_EXTRA = ['llm_fuel_classification', 'llm_ml_role', 'llm_confidence'];
const HIDDEN_COLS = new Set([
  'llm_ml_strategy', 'llm_ml_paradigm', 'llm_ml_method',
  'llm_result_raw', 'llm_batch_id', 'llm_processed_at',
  'llm_tokens_input', 'llm_tokens_output', 'llm_tokens_reasoning', 'llm_scope',
  'matched_fuel_stems', 'matched_impact_stems',
]);

function uid() { return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`; }

function stripOuterFrSubsetCte(sql) {
  let s = sql.trim();
  for (let k = 0; k < 5; k++) {
    const m = s.match(/^\s*WITH\s+_fr_subset\s+AS\s*\(/i);
    if (!m) break;
    let depth = 1, i = m[0].length, found = false;
    while (i < s.length) {
      if (s[i] === '(') depth++;
      else if (s[i] === ')') { depth--; if (depth === 0) { s = s.slice(i + 1).trim().replace(/;$/, ''); found = true; break; } }
      i++;
    }
    if (!found) break;
  }
  return s;
}

function renderCell(value, colName) {
  const s = String(value ?? '');
  if (colName === 'abstract_text' && s.length > 60)
    return <span className="whitespace-normal line-clamp-3 leading-snug">{s}</span>;
  return s;
}

/* ── Icons ─────────────────────────────────────────────────────────────────── */
function SortIcon({ dir, index }) {
  if (!dir) return <span className="text-gray-300 text-[9px] ml-0.5 opacity-0 group-hover/th:opacity-100">⇅</span>;
  return (
    <span className="text-blue-600 text-[10px] ml-0.5 inline-flex items-center gap-px">
      {dir === 'asc' ? '▲' : '▼'}
      {index > 0 && <span className="text-[8px] text-blue-400">{index}</span>}
    </span>
  );
}

function FunnelIcon({ active }) {
  return (
    <svg viewBox="0 0 16 16" className={`w-3 h-3 shrink-0 ${active ? 'text-blue-600' : 'text-gray-300 group-hover/th:text-gray-400'}`}
      fill="currentColor">
      <path d="M1 2a1 1 0 011-1h12a1 1 0 01.8 1.6L10 9.5V14a1 1 0 01-1.5.86l-2-1.14A1 1 0 016 12.86V9.5L1.2 2.6A1 1 0 011 2z" />
    </svg>
  );
}

function Chevron({ open }) {
  return (
    <svg className={`w-3.5 h-3.5 text-gray-400 transition-transform ${open ? 'rotate-90' : ''}`}
      fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
    </svg>
  );
}

/* ── Column filter popover with distinct-value suggestions ─────────────────── */
function ColumnFilterPopover({ pipelineId, subsetId, col, colType, filter, onApply, onClear, onClose }) {
  const ops = opsForType(colType);
  const [op, setOp]       = useState(filter?.op || ops[0].id);
  const [val, setVal]     = useState(filter?.value ?? '');
  const [distinct, setDistinct] = useState(null);
  const [loading, setLoading]   = useState(false);
  const needsValue = !['is_null', 'is_not_null'].includes(op);
  const ref = useRef(null);

  useEffect(() => {
    const h = e => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [onClose]);

  useEffect(() => {
    setLoading(true);
    const q = subsetId != null ? `?subset_id=${subsetId}` : '';
    apiFetch(`/api/pipeline/${pipelineId}/column-values/${encodeURIComponent(col)}${q}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setDistinct(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [pipelineId, subsetId, col]);

  const showPickList = needsValue && distinct && !distinct.truncated && distinct.values?.length > 0
    && ['eq', 'neq'].includes(op);

  return (
    <div ref={ref}
      className="absolute top-full left-0 mt-1 z-50 bg-white border border-gray-200 rounded-lg shadow-xl p-2.5 w-64 space-y-2"
      onClick={e => e.stopPropagation()}>
      <p className="text-[10px] text-gray-400 font-medium uppercase tracking-wide truncate">Filter: {col}</p>
      <select value={op} onChange={e => setOp(e.target.value)}
        className="w-full border rounded px-2 py-1 text-xs">
        {ops.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>

      {needsValue && !showPickList && (
        <input value={val} onChange={e => setVal(e.target.value)} autoFocus placeholder="Value…"
          className="w-full border rounded px-2 py-1 text-xs"
          onKeyDown={e => { if (e.key === 'Enter') { onApply(op, val); onClose(); } }} />
      )}

      {showPickList && (
        <div className="max-h-40 overflow-y-auto border rounded divide-y divide-gray-50">
          {loading && <p className="text-[10px] text-gray-400 px-2 py-1">Loading…</p>}
          {distinct.values.map((v, i) => {
            const sv = String(v);
            const selected = val === sv;
            return (
              <button key={i} type="button"
                onClick={() => { setVal(sv); onApply(op, sv); onClose(); }}
                className={`w-full text-left px-2 py-1 text-xs truncate hover:bg-blue-50 ${selected ? 'bg-blue-50 font-medium text-blue-700' : 'text-gray-700'}`}>
                {sv || <span className="italic text-gray-400">(empty)</span>}
              </button>
            );
          })}
        </div>
      )}

      {needsValue && showPickList && (
        <input value={val} onChange={e => setVal(e.target.value)} placeholder="Or type value…"
          className="w-full border rounded px-2 py-1 text-[11px] text-gray-500"
          onKeyDown={e => { if (e.key === 'Enter') { onApply(op, val); onClose(); } }} />
      )}

      <div className="flex gap-1.5 pt-1">
        <button type="button"
          onClick={() => { onApply(op, val); onClose(); }}
          className="flex-1 text-[11px] px-2 py-1 bg-blue-600 text-white rounded hover:bg-blue-700 font-medium">
          Apply
        </button>
        {filter && (
          <button type="button" onClick={() => { onClear(); onClose(); }}
            className="flex-1 text-[11px] px-2 py-1 border rounded hover:bg-gray-50">
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════ */
export default function ResearchDbViewer({ pipelineId, subset, llmOnly, onClose, onSubsetSaved, initialCustomSql = '' }) {
  const [schema, setSchema] = useState([]);
  const [filters, setFilters] = useState([]);
  const [sortRules, setSortRules] = useState([{ id: uid(), column: 'publication_year', dir: 'desc' }]);
  const [visibleCols, setVisibleCols] = useState([]);
  const [builderSql, setBuilderSql] = useState('');
  const [customSql, setCustomSql] = useState('');
  const [useCustomSql, setUseCustomSql] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [table, setTable] = useState(null);
  const [loading, setLoading] = useState(false);
  const [explainLoading, setExplainLoading] = useState(false);
  const [expanded, setExpanded] = useState({});
  const [expandedDetails, setExpandedDetails] = useState({});
  const [detail, setDetail] = useState(null);
  const [sqlError, setSqlError] = useState('');
  const [sqlOpen, setSqlOpen] = useState(false);
  const [colPickerOpen, setColPickerOpen] = useState(false);
  const [filterPopCol, setFilterPopCol] = useState(null);

  const customSqlRef = useRef(customSql);
  customSqlRef.current = customSql;
  const colPickerRef = useRef(null);

  const colNames = useMemo(() => schema.map(c => c.name).filter(n => !HIDDEN_COLS.has(n)), [schema]);
  const allColNames = useMemo(() => schema.map(c => c.name), [schema]);
  const colTypeMap = useMemo(() => {
    const m = {};
    schema.forEach(c => { m[c.name] = c.type || 'TEXT'; });
    return m;
  }, [schema]);
  const filterMap = useMemo(() => {
    const m = {};
    filters.forEach(f => { m[f.column] = f; });
    return m;
  }, [filters]);
  const sortMap = useMemo(() => {
    const m = {};
    sortRules.forEach((s, i) => { m[s.column] = { dir: s.dir, idx: i + 1 }; });
    return m;
  }, [sortRules]);

  useEffect(() => {
    apiFetch(`/api/pipeline/${pipelineId}/schema`)
      .then(r => r.ok ? r.json() : [])
      .then(s => {
        setSchema(s);
        const names = s.map(c => c.name).filter(n => !HIDDEN_COLS.has(n));
        const base = [...DEFAULT_VISIBLE, ...(llmOnly ? LLM_EXTRA : [])].filter(c => names.includes(c));
        setVisibleCols(base.length ? base : names.slice(0, 8));
      })
      .catch(() => {});
  }, [pipelineId, llmOnly]);

  useEffect(() => { setUseCustomSql(false); setCustomSql(''); setSqlError(''); }, [subset?.id]);
  useEffect(() => {
    if (!initialCustomSql || !subset?.id) return;
    setUseCustomSql(true);
    setCustomSql(initialCustomSql);
    setSqlOpen(true);
  }, [initialCustomSql, subset?.id]);

  useEffect(() => {
    const h = e => { if (colPickerRef.current && !colPickerRef.current.contains(e.target)) setColPickerOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  /* ── Explain SQL ─────────────────────────────────────────────────────────── */
  const refreshExplain = useCallback(async () => {
    if (!subset?.id || useCustomSql) return;
    setExplainLoading(true); setSqlError('');
    try {
      const res = await apiFetch(`/api/pipeline/${pipelineId}/records/explain-sql`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subset_id: subset.id,
          filters: filters.map(({ column, op, value }) => ({ column, op, value })),
          sort: sortRules.map(({ column, dir }) => ({ column, dir })),
          visible_cols: visibleCols, custom_sql: null,
        }),
      });
      const d = await res.json();
      if (!res.ok) { setSqlError(d.detail || 'Explain failed'); return; }
      setBuilderSql(d.sql || '');
    } finally { setExplainLoading(false); }
  }, [pipelineId, subset?.id, filters, sortRules, visibleCols, useCustomSql]);

  useEffect(() => { refreshExplain(); }, [refreshExplain]);

  /* ── Load records ────────────────────────────────────────────────────────── */
  const loadTable = useCallback(async pg => {
    if (!subset?.id) return;
    setLoading(true); setSqlError('');
    setExpanded({}); setExpandedDetails({});
    try {
      const res = await apiFetch(`/api/pipeline/${pipelineId}/records/view`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subset_id: subset.id,
          filters: filters.map(({ column, op, value }) => ({ column, op, value })),
          sort: sortRules.map(({ column, dir }) => ({ column, dir })),
          page: pg, page_size: pageSize, visible_cols: visibleCols,
          custom_sql: useCustomSql ? (customSqlRef.current.trim() || null) : null,
        }),
      });
      const d = await res.json();
      if (!res.ok) { setSqlError(d.detail || 'Load failed'); setTable(null); return; }
      setTable(d); setPage(pg);
    } finally { setLoading(false); }
  }, [pipelineId, subset?.id, filters, sortRules, pageSize, visibleCols, useCustomSql]);

  useEffect(() => {
    if (!subset?.id || useCustomSql) return;
    loadTable(1);
  }, [subset?.id, filters, sortRules, pageSize, visibleCols, useCustomSql, loadTable]);
  useEffect(() => {
    if (!subset?.id || !useCustomSql) return;
    const t = customSqlRef.current.trim();
    if (!t) return;
    loadTable(1);
  }, [subset?.id, useCustomSql, loadTable]);

  /* ── Expand row: fetch ALL fields ────────────────────────────────────────── */
  const toggleExpand = async (i, oid) => {
    const willOpen = !expanded[i];
    setExpanded(e => ({ ...e, [i]: willOpen }));
    if (willOpen && oid && !expandedDetails[oid]) {
      try {
        const q = new URLSearchParams({ record_id: oid, subset_id: String(subset.id) });
        const res = await apiFetch(`/api/pipeline/${pipelineId}/record-detail?${q}`);
        if (res.ok) {
          const data = await res.json();
          setExpandedDetails(prev => ({ ...prev, [oid]: data }));
        }
      } catch (err) {
        /* silently ignore */
      }
    }
  };

  /* ── Column-header sort (click to cycle, additive with shift) ────────────── */
  const cycleSort = (col, additive) => {
    setSortRules(prev => {
      const existing = prev.find(s => s.column === col);
      if (additive) {
        if (!existing) return [...prev, { id: uid(), column: col, dir: 'desc' }];
        if (existing.dir === 'desc') return prev.map(s => s.column === col ? { ...s, dir: 'asc' } : s);
        return prev.filter(s => s.column !== col);
      }
      if (!existing) return [{ id: uid(), column: col, dir: 'desc' }];
      if (existing.dir === 'desc') return [{ ...existing, dir: 'asc' }];
      return [];
    });
  };

  /* ── Column-header filter helpers ────────────────────────────────────────── */
  const applyFilter = (col, op, val) => {
    setFilters(prev => {
      const rest = prev.filter(f => f.column !== col);
      return [...rest, { id: uid(), column: col, op, value: val }];
    });
  };
  const clearFilter = col => setFilters(prev => prev.filter(f => f.column !== col));
  const toggleCol = name => setVisibleCols(v => (v.includes(name) ? v.filter(x => x !== name) : [...v, name]));

  const openDetail = async openalexId => {
    const q = new URLSearchParams({ record_id: openalexId, subset_id: String(subset.id) });
    const res = await apiFetch(`/api/pipeline/${pipelineId}/record-detail?${q}`);
    if (res.ok) setDetail(await res.json());
  };

  const cols = table?.columns || visibleCols;
  const isAbstractCol = c => c === 'abstract_text';
  const activeFilterCount = filters.length;
  const activeSortCount = sortRules.length;

  /* ═══════════════════════════════════════════════════════════════════════════ */
  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-gray-50 rounded-2xl shadow-2xl w-full max-w-[95vw] max-h-[94vh] overflow-hidden flex flex-col border border-gray-200">

        {/* ── Header ────────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between px-5 py-2.5 border-b border-gray-200 bg-white shrink-0">
          <div className="flex items-center gap-3 flex-wrap min-w-0">
            <div className="min-w-0">
              <h2 className="text-sm font-bold text-gray-900">Research DB Viewer</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Subset: <span className="font-medium text-gray-700">{subset.name}</span>
                {subset.row_count != null && <span className="ml-1">· {subset.row_count.toLocaleString()} rows</span>}
              </p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-700 text-2xl leading-none px-2 shrink-0">×</button>
        </div>

        {/* ── Toolbar ───────────────────────────────────────────────────── */}
        <div className="flex items-center gap-2 px-4 py-1.5 bg-white border-b border-gray-100 shrink-0 flex-wrap text-xs">
          {activeFilterCount > 0 && (
            <span className="inline-flex items-center gap-1 bg-blue-50 text-blue-700 rounded-full px-2.5 py-0.5 font-medium">
              {activeFilterCount} filter{activeFilterCount > 1 ? 's' : ''}
              <button type="button" onClick={() => setFilters([])} className="text-blue-400 hover:text-blue-700 ml-0.5" title="Clear all filters">×</button>
            </span>
          )}
          {activeSortCount > 0 && (
            <span className="inline-flex items-center gap-1 bg-indigo-50 text-indigo-700 rounded-full px-2.5 py-0.5 font-medium">
              {activeSortCount} sort{activeSortCount > 1 ? 's' : ''}
              <button type="button" onClick={() => setSortRules([])} className="text-indigo-400 hover:text-indigo-700 ml-0.5" title="Clear all sorts">×</button>
            </span>
          )}
          {useCustomSql && <span className="bg-amber-50 text-amber-700 rounded-full px-2.5 py-0.5 font-medium">custom SQL</span>}

          <span className="text-[10px] text-gray-400 ml-1 hidden sm:inline">Click header to sort · Shift+click for multi-sort · Funnel to filter</span>

          <div className="ml-auto flex items-center gap-1.5">
            <div className="relative" ref={colPickerRef}>
              <button type="button" onClick={() => setColPickerOpen(o => !o)}
                className={`px-2 py-1 border rounded-lg hover:bg-gray-50 ${colPickerOpen ? 'bg-gray-100 text-gray-900' : 'text-gray-600'}`}>
                Columns ({visibleCols.length}/{colNames.length})
              </button>
              {colPickerOpen && (
                <div className="absolute right-0 top-full mt-1 z-50 bg-white border border-gray-200 rounded-lg shadow-xl p-3 w-80 max-h-72 overflow-y-auto">
                  <div className="flex gap-2 mb-2 pb-2 border-b border-gray-100">
                    <button type="button" onClick={() => setVisibleCols([...colNames])}
                      className="text-[11px] px-2 py-0.5 bg-blue-50 text-blue-700 rounded hover:bg-blue-100 font-medium">All</button>
                    <button type="button" onClick={() => setVisibleCols(DEFAULT_VISIBLE.filter(c => colNames.includes(c)))}
                      className="text-[11px] px-2 py-0.5 bg-gray-50 text-gray-600 rounded hover:bg-gray-100 font-medium">Default</button>
                    <button type="button" onClick={() => setVisibleCols([])}
                      className="text-[11px] px-2 py-0.5 bg-gray-50 text-gray-600 rounded hover:bg-gray-100 font-medium">None</button>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {colNames.map(c => (
                      <label key={c} className={`inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border cursor-pointer transition
                        ${visibleCols.includes(c) ? 'bg-blue-50 border-blue-200 text-blue-800' : 'bg-gray-50 border-gray-100 text-gray-500 hover:bg-gray-100'}`}>
                        <input type="checkbox" checked={visibleCols.includes(c)} onChange={() => toggleCol(c)} className="w-3 h-3" />
                        {c}
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <button type="button" onClick={() => setSqlOpen(o => !o)}
              className={`px-2 py-1 border rounded-lg hover:bg-gray-50 ${sqlOpen ? 'bg-gray-100 text-gray-900' : 'text-gray-600'}`}>
              SQL
            </button>

            <select value={pageSize} onChange={e => setPageSize(Number(e.target.value))} className="border rounded px-1.5 py-1 text-gray-600">
              {[25, 50, 100].map(n => <option key={n} value={n}>{n}/pg</option>)}
            </select>

            <button type="button" onClick={() => loadTable(page)} disabled={loading}
              className="px-2 py-1 border rounded-lg hover:bg-gray-50 disabled:opacity-50 text-gray-600" title="Refresh">↻</button>
          </div>
        </div>

        {/* ── SQL panel ─────────────────────────────────────────────────── */}
        {sqlOpen && (
          <div className="px-4 py-2.5 bg-white border-b border-gray-100 shrink-0 space-y-2">
            <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
              <input type="checkbox" checked={useCustomSql} onChange={e => {
                const on = e.target.checked;
                setUseCustomSql(on);
                if (on) {
                  let next = builderSql.replace(/\s*LIMIT\s+<page_size>\s+OFFSET\s+<offset>\s*$/i, '').trim();
                  next = stripOuterFrSubsetCte(next);
                  setCustomSql(next);
                } else setCustomSql('');
              }} />
              Custom SQL (subset-scoped, must use <code className="bg-gray-100 px-1 rounded">_fr_subset</code>)
            </label>
            {explainLoading && !useCustomSql && <p className="text-xs text-gray-400">Updating…</p>}
            <textarea
              value={useCustomSql ? customSql : builderSql}
              onChange={e => { if (useCustomSql) setCustomSql(e.target.value); }}
              readOnly={!useCustomSql}
              rows={useCustomSql ? 6 : 3}
              className={`w-full border rounded-lg px-3 py-2 text-xs font-mono resize-y ${useCustomSql ? 'bg-white' : 'bg-gray-50 text-gray-600'}`}
              placeholder={useCustomSql ? 'SELECT * FROM _fr_subset WHERE ...' : 'Generated from column filters/sort'}
            />
            {useCustomSql && (
              <div className="flex gap-2">
                <button type="button" onClick={() => loadTable(1)}
                  className="text-xs px-3 py-1 bg-blue-600 text-white rounded-lg hover:bg-blue-700">Run</button>
                <button type="button" onClick={() => { setUseCustomSql(false); setCustomSql(''); }}
                  className="text-xs px-3 py-1 border rounded-lg hover:bg-gray-50">Back to builder</button>
              </div>
            )}
            {sqlError && <p className="text-xs text-red-600">{sqlError}</p>}
          </div>
        )}
        {!sqlOpen && sqlError && <p className="text-xs text-red-600 px-4 py-1 bg-red-50 border-b border-red-100 shrink-0">{sqlError}</p>}

        {/* ── Table info ────────────────────────────────────────────────── */}
        {table && (
          <div className="flex items-center justify-between px-4 py-1 bg-gray-50 border-b border-gray-100 shrink-0 text-xs text-gray-500">
            <span>{table.total?.toLocaleString()} matching</span>
            <span>page {table.page} of {Math.max(1, Math.ceil(table.total / table.per_page))}</span>
          </div>
        )}

        {/* ── Records table ─────────────────────────────────────────────── */}
        <div className="flex-1 overflow-auto">
          {loading && <p className="text-xs text-gray-400 text-center py-10">Loading…</p>}
          {table?.rows && !loading && (
            <table className="w-full text-xs border-collapse">
              <thead className="bg-gray-100 sticky top-0 z-10">
                <tr>
                  <th className="w-7 px-1 bg-gray-100" />
                  {cols.map(c => {
                    const si = sortMap[c];
                    const hasFilter = !!filterMap[c];
                    return (
                      <th key={c} className={`relative px-2 py-1.5 text-left font-medium whitespace-nowrap select-none group/th bg-gray-100
                        ${isAbstractCol(c) ? 'min-w-[260px]' : ''}
                        ${hasFilter ? 'text-blue-700' : 'text-gray-600'}`}>
                        <div className="flex items-center gap-0.5">
                          <span className="cursor-pointer hover:text-blue-600 truncate"
                            onClick={e => cycleSort(c, e.shiftKey)}>
                            {c}
                          </span>
                          <SortIcon dir={si?.dir} index={activeSortCount > 1 ? si?.idx : 0} />
                          <button type="button"
                            className="ml-auto shrink-0 p-0.5 rounded hover:bg-gray-200/70"
                            onClick={e => { e.stopPropagation(); setFilterPopCol(prev => prev === c ? null : c); }}>
                            <FunnelIcon active={hasFilter} />
                          </button>
                        </div>
                        {filterPopCol === c && (
                          <ColumnFilterPopover
                            pipelineId={pipelineId}
                            subsetId={subset?.id}
                            col={c}
                            colType={colTypeMap[c]}
                            filter={filterMap[c]}
                            onApply={(op, val) => applyFilter(c, op, val)}
                            onClear={() => clearFilter(c)}
                            onClose={() => setFilterPopCol(null)}
                          />
                        )}
                      </th>
                    );
                  })}
                  <th className="w-12 px-1 bg-gray-100 text-gray-500 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody className="bg-white">
                {table.rows.map((row, i) => {
                  const oidIdx = cols.indexOf('openalex_id');
                  const oid = oidIdx >= 0 ? row[oidIdx] : null;
                  const isOpen = expanded[i];
                  const fullRec = oid ? expandedDetails[oid] : null;

                  return (
                    <React.Fragment key={`row-${i}-${oid ?? i}`}>
                      <tr className={`border-t border-gray-100 hover:bg-blue-50/40 align-top ${isOpen ? 'bg-blue-50/20' : ''}`}>
                        <td className="px-1 pt-1.5">
                          <button type="button" onClick={() => toggleExpand(i, oid)}
                            className="text-gray-400 hover:text-gray-700 text-[10px] w-4 text-center">
                            {isOpen ? '▼' : '▶'}
                          </button>
                        </td>
                        {row.map((cell, j) => (
                          <td key={j}
                            className={`px-2 py-1.5 ${isAbstractCol(cols[j]) ? 'max-w-[360px] text-[11px] leading-relaxed text-gray-700' : 'max-w-[200px] truncate'}`}
                            title={!isAbstractCol(cols[j]) ? String(cell ?? '') : undefined}>
                            {renderCell(cell, cols[j])}
                          </td>
                        ))}
                        <td className="px-1 pt-1.5">
                          {oid && <button type="button" onClick={() => openDetail(oid)}
                            className="text-blue-600 hover:underline text-[10px]">Open</button>}
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="bg-slate-50 border-t border-gray-100">
                          <td />
                          <td colSpan={cols.length + 1} className="px-3 py-3 text-[11px] text-gray-700">
                            {fullRec ? (
                              <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 max-h-[50vh] overflow-y-auto">
                                {allColNames.map(c => {
                                  const val = fullRec[c];
                                  if (val == null || val === '') return null;
                                  const sv = String(val);
                                  const isAbs = isAbstractCol(c);
                                  return (
                                    <React.Fragment key={c}>
                                      <span className="text-gray-400 font-medium whitespace-nowrap py-0.5">{c}</span>
                                      {isAbs ? (
                                        <p className="whitespace-pre-wrap leading-relaxed text-gray-800 py-0.5">{sv}</p>
                                      ) : (
                                        <span className="font-mono break-all text-gray-700 py-0.5">{sv}</span>
                                      )}
                                    </React.Fragment>
                                  );
                                })}
                              </div>
                            ) : (
                              <span className="text-gray-400">Loading all fields…</span>
                            )}
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* ── Pagination ────────────────────────────────────────────────── */}
        {table && table.total > table.per_page && (
          <div className="flex justify-center gap-2 items-center px-4 py-2 border-t border-gray-200 bg-white shrink-0">
            <button type="button" disabled={page <= 1 || loading} onClick={() => loadTable(page - 1)}
              className="text-xs px-3 py-1.5 border rounded-lg disabled:opacity-30 hover:bg-gray-50">← Prev</button>
            <span className="text-xs text-gray-500">Page {page} of {Math.ceil(table.total / table.per_page)}</span>
            <button type="button" disabled={page >= Math.ceil(table.total / table.per_page) || loading} onClick={() => loadTable(page + 1)}
              className="text-xs px-3 py-1.5 border rounded-lg disabled:opacity-30 hover:bg-gray-50">Next →</button>
          </div>
        )}

        {/* ── Detail modal ──────────────────────────────────────────────── */}
        {detail && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[60] p-4">
            <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl max-h-[90vh] overflow-y-auto p-6">
              <div className="flex justify-between items-start mb-4">
                <h3 className="font-bold text-gray-900 text-sm flex-1 pr-4">{detail.title}</h3>
                <button type="button" onClick={() => setDetail(null)} className="text-gray-400 hover:text-gray-700 text-xl leading-none">×</button>
              </div>
              <div className="space-y-1.5 max-h-[70vh] overflow-y-auto">
                {Object.entries(detail).map(([k, v]) => v != null && v !== '' && (
                  <div key={k} className={`flex gap-2 text-xs border-b border-gray-50 pb-1 ${k === 'abstract_text' ? 'flex-col' : ''}`}>
                    <span className={`text-gray-500 font-medium ${k === 'abstract_text' ? '' : 'w-44 shrink-0'}`}>{k}</span>
                    {k === 'abstract_text' ? (
                      <p className="text-gray-800 whitespace-pre-wrap leading-relaxed text-[13px]">{String(v)}</p>
                    ) : (
                      <span className="text-gray-800 break-words flex-1">{
                        typeof v === 'string' && (v.startsWith('[') || v.startsWith('{'))
                          ? <span className="font-mono text-[11px]">{v}</span>
                          : String(v)
                      }</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  );
}
