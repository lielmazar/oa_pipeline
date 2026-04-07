import { useState, useEffect, useCallback } from 'react';
import { apiFetch, setUnauthHandler } from './api/client';
import { useInterval } from './hooks/useInterval';
import LoginOverlay from './components/LoginOverlay';
import NavBar from './components/NavBar';
import HomeView from './views/HomeView';
import PipelineView from './views/PipelineView';
import QAView from './views/QAView';

export default function App() {
  const [view, setView]                     = useState('loading');
  const [me, setMe]                         = useState(null);
  const [activePipeline, setActivePipeline] = useState(null);
  const [filterRunning, setFilterRunning]   = useState(false);

  // Lightweight poll: check if any pipeline has an active filter run
  const refreshRunStatus = useCallback(async () => {
    if (!me) return;
    try {
      const res = await apiFetch('/api/pipeline');
      if (!res.ok) return;
      // We just track whether the global "running" badge shows
      // Detailed run status is handled within PipelineView
    } catch {}
  }, [me]);

  useEffect(() => {
    setUnauthHandler(() => { setMe(null); setView('login'); });
    fetch('/api/auth/me')
      .then(r => r.ok ? r.json() : null)
      .then(u => {
        if (u) { setMe(u); setView('home'); }
        else   { setView('login'); }
      })
      .catch(() => setView('login'));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleLogin = d => {
    setMe({ username: d.username });
    setView('home');
  };

  const handleLogout = async () => {
    await apiFetch('/api/auth/logout', { method: 'POST' });
    setMe(null);
    setActivePipeline(null);
    setView('login');
  };

  if (view === 'loading') {
    return (
      <div className="flex items-center justify-center h-screen text-gray-400 text-sm">
        Loading…
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <LoginOverlay visible={!me} onLogin={handleLogin} />
      {me && (
        <>
          <NavBar
            me={me}
            view={view}
            filterRunning={filterRunning}
            onShowHome={() => { setActivePipeline(null); setView('home'); }}
            onShowQA={()   => setView('qa')}
            onLogout={handleLogout}
          />

          {view === 'home' && (
            <HomeView
              onOpenPipeline={p => { setActivePipeline(p); setView('pipeline'); }}
            />
          )}

          {view === 'pipeline' && activePipeline && (
            <PipelineView
              key={activePipeline.id}
              initialPipeline={activePipeline}
              onBack={() => { setView('home'); setActivePipeline(null); }}
              onFilterRunChange={setFilterRunning}
            />
          )}

          {view === 'qa' && (
            <QAView />
          )}
        </>
      )}
    </div>
  );
}
