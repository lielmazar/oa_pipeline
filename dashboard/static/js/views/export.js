// ── Export / Article Data Package View ─────────────────────────────────────────

function ExportView({ pipelines }) {
  const [pipelineId, setPipelineId] = useState(null);
  const [prisma, setPrisma] = useState(null);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState('');

  useEffect(() => {
    if (!pipelineId) return;
    apiFetch(`/api/export/prisma/${pipelineId}`).then(r => r.json()).then(setPrisma).catch(() => {});
  }, [pipelineId]);

  const downloadCSV = async (matrixId) => {
    setDownloading(matrixId);
    try {
      const res = await apiFetch(`/api/export/cooccurrence/${pipelineId}?matrix=${matrixId}`);
      if (!res.ok) throw new Error('Download failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `${matrixId}.csv`; a.click();
      URL.revokeObjectURL(url);
    } catch {}
    setDownloading('');
  };

  const downloadPackage = async () => {
    setDownloading('package');
    try {
      const res = await apiFetch(`/api/export/article-package/${pipelineId}`);
      if (!res.ok) throw new Error('Download failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `article_data_pipeline_${pipelineId}.zip`; a.click();
      URL.revokeObjectURL(url);
    } catch {}
    setDownloading('');
  };

  const matrices = [
    { id: 'fuelclass_x_mlrole', label: 'Fuel Classification × ML Role' },
    { id: 'mlrole_x_algorithm', label: 'ML Role × Algorithm' },
    { id: 'fuelclass_x_algorithm', label: 'Fuel Classification × Algorithm' },
    { id: 'fuelclass_x_datasource', label: 'Fuel Classification × Data Source' },
  ];

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-gray-900">Export & Data Package</h2>
        <p className="text-sm text-gray-500 mt-1">Download co-occurrence matrices, PRISMA data, and article data packages.</p>
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-6">
        <select value={pipelineId || ''} onChange={e => setPipelineId(Number(e.target.value) || null)}
          className="border rounded-lg px-3 py-2 text-sm min-w-[200px]">
          <option value="">Select pipeline...</option>
          {pipelines.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </div>

      {!pipelineId && (
        <div className="text-center text-gray-400 py-16">Select a pipeline to export data.</div>
      )}

      {pipelineId && (
        <div className="space-y-6">
          {/* Co-occurrence CSVs */}
          <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
            <h3 className="text-lg font-semibold text-gray-900 mb-3">Co-occurrence Matrices (CSV)</h3>
            <p className="text-sm text-gray-500 mb-4">Pivot tables with row/column totals.</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {matrices.map(m => (
                <button key={m.id} onClick={() => downloadCSV(m.id)}
                  disabled={downloading === m.id}
                  className="flex items-center gap-3 p-3 border rounded-lg hover:bg-blue-50 hover:border-blue-300 transition text-left">
                  <svg className="w-5 h-5 text-blue-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                  </svg>
                  <div>
                    <div className="text-sm font-medium">{m.label}</div>
                    <div className="text-xs text-gray-400">{downloading === m.id ? 'Downloading…' : 'CSV pivot table'}</div>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* PRISMA Flow */}
          {prisma && (
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              <h3 className="text-lg font-semibold text-gray-900 mb-3">PRISMA Flow Data</h3>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
                {Object.entries(prisma).filter(([k]) => k !== 'error').map(([key, val]) => (
                  <div key={key} className="bg-gray-50 rounded-lg p-3 border text-center">
                    <div className="text-lg font-bold text-gray-800">{typeof val === 'number' ? val.toLocaleString() : val}</div>
                    <div className="text-xs text-gray-500 mt-0.5">{key.replace(/_/g, ' ')}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Article Data Package */}
          <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
            <h3 className="text-lg font-semibold text-gray-900 mb-3">Article Data Package</h3>
            <p className="text-sm text-gray-500 mb-4">
              ZIP containing all co-occurrence CSVs, orthogonality metrics, data quality audit, and exemplar papers.
            </p>
            <button onClick={downloadPackage} disabled={downloading === 'package'}
              className="bg-blue-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition flex items-center gap-2">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
              </svg>
              {downloading === 'package' ? 'Generating…' : 'Export Article Data Package'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
