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
  const [globalRunMeta, setGlobalRunMeta]   = useState(null);
  const [stageRunMeta, setStageRunMeta]     = useState(null);
  const [qaContext, setQaContext]           = useState(null);

  const toRunMeta = useCallback((data) => {
    const run = data?.run;
    const state = data?.state || {};
    const status = data?.status || 'idle';
    const isRunning = !!run && !run?.ended_at && status === 'running';
    if (!isRunning) return null;

    const processed = state.files_processed ?? ((state.processed_files?.length || 0) + (state.failed_files?.length || 0));
    const total = state.total_files ?? (state.all_files?.length || 0);
    const percent = total > 0 ? Math.round((processed / total) * 100) : 0;
    const etaSec = Number(state.eta_sec || 0);
    const mbPerSec = Number(state.mb_per_sec || 0);

    return {
      isRunning,
      status,
      pipelineId: data?.pipeline_id ?? run?.pipeline_id,
      processed,
      total,
      percent,
      etaSec,
      mbPerSec,
    };
  }, []);

  const refreshGlobalRunMeta = useCallback(async () => {
    if (!me) return;
    try {
      const res = await apiFetch('/api/pipeline/run/active');
      if (!res.ok) { setGlobalRunMeta(null); return; }
      const data = await res.json();
      setGlobalRunMeta(toRunMeta(data));
    } catch {
      setGlobalRunMeta(null);
    }
  }, [me, toRunMeta]);

  useEffect(() => {
    setUnauthHandler(() => { setMe(null); setView('login'); });
    fetch('/api/auth/me')
      .then(r => r.ok ? r.json() : null)
      .then(u => {
        if (u) {
          setMe(u);
          const params = new URLSearchParams(window.location.search);
          if (params.get('qa') === '1' || params.get('view') === 'qa') {
            const pid = params.get('pipeline');
            const sid = params.get('subset');
            setQaContext({
              pipelineId: pid ? Number(pid) : null,
              subsetId: sid ? Number(sid) : null,
            });
            setView('qa');
          } else {
            setView('home');
          }
        } else {
          setView('login');
        }
      })
      .catch(() => setView('login'));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!me) {
      setGlobalRunMeta(null);
      return;
    }
    refreshGlobalRunMeta();
  }, [me, refreshGlobalRunMeta]);

  useInterval(refreshGlobalRunMeta, me ? 3000 : null);

  const handleLogin = d => {
    setMe({ username: d.username });
    setView('home');
  };

  const handleLogout = async () => {
    await apiFetch('/api/auth/logout', { method: 'POST' });
    setMe(null);
    setActivePipeline(null);
    setStageRunMeta(null);
    setGlobalRunMeta(null);
    setView('login');
  };

  const filterRunMeta = stageRunMeta?.isRunning ? stageRunMeta : globalRunMeta;

  const openRunningPipeline = useCallback(async () => {
    const pipelineId = filterRunMeta?.pipelineId;
    if (!pipelineId) return;

    try {
      let pipeline = null;
      const currentRes = await apiFetch(`/api/pipeline/${pipelineId}`);
      if (!currentRes.ok) return;
      pipeline = await currentRes.json();

      if (pipeline.current_stage !== 'openalex_search') {
        const setStageRes = await apiFetch(`/api/pipeline/${pipelineId}/set-stage`, {
          method: 'POST',
          body: JSON.stringify({ stage: 'openalex_search' }),
        });
        if (setStageRes.ok) pipeline = await setStageRes.json();
      }

      setActivePipeline(pipeline);
      setView('pipeline');
    } catch {}
  }, [filterRunMeta]);

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
            filterRunMeta={filterRunMeta}
            onShowHome={() => { setActivePipeline(null); setStageRunMeta(null); setView('home'); }}
            onShowQA={()   => { setQaContext(null); setView('qa'); }}
            onRunPillClick={openRunningPipeline}
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
              onBack={() => { setView('home'); setActivePipeline(null); setStageRunMeta(null); }}
              onFilterRunChange={setStageRunMeta}
              onOpenQA={(ctx) => {
                setQaContext(ctx);
                setView('qa');
                try {
                  const u = new URL(window.location.href);
                  u.searchParams.set('qa', '1');
                  if (ctx?.pipelineId) u.searchParams.set('pipeline', String(ctx.pipelineId));
                  else u.searchParams.delete('pipeline');
                  if (ctx?.subsetId) u.searchParams.set('subset', String(ctx.subsetId));
                  else u.searchParams.delete('subset');
                  window.history.replaceState({}, '', `${u.pathname}${u.search}`);
                } catch { /* ignore */ }
              }}
            />
          )}

          {view === 'qa' && (
            <QAView
              initialContext={qaContext}
              onContextConsumed={() => setQaContext(null)}
            />
          )}
        </>
      )}
    </div>
  );
}
