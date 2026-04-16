import { useState, useEffect } from 'react';
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

function SummaryTable({ title, data, summaryKey, onDrilldown }) {
  if (!data || data.length === 0) return null;
  const total = data.reduce((s, r) => s + r.count, 0);
  const canDrill = onDrilldown && DRILLDOWN_MAP[summaryKey];

  const handleClick = (label) => {
    if (!canDrill) return;
    const filters = DRILLDOWN_MAP[summaryKey](label);
    onDrilldown(filters);
  };

  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      <div className="px-3 py-2 bg-slate-50 border-b border-slate-200">
        <h3 className="text-xs font-semibold text-slate-700">{title}</h3>
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
                  <button
                    type="button"
                    onClick={() => handleClick(row.label)}
                    className="text-teal-600 hover:text-teal-800 hover:underline font-medium cursor-pointer"
                    title="Click to view these records in the Data tab"
                  >
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
  );
}

export default function SummaryTables({ pipelineId, tab = 'search', onDrilldown }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiFetch(`/api/pipeline/${pipelineId}/summary?tab=${tab}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(d => { if (!cancelled) setData(d); })
      .catch(e => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [pipelineId, tab]);

  if (loading) return <div className="text-xs text-slate-400 py-4">Loading summary...</div>;
  if (error) return <div className="text-xs text-red-500 py-4">Error loading summary: {error}</div>;
  if (!data) return null;

  const keys = Object.keys(data).filter(k => data[k]?.length > 0);
  if (keys.length === 0) return <div className="text-xs text-slate-400 py-4">No summary data available.</div>;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {keys.map(k => (
        <SummaryTable
          key={k}
          title={FRIENDLY_NAMES[k] || k}
          data={data[k]}
          summaryKey={k}
          onDrilldown={onDrilldown}
        />
      ))}
    </div>
  );
}
