import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  PieChart, Pie, Cell, ResponsiveContainer,
} from 'recharts';

const EXPLORER_CHART_PREFS_KEY = 'oa-pipeline-explorer-chart-prefs';

export function loadExplorerChartPrefs(pipelineId) {
  try {
    const raw = sessionStorage.getItem(EXPLORER_CHART_PREFS_KEY);
    const o = raw ? JSON.parse(raw) : {};
    const p = o[String(pipelineId)] || {};
    return {
      topN: Math.min(500, Math.max(1, Number(p.topN) || 20)),
      cutoffYear: Number.isFinite(Number(p.cutoffYear)) ? Number(p.cutoffYear) : 2000,
    };
  } catch {
    return { topN: 20, cutoffYear: 2000 };
  }
}

export function mergeExplorerChartPrefs(pipelineId, patch) {
  const cur = loadExplorerChartPrefs(pipelineId);
  const n = {
    topN: patch.topN != null
      ? Math.min(500, Math.max(1, Number(patch.topN) || 20))
      : cur.topN,
    cutoffYear: patch.cutoffYear != null && Number.isFinite(Number(patch.cutoffYear))
      ? Number(patch.cutoffYear)
      : cur.cutoffYear,
  };
  try {
    const raw = sessionStorage.getItem(EXPLORER_CHART_PREFS_KEY);
    const o = raw ? JSON.parse(raw) : {};
    o[String(pipelineId)] = n;
    sessionStorage.setItem(EXPLORER_CHART_PREFS_KEY, JSON.stringify(o));
  } catch { /* ignore */ }
  return n;
}

export const SUBSET_PALETTE = [
  '#0d9488', '#7c3aed', '#ea580c', '#2563eb', '#db2777',
  '#65a30d', '#0891b2',
];

const PIE_LABEL_COLORS = [
  '#0d9488', '#7c3aed', '#ea580c', '#2563eb', '#db2777',
  '#65a30d', '#0891b2', '#f59e0b', '#6366f1', '#ec4899',
  '#14b8a6', '#a855f7', '#f97316', '#06b6d4', '#84cc16',
];

function NoData() {
  return <p className="text-xs text-slate-400 text-center py-4">No data</p>;
}

function BarTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-white border border-slate-200 rounded-md shadow-md px-2 py-1.5 text-[11px] max-w-xs">
      <p className="font-semibold text-slate-800 mb-0.5 break-words">{label}</p>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-sm shrink-0" style={{ background: p.fill }} />
          <span className="text-slate-500 truncate flex-1">{p.name}</span>
          <span className="font-semibold text-slate-800 tabular-nums">
            {(p.value ?? 0).toLocaleString()}
          </span>
        </div>
      ))}
    </div>
  );
}

function mergeBarData(subsetResults) {
  const labelOrder = [];
  const labelSet = new Set();
  subsetResults.forEach(({ rows }) => {
    (rows || []).forEach(r => {
      const label = String(r[0] ?? '');
      if (!labelSet.has(label)) { labelSet.add(label); labelOrder.push(label); }
    });
  });
  const map = {};
  labelOrder.forEach(l => { map[l] = { label: l }; });
  subsetResults.forEach(({ subset, rows }) => {
    (rows || []).forEach(r => {
      const label = String(r[0] ?? '');
      map[label][subset.name] = Number(r[1]) || 0;
    });
  });
  return labelOrder.map(l => map[l]);
}

export function GroupedBarChart({ subsetResults }) {
  const data = mergeBarData(subsetResults);
  if (!data.length) return <NoData />;

  const subsetNames = subsetResults.map(s => s.subset.name);
  const barGroupW = subsetNames.length * 14 + 8;
  const minWidth = Math.max(400, data.length * barGroupW + 80);

  return (
    <div className="overflow-x-auto -mx-1 px-1">
      <div style={{ minWidth }}>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={data} margin={{ top: 4, right: 8, bottom: 56, left: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 9, fill: '#64748b' }}
              angle={-40}
              textAnchor="end"
              height={56}
              interval={0}
            />
            <YAxis
              tick={{ fontSize: 9, fill: '#64748b' }}
              width={36}
              tickFormatter={v => (v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : v)}
            />
            <Tooltip content={<BarTooltip />} cursor={{ fill: 'rgba(0,0,0,0.04)' }} />
            <Legend wrapperStyle={{ fontSize: 10, paddingTop: 2 }} iconType="square" iconSize={8} />
            {subsetNames.map((name, i) => (
              <Bar
                key={name}
                dataKey={name}
                fill={SUBSET_PALETTE[i % SUBSET_PALETTE.length]}
                radius={[2, 2, 0, 0]}
                maxBarSize={24}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function MultiPieChart({ subsetResults }) {
  const allLabels = [];
  const labelSet = new Set();
  subsetResults.forEach(({ rows }) => {
    (rows || []).forEach(r => {
      const l = String(r[0] ?? '');
      if (!labelSet.has(l)) { labelSet.add(l); allLabels.push(l); }
    });
  });
  const colorMap = {};
  allLabels.forEach((l, i) => { colorMap[l] = PIE_LABEL_COLORS[i % PIE_LABEL_COLORS.length]; });

  if (!subsetResults.some(s => s.rows?.length > 0)) return <NoData />;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 justify-center">
        {allLabels.map(l => (
          <div key={l} className="flex items-center gap-1 text-[10px] text-slate-600">
            <span className="w-2 h-2 rounded-sm shrink-0" style={{ background: colorMap[l] }} />
            <span>{l}</span>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
        {subsetResults.map(({ subset, rows }) => {
          const pieData = (rows || [])
            .map(r => ({ name: String(r[0] ?? ''), value: Number(r[1]) || 0 }))
            .filter(d => d.value > 0);
          return (
            <div key={subset.id} className="text-center min-w-0">
              <p className="text-[10px] font-medium text-slate-600 truncate" title={subset.name}>
                {subset.name}
              </p>
              {subset.row_count != null && (
                <p className="text-[9px] text-slate-400 leading-tight">{subset.row_count.toLocaleString()}</p>
              )}
              {!pieData.length ? (
                <p className="text-[10px] text-slate-300 py-6">No data</p>
              ) : (
                <ResponsiveContainer width="100%" height={120}>
                  <PieChart>
                    <Pie
                      data={pieData}
                      cx="50%"
                      cy="50%"
                      outerRadius="80%"
                      innerRadius="28%"
                      dataKey="value"
                      stroke="#fff"
                      strokeWidth={1.5}
                      paddingAngle={1}
                    >
                      {pieData.map(d => <Cell key={d.name} fill={colorMap[d.name]} />)}
                    </Pie>
                    <Tooltip
                      formatter={(v, name) => [v.toLocaleString(), name]}
                      contentStyle={{ borderRadius: 6, border: '1px solid #e2e8f0', fontSize: 10 }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function HeatmapGrid({ subsetResults, activeSubsetId, onSubsetChange }) {
  const active = subsetResults.find(s => s.subset.id === activeSubsetId) || subsetResults[0];
  if (!active) return <NoData />;
  const { rows } = active;
  if (!rows?.length) return <NoData />;

  const xVals = [...new Set(rows.map(r => r[0]))];
  const yVals = [...new Set(rows.map(r => r[1]))];
  const cell = {};
  rows.forEach(r => { cell[`${r[0]}|${r[1]}`] = Number(r[2]) || 0; });
  const maxVal = Math.max(...rows.map(r => Number(r[2]) || 0), 1);

  return (
    <div className="space-y-1.5">
      {subsetResults.length > 1 && (
        <div className="flex flex-wrap gap-1">
          {subsetResults.map(({ subset }) => (
            <button
              key={subset.id}
              type="button"
              onClick={() => onSubsetChange(subset.id)}
              className={`text-[10px] px-2 py-0.5 rounded-full border transition font-medium ${
                subset.id === active.subset.id
                  ? 'bg-teal-600 text-white border-teal-600'
                  : 'bg-white text-slate-500 border-slate-200 hover:border-teal-400'
              }`}
            >
              {subset.name}
            </button>
          ))}
        </div>
      )}
      <div className="overflow-auto max-h-96 rounded border border-slate-100">
        <table className="text-[11px] border-collapse w-full">
          <thead>
            <tr>
              <th className="px-1.5 py-1 bg-slate-50/80 sticky left-0 top-0 z-20 backdrop-blur" />
              {yVals.map(y => (
                <th key={y} className="px-1 py-1 text-slate-500 font-medium whitespace-nowrap bg-slate-50/80 sticky top-0 z-10 backdrop-blur">
                  {y}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {xVals.map(x => (
              <tr key={x}>
                <td className="px-1.5 py-0.5 text-slate-700 whitespace-nowrap font-medium bg-slate-50/80 sticky left-0 z-10 backdrop-blur">
                  {x}
                </td>
                {yVals.map(y => {
                  const v = cell[`${x}|${y}`] || 0;
                  const alpha = maxVal > 0 ? v / maxVal : 0;
                  return (
                    <td
                      key={y}
                      title={`${x} × ${y}: ${v}`}
                      className="px-1 py-0.5 text-center tabular-nums min-w-[2rem]"
                      style={{
                        backgroundColor: `rgba(13, 148, 136, ${alpha * 0.7 + 0.03})`,
                        color: alpha > 0.55 ? '#fff' : '#475569',
                      }}
                    >
                      {v > 0 ? v.toLocaleString() : ''}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
