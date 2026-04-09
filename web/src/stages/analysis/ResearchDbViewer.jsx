import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { apiFetch } from '../../api/client';
import { useRecordStars } from './useRecordStars.js';

const DB_VIEWER_W_KEY = (pid) => `oa-db-viewer-width-${pid}`;

function readPanelWidth(pipelineId) {
  try {
    const n = Number(sessionStorage.getItem(DB_VIEWER_W_KEY(pipelineId)));
    if (Number.isFinite(n) && n >= 320) return Math.min(n, typeof window !== 'undefined' ? window.innerWidth - 40 : 1600);
  } catch { /* */ }
  return typeof window !== 'undefined' ? Math.min(1120, Math.floor(window.innerWidth * 0.92)) : 960;
}

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
  if (colName === 'abstract_text' && s.length > 60) {
    return <span className="whitespace-normal line-clamp-[8] leading-relaxed text-[11px]">{s}</span>;
  }
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

function filterChipLabel(f) {
  if (f.op === 'in') {
    const arr = Array.isArray(f.value) ? f.value : [];
    const shown = arr.slice(0, 3).map((x) => String(x));
    const more = arr.length > 3 ? ` +${arr.length - 3}` : '';
    const blank = f.include_null ? ' · ∅' : '';
    return `${f.column} ∈ (${shown.join(', ')}${more})${blank}`;
  }
  const v = f.value != null && f.value !== '' ? String(f.value) : '';
  const short = v.length > 28 ? `${v.slice(0, 28)}…` : v;
  const op = (() => {
    if (f.op === 'contains') return 'contains';
    if (f.op === 'starts_with') return 'starts with';
    if (f.op === 'is_null') return 'is null';
    if (f.op === 'is_not_null') return 'is not null';
    return f.op;
  })();
  if (f.op === 'is_null' || f.op === 'is_not_null') return `${f.column} ${op}`;
  return `${f.column} ${op} ${short}`;
}

function valKey(v) {
  if (v === null || v === undefined) return '__null__';
  return `${typeof v}:${String(v)}`;
}

/* ── Excel-style column filter (checkboxes + blanks) + sort ─────────────────── */
function ColumnFilterPopover({
  pipelineId,
  subsetId,
  col,
  colType,
  filter,
  onApply,
  onClear,
  onClose,
  onSortAsc,
  onSortDesc,
  fixedPosition,
}) {
  const [distinct, setDistinct] = useState(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [checked, setChecked] = useState(() => new Set());
  const [includeBlank, setIncludeBlank] = useState(false);
  const [advOpen, setAdvOpen] = useState(false);
  const ops = opsForType(colType);
  const [advOp, setAdvOp] = useState(filter?.op && filter.op !== 'in' ? filter.op : ops[0].id);
  const [advVal, setAdvVal] = useState(filter?.value != null && filter.op !== 'in' ? String(filter.value) : '');
  const ref = useRef(null);

  const isNum = ['INT', 'INTEGER', 'REAL', 'FLOAT', 'NUMERIC', 'DOUBLE'].some((k) =>
    (colType || '').toUpperCase().includes(k));

  useEffect(() => {
    const h = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [onClose]);

  useEffect(() => {
    setLoading(true);
    const q = new URLSearchParams();
    if (subsetId != null) q.set('subset_id', String(subsetId));
    q.set('limit', '2500');
    apiFetch(`/api/pipeline/${pipelineId}/column-values/${encodeURIComponent(col)}?${q}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d) setDistinct(d);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [pipelineId, subsetId, col]);

  useEffect(() => {
    if (!distinct) return;
    const valuesList = distinct.values || [];
    const bc = distinct.blank_count ?? 0;
    if (!filter) {
      setChecked(new Set(valuesList.map(valKey)));
      setIncludeBlank(bc > 0);
      return;
    }
    if (filter.op === 'in' && Array.isArray(filter.value)) {
      setChecked(new Set(filter.value.map(valKey)));
      setIncludeBlank(!!filter.include_null);
    } else if (filter.op === 'eq') {
      setChecked(new Set([valKey(filter.value)]));
      setIncludeBlank(!!filter.include_null);
    } else {
      setChecked(new Set());
      setIncludeBlank(false);
    }
  }, [distinct, filter]);

  const values = distinct?.values || [];
  const blankCount = distinct?.blank_count ?? 0;
  const filteredVals = useMemo(() => {
    const s = search.trim().toLowerCase();
    if (!s) return values;
    return values.filter((v) => String(v).toLowerCase().includes(s));
  }, [values, search]);

  const toggleKey = (k) => {
    setChecked((prev) => {
      const n = new Set(prev);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });
  };

  const selectAllVisible = () => {
    setChecked((prev) => {
      const n = new Set(prev);
      filteredVals.forEach((v) => n.add(valKey(v)));
      return n;
    });
  };

  const clearSelection = () => {
    setChecked(new Set());
    setIncludeBlank(false);
  };

  const applyExcel = () => {
    const picked = [];
    values.forEach((v) => {
      if (checked.has(valKey(v))) picked.push(v);
    });
    const allValuesChecked = values.length > 0 && values.every((v) => checked.has(valKey(v)));
    const blankRowNeeded = blankCount > 0;
    const blankOk = !blankRowNeeded || includeBlank;
    if (allValuesChecked && blankOk) {
      onClear();
      onClose();
      return;
    }
    if (picked.length === 0 && !includeBlank) {
      onClear();
      onClose();
      return;
    }
    onApply('in', picked, includeBlank);
    onClose();
  };

  const applyAdvanced = () => {
    if (['is_null', 'is_not_null'].includes(advOp)) {
      onApply(advOp, null, false);
    } else {
      onApply(advOp, isNum && advVal !== '' ? Number(advVal) : advVal, false);
    }
    onClose();
  };

  const posClass = fixedPosition
    ? 'fixed z-[100] bg-white border border-gray-300 rounded-lg shadow-2xl w-[min(26rem,calc(100vw-1rem))] max-h-[min(32rem,calc(100vh-2rem))] flex flex-col overflow-hidden'
    : 'absolute top-full left-0 mt-1 z-50 bg-white border border-gray-300 rounded-lg shadow-2xl w-[min(26rem,calc(100vw-1rem))] max-h-96 flex flex-col overflow-hidden';

  return (
    <div
      ref={ref}
      style={fixedPosition ? { top: fixedPosition.top, left: fixedPosition.left } : undefined}
      className={posClass}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="bg-gray-50 border-b border-gray-200 px-2.5 py-2 shrink-0">
        <p className="text-[11px] font-semibold text-gray-800 truncate" title={col}>{col}</p>
        <div className="flex gap-1.5 mt-2">
          <button
            type="button"
            onClick={() => {
              onSortAsc?.();
              onClose();
            }}
            className="flex-1 text-[11px] px-2 py-1.5 rounded border border-gray-200 bg-white hover:bg-blue-50 hover:border-blue-200 font-medium text-gray-800"
          >
            {isNum ? 'Sort ↑ smallest' : 'Sort A → Z'}
          </button>
          <button
            type="button"
            onClick={() => {
              onSortDesc?.();
              onClose();
            }}
            className="flex-1 text-[11px] px-2 py-1.5 rounded border border-gray-200 bg-white hover:bg-blue-50 hover:border-blue-200 font-medium text-gray-800"
          >
            {isNum ? 'Sort ↓ largest' : 'Sort Z → A'}
          </button>
        </div>
      </div>

      <div className="px-2.5 py-2 flex-1 min-h-0 flex flex-col gap-2">
        <p className="text-[10px] text-gray-500 font-medium uppercase tracking-wide">Filter by values</p>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search values…"
          className="w-full border rounded px-2 py-1 text-xs"
        />
        <div className="flex gap-1.5">
          <button type="button" onClick={selectAllVisible} className="text-[10px] px-2 py-0.5 border rounded bg-white hover:bg-gray-50">
            Select all shown
          </button>
          <button type="button" onClick={clearSelection} className="text-[10px] px-2 py-0.5 border rounded bg-white hover:bg-gray-50">
            Clear
          </button>
        </div>
        {distinct?.truncated && (
          <p className="text-[9px] text-amber-800 bg-amber-50 border border-amber-100 rounded px-1.5 py-0.5">
            List truncated — narrow with search or use advanced filter.
          </p>
        )}
        <div className="flex-1 min-h-[8rem] overflow-y-auto border rounded bg-white divide-y divide-gray-100">
          {loading && <p className="text-[10px] text-gray-400 px-2 py-2">Loading values…</p>}
          {!loading && blankCount > 0 && (
            <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-gray-50 cursor-pointer text-xs">
              <input
                type="checkbox"
                checked={includeBlank}
                onChange={(e) => setIncludeBlank(e.target.checked)}
                className="rounded border-gray-300"
              />
              <span className="text-gray-500 italic">(Blanks)</span>
              <span className="text-gray-400 text-[10px] ml-auto">{blankCount}</span>
            </label>
          )}
          {!loading &&
            filteredVals.map((v, i) => {
              const k = valKey(v);
              const label = v === null || v === '' ? '(empty)' : String(v);
              return (
                <label key={i} className="flex items-center gap-2 px-2 py-1 hover:bg-blue-50/50 cursor-pointer text-xs">
                  <input
                    type="checkbox"
                    checked={checked.has(k)}
                    onChange={() => toggleKey(k)}
                    className="rounded border-gray-300 shrink-0"
                  />
                  <span className="truncate" title={label}>{label}</span>
                </label>
              );
            })}
          {!loading && filteredVals.length === 0 && !blankCount && (
            <p className="text-[10px] text-gray-400 px-2 py-2">No values</p>
          )}
        </div>

        <div className="flex gap-1.5 pt-1">
          <button
            type="button"
            onClick={applyExcel}
            className="flex-1 text-[11px] px-2 py-1.5 bg-blue-600 text-white rounded-md hover:bg-blue-700 font-medium"
          >
            OK
          </button>
          {filter && (
            <button type="button" onClick={() => { onClear(); onClose(); }} className="text-[11px] px-2 py-1.5 border border-gray-200 rounded-md hover:bg-gray-50">
              Clear
            </button>
          )}
        </div>

        <button type="button" onClick={() => setAdvOpen((o) => !o)} className="text-[10px] text-blue-700 hover:underline text-left">
          {advOpen ? '▼' : '▶'} Advanced text / number filter
        </button>
        {advOpen && (
          <div className="border rounded p-2 space-y-1.5 bg-gray-50/80">
            <select value={advOp} onChange={(e) => setAdvOp(e.target.value)} className="w-full border rounded px-2 py-1 text-xs">
              {ops.map((o) => (
                <option key={o.id} value={o.id}>{o.label}</option>
              ))}
            </select>
            {!['is_null', 'is_not_null'].includes(advOp) && (
              <input
                value={advVal}
                onChange={(e) => setAdvVal(e.target.value)}
                placeholder="Value…"
                className="w-full border rounded px-2 py-1 text-xs"
              />
            )}
            <button type="button" onClick={applyAdvanced} className="w-full text-[11px] py-1 bg-white border rounded hover:bg-gray-50">
              Apply advanced
            </button>
          </div>
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
  const [customSqlPanelOpen, setCustomSqlPanelOpen] = useState(false);
  const [colPickerOpen, setColPickerOpen] = useState(false);
  /** { col, top, left } — fixed-position popover anchored to filter button */
  const [filterPop, setFilterPop] = useState(null);
  const [panelWidth, setPanelWidth] = useState(() => readPanelWidth(pipelineId));
  const resizeActive = useRef(false);

  const { isStarred, toggleStar } = useRecordStars(pipelineId);

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

  /** Always request openalex_id from the API so expand / detail / star work even when the column is hidden. */
  const visibleColsForQuery = useMemo(() => {
    if (!colNames.includes('openalex_id')) return visibleCols;
    return [...new Set(['openalex_id', ...visibleCols])];
  }, [visibleCols, colNames]);

  const apiCols = table?.columns || visibleColsForQuery;
  const displayCols = useMemo(
    () => visibleCols.filter((c) => apiCols.includes(c)),
    [visibleCols, apiCols],
  );

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
    setCustomSqlPanelOpen(true);
  }, [initialCustomSql, subset?.id]);

  useEffect(() => {
    if (useCustomSql) setCustomSqlPanelOpen(true);
  }, [useCustomSql]);

  useEffect(() => {
    const h = e => { if (colPickerRef.current && !colPickerRef.current.contains(e.target)) setColPickerOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  useEffect(() => {
    if (!filterPop) return;
    const close = () => setFilterPop(null);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
      document.removeEventListener('keydown', onKey);
    };
  }, [filterPop]);

  useEffect(() => {
    setPanelWidth(readPanelWidth(pipelineId));
  }, [pipelineId]);

  useEffect(() => {
    try {
      sessionStorage.setItem(DB_VIEWER_W_KEY(pipelineId), String(panelWidth));
    } catch { /* */ }
  }, [pipelineId, panelWidth]);

  const startResize = useCallback(
    (e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = panelWidth;
      const onMove = (ev) => {
        const next = Math.min(Math.max(320, startW + (startX - ev.clientX)), window.innerWidth - 40);
        setPanelWidth(next);
      };
      const onUp = () => {
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        resizeActive.current = false;
      };
      resizeActive.current = true;
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    },
    [panelWidth],
  );

  /* ── Explain SQL ─────────────────────────────────────────────────────────── */
  const refreshExplain = useCallback(async () => {
    if (!subset?.id) return;
    if (useCustomSql && !customSqlRef.current.trim()) return;
    setExplainLoading(true); setSqlError('');
    try {
      const res = await apiFetch(`/api/pipeline/${pipelineId}/records/explain-sql`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subset_id: subset.id,
          filters: filters.map(({ column, op, value, include_null }) => ({
            column, op, value, include_null: !!include_null,
          })),
          sort: sortRules.map(({ column, dir }) => ({ column, dir })),
          visible_cols: visibleColsForQuery,
          custom_sql: useCustomSql ? (customSqlRef.current.trim() || null) : null,
        }),
      });
      const d = await res.json();
      if (!res.ok) { setSqlError(d.detail || 'Explain failed'); return; }
      if (!useCustomSql) setBuilderSql(d.sql || '');
    } finally { setExplainLoading(false); }
  }, [pipelineId, subset?.id, filters, sortRules, visibleColsForQuery, useCustomSql]);

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
          filters: filters.map(({ column, op, value, include_null }) => ({
            column, op, value, include_null: !!include_null,
          })),
          sort: sortRules.map(({ column, dir }) => ({ column, dir })),
          page: pg, page_size: pageSize, visible_cols: visibleColsForQuery,
          custom_sql: useCustomSql ? (customSqlRef.current.trim() || null) : null,
        }),
      });
      const d = await res.json();
      if (!res.ok) { setSqlError(d.detail || 'Load failed'); setTable(null); return; }
      setTable(d); setPage(pg);
    } finally { setLoading(false); }
  }, [pipelineId, subset?.id, filters, sortRules, pageSize, visibleColsForQuery, useCustomSql]);

  useEffect(() => {
    if (!subset?.id) return;
    if (useCustomSql && !customSqlRef.current.trim()) return;
    loadTable(1);
  }, [subset?.id, filters, sortRules, pageSize, visibleColsForQuery, useCustomSql, customSql, loadTable]);

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
  const applyColumnFilter = (col, op, val, includeNull = false) => {
    setFilters((prev) => {
      const rest = prev.filter((f) => f.column !== col);
      if (op === 'in' && (!val || val.length === 0) && !includeNull) return rest;
      return [...rest, { id: uid(), column: col, op, value: val, include_null: !!includeNull }];
    });
  };
  const clearFilter = (col) => setFilters((prev) => prev.filter((f) => f.column !== col));

  const downloadCsv = async () => {
    if (!subset?.id) return;
    try {
      const res = await apiFetch(`/api/pipeline/${pipelineId}/records/export-csv`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subset_id: subset.id,
          filters: filters.map(({ column, op, value, include_null }) => ({
            column, op, value, include_null: !!include_null,
          })),
          sort: sortRules.map(({ column, dir }) => ({ column, dir })),
          visible_cols: visibleColsForQuery,
          custom_sql: useCustomSql ? (customSqlRef.current.trim() || null) : null,
          max_rows: 500_000,
        }),
      });
      if (!res.ok) {
        const t = await res.text();
        alert(t || 'Export failed');
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `pipeline_${pipelineId}_subset_${subset.id}_records.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert(err.message || 'Export failed');
    }
  };
  const toggleCol = name => setVisibleCols(v => (v.includes(name) ? v.filter(x => x !== name) : [...v, name]));

  const openDetail = async openalexId => {
    const q = new URLSearchParams({ record_id: openalexId, subset_id: String(subset.id) });
    const res = await apiFetch(`/api/pipeline/${pipelineId}/record-detail?${q}`);
    if (res.ok) setDetail(await res.json());
  };

  const isAbstractCol = (c) => c === 'abstract_text';
  const activeSortCount = sortRules.length;

  /* ═══════════════════════════════════════════════════════════════════════════ */
  return (
    <>
      <button
        type="button"
        className="fixed inset-0 bg-black/40 z-[90] cursor-default border-0 p-0"
        aria-label="Close viewer"
        onClick={onClose}
      />
      <div
        className="fixed top-0 right-0 bottom-0 z-[95] flex flex-col bg-gray-50 shadow-[-8px_0_32px_rgba(0,0,0,0.12)] border-l border-gray-200 max-h-screen"
        style={{ width: panelWidth }}
      >
        <div
          role="separator"
          aria-orientation="vertical"
          tabIndex={0}
          onMouseDown={startResize}
          className="absolute left-0 top-0 bottom-0 w-1.5 cursor-ew-resize z-[96] hover:bg-blue-500/25 active:bg-blue-500/40 select-none touch-none"
          title="Drag to resize"
        />

        {/* ── Header ────────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between pl-4 pr-4 py-2.5 border-b border-gray-200 bg-white shrink-0">
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

        {/* ── Toolbar (Excel-style filter/sort summary) ───────────────── */}
        <div className="flex items-start gap-2 px-4 py-2 bg-white border-b border-gray-200 shrink-0 flex-wrap text-xs">
          <div className="flex flex-wrap items-center gap-1.5 min-w-0 flex-1">
            {filters.map(f => (
              <span
                key={f.id}
                className="inline-flex items-center gap-1 max-w-full bg-blue-50 border border-blue-200 text-blue-900 rounded px-2 py-0.5 font-mono text-[10px]">
                <span className="truncate" title={filterChipLabel(f)}>{filterChipLabel(f)}</span>
                <button
                  type="button"
                  className="text-blue-400 hover:text-blue-800 shrink-0 font-sans"
                  title="Remove filter"
                  onClick={() => setFilters(prev => prev.filter(x => x.id !== f.id))}>
                  ×
                </button>
              </span>
            ))}
            {filters.length > 1 && (
              <button type="button" onClick={() => setFilters([])} className="text-[10px] text-blue-600 hover:underline">
                Clear all filters
              </button>
            )}
            {sortRules.length > 0 && (
              <span className="inline-flex items-center gap-1.5 text-[11px] text-gray-700 bg-gray-50 border border-gray-200 rounded px-2 py-0.5">
                <span className="text-gray-500 font-medium">Sort:</span>
                <span className="font-mono">
                  {sortRules.map(s => `${s.column} ${s.dir === 'desc' ? '↓' : '↑'}`).join(' → ')}
                </span>
                <button type="button" onClick={() => setSortRules([])} className="text-gray-400 hover:text-gray-700" title="Clear sort">×</button>
              </span>
            )}
            {useCustomSql && (
              <span className="bg-amber-50 border border-amber-200 text-amber-900 rounded px-2 py-0.5 text-[10px] font-medium">
                Drilldown / custom SQL — filters &amp; sort apply on top of this query
              </span>
            )}
          </div>

          <span className="text-[10px] text-gray-500 hidden lg:inline max-w-[220px] text-right leading-snug">
            Header: click sort · Shift+click multi-sort · ▼ filter &amp; sort
          </span>

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
                  {colNames.includes('openalex_id') && !visibleCols.includes('openalex_id') && (
                    <p className="text-[10px] text-gray-500 mt-2 leading-snug border-t border-gray-100 pt-2">
                      <code className="text-[10px] bg-gray-100 px-1 rounded">openalex_id</code> is still loaded for expand, detail, and star when hidden.
                    </p>
                  )}
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

            <button
              type="button"
              onClick={downloadCsv}
              disabled={loading || !table?.total}
              className="px-2 py-1 border rounded-lg hover:bg-gray-50 disabled:opacity-40 text-gray-700 text-[11px] font-medium"
              title="Download all matching rows as CSV"
            >
              ⭳ CSV
            </button>
          </div>
        </div>

        {/* ── SQL panel ─────────────────────────────────────────────────── */}
        {sqlOpen && (
          <div className="px-4 py-2.5 bg-white border-b border-gray-100 shrink-0 space-y-2">
            {!useCustomSql && explainLoading && <p className="text-xs text-gray-400">Updating…</p>}
            {!useCustomSql && (
              <>
                <p className="text-[11px] text-gray-500">Query generated from filters, sorts, and visible columns.</p>
                <textarea
                  value={builderSql}
                  readOnly
                  rows={3}
                  className="w-full border rounded-lg px-3 py-2 text-xs font-mono resize-y bg-gray-50 text-gray-600"
                  placeholder="Generated from column filters/sort"
                />
              </>
            )}
            {useCustomSql && (
              <p className="text-xs text-amber-900 bg-amber-50 border border-amber-100 rounded-lg px-2 py-1.5">
                Custom SQL is active — edit below and click Run. Disable it inside the Custom SQL panel to return to the builder.
              </p>
            )}

            <details
              className="rounded-lg border border-amber-200/90 bg-amber-50/40 overflow-hidden"
              open={customSqlPanelOpen}
              onToggle={(e) => setCustomSqlPanelOpen(e.target.open)}
            >
              <summary className="px-3 py-2 text-xs font-medium text-amber-950 cursor-pointer select-none hover:bg-amber-50/80">
                Custom SQL{' '}
                <span className="font-normal text-amber-800/90">
                  (subset-scoped — must reference <code className="bg-amber-100/80 px-1 rounded text-[10px]">_fr_subset</code>)
                </span>
              </summary>
              <div className="px-3 pb-3 pt-2 space-y-2 border-t border-amber-100 bg-white/95">
                <label className="flex items-center gap-2 text-xs text-gray-700 cursor-pointer">
                  <input type="checkbox" checked={useCustomSql} onChange={e => {
                    const on = e.target.checked;
                    setUseCustomSql(on);
                    if (on) {
                      let next = builderSql.replace(/\s*LIMIT\s+<page_size>\s+OFFSET\s+<offset>\s*$/i, '').trim();
                      next = stripOuterFrSubsetCte(next);
                      setCustomSql(next);
                      setCustomSqlPanelOpen(true);
                    } else setCustomSql('');
                  }} />
                  Use custom SQL instead of the generated query
                </label>
                {useCustomSql && (
                  <>
                    <textarea
                      value={customSql}
                      onChange={e => setCustomSql(e.target.value)}
                      rows={6}
                      className="w-full border rounded-lg px-3 py-2 text-xs font-mono resize-y bg-white"
                      placeholder="SELECT * FROM _fr_subset WHERE ..."
                    />
                    <div className="flex flex-wrap gap-2">
                      <button type="button" onClick={() => loadTable(1)}
                        className="text-xs px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium">Run</button>
                      <button type="button" onClick={() => { setUseCustomSql(false); setCustomSql(''); }}
                        className="text-xs px-3 py-1.5 border rounded-lg hover:bg-gray-50">Back to builder</button>
                    </div>
                  </>
                )}
              </div>
            </details>
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
              <thead className="bg-gray-100 sticky top-0 z-10 border-b-2 border-gray-300 shadow-sm">
                <tr>
                  <th className="w-7 px-1 bg-gray-100 border-r border-gray-200 align-top" />
                  <th className="w-8 px-0.5 bg-gray-100 border-r border-gray-200 align-top text-center text-[10px] text-gray-400 font-normal">★</th>
                  {displayCols.map((c) => {
                    const si = sortMap[c];
                    const hasFilter = !!filterMap[c];
                    return (
                      <th
                        key={c}
                        className={`relative align-top px-1.5 py-1.5 text-left select-none group/th bg-gray-100 border-r border-gray-200 last:border-r-0
                        ${isAbstractCol(c) ? 'min-w-[min(28rem,50vw)] max-w-[min(48rem,72vw)]' : 'min-w-[104px] max-w-[200px]'}
                        ${hasFilter ? 'bg-blue-50/60' : ''}`}
                      >
                        <div className="flex flex-col gap-1">
                          <button
                            type="button"
                            className={`flex items-start gap-1 w-full text-left rounded px-0.5 py-0.5 -mx-0.5 hover:bg-gray-200/70 ${hasFilter ? 'text-blue-900' : 'text-gray-900'}`}
                            title="Sort (Shift+click for multi-sort)"
                            onClick={(e) => cycleSort(c, e.shiftKey)}
                          >
                            <span
                              className={`font-semibold text-[11px] leading-snug flex-1 min-w-0 text-left ${isAbstractCol(c) ? 'break-words' : 'truncate'}`}
                            >
                              {c}
                            </span>
                            <span className="shrink-0 pt-0.5">
                              <SortIcon dir={si?.dir} index={activeSortCount > 1 ? si?.idx : 0} />
                            </span>
                          </button>
                          <button
                            type="button"
                            className={`w-full flex items-center justify-center gap-1 rounded border px-1 py-0.5 text-[10px] font-medium
                              ${hasFilter ? 'border-blue-300 bg-blue-50 text-blue-900' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'}`}
                            title="Filter this column"
                            onClick={(e) => {
                              e.stopPropagation();
                              const r = e.currentTarget.getBoundingClientRect();
                              const popoverW = 288;
                              const left = Math.min(Math.max(8, r.left), window.innerWidth - popoverW - 8);
                              setFilterPop((prev) => (prev?.col === c ? null : { col: c, top: r.bottom + 4, left }));
                            }}
                          >
                            <FunnelIcon active={hasFilter} />
                            <span>Filter</span>
                          </button>
                        </div>
                      </th>
                    );
                  })}
                  <th className="w-12 px-1 bg-gray-100 text-gray-500 font-medium border-l border-gray-200 align-top">Detail</th>
                </tr>
              </thead>
              <tbody className="bg-white">
                {table.rows.map((row, i) => {
                  const oidIdx = apiCols.indexOf('openalex_id');
                  const oid = oidIdx >= 0 ? row[oidIdx] : null;
                  const isOpen = expanded[i];
                  const fullRec = oid ? expandedDetails[oid] : null;

                  return (
                    <React.Fragment key={`row-${i}-${oid ?? i}`}>
                      <tr className={`border-b border-gray-100 hover:bg-blue-50/50 align-top ${isOpen ? 'bg-blue-50/25' : ''} ${i % 2 === 1 ? 'bg-gray-50/40' : ''}`}>
                        <td className="px-1 pt-1.5">
                          <button type="button" onClick={() => toggleExpand(i, oid)}
                            className="text-gray-400 hover:text-gray-700 text-[10px] w-4 text-center">
                            {isOpen ? '▼' : '▶'}
                          </button>
                        </td>
                        <td className="px-0.5 pt-1.5 text-center align-top">
                          {oid ? (
                            <button
                              type="button"
                              onClick={() => toggleStar(oid)}
                              className={`text-base leading-none ${isStarred(oid) ? 'text-amber-500' : 'text-gray-300 hover:text-amber-400'}`}
                              title={isStarred(oid) ? 'Unstar' : 'Star record'}
                            >
                              ★
                            </button>
                          ) : '—'}
                        </td>
                        {displayCols.map((c) => {
                          const j = apiCols.indexOf(c);
                          const cell = j >= 0 ? row[j] : null;
                          return (
                            <td
                              key={c}
                              className={`px-2 py-1.5 align-top ${isAbstractCol(c)
                                ? 'min-w-[min(20rem,40vw)] max-w-[min(48rem,72vw)] text-[11px] leading-relaxed text-gray-700'
                                : 'max-w-[200px] truncate'}`}
                              title={!isAbstractCol(c) ? String(cell ?? '') : undefined}
                            >
                              {renderCell(cell, c)}
                            </td>
                          );
                        })}
                        <td className="px-1 pt-1.5">
                          {oid && <button type="button" onClick={() => openDetail(oid)}
                            className="text-blue-600 hover:underline text-[10px]">Open</button>}
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="bg-slate-50 border-t border-gray-100">
                          <td />
                          <td />
                          <td colSpan={displayCols.length + 1} className="px-3 py-3 text-[11px] text-gray-700">
                            {fullRec ? (
                              <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 max-h-[min(72vh,52rem)] overflow-y-auto">
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
        {filterPop && createPortal(
          <ColumnFilterPopover
            pipelineId={pipelineId}
            subsetId={subset?.id}
            col={filterPop.col}
            colType={colTypeMap[filterPop.col]}
            filter={filterMap[filterPop.col]}
            fixedPosition={{ top: filterPop.top, left: filterPop.left }}
            onApply={(op, val, includeNull = false) => applyColumnFilter(filterPop.col, op, val, includeNull)}
            onClear={() => clearFilter(filterPop.col)}
            onClose={() => setFilterPop(null)}
            onSortAsc={() => setSortRules([{ id: uid(), column: filterPop.col, dir: 'asc' }])}
            onSortDesc={() => setSortRules([{ id: uid(), column: filterPop.col, dir: 'desc' }])}
          />,
          document.body,
        )}

        {detail && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[110] p-4">
            <div className="bg-white rounded-xl shadow-2xl w-full max-w-5xl max-h-[92vh] overflow-y-auto p-6">
              <div className="flex justify-between items-start mb-4">
                <h3 className="font-bold text-gray-900 text-sm flex-1 pr-4">{detail.title}</h3>
                <button type="button" onClick={() => setDetail(null)} className="text-gray-400 hover:text-gray-700 text-xl leading-none">×</button>
              </div>
              <div className="space-y-1.5 max-h-[min(78vh,52rem)] overflow-y-auto">
                {Object.entries(detail).map(([k, v]) => v != null && v !== '' && (
                  <div key={k} className={`flex gap-2 text-xs border-b border-gray-50 pb-1 ${k === 'abstract_text' ? 'flex-col' : ''}`}>
                    <span className={`text-gray-500 font-medium ${k === 'abstract_text' ? '' : 'w-44 shrink-0'}`}>{k}</span>
                    {k === 'abstract_text' ? (
                      <p className="text-gray-800 whitespace-pre-wrap leading-relaxed text-[14px]">{String(v)}</p>
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
    </>
  );
}
