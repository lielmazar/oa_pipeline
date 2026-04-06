// ── App ───────────────────────────────────────────────────────────────────────

function App() {
  const [view, setView] = useState('loading');
  const [me, setMe] = useState(null);
  const [activePipeline, setActivePipeline] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [pipelines, setPipelines] = useState([]);

  const runningCount = jobs.filter(j => j.status === 'running').length;

  const refreshGlobal = useCallback(async () => {
    try {
      const [jobsRes, pipRes] = await Promise.all([
        apiFetch('/api/jobs'),
        apiFetch('/api/pipelines'),
      ]);
      setJobs(await jobsRes.json());
      setPipelines(await pipRes.json());
    } catch {}
  }, []);

  useEffect(() => {
    _onUnauth = () => { setMe(null); setView('login'); };
    fetch('/api/auth/me')
      .then(r => r.ok ? r.json() : null)
      .then(u => {
        if (u) { setMe(u); setView('home'); refreshGlobal(); }
        else   { setView('login'); }
      })
      .catch(() => setView('login'));
  }, []);

  // Refresh badge periodically when not on jobs page (30s idle vs 10s active)
  useInterval(refreshGlobal, me && view !== 'jobs' ? 30000 : null);

  const handleLogin = d => {
    setMe({ username: d.username });
    setView('home');
    refreshGlobal();
  };

  const handleLogout = async () => {
    await apiFetch('/api/auth/logout', { method: 'POST' });
    setMe(null); setView('login');
  };

  if (view === 'loading') {
    return <div className="flex items-center justify-center h-screen text-gray-400">Loading...</div>;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <LoginOverlay visible={!me} onLogin={handleLogin} />
      {me && (
        <>
          <NavBar me={me} view={view} runningCount={runningCount}
            onShowHome={() => { setView('home'); refreshGlobal(); }}
            onShowJobs={() => { setView('jobs'); refreshGlobal(); }}
            onShowQA={() => { setView('qa'); refreshGlobal(); }}
            onShowIRR={() => { setView('irr'); refreshGlobal(); }}
            onShowOrthogonality={() => { setView('orthogonality'); refreshGlobal(); }}
            onShowExport={() => { setView('export'); refreshGlobal(); }}
            onLogout={handleLogout}
          />
          {view === 'home' && (
            <HomeView
              onOpenPipeline={p => { setActivePipeline(p); setView('pipeline'); }}
              runningJobs={jobs}
            />
          )}
          {view === 'pipeline' && activePipeline && (
            <PipelineView
              key={activePipeline.id}
              initialPipeline={activePipeline}
              onBack={() => { setView('home'); refreshGlobal(); }}
              onNavigateTo={p => setActivePipeline(p)}
            />
          )}
          {view === 'jobs' && (
            <JobsView jobs={jobs} pipelines={pipelines} onRefresh={refreshGlobal} />
          )}
          {view === 'qa' && (
            <QAView pipelines={pipelines} onRefresh={refreshGlobal} />
          )}
          {view === 'irr' && (
            <EnhancedQAView pipelines={pipelines} onRefresh={refreshGlobal} />
          )}
          {view === 'orthogonality' && (
            <OrthogonalityView pipelines={pipelines} />
          )}
          {view === 'export' && (
            <ExportView pipelines={pipelines} />
          )}
        </>
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('app-root')).render(React.createElement(App));
