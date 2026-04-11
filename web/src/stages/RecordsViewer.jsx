import { useState, useEffect, useCallback, useMemo, useRef, Fragment } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getExpandedRowModel,
  flexRender,
} from '@tanstack/react-table';
import { apiFetch } from '../api/client';

const DEFAULT_VISIBLE = new Set([
  'title', 'publication_year', 'type',
  'primary_location_source_display_name', 'cited_by_count',
  'matched_fuel_subgroups', 'matched_impact_subgroups',
  'llm_is_hydrocarbon', 'llm_research_objective',
  'llm_ml_role', 'llm_ml_algorithm', 'llm_confidence',
]);

const JSON_ARRAY_COLS = new Set([
  'authors_countries', 'authors_institutions',
  'matched_fuel_terms', 'matched_impact_terms',
  'matched_fuel_stems', 'matched_impact_stems',
  'matched_fuel_subgroups', 'matched_impact_subgroups',
  'llm_ml_role', 'llm_ml_algorithm', 'llm_research_objective',
]);

const BOOL_COLS = new Set(['llm_is_hydrocarbon', 'is_oa', 'is_retracted', 'has_fulltext',
  'open_access_is_oa', 'primary_location_is_oa', 'open_access_any_repository_has_fulltext']);

const HIDDEN_COLS = new Set([
  'llm_result_raw', 'llm_batch_id', 'llm_tokens_input',
  'llm_tokens_output', 'llm_tokens_reasoning', 'abstract_text',
]);

const FRIENDLY = {
  primary_location_source_display_name: 'Journal',
  open_access_oa_status: 'OA Status',
  cited_by_count: 'Citations',
  publication_year: 'Year',
  matched_fuel_subgroups: 'Fuel Subgroups',
  matched_impact_subgroups: 'Impact Subgroups',
  llm_is_hydrocarbon: 'Hydrocarbon?',
  llm_ml_role: 'ML Role',
  llm_ml_algorithm: 'ML Algorithm',
  llm_research_objective: 'Research Obj.',
  llm_ml_data_source: 'Data Source',
  llm_confidence: 'Confidence',
  llm_justification: 'Justification',
};

function friendlyName(col) {
  return FRIENDLY[col] || col.replace(/^(llm_|matched_)/, '').replace(/_/g, ' ');
}

function parseJsonArray(val) {
  if (!val) return [];
  if (Array.isArray(val)) return val;
  try {
    const parsed = JSON.parse(val);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch {
    return [val];
  }
}

function Badge({ children, color = 'slate' }) {
  const colors = {
    slate: 'bg-slate-100 text-slate-600',
    teal: 'bg-teal-100 text-teal-700',
    green: 'bg-green-100 text-green-700',
    red: 'bg-red-100 text-red-600',
    amber: 'bg-amber-100 text-amber-700',
  };
  return (
    <span className={`inline-block text-[10px] px-1.5 py-0.5 rounded-full font-medium mr-0.5 mb-0.5 whitespace-nowrap ${colors[color] || colors.slate}`}>
      {children}
    </span>
  );
}

function CellRenderer({ value, colId }) {
  if (value == null || value === '') return <span className="text-slate-300">—</span>;

  if (JSON_ARRAY_COLS.has(colId)) {
    const items = parseJsonArray(value);
    if (!items.length) return <span className="text-slate-300">—</span>;
    return (
      <span className="flex flex-wrap gap-0.5">
        {items.slice(0, 5).map((it, i) => <Badge key={i}>{String(it)}</Badge>)}
        {items.length > 5 && <Badge color="amber">+{items.length - 5}</Badge>}
      </span>
    );
  }

  if (BOOL_COLS.has(colId)) {
    const bool = value === 1 || value === '1' || value === true;
    return <Badge color={bool ? 'green' : 'red'}>{bool ? 'Yes' : 'No'}</Badge>;
  }

  if (colId === 'doi' && value) {
    const url = value.startsWith('http') ? value : `https://doi.org/${value}`;
    return (
      <a href={url} target="_blank" rel="noopener noreferrer"
        className="text-teal-600 hover:text-teal-800 underline text-[10px]" title={value}>
        DOI
      </a>
    );
  }

  return <span className="break-words">{String(value)}</span>;
}

/* ── Excel-style column filter dropdown ────────────────────────────── */

function ColumnFilterDropdown({ pipelineId, colId, subsetId, currentValue, onApply, isBool }) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState(null);
  const [selected, setSelected] = useState(new Set());
  const [filterText, setFilterText] = useState('');
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const params = new URLSearchParams({ column: colId });
    if (subsetId != null) params.set('subset_id', String(subsetId));
    apiFetch(`/api/pipeline/${pipelineId}/records/distinct?${params}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (cancelled || !d?.values) return;
        setValues(d.values);
        if (currentValue) {
          setSelected(new Set(currentValue.split('||')));
        } else {
          setSelected(new Set());
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [open, pipelineId, colId, subsetId, currentValue]);

  useEffect(() => {
    function handleClickOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    if (open) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  const toggle = (v) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(v)) next.delete(v); else next.add(v);
      return next;
    });
  };

  const apply = () => {
    if (selected.size === 0) {
      onApply(colId, null);
    } else if (selected.size === 1) {
      onApply(colId, [...selected][0]);
    } else {
      onApply(colId, [...selected].join('||'));
    }
    setOpen(false);
  };

  const clear = () => {
    setSelected(new Set());
    onApply(colId, null);
    setOpen(false);
  };

  const hasFilter = !!currentValue;

  const filteredValues = values
    ? (filterText ? values.filter(v => String(v).toLowerCase().includes(filterText.toLowerCase())) : values)
    : null;

  return (
    <div className="relative inline-block" ref={ref}>
      <button
        onClick={() => setOpen(p => !p)}
        className={`text-[10px] px-1.5 py-0.5 rounded border transition w-full text-left truncate
          ${hasFilter
            ? 'bg-teal-50 border-teal-300 text-teal-700 font-medium'
            : 'bg-white border-slate-200 text-slate-400 hover:border-teal-300'
          }`}
        title={hasFilter ? `Filtered: ${currentValue}` : 'Filter…'}
      >
        {hasFilter ? currentValue.replace(/\|\|/g, ', ') : '▼ Filter'}
      </button>
      {open && (
        <div className="absolute z-50 mt-1 left-0 bg-white border border-slate-200 rounded-lg shadow-xl w-52 max-h-64 flex flex-col">
          <div className="p-1.5 border-b border-slate-100">
            <input
              type="text"
              placeholder="Search…"
              value={filterText}
              onChange={e => setFilterText(e.target.value)}
              className="w-full text-[10px] border border-slate-200 rounded px-1.5 py-0.5 focus:ring-1 focus:ring-teal-400 focus:outline-none"
              autoFocus
            />
          </div>
          <div className="overflow-y-auto flex-1 px-1 py-0.5">
            {filteredValues === null ? (
              <p className="text-[10px] text-slate-400 p-2 text-center">Loading…</p>
            ) : filteredValues.length === 0 ? (
              <p className="text-[10px] text-slate-400 p-2 text-center">No values</p>
            ) : (
              filteredValues.map((v, i) => {
                let display = v === '' ? '(empty)' : String(v);
                if (isBool) {
                  display = (v === 1 || v === '1') ? 'Yes' : (v === 0 || v === '0') ? 'No' : display;
                }
                return (
                  <label key={i} className="flex items-center gap-1.5 px-1 py-[3px] text-[10px] text-slate-600 hover:bg-slate-50 rounded cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selected.has(String(v))}
                      onChange={() => toggle(String(v))}
                      className="rounded border-slate-300 text-teal-600 focus:ring-teal-500 h-3 w-3"
                    />
                    <span className="truncate" title={display}>{display}</span>
                  </label>
                );
              })
            )}
          </div>
          <div className="flex items-center gap-1 p-1.5 border-t border-slate-100">
            <button onClick={apply}
              className="flex-1 text-[10px] font-medium text-white bg-teal-600 hover:bg-teal-700 rounded px-2 py-1 transition">
              Apply{selected.size > 0 && ` (${selected.size})`}
            </button>
            <button onClick={clear}
              className="text-[10px] text-slate-400 hover:text-red-500 px-2 py-1">
              Clear
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Record detail modal ──────────────────────────────────────────── */

function RecordDetailModal({ record, allColumns, onClose }) {
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-2xl w-[90vw] max-w-3xl max-h-[85vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200 shrink-0">
          <h3 className="text-sm font-bold text-slate-800 truncate pr-4">
            {record.title || record.openalex_id || 'Record Detail'}
          </h3>
          <button onClick={onClose}
            className="text-slate-400 hover:text-slate-700 text-lg font-bold leading-none">×</button>
        </div>
        <div className="overflow-y-auto flex-1 px-5 py-4 space-y-3 text-xs text-slate-700">
          {allColumns.filter(c => c !== 'abstract_text' && c !== 'llm_result_raw').map(col => {
            const val = record[col];
            if (val == null || val === '') return null;
            return (
              <div key={col} className="grid grid-cols-[140px_1fr] gap-2 items-start">
                <span className="font-semibold text-slate-500 text-[10px] uppercase truncate" title={col}>
                  {friendlyName(col)}
                </span>
                <div className="min-w-0 break-words">
                  {JSON_ARRAY_COLS.has(col) ? (
                    <span className="flex flex-wrap gap-0.5">
                      {parseJsonArray(val).map((it, i) => <Badge key={i} color="teal">{String(it)}</Badge>)}
                    </span>
                  ) : BOOL_COLS.has(col) ? (
                    <Badge color={(val === 1 || val === '1') ? 'green' : 'red'}>{(val === 1 || val === '1') ? 'Yes' : 'No'}</Badge>
                  ) : col === 'doi' ? (
                    <a href={String(val).startsWith('http') ? val : `https://doi.org/${val}`}
                      target="_blank" rel="noopener noreferrer" className="text-teal-600 hover:underline">
                      {val}
                    </a>
                  ) : (
                    <span>{String(val)}</span>
                  )}
                </div>
              </div>
            );
          })}

          {record.abstract_text && (
            <div>
              <h5 className="font-semibold text-slate-500 text-[10px] uppercase mb-1">Abstract</h5>
              <p className="leading-relaxed whitespace-pre-wrap">{record.abstract_text}</p>
            </div>
          )}

          {record.llm_result_raw && (
            <div>
              <h5 className="font-semibold text-slate-500 text-[10px] uppercase mb-1">Raw LLM Output</h5>
              <pre className="bg-slate-100 border border-slate-200 rounded p-2 text-[10px] overflow-auto max-h-48 whitespace-pre-wrap">
                {record.llm_result_raw}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Main RecordsViewer ───────────────────────────────────────────── */

export default function RecordsViewer({ pipelineId, subsets = [], initialFilters }) {
  const [data, setData] = useState({ columns: [], rows: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sort, setSort] = useState('publication_year');
  const [sortDir, setSortDir] = useState('desc');
  const [search, setSearch] = useState('');
  const [subsetId, setSubsetId] = useState(null);
  const [columnFilters, setColumnFilters] = useState(initialFilters || {});
  const [visibleCols, setVisibleCols] = useState(DEFAULT_VISIBLE);
  const [showColPicker, setShowColPicker] = useState(false);
  const [detailRecord, setDetailRecord] = useState(null);
  const [starredIds, setStarredIds] = useState(new Set());
  const [showStarredOnly, setShowStarredOnly] = useState(false);
  const [colWidths, setColWidths] = useState({});
  const colPickerRef = useRef(null);
  const searchTimerRef = useRef(null);

  useEffect(() => {
    if (initialFilters && Object.keys(initialFilters).length) {
      setColumnFilters(initialFilters);
      setPage(1);
    }
  }, [initialFilters]);

  // Load starred records
  useEffect(() => {
    apiFetch(`/api/pipeline/${pipelineId}/starred`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.ids) setStarredIds(new Set(d.ids)); })
      .catch(() => {});
  }, [pipelineId]);

  const toggleStar = useCallback(async (openalexId) => {
    const isStarred = starredIds.has(openalexId);
    if (isStarred) {
      await apiFetch(`/api/pipeline/${pipelineId}/starred/${encodeURIComponent(openalexId)}`, { method: 'DELETE' });
      setStarredIds(prev => { const n = new Set(prev); n.delete(openalexId); return n; });
    } else {
      await apiFetch(`/api/pipeline/${pipelineId}/starred`, {
        method: 'POST',
        body: JSON.stringify({ openalex_id: openalexId }),
      });
      setStarredIds(prev => new Set(prev).add(openalexId));
    }
  }, [pipelineId, starredIds]);

  const fetchRecords = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
        sort,
        dir: sortDir,
      });
      if (search) params.set('q', search);
      if (subsetId != null) params.set('subset_id', String(subsetId));
      const mergedFilters = { ...columnFilters };
      if (showStarredOnly && starredIds.size > 0) {
        mergedFilters['openalex_id'] = [...starredIds].join('||');
      }
      if (Object.keys(mergedFilters).length) {
        params.set('filters', JSON.stringify(mergedFilters));
      }
      const res = await apiFetch(`/api/pipeline/${pipelineId}/records?${params}`);
      if (res.ok) {
        const d = await res.json();
        setData({ columns: d.columns || [], rows: d.rows || [], total: d.total || 0 });
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, [pipelineId, page, pageSize, sort, sortDir, search, subsetId, columnFilters, showStarredOnly, starredIds]);

  useEffect(() => { fetchRecords(); }, [fetchRecords]);

  useEffect(() => {
    function handleClickOutside(e) {
      if (colPickerRef.current && !colPickerRef.current.contains(e.target)) {
        setShowColPicker(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const allColumns = data.columns.filter(c => !HIDDEN_COLS.has(c));

  const rowData = useMemo(() =>
    data.rows.map((row, idx) => {
      const obj = { _idx: idx };
      data.columns.forEach((col, ci) => { obj[col] = row[ci]; });
      return obj;
    }),
  [data.rows, data.columns]);

  const columns = useMemo(() => {
    const cols = [
      {
        id: '_star',
        header: () => <span title="Starred">★</span>,
        size: 28,
        minSize: 28,
        maxSize: 28,
        enableResizing: false,
        cell: ({ row }) => {
          const oid = row.original.openalex_id;
          const starred = starredIds.has(oid);
          return (
            <button onClick={() => toggleStar(oid)}
              className={`text-sm leading-none px-0.5 transition ${starred ? 'text-amber-400' : 'text-slate-300 hover:text-amber-300'}`}
              title={starred ? 'Unstar' : 'Star'}>
              {starred ? '★' : '☆'}
            </button>
          );
        },
      },
      {
        id: '_actions',
        header: () => null,
        size: 28,
        minSize: 28,
        maxSize: 28,
        enableResizing: false,
        cell: ({ row }) => (
          <button onClick={() => setDetailRecord(row.original)}
            className="text-slate-400 hover:text-teal-600 transition text-[10px] leading-none px-0.5"
            title="View full record">
            ⓘ
          </button>
        ),
      },
      {
        id: '_expander',
        header: () => null,
        size: 28,
        minSize: 28,
        maxSize: 28,
        enableResizing: false,
        cell: ({ row }) => (
          <button onClick={row.getToggleExpandedHandler()}
            className="text-slate-400 hover:text-teal-600 transition text-sm leading-none px-0.5">
            {row.getIsExpanded() ? '▾' : '▸'}
          </button>
        ),
      },
    ];

    allColumns.filter(c => visibleCols.has(c)).forEach(colId => {
      const defaultW = colId === 'title' ? 340 : 130;
      cols.push({
        accessorKey: colId,
        header: friendlyName(colId),
        size: colWidths[colId] || defaultW,
        minSize: 60,
        maxSize: 800,
        enableResizing: true,
        cell: info => <CellRenderer value={info.getValue()} colId={colId} />,
      });
    });

    return cols;
  }, [allColumns, visibleCols, starredIds, toggleStar, colWidths]);

  const table = useReactTable({
    data: rowData,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getRowCanExpand: () => true,
    manualSorting: true,
    manualPagination: true,
    pageCount: Math.ceil(data.total / pageSize),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const toggleSort = (colId) => {
    if (sort === colId) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSort(colId);
      setSortDir('desc');
    }
    setPage(1);
  };

  const handleFilterApply = (colId, value) => {
    setColumnFilters(prev => {
      const next = { ...prev };
      if (value) next[colId] = value;
      else delete next[colId];
      return next;
    });
    setPage(1);
  };

  const removeFilter = (col) => {
    setColumnFilters(prev => {
      const next = { ...prev };
      delete next[col];
      return next;
    });
    setPage(1);
  };

  const totalPages = Math.ceil(data.total / pageSize);
  const startRow = data.total === 0 ? 0 : (page - 1) * pageSize + 1;
  const endRow = Math.min(page * pageSize, data.total);

  const columnSizing = table.getState().columnSizing;
  useEffect(() => {
    if (Object.keys(columnSizing).length) {
      setColWidths(prev => ({ ...prev, ...columnSizing }));
    }
  }, [columnSizing]);

  return (
    <div className="space-y-2">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        {subsets.length > 1 && (
          <select value={subsetId ?? ''}
            onChange={e => { setSubsetId(e.target.value ? Number(e.target.value) : null); setPage(1); }}
            className="text-xs border border-slate-200 rounded-md px-2 py-1.5 bg-white text-slate-700 focus:ring-1 focus:ring-teal-400 focus:outline-none">
            <option value="">All subsets</option>
            {subsets.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        )}

        <input type="text" placeholder="Search title / abstract…"
          defaultValue={search}
          onChange={e => {
            if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
            searchTimerRef.current = setTimeout(() => {
              setSearch(e.target.value);
              setPage(1);
            }, 400);
          }}
          className="text-xs border border-slate-200 rounded-md px-2.5 py-1.5 w-52 bg-white text-slate-700 placeholder:text-slate-400 focus:ring-1 focus:ring-teal-400 focus:outline-none" />

        <div className="relative" ref={colPickerRef}>
          <button onClick={() => setShowColPicker(p => !p)}
            className="text-xs border border-slate-200 rounded-md px-2.5 py-1.5 bg-white text-slate-600 hover:border-teal-400 transition">
            Columns ▾
          </button>
          {showColPicker && (
            <div className="absolute z-50 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg p-2 max-h-72 overflow-y-auto w-56">
              {allColumns.map(col => (
                <label key={col} className="flex items-center gap-1.5 px-1 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50 rounded cursor-pointer">
                  <input type="checkbox" checked={visibleCols.has(col)}
                    onChange={() => {
                      setVisibleCols(prev => {
                        const next = new Set(prev);
                        if (next.has(col)) next.delete(col); else next.add(col);
                        return next;
                      });
                    }}
                    className="rounded border-slate-300 text-teal-600 focus:ring-teal-500 h-3 w-3" />
                  {friendlyName(col)}
                </label>
              ))}
            </div>
          )}
        </div>

        <select value={pageSize}
          onChange={e => { setPageSize(Number(e.target.value)); setPage(1); }}
          className="text-xs border border-slate-200 rounded-md px-2 py-1.5 bg-white text-slate-700 focus:ring-1 focus:ring-teal-400 focus:outline-none">
          {[25, 50, 100].map(n => <option key={n} value={n}>{n} / page</option>)}
        </select>

        {starredIds.size > 0 && (
          <button
            onClick={() => { setShowStarredOnly(p => !p); setPage(1); }}
            className={`text-[11px] font-medium rounded-full px-2.5 py-0.5 border transition
              ${showStarredOnly
                ? 'bg-amber-100 text-amber-700 border-amber-300'
                : 'bg-white text-amber-600 border-amber-200 hover:border-amber-400'}`}
          >
            ★ {starredIds.size} starred{showStarredOnly ? ' (filtered)' : ''}
          </button>
        )}

        <span className="text-[11px] text-slate-400 ml-auto tabular-nums">
          {loading ? 'Loading…' : `${startRow.toLocaleString()}–${endRow.toLocaleString()} of ${data.total.toLocaleString()}`}
        </span>
      </div>

      {/* Active filter pills */}
      {Object.keys(columnFilters).length > 0 && (
        <div className="flex flex-wrap gap-1">
          {Object.entries(columnFilters).map(([col, val]) => {
            let label = col;
            const baseCol = col.replace(/__lte$|__gte$/, '');
            if (col.endsWith('__lte')) label = friendlyName(baseCol) + ' ≤';
            else if (col.endsWith('__gte')) label = friendlyName(baseCol) + ' ≥';
            else label = friendlyName(col) + ' =';
            let valDisplay = val;
            if (BOOL_COLS.has(baseCol)) {
              valDisplay = val.split('||').map(v => v === '1' ? 'Yes' : v === '0' ? 'No' : v).join(', ');
            } else {
              valDisplay = val.includes('||') ? val.replace(/\|\|/g, ', ') : val;
            }
            return (
              <span key={col}
                className="inline-flex items-center gap-1 text-[10px] bg-teal-50 text-teal-700 border border-teal-200 rounded-full px-2 py-0.5 font-medium">
                {label} {valDisplay}
                <button onClick={() => removeFilter(col)}
                  className="hover:text-red-600 font-bold leading-none">×</button>
              </span>
            );
          })}
          <button onClick={() => { setColumnFilters({}); setPage(1); }}
            className="text-[10px] text-slate-400 hover:text-red-500 font-medium">
            Clear all
          </button>
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto border border-slate-200 rounded-lg">
        <table className="text-xs border-collapse" style={{ width: table.getTotalSize() }}>
          <thead>
            <tr className="bg-slate-50">
              {table.getHeaderGroups()[0].headers.map(header => {
                const colId = header.column.columnDef.accessorKey;
                const isSorted = sort === colId;
                return (
                  <th key={header.id}
                    style={{ width: header.getSize(), position: 'relative' }}
                    className="px-2 py-1.5 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wide border-b border-slate-200 sticky top-0 bg-slate-50 z-10 whitespace-nowrap">
                    {colId ? (
                      <button onClick={() => toggleSort(colId)}
                        className="hover:text-teal-700 transition inline-flex items-center gap-0.5">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {isSorted && <span className="text-teal-600">{sortDir === 'asc' ? '↑' : '↓'}</span>}
                      </button>
                    ) : (
                      flexRender(header.column.columnDef.header, header.getContext())
                    )}
                    {header.column.getCanResize() && (
                      <div
                        onMouseDown={header.getResizeHandler()}
                        onTouchStart={header.getResizeHandler()}
                        className="absolute right-0 top-0 h-full w-1 cursor-col-resize select-none touch-none hover:bg-teal-400"
                        style={{ transform: header.column.getIsResizing() ? 'translateX(0)' : '' }}
                      />
                    )}
                  </th>
                );
              })}
            </tr>
            {/* Per-column filter row */}
            <tr className="bg-slate-50/60">
              {table.getHeaderGroups()[0].headers.map(header => {
                const colId = header.column.columnDef.accessorKey;
                return (
                  <th key={header.id + '-f'} className="px-1 py-0.5 border-b border-slate-200" style={{ width: header.getSize() }}>
                    {colId ? (
                      <ColumnFilterDropdown
                        pipelineId={pipelineId}
                        colId={colId}
                        subsetId={subsetId}
                        currentValue={columnFilters[colId] || null}
                        onApply={handleFilterApply}
                        isBool={BOOL_COLS.has(colId)}
                      />
                    ) : null}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {table.getRowModel().rows.map(row => (
              <Fragment key={row.id}>
                <tr className="border-b border-slate-100 even:bg-slate-50/40 hover:bg-teal-50/30 transition">
                  {row.getVisibleCells().map(cell => (
                    <td key={cell.id}
                      style={{ width: cell.column.getSize() }}
                      className={`px-2 py-1.5 align-top ${cell.column.columnDef.accessorKey === 'title' ? 'whitespace-normal break-words' : ''}`}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
                {row.getIsExpanded() && (
                  <tr>
                    <td colSpan={row.getVisibleCells().length}>
                      <ExpandedRow row={row.original} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {!loading && rowData.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="text-center text-slate-400 py-8">
                  No records found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between">
        <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1}
          className="text-xs px-3 py-1.5 rounded-md border border-slate-200 bg-white text-slate-600 hover:border-teal-400 transition disabled:opacity-40 disabled:cursor-not-allowed">
          ← Previous
        </button>
        <span className="text-[11px] text-slate-500 tabular-nums">
          Page {page} of {totalPages || 1}
        </span>
        <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages}
          className="text-xs px-3 py-1.5 rounded-md border border-slate-200 bg-white text-slate-600 hover:border-teal-400 transition disabled:opacity-40 disabled:cursor-not-allowed">
          Next →
        </button>
      </div>

      {/* Detail modal */}
      {detailRecord && (
        <RecordDetailModal
          record={detailRecord}
          allColumns={data.columns}
          onClose={() => setDetailRecord(null)}
        />
      )}
    </div>
  );
}

/* ── Inline expanded row (quick view) ─────────────────────────────── */

function ExpandedRow({ row }) {
  const get = (col) => row[col];
  const abstract = get('abstract_text');
  const doi = get('doi');
  const countries = parseJsonArray(get('authors_countries'));
  const justification = get('llm_justification');
  const raw = get('llm_result_raw');

  const llmFields = [
    ['Research Objective', get('llm_research_objective')],
    ['ML Role', get('llm_ml_role')],
    ['ML Algorithm', get('llm_ml_algorithm')],
    ['Data Source', get('llm_ml_data_source')],
    ['Confidence', get('llm_confidence')],
  ];

  const [showRaw, setShowRaw] = useState(false);

  return (
    <div className="px-4 py-3 bg-slate-50 border-t border-slate-100 space-y-3 text-xs text-slate-700">
      {abstract && (
        <div>
          <h5 className="font-semibold text-slate-500 text-[10px] uppercase mb-1">Abstract</h5>
          <p className="leading-relaxed max-h-40 overflow-y-auto">{abstract}</p>
        </div>
      )}

      <div className="flex flex-wrap gap-4">
        {doi && (
          <div>
            <span className="font-semibold text-slate-500 text-[10px] uppercase">DOI: </span>
            <a href={doi.startsWith('http') ? doi : `https://doi.org/${doi}`}
              target="_blank" rel="noopener noreferrer"
              className="text-teal-600 hover:underline">{doi}</a>
          </div>
        )}
        {countries.length > 0 && (
          <div>
            <span className="font-semibold text-slate-500 text-[10px] uppercase">Countries: </span>
            {countries.map((c, i) => <Badge key={i}>{c}</Badge>)}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        {llmFields.map(([label, val]) => (
          <div key={label}>
            <span className="font-semibold text-slate-500 text-[10px] uppercase">{label}</span>
            <div className="mt-0.5">
              {(val && typeof val === 'string' && val.startsWith('['))
                ? parseJsonArray(val).map((v, i) => <Badge key={i} color="teal">{v}</Badge>)
                : <span>{val ?? '—'}</span>
              }
            </div>
          </div>
        ))}
      </div>

      {justification && (
        <div>
          <h5 className="font-semibold text-slate-500 text-[10px] uppercase mb-1">Justification</h5>
          <p className="italic">{justification}</p>
        </div>
      )}

      {raw && (
        <div>
          <button onClick={() => setShowRaw(!showRaw)}
            className="text-[10px] text-teal-600 hover:text-teal-800 font-medium">
            {showRaw ? 'Hide' : 'Show'} raw LLM output
          </button>
          {showRaw && (
            <pre className="mt-1 bg-slate-100 border border-slate-200 rounded p-2 text-[10px] overflow-auto max-h-48 whitespace-pre-wrap">
              {raw}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
