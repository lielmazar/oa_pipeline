import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';

const FRIENDLY_NAMES = {
  publications_by_year: 'Publications by Year',
  top_countries: 'Top Countries',
  top_journals: 'Top Journals',
  oa_status: 'Open Access Status',
  work_types: 'Work Types',
  fuel_subgroups: 'Fuel Subgroups',
  impact_subgroups: 'Impact Subgroups',
  research_field: 'Research Field',
  uses_ml: 'ML/AI Usage',
  confidence: 'Confidence Level',
  fuel_relevant: 'Fuel Relevance',
};

const DRILLDOWN_MAP = {
  research_field:        (label) => ({ research_field: label }),
  uses_ml:               (label) => ({ uses_ml: label === 'Uses ML/AI' ? '1' : '0' }),
  confidence:            (label) => ({ confidence: label }),
  fuel_relevant:         (label) => ({ fuel_relevant: label === 'Fuel relevant' ? '1' : '0' }),
  publications_by_year:  (label) => ({ publication_year: label }),
  oa_status:             (label) => ({ open_access_oa_status: label }),
  work_types:            (label) => ({ type: label }),
  top_journals:          (label) => ({ primary_location_source_display_name: label }),
  top_countries:         (label) => ({ authors_countries: label }),
  fuel_subgroups:        (label) => ({ matched_fuel_subgroups: label }),
  impact_subgroups:      (label) => ({ matched_impact_subgroups: label }),
};

const EXPANDABLE_KEYS = new Set(['top_countries', 'top_journals']);

/* ── Full-data popup modal ────────────────────────────────────────── */

function ExpandedTableModal({ pipelineId, summaryKey, title, onClose, onDrilldown }) {
  const [rows, setRows] = useState(null);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    apiFetch(`/api/pipeline/${pipelineId}/summary/${summaryKey}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (!cancelled && d?.rows) setRows(d.rows); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [pipelineId, summaryKey]);

  const canDrill = onDrilldown && DRILLDOWN_MAP[summaryKey];
  const filtered = rows
    ? (filter ? rows.filter(r => String(r.label).toLowerCase().includes(filter.toLowerCase())) : rows)
    : null;
  const total = filtered ? filtered.reduce((s, r) => s + r.count, 0) : 0;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-2xl w-[90vw] max-w-2xl max-h-[85vh] flex flex-col"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200 shrink-0">
          <h3 className="text-sm font-bold text-slate-800">{title}</h3>
          <button onClick={onClose}
            className="text-slate-400 hover:text-slate-700 text-lg font-bold leading-none">×</button>
        </div>
        <div className="px-5 py-2 border-b border-slate-100">
          <input type="text" placeholder="Search…" value={filter}
            onChange={e => setFilter(e.target.value)}
            className="w-full text-xs border border-slate-200 rounded-md px-2.5 py-1.5 focus:ring-1 focus:ring-teal-400 focus:outline-none"
            autoFocus />
        </div>
        <div className="overflow-y-auto flex-1">
          {!filtered ? (
            <p className="text-xs text-slate-400 text-center py-8">Loading…</p>
          ) : (
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-slate-50 z-10">
                <tr className="border-b border-slate-200">
                  <th className="text-left px-4 py-1.5 text-slate-500 font-medium">#</th>
                  <th className="text-left px-4 py-1.5 text-slate-500 font-medium">Label</th>
                  <th className="text-right px-4 py-1.5 text-slate-500 font-medium w-24">Count</th>
                  <th className="text-right px-4 py-1.5 text-slate-500 font-medium w-16">%</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((row, i) => (
                  <tr key={i} className="border-b border-slate-50 hover:bg-slate-50">
                    <td className="px-4 py-1 text-slate-400 tabular-nums">{i + 1}</td>
                    <td className="px-4 py-1 text-slate-700" title={row.label}>{row.label}</td>
                    <td className="px-4 py-1 text-right tabular-nums">
                      {canDrill ? (
                        <button type="button"
                          onClick={() => { onDrilldown(DRILLDOWN_MAP[summaryKey](row.label)); onClose(); }}
                          className="text-teal-600 hover:text-teal-800 hover:underline font-medium cursor-pointer"
                          title="View in Data tab">
                          {row.count.toLocaleString()}
                        </button>
                      ) : (
                        <span className="text-slate-600">{row.count.toLocaleString()}</span>
                      )}
                    </td>
                    <td className="px-4 py-1 text-right text-slate-400 tabular-nums">
                      {total > 0 ? (100 * row.count / total).toFixed(1) : '0.0'}%
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-slate-200 bg-slate-50">
                  <td className="px-4 py-1" />
                  <td className="px-4 py-1 font-medium text-slate-600">
                    Total ({filtered.length.toLocaleString()} items)
                  </td>
                  <td className="px-4 py-1 text-right font-medium text-slate-600 tabular-nums">{total.toLocaleString()}</td>
                  <td className="px-4 py-1 text-right text-slate-400">100%</td>
                </tr>
              </tfoot>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Individual summary table ─────────────────────────────────────── */

function SummaryTable({ title, data, summaryKey, onDrilldown, pipelineId }) {
  const [showModal, setShowModal] = useState(false);
  if (!data || data.length === 0) return null;

  const total = data.reduce((s, r) => s + r.count, 0);
  const canDrill = onDrilldown && DRILLDOWN_MAP[summaryKey];
  const expandable = EXPANDABLE_KEYS.has(summaryKey);

  const handleClick = (label) => {
    if (!canDrill) return;
    onDrilldown(DRILLDOWN_MAP[summaryKey](label));
  };

  return (
    <>
      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        <div className="px-3 py-2 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
          <h3 className="text-xs font-semibold text-slate-700">{title}</h3>
          {expandable && (
            <button onClick={() => setShowModal(true)}
              className="text-[10px] text-teal-600 hover:text-teal-800 font-medium">
              Show all →
            </button>
          )}
        </div>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-100">
              <th className="text-left px-3 py-1.5 text-slate-500 font-medium">Label</th>
              <th className="text-right px-3 py-1.5 text-slate-500 font-medium w-20">Count</th>
              <th className="text-right px-3 py-1.5 text-slate-500 font-medium w-16">%</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => (
              <tr key={i} className="border-b border-slate-50 hover:bg-slate-50">
                <td className="px-3 py-1 text-slate-700 truncate max-w-xs" title={row.label}>{row.label}</td>
                <td className="px-3 py-1 text-right tabular-nums">
                  {canDrill ? (
                    <button type="button"
                      onClick={() => handleClick(row.label)}
                      className="text-teal-600 hover:text-teal-800 hover:underline font-medium cursor-pointer"
                      title="Click to view these records in the Data tab">
                      {row.count.toLocaleString()}
                    </button>
                  ) : (
                    <span className="text-slate-600">{row.count.toLocaleString()}</span>
                  )}
                </td>
                <td className="px-3 py-1 text-right text-slate-400 tabular-nums">
                  {total > 0 ? (100 * row.count / total).toFixed(1) : '0.0'}%
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-slate-200 bg-slate-50">
              <td className="px-3 py-1 font-medium text-slate-600">Total</td>
              <td className="px-3 py-1 text-right font-medium text-slate-600 tabular-nums">{total.toLocaleString()}</td>
              <td className="px-3 py-1 text-right text-slate-400">100%</td>
            </tr>
          </tfoot>
        </table>
      </div>

      {showModal && (
        <ExpandedTableModal
          pipelineId={pipelineId}
          summaryKey={summaryKey}
          title={title}
          onClose={() => setShowModal(false)}
          onDrilldown={onDrilldown}
        />
      )}
    </>
  );
}

/* ── Main SummaryTables component ─────────────────────────────────── */

const OA_OPTIONS = [
  { value: '', label: 'All records' },
  { value: 'oa', label: 'Open Access only' },
  { value: 'closed', label: 'Closed Access only' },
];

export default function SummaryTables({ pipelineId, tab = 'search', onDrilldown }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [oaFilter, setOaFilter] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ tab });
    if (oaFilter) params.set('oa_filter', oaFilter);
    apiFetch(`/api/pipeline/${pipelineId}/summary?${params}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(d => { if (!cancelled) setData(d); })
      .catch(e => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [pipelineId, tab, oaFilter]);

  if (loading && !data) return <div className="text-xs text-slate-400 py-4">Loading summary...</div>;
  if (error) return <div className="text-xs text-red-500 py-4">Error loading summary: {error}</div>;
  if (!data) return null;

  const keys = Object.keys(data).filter(k => data[k]?.length > 0);
  if (keys.length === 0 && !loading) return <div className="text-xs text-slate-400 py-4">No summary data available.</div>;

  return (
    <div className="space-y-3">
      {/* OA filter bar */}
      <div className="flex items-center gap-1">
        {OA_OPTIONS.map(opt => (
          <button key={opt.value} type="button"
            onClick={() => setOaFilter(opt.value)}
            className={`text-[11px] px-3 py-1 rounded-full border transition font-medium
              ${oaFilter === opt.value
                ? 'bg-teal-600 text-white border-teal-600'
                : 'bg-white text-slate-500 border-slate-200 hover:border-teal-300 hover:text-teal-700'}`}>
            {opt.label}
          </button>
        ))}
        {loading && <span className="text-[10px] text-slate-400 ml-2">Updating…</span>}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {keys.map(k => (
          <SummaryTable
            key={k}
            title={FRIENDLY_NAMES[k] || k}
            data={data[k]}
            summaryKey={k}
            onDrilldown={onDrilldown}
            pipelineId={pipelineId}
          />
        ))}
      </div>
    </div>
  );
}
