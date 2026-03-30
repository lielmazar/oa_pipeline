/**
 * OpenAlex Pipeline Dashboard — Frontend Application
 * Pipeline-centric SPA: home (pipeline list) and pipeline detail (7 stages).
 */

// ── State ──────────────────────────────────────────────────────────────────────

const S = {
  view: 'home',            // 'home' | 'pipeline'
  me: null,                // { user_id, username }
  pipelines: [],           // list from /api/pipelines
  activePipeline: null,    // current pipeline object
  pipelineStatus: null,    // stage-specific status
  activeJobId: null,       // job being streamed
  sseSource: null,         // EventSource
  outputLines: [],         // [{seq, text}]
  autoScroll: true,
  forkTargetId: null,      // pipeline being forked
  db: {
    page: 1, sort: 'publication_year', dir: 'desc', search: '', total: 0,
  },
  sqlResult: null,
  pollTimer: null,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function el(id) { return document.getElementById(id); }
function show(id) { el(id).classList.remove('hidden'); }
function hide(id) { el(id).classList.add('hidden'); }

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    ...opts,
  });
  if (res.status === 401) {
    show('login-overlay');
    throw new Error('Session expired');
  }
  return res;
}

function stripAnsi(str) {
  return str
    .replace(/\x1b\[[0-9;]*[A-Za-z]/g, '')
    .replace(/\x1b[()][A-Z]/g, '')
    .replace(/\r/g, '');
}

function timeAgo(ts) {
  if (!ts) return '';
  const secs = Math.floor((Date.now() / 1000) - ts);
  if (secs < 60)   return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Auth ──────────────────────────────────────────────────────────────────────

el('login-form').addEventListener('submit', async e => {
  e.preventDefault();
  const username = el('login-username').value.trim();
  const password = el('login-password').value;
  el('login-error').classList.add('hidden');
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const d = await res.json();
      el('login-error').textContent = d.error || 'Login failed';
      el('login-error').classList.remove('hidden');
      return;
    }
    const d = await res.json();
    S.me = { username: d.username };
    hide('login-overlay');
    el('nav-username').textContent = d.username;
    await loadHome();
  } catch (err) {
    el('login-error').textContent = err.message;
    el('login-error').classList.remove('hidden');
  }
});

el('nav-logout').addEventListener('click', async () => {
  await apiFetch('/api/auth/logout', { method: 'POST' });
  S.me = null;
  show('login-overlay');
  hide('view-home');
  hide('view-pipeline');
});

// ── Navigation ────────────────────────────────────────────────────────────────

el('nav-home').addEventListener('click', () => showHome());
el('btn-back').addEventListener('click', () => showHome());

function showHome() {
  S.view = 'home';
  clearPollTimer();
  disconnectSSE();
  hide('view-pipeline');
  hide('view-jobs');
  show('view-home');
  loadHome();
}

function showPipeline(pipeline) {
  S.view = 'pipeline';
  S.activePipeline = pipeline;
  S.pipelineStatus = null;
  hide('view-home');
  hide('view-jobs');
  show('view-pipeline');
  renderPipelineHeader(pipeline);
  renderStageContent(pipeline);
  loadPipelineStatus(pipeline.id);
}

// ── Home view ─────────────────────────────────────────────────────────────────

el('btn-new-pipeline').addEventListener('click', () => {
  el('new-pipeline-name').value = '';
  el('new-pipeline-desc').value = '';
  el('new-pipeline-error').classList.add('hidden');
  show('modal-new-pipeline');
  el('new-pipeline-name').focus();
});
el('btn-cancel-new-pipeline').addEventListener('click', () => hide('modal-new-pipeline'));

el('form-new-pipeline').addEventListener('submit', async e => {
  e.preventDefault();
  const name = el('new-pipeline-name').value.trim();
  if (!name) return;
  el('new-pipeline-error').classList.add('hidden');
  try {
    const res = await apiFetch('/api/pipelines', {
      method: 'POST',
      body: JSON.stringify({ name, description: el('new-pipeline-desc').value.trim() }),
    });
    const pipeline = await res.json();
    if (!res.ok) {
      el('new-pipeline-error').textContent = pipeline.error || 'Failed to create';
      el('new-pipeline-error').classList.remove('hidden');
      return;
    }
    hide('modal-new-pipeline');
    showPipeline(pipeline);
  } catch (err) {
    el('new-pipeline-error').textContent = err.message;
    el('new-pipeline-error').classList.remove('hidden');
  }
});

async function loadHome() {
  try {
    const [pipRes, jobsRes] = await Promise.all([
      apiFetch('/api/pipelines'),
      apiFetch('/api/jobs'),
    ]);
    const pipelines = await pipRes.json();
    const jobs = await jobsRes.json();
    S.pipelines = pipelines;
    const searchingPipelines = new Set(
      jobs
        .filter(j => j.script === 'run_pipeline' && j.status === 'running')
        .map(j => j.pipeline_id)
    );
    renderPipelinesGrid(pipelines, searchingPipelines);
    updateJobsBadge(jobs);
  } catch (err) {
    el('pipelines-grid').innerHTML = `<div class="col-span-full text-red-500 py-8 text-center">${escHtml(err.message)}</div>`;
  }
}

const STAGE_COLORS = {
  keyword_selection:    'bg-gray-200 text-gray-700',
  openalex_search:      'bg-blue-100 text-blue-800',
  results_review:       'bg-cyan-100 text-cyan-800',
  report_generation:    'bg-emerald-100 text-emerald-800',
  llm_classification:   'bg-purple-100 text-purple-800',
  llm_results_review:   'bg-indigo-100 text-indigo-800',
  llm_report_generation:'bg-green-100 text-green-800',
};

function renderPipelinesGrid(pipelines, searchingPipelines = new Set()) {
  const grid = el('pipelines-grid');
  if (!pipelines.length) {
    grid.innerHTML = `<div class="col-span-full text-center text-gray-400 py-16">
      <p class="text-lg mb-2">No pipelines yet</p>
      <p class="text-sm">Click "New Pipeline" to get started</p>
    </div>`;
    return;
  }
  grid.innerHTML = pipelines.map(p => {
    const stageColor = STAGE_COLORS[p.current_stage] || 'bg-gray-100 text-gray-700';
    const isOwner = p.is_owner;
    const ownerLabel = isOwner ? 'you' : (p.owner_username || 'unknown');
    const forkedBadge = p.forked_from_id
      ? `<span class="text-xs text-purple-500 ml-1">(fork)</span>` : '';
    return `
      <div class="bg-white rounded-xl border border-gray-200 p-5 flex flex-col gap-3 hover:shadow-md transition cursor-pointer pipeline-card"
           data-id="${p.id}">
        <div class="flex items-start justify-between gap-2">
          <div>
            <h3 class="font-bold text-base leading-tight">${escHtml(p.name)}${forkedBadge}</h3>
            <p class="text-xs text-gray-400 mt-0.5">by ${escHtml(ownerLabel)}</p>
          </div>
          <div class="flex flex-col items-end gap-1 flex-shrink-0">
            <span class="text-xs font-medium px-2 py-1 rounded-full ${stageColor}">
              ${escHtml(p.stage_label || p.current_stage)}
            </span>
            ${searchingPipelines.has(p.id) ? `<span class="flex items-center gap-1 text-xs text-blue-700 font-medium"><span class="inline-block w-2 h-2 rounded-full bg-blue-500 animate-pulse"></span>searching…</span>` : ''}
          </div>
        </div>
        ${p.description ? `<p class="text-sm text-gray-500 line-clamp-2">${escHtml(p.description)}</p>` : ''}
        <div class="flex items-center justify-between mt-auto pt-2 border-t border-gray-100">
          <span class="text-xs text-gray-400">${p.updated_at ? timeAgo(new Date(p.updated_at).getTime() / 1000) : ''}</span>
          <div class="flex gap-2">
            ${!isOwner ? `<button class="btn-fork-card text-xs text-purple-700 border border-purple-200 px-2 py-1 rounded hover:bg-purple-50" data-id="${p.id}" data-name="${escHtml(p.name)}">Fork</button>` : ''}
            <button class="btn-open-pipeline text-xs text-blue-700 border border-blue-200 px-2 py-1 rounded hover:bg-blue-50" data-id="${p.id}">Open</button>
            ${isOwner ? `<button class="btn-delete-card text-xs text-red-600 border border-red-200 px-2 py-1 rounded hover:bg-red-50" data-id="${p.id}" data-name="${escHtml(p.name)}">Delete</button>` : ''}
          </div>
        </div>
      </div>`;
  }).join('');

  grid.querySelectorAll('.pipeline-card').forEach(card => {
    card.addEventListener('click', async e => {
      if (e.target.closest('button')) return; // let buttons handle their own clicks
      const id = Number(card.dataset.id);
      openPipelineById(id);
    });
  });
  grid.querySelectorAll('.btn-open-pipeline').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      openPipelineById(Number(btn.dataset.id));
    });
  });
  grid.querySelectorAll('.btn-fork-card').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      openForkModal(Number(btn.dataset.id), btn.dataset.name);
    });
  });
  grid.querySelectorAll('.btn-delete-card').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      if (!confirm(`Delete pipeline "${btn.dataset.name}"?`)) return;
      await apiFetch(`/api/pipelines/${btn.dataset.id}`, { method: 'DELETE' });
      loadHome();
    });
  });
}

async function openPipelineById(id) {
  const res = await apiFetch(`/api/pipelines/${id}`);
  const pipeline = await res.json();
  if (!res.ok) { alert(pipeline.error); return; }
  showPipeline(pipeline);
}

// ── Fork modal ────────────────────────────────────────────────────────────────

function openForkModal(id, name) {
  S.forkTargetId = id;
  el('fork-pipeline-name').value = `My Fork of ${name}`;
  el('fork-pipeline-error').classList.add('hidden');
  show('modal-fork-pipeline');
  el('fork-pipeline-name').focus();
  el('fork-pipeline-name').select();
}
el('btn-cancel-fork-pipeline').addEventListener('click', () => hide('modal-fork-pipeline'));

el('form-fork-pipeline').addEventListener('submit', async e => {
  e.preventDefault();
  const name = el('fork-pipeline-name').value.trim();
  if (!name) return;
  el('fork-pipeline-error').classList.add('hidden');
  try {
    const res = await apiFetch(`/api/pipelines/${S.forkTargetId}/fork`, {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
    const pipeline = await res.json();
    if (!res.ok) {
      el('fork-pipeline-error').textContent = pipeline.error || 'Fork failed';
      el('fork-pipeline-error').classList.remove('hidden');
      return;
    }
    hide('modal-fork-pipeline');
    showPipeline(pipeline);
  } catch (err) {
    el('fork-pipeline-error').textContent = err.message;
    el('fork-pipeline-error').classList.remove('hidden');
  }
});

// ── Pipeline detail header ─────────────────────────────────────────────────────

const STAGE_IDS = [
  'keyword_selection', 'openalex_search', 'results_review',
  'report_generation', 'llm_classification', 'llm_results_review', 'llm_report_generation',
];
const STAGE_LABELS_MAP = {
  keyword_selection:    '① Keywords',
  openalex_search:      '② Search',
  results_review:       '③ Review',
  report_generation:    '④ Report',
  llm_classification:   '⑤ LLM',
  llm_results_review:   '⑥ LLM Review',
  llm_report_generation:'⑦ Final Report',
};

function renderPipelineHeader(pipeline) {
  el('pipeline-title').textContent = pipeline.name;
  el('pipeline-owner-badge').textContent = pipeline.is_owner ? 'owner' : `by ${pipeline.owner_username || '?'}`;

  // Fork / Delete buttons
  const btnFork = el('btn-fork-pipeline');
  const btnDel  = el('btn-delete-pipeline');
  if (pipeline.is_owner) {
    hide('btn-fork-pipeline');
    show('btn-delete-pipeline');
  } else {
    show('btn-fork-pipeline');
    hide('btn-delete-pipeline');
    btnFork.onclick = () => openForkModal(pipeline.id, pipeline.name);
  }
  btnDel.onclick = async () => {
    if (!confirm(`Delete pipeline "${pipeline.name}"?`)) return;
    await apiFetch(`/api/pipelines/${pipeline.id}`, { method: 'DELETE' });
    showHome();
  };

  // Stage stepper
  const currentIdx = STAGE_IDS.indexOf(pipeline.current_stage);
  const stepper = el('stage-stepper');
  stepper.innerHTML = STAGE_IDS.map((sid, i) => {
    let dotClass = 'w-7 h-7 rounded-full border-2 flex items-center justify-center text-xs font-bold flex-shrink-0 ';
    if (i < currentIdx)       dotClass += 'bg-green-500 border-green-500 text-white';
    else if (i === currentIdx) dotClass += 'bg-blue-600 border-blue-600 text-white';
    else                       dotClass += 'bg-white border-gray-300 text-gray-400';

    const lineClass = i < STAGE_IDS.length - 1
      ? `flex-1 h-0.5 mx-1 ${i < currentIdx ? 'bg-green-400' : 'bg-gray-200'}`
      : '';

    const label = STAGE_LABELS_MAP[sid] || sid;
    return `
      <div class="flex items-center ${i < STAGE_IDS.length - 1 ? 'flex-1' : ''}">
        <div class="flex flex-col items-center">
          <div class="${dotClass}">${i + 1}</div>
          <span class="text-xs mt-1 whitespace-nowrap ${i === currentIdx ? 'text-blue-700 font-semibold' : 'text-gray-400'}"
                style="font-size:0.65rem">${escHtml(label)}</span>
        </div>
        ${i < STAGE_IDS.length - 1 ? `<div class="${lineClass}"></div>` : ''}
      </div>`;
  }).join('');

  // Advance / Go Back buttons
  const nextLabel = pipeline.next_stage_label || pipeline.next_stage;
  const prevLabel = pipeline.prev_stage_label || pipeline.prev_stage;
  if (pipeline.is_owner && pipeline.next_stage) {
    show('advance-bar');
    el('advance-stage-label').textContent = nextLabel;
    el('btn-advance-stage').onclick = async () => {
      if (!confirm(`Advance to "${nextLabel}"?`)) return;
      const res = await apiFetch(`/api/pipelines/${pipeline.id}/advance`, { method: 'POST' });
      const updated = await res.json();
      if (!res.ok) { alert(updated.error); return; }
      S.activePipeline = updated;
      renderPipelineHeader(updated);
      renderStageContent(updated);
      loadPipelineStatus(updated.id);
    };
  } else {
    hide('advance-bar');
  }

  const goBackBtn = el('btn-go-back-stage');
  if (pipeline.is_owner && pipeline.prev_stage) {
    goBackBtn.classList.remove('hidden');
    goBackBtn.textContent = `← Back to ${prevLabel}`;
    goBackBtn.onclick = async () => {
      if (!confirm(`Go back to "${prevLabel}"?`)) return;
      const res = await apiFetch(`/api/pipelines/${pipeline.id}/go-back`, { method: 'POST' });
      const updated = await res.json();
      if (!res.ok) { alert(updated.error); return; }
      S.activePipeline = updated;
      renderPipelineHeader(updated);
      renderStageContent(updated);
      loadPipelineStatus(updated.id);
    };
  } else {
    goBackBtn.classList.add('hidden');
  }
}

// ── Stage content renderer ────────────────────────────────────────────────────

function renderStageContent(pipeline) {
  const container = el('stage-content');
  const stage = pipeline.current_stage;
  switch (stage) {
    case 'keyword_selection':
      container.innerHTML = renderKeywordStage(pipeline);
      initKeywordStage(pipeline);
      break;
    case 'openalex_search':
      container.innerHTML = renderSearchStage(pipeline);
      initSearchStage(pipeline);
      break;
    case 'results_review':
    case 'llm_results_review':
      container.innerHTML = renderReviewStage(pipeline, stage === 'llm_results_review');
      initReviewStage(pipeline);
      break;
    case 'report_generation':
    case 'llm_report_generation':
      container.innerHTML = renderReportStage(pipeline, stage === 'llm_report_generation');
      initReportStage(pipeline, stage === 'llm_report_generation');
      break;
    case 'llm_classification':
      container.innerHTML = renderLLMStage(pipeline);
      initLLMStage(pipeline);
      break;
    default:
      container.innerHTML = `<div class="text-gray-500">Unknown stage: ${escHtml(stage)}</div>`;
  }
}

// ── Stage: Keyword Selection ──────────────────────────────────────────────────

function renderKeywordStage(pipeline) {
  const config = pipeline.config || {};
  const fuel = (config.fuel_terms || []).map(t => `<span class="tag-pill">${escHtml(t)}<button data-term="${escHtml(t)}" data-list="fuel">✕</button></span>`).join('');
  const impact = (config.impact_terms || []).map(t => `<span class="tag-pill">${escHtml(t)}<button data-term="${escHtml(t)}" data-list="impact">✕</button></span>`).join('');
  return `
    <div class="space-y-6">
      <div class="bg-white rounded-xl border border-gray-200 p-6">
        <h3 class="font-semibold text-lg mb-1">Stage 1: Keyword Selection</h3>
        <p class="text-sm text-gray-500 mb-4">Configure the fuel terms and impact terms used to filter OpenAlex records.</p>

        <div class="space-y-5">
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-2">Fuel Terms (${(config.fuel_terms || []).length})</label>
            <div id="fuel-tags" class="flex flex-wrap gap-1.5 p-2 border rounded-lg min-h-[2.5rem] bg-gray-50">${fuel || '<span class="text-gray-400 text-xs">No terms yet — add below</span>'}</div>
            <div class="flex gap-2 mt-2">
              <input id="fuel-input" type="text" placeholder="Add fuel term..."
                     class="flex-1 border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
              <button id="btn-add-fuel" class="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 transition">Add</button>
            </div>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700 mb-2">Impact Terms (${(config.impact_terms || []).length})</label>
            <div id="impact-tags" class="flex flex-wrap gap-1.5 p-2 border rounded-lg min-h-[2.5rem] bg-gray-50">${impact || '<span class="text-gray-400 text-xs">No terms yet — add below</span>'}</div>
            <div class="flex gap-2 mt-2">
              <input id="impact-input" type="text" placeholder="Add impact term..."
                     class="flex-1 border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
              <button id="btn-add-impact" class="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 transition">Add</button>
            </div>
          </div>

          <div id="keyword-save-status" class="text-sm text-green-600 hidden">Keywords saved.</div>
          ${pipeline.is_owner ? `<button id="btn-save-keywords" class="w-full py-2 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700 transition">Save Keywords</button>` : ''}
        </div>
      </div>
    </div>`;
}

function initKeywordStage(pipeline) {
  if (!pipeline.is_owner) return;
  const config = pipeline.config || {};
  let fuel = [...(config.fuel_terms || [])];
  let impact = [...(config.impact_terms || [])];

  function renderTags(list, containerId, listKey) {
    const container = el(containerId);
    if (!list.length) {
      container.innerHTML = `<span class="text-gray-400 text-xs">No terms yet — add below</span>`;
      return;
    }
    container.innerHTML = list.map(t =>
      `<span class="tag-pill">${escHtml(t)}<button data-term="${escHtml(t)}" data-list="${listKey}">✕</button></span>`
    ).join('');
    container.querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', () => {
        if (btn.dataset.list === 'fuel') fuel = fuel.filter(x => x !== btn.dataset.term);
        else impact = impact.filter(x => x !== btn.dataset.term);
        renderTags(btn.dataset.list === 'fuel' ? fuel : impact,
                   btn.dataset.list === 'fuel' ? 'fuel-tags' : 'impact-tags',
                   btn.dataset.list);
      });
    });
  }

  function addTerm(inputId, listRef, containerId, listKey) {
    const input = el(inputId);
    const term = input.value.trim();
    if (!term || listRef.includes(term)) { input.value = ''; return; }
    listRef.push(term);
    input.value = '';
    renderTags(listRef, containerId, listKey);
  }

  el('btn-add-fuel').addEventListener('click', () => addTerm('fuel-input', fuel, 'fuel-tags', 'fuel'));
  el('fuel-input').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addTerm('fuel-input', fuel, 'fuel-tags', 'fuel'); } });
  el('btn-add-impact').addEventListener('click', () => addTerm('impact-input', impact, 'impact-tags', 'impact'));
  el('impact-input').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addTerm('impact-input', impact, 'impact-tags', 'impact'); } });

  el('btn-save-keywords').addEventListener('click', async () => {
    const newConfig = { ...config, fuel_terms: fuel, impact_terms: impact };
    const res = await apiFetch(`/api/pipelines/${pipeline.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ config: newConfig }),
    });
    const updated = await res.json();
    if (!res.ok) { alert(updated.error); return; }
    S.activePipeline = updated;
    el('keyword-save-status').classList.remove('hidden');
    setTimeout(() => el('keyword-save-status')?.classList.add('hidden'), 3000);
  });
}

// ── Stage: OpenAlex Search ────────────────────────────────────────────────────

function renderSearchStage(pipeline) {
  return `
    <div class="space-y-4">
      <div class="bg-white rounded-xl border border-gray-200 p-6">
        <h3 class="font-semibold text-lg mb-1">Stage 2: OpenAlex Search</h3>
        <p class="text-sm text-gray-500 mb-4">Run the parallel pipeline to filter OpenAlex records using your keywords.</p>
        <div id="cpu-info" class="text-xs text-gray-400 mb-3"></div>
        ${pipeline.is_owner ? `
        <div class="grid grid-cols-2 gap-4 mb-4">
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">Max files <span id="search-max-hint" class="text-gray-400 font-normal text-xs"></span></label>
            <input id="search-max-files" type="number" placeholder="blank = all files" min="1"
                   class="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
          </div>
          <div class="flex items-end pb-0.5">
            <span class="text-xs text-gray-400">Workers: <strong class="text-blue-700">16 cores</strong> (fixed)</span>
          </div>
        </div>
        <div class="mb-4">
          <label class="block text-xs font-medium text-gray-500 mb-1">OpenAlex snapshot dir (saved locally) <button id="btn-toggle-dir" class="text-blue-500 hover:underline text-xs ml-1">edit</button></label>
          <div id="dir-display" class="text-xs text-gray-600 font-mono bg-gray-50 px-3 py-1.5 rounded border">
            <span id="local-dir-value">not set — click edit</span>
          </div>
          <input id="search-local-dir" type="text" placeholder="/path/to/openalex/works" style="display:none"
                 class="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 mt-1">
        </div>
        <div class="flex gap-3">
          <button id="btn-start-search" class="px-4 py-2 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700 transition">
            ▶ Start Search
          </button>
          <button id="btn-kill-search" class="hidden px-4 py-2 bg-red-600 text-white rounded-lg font-semibold hover:bg-red-700 transition">
            ■ Stop
          </button>
        </div>` : '<p class="text-gray-400 text-sm italic">View only — you are not the owner.</p>'}
      </div>
      <div id="search-progress-bar" class="hidden bg-white rounded-xl border border-gray-200 p-4">
        <div class="flex justify-between text-sm text-gray-600 mb-2">
          <span id="search-prog-label">Progress</span>
          <span id="search-prog-pct"></span>
        </div>
        <div class="w-full bg-gray-200 rounded-full h-2">
          <div id="search-prog-fill" class="bg-blue-500 h-2 rounded-full transition-all" style="width:0%"></div>
        </div>
      </div>
      <div id="search-terminal-wrap" class="hidden bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div class="bg-gray-800 px-4 py-2 flex justify-between items-center">
          <span id="search-job-label" class="text-gray-300 text-sm font-mono"></span>
          <span id="search-job-status" class="text-xs px-2 py-0.5 rounded bg-gray-700 text-gray-300"></span>
        </div>
        <div id="search-output" class="terminal bg-gray-900 text-green-400 p-4 h-80 overflow-y-auto whitespace-pre-wrap"></div>
      </div>
    </div>`;
}

function initSearchStage(pipeline) {
  // Show total files count and populate local dir from localStorage
  const savedDir = localStorage.getItem('oa_local_dir') || '';
  if (el('local-dir-value')) el('local-dir-value').textContent = savedDir || 'not set — click edit';
  if (el('search-local-dir')) el('search-local-dir').value = savedDir;

  el('btn-toggle-dir')?.addEventListener('click', () => {
    const inp = el('search-local-dir');
    const disp = el('dir-display');
    if (inp.style.display === 'none') {
      inp.style.display = '';
      disp.style.display = 'none';
      inp.focus();
    } else {
      const val = inp.value.trim();
      localStorage.setItem('oa_local_dir', val);
      inp.style.display = 'none';
      disp.style.display = '';
      el('local-dir-value').textContent = val || 'not set — click edit';
    }
  });

  apiFetch(`/api/pipelines/${pipeline.id}/file-count`).then(r => r.json()).then(info => {
    const hint = el('search-max-hint');
    if (hint && info.total_files) {
      hint.textContent = `(${info.total_files.toLocaleString()} files total)`;
    }
    const cpuInfo = el('cpu-info');
    if (cpuInfo) cpuInfo.textContent = `Server: 16 cores fixed for OpenAlex search`;
  }).catch(() => {});

  if (!pipeline.is_owner) return;

  el('btn-start-search').addEventListener('click', async () => {
    const maxFiles = el('search-max-files').value;
    const localDir = localStorage.getItem('oa_local_dir') || '';

    const args = {
      '--output-db': pipeline.output_db_path,
      '--state-file': pipeline.state_file_path,
      '--workers': 16,
      '--keyword-config': null,
    };
    if (maxFiles) args['--max-files'] = maxFiles;
    if (localDir) args['--local-dir'] = localDir;

    const res = await apiFetch('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ script: 'run_pipeline', pipeline_id: pipeline.id, args }),
    });
    const job = await res.json();
    if (!res.ok) { alert(job.error); return; }
    startJobStream('search', job.id, pipeline);
  });

  el('btn-kill-search').addEventListener('click', async () => {
    if (S.activeJobId) {
      await apiFetch(`/api/jobs/${S.activeJobId}`, { method: 'DELETE' });
    }
  });
}

// ── Stage: Results Review ─────────────────────────────────────────────────────

function renderReviewStage(pipeline, isLLM) {
  const stageNum = isLLM ? 6 : 3;
  const stageTitle = isLLM ? 'Stage 6: LLM Results Review' : 'Stage 3: Results Review';
  const llmCols = isLLM ? `
    <option value="llm_fuel_classification">LLM Fuel Type</option>
    <option value="llm_confidence">LLM Confidence</option>
    <option value="llm_is_hydrocarbon">Is Hydrocarbon</option>
  ` : '';
  return `
    <div class="space-y-4">
      <div class="bg-white rounded-xl border border-gray-200 p-6">
        <h3 class="font-semibold text-lg mb-1">${stageTitle}</h3>
        <p class="text-sm text-gray-500 mb-4">Browse and search the filtered records database.</p>
        <div id="db-stats" class="flex gap-4 text-sm text-gray-600 mb-4"></div>
        <div class="flex gap-3 mb-4">
          <input id="db-search" type="text" placeholder="Search title, DOI, abstract..."
                 class="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
          <select id="db-sort" class="border rounded-lg px-3 py-2 text-sm">
            <option value="publication_year">Year ↓</option>
            <option value="title">Title ↑</option>
            <option value="fwci">FWCI ↓</option>
            ${llmCols}
          </select>
          <button id="btn-db-search" class="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 transition">Search</button>
        </div>
        <div id="db-table-wrap" class="overflow-x-auto rounded-lg border border-gray-200 min-h-[12rem]">
          <div class="text-gray-400 text-center py-8">Loading records...</div>
        </div>
        <div id="db-pagination" class="flex justify-between items-center mt-3 text-sm text-gray-500"></div>
      </div>

      <!-- SQL Query -->
      <div class="bg-white rounded-xl border border-gray-200 p-6">
        <h4 class="font-semibold mb-3">SQL Query</h4>
        <textarea id="db-sql" rows="3" placeholder="SELECT * FROM filtered_records LIMIT 10"
                  class="w-full border rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-400 mb-2"></textarea>
        <div class="flex gap-3">
          <button id="btn-run-sql" class="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 transition">Run Query</button>
          <button id="btn-export-csv" class="hidden px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-sm hover:bg-gray-50 transition">Export CSV</button>
        </div>
        <div id="sql-result-wrap" class="mt-4 overflow-x-auto"></div>
      </div>
    </div>`;
}

function initReviewStage(pipeline) {
  S.db = { page: 1, sort: 'publication_year', dir: 'desc', search: '', total: 0 };
  loadDbRecords(pipeline);

  el('btn-db-search').addEventListener('click', () => {
    S.db.search = el('db-search').value;
    S.db.page = 1;
    loadDbRecords(pipeline);
  });
  el('db-search').addEventListener('keydown', e => {
    if (e.key === 'Enter') { S.db.search = el('db-search').value; S.db.page = 1; loadDbRecords(pipeline); }
  });
  el('db-sort').addEventListener('change', () => {
    S.db.sort = el('db-sort').value;
    S.db.page = 1;
    loadDbRecords(pipeline);
  });

  el('btn-run-sql').addEventListener('click', async () => {
    const sql = el('db-sql').value.trim();
    if (!sql) return;
    const res = await apiFetch('/api/db/query', {
      method: 'POST',
      body: JSON.stringify({ pipeline_id: pipeline.id, sql }),
    });
    const result = await res.json();
    S.sqlResult = result;
    renderSqlResult(result);
    el('btn-export-csv').classList.toggle('hidden', !(result.columns && result.rows));
  });

  el('btn-export-csv').addEventListener('click', () => {
    if (!S.sqlResult?.rows) return;
    const csv = [S.sqlResult.columns.join(','), ...S.sqlResult.rows.map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))].join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
    a.download = `pipeline_${pipeline.id}_query.csv`;
    a.click();
  });

  el('btn-close-record')?.addEventListener('click', () => hide('modal-record'));
}

async function loadDbRecords(pipeline) {
  const params = new URLSearchParams({
    pipeline_id: pipeline.id,
    page: S.db.page,
    sort: S.db.sort,
    dir: S.db.dir,
    q: S.db.search,
  });
  const res = await apiFetch(`/api/db/records?${params}`);
  const data = await res.json();
  if (data.error) {
    el('db-table-wrap').innerHTML = `<div class="text-red-500 p-4">${escHtml(data.error)}</div>`;
    return;
  }
  S.db.total = data.total || 0;
  renderDbTable(data, pipeline);
  renderDbPagination(data, pipeline);
  const statsEl = el('db-stats');
  if (statsEl) statsEl.innerHTML = `<span class="font-medium">${(data.total || 0).toLocaleString()}</span> records`;
}

function renderDbTable(data, pipeline) {
  const wrap = el('db-table-wrap');
  if (!data.rows?.length) {
    wrap.innerHTML = '<div class="text-gray-400 text-center py-8">No records found</div>';
    return;
  }
  const SHOW_COLS = ['title', 'publication_year', 'doi', 'llm_confidence', 'llm_fuel_classification'];
  const colIdxs = data.columns.map((c, i) => SHOW_COLS.includes(c) ? i : -1).filter(i => i >= 0);
  const visibleCols = colIdxs.map(i => data.columns[i]);
  wrap.innerHTML = `
    <table class="w-full text-sm text-left">
      <thead class="bg-gray-50 border-b border-gray-200">
        <tr>${visibleCols.map(c => `<th class="px-3 py-2 font-medium text-gray-600">${escHtml(c)}</th>`).join('')}</tr>
      </thead>
      <tbody>
        ${data.rows.map((row, ri) => `
          <tr class="border-b border-gray-100 hover:bg-blue-50 cursor-pointer db-row" data-rowid="${escHtml(row[data.columns.indexOf('openalex_id')] || ri)}">
            ${colIdxs.map(i => `<td class="px-3 py-2 max-w-xs truncate">${escHtml(String(row[i] ?? ''))}</td>`).join('')}
          </tr>`).join('')}
      </tbody>
    </table>`;
  wrap.querySelectorAll('.db-row').forEach(row => {
    row.addEventListener('click', async () => {
      const rowId = row.dataset.rowid;
      const res = await apiFetch(`/api/db/records/${encodeURIComponent(rowId)}?pipeline_id=${pipeline.id}`);
      const record = await res.json();
      if (!res.ok) return;
      showRecordModal(record);
    });
  });
}

function renderDbPagination(data, pipeline) {
  const total = data.total || 0;
  const perPage = data.per_page || 100;
  const pages = Math.ceil(total / perPage);
  const pag = el('db-pagination');
  if (!pag) return;
  pag.innerHTML = `
    <span>Page ${S.db.page} of ${pages} (${total.toLocaleString()} records)</span>
    <div class="flex gap-2">
      <button id="db-prev" ${S.db.page <= 1 ? 'disabled' : ''} class="px-3 py-1 border rounded hover:bg-gray-100 disabled:opacity-40">← Prev</button>
      <button id="db-next" ${S.db.page >= pages ? 'disabled' : ''} class="px-3 py-1 border rounded hover:bg-gray-100 disabled:opacity-40">Next →</button>
    </div>`;
  el('db-prev').addEventListener('click', () => { S.db.page--; loadDbRecords(pipeline); });
  el('db-next').addEventListener('click', () => { S.db.page++; loadDbRecords(pipeline); });
}

function renderSqlResult(result) {
  const wrap = el('sql-result-wrap');
  if (!wrap) return;
  if (result.error) {
    wrap.innerHTML = `<div class="text-red-600 text-sm">${escHtml(result.error)}</div>`;
    return;
  }
  if (!result.rows?.length) {
    wrap.innerHTML = '<div class="text-gray-400 text-sm">No results</div>';
    return;
  }
  wrap.innerHTML = `
    <div class="text-xs text-gray-500 mb-1">${result.rows.length} row(s)</div>
    <div class="overflow-x-auto rounded border border-gray-200">
      <table class="text-xs">
        <thead class="bg-gray-50 border-b"><tr>${result.columns.map(c => `<th class="px-2 py-1 text-left font-medium">${escHtml(c)}</th>`).join('')}</tr></thead>
        <tbody>${result.rows.map(r => `<tr class="border-b border-gray-100">${r.map(v => `<td class="px-2 py-1 max-w-xs truncate">${escHtml(String(v ?? ''))}</td>`).join('')}</tr>`).join('')}</tbody>
      </table>
    </div>`;
}

function showRecordModal(record) {
  el('modal-record-title').textContent = record.title || record.openalex_id || 'Record';
  const oaId = record.openalex_id || '';
  const oaLink = oaId
    ? `<a href="https://openalex.org/${oaId}" target="_blank" class="text-blue-600 hover:underline text-xs ml-2">Open in OpenAlex ↗</a>`
    : '';

  // Try to parse JSON columns for nice display
  const JSON_COLS = new Set(['matched_fuel_terms','matched_impact_terms','authors_countries',
    'authors_institutions','llm_fuel_classification','llm_ml_paradigm','llm_ml_method',
    'llm_ml_model_type','concepts','topics']);

  const body = el('modal-record-body');
  const rows = Object.entries(record)
    .filter(([k]) => !k.startsWith('_'))
    .map(([k, v]) => {
      let display;
      if (v === null || v === undefined || v === '') {
        display = `<span class="text-gray-300 italic">—</span>`;
      } else if (JSON_COLS.has(k) && typeof v === 'string' && (v.startsWith('[') || v.startsWith('{'))) {
        try {
          const parsed = JSON.parse(v);
          if (Array.isArray(parsed)) {
            display = parsed.map(x => `<span class="inline-block bg-blue-50 text-blue-800 text-xs px-1.5 py-0.5 rounded mr-1 mb-1">${escHtml(String(x))}</span>`).join('');
          } else {
            display = `<pre class="text-xs bg-gray-50 p-2 rounded overflow-x-auto">${escHtml(JSON.stringify(parsed, null, 2))}</pre>`;
          }
        } catch {
          display = `<span class="break-all">${escHtml(String(v))}</span>`;
        }
      } else if (typeof v === 'string' && v.length > 300) {
        display = `<span class="break-all text-sm leading-relaxed">${escHtml(v)}</span>`;
      } else {
        display = `<span class="break-all">${escHtml(String(v))}</span>`;
      }
      return `
        <div class="flex gap-2 py-1 border-b border-gray-50">
          <span class="text-gray-500 font-medium w-44 flex-shrink-0 text-xs pt-0.5">${escHtml(k)}</span>
          <div class="flex-1 min-w-0 text-sm">${display}</div>
        </div>`;
    }).join('');

  body.innerHTML = `
    <div class="flex items-center gap-2 mb-3 pb-2 border-b">
      <span class="text-xs font-mono text-gray-400">${escHtml(oaId)}</span>
      ${oaLink}
      ${record.doi ? `<a href="https://doi.org/${escHtml(record.doi)}" target="_blank" class="text-blue-600 hover:underline text-xs">DOI ↗</a>` : ''}
    </div>
    ${rows}`;
  show('modal-record');
}

el('btn-close-record').addEventListener('click', () => hide('modal-record'));

// ── Stage: Report Generation ──────────────────────────────────────────────────

function renderReportStage(pipeline, isLLM) {
  const stageNum = isLLM ? 7 : 4;
  const reportKey = isLLM ? 'llm_report_path' : 'report_path';
  const hasReport = Boolean(pipeline[reportKey]);
  return `
    <div class="space-y-4">
      <div class="bg-white rounded-xl border border-gray-200 p-6">
        <h3 class="font-semibold text-lg mb-1">Stage ${stageNum}: ${isLLM ? 'Final Report' : 'Report Generation'}</h3>
        <p class="text-sm text-gray-500 mb-4">${isLLM ? 'Generate the final enriched report with LLM classification results.' : 'Generate an HTML report with charts and statistics.'}</p>
        ${pipeline.is_owner ? `
        <button id="btn-gen-report" class="px-4 py-2 bg-emerald-600 text-white rounded-lg font-semibold hover:bg-emerald-700 transition mb-4">
          ▶ Generate ${isLLM ? 'Final Report' : 'Report'}
        </button>` : ''}
      </div>
      <div id="report-terminal-wrap" class="hidden bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div class="bg-gray-800 px-4 py-2 flex justify-between items-center">
          <span id="report-job-label" class="text-gray-300 text-sm font-mono"></span>
          <span id="report-job-status" class="text-xs px-2 py-0.5 rounded bg-gray-700 text-gray-300"></span>
        </div>
        <div id="report-output" class="terminal bg-gray-900 text-green-400 p-4 h-40 overflow-y-auto whitespace-pre-wrap"></div>
      </div>
      <div id="report-preview-wrap" class="${hasReport ? '' : 'hidden'} bg-white rounded-xl border border-gray-200 p-4">
        <div class="flex justify-between items-center mb-3">
          <h4 class="font-semibold">Report Preview</h4>
          <a id="report-open-link" href="/api/report?pipeline_id=${pipeline.id}${isLLM ? '&llm=1' : ''}" target="_blank"
             class="text-sm text-blue-600 hover:underline">Open in new tab ↗</a>
        </div>
        <iframe id="report-iframe"
                src="${hasReport ? `/api/report?pipeline_id=${pipeline.id}${isLLM ? '&llm=1' : ''}` : ''}"
                class="w-full h-96 rounded border border-gray-200"></iframe>
      </div>
    </div>`;
}

function initReportStage(pipeline, isLLM) {
  if (!pipeline.is_owner) return;
  el('btn-gen-report')?.addEventListener('click', async () => {
    const args = {
      '--db': pipeline.output_db_path,
      '--output': isLLM ? pipeline.llm_report_path : pipeline.report_path,
      '--state': pipeline.state_file_path,
    };
    const res = await apiFetch('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ script: 'generate_report', pipeline_id: pipeline.id, args }),
    });
    const job = await res.json();
    if (!res.ok) { alert(job.error); return; }
    startJobStream('report', job.id, pipeline, () => {
      // On completion, show iframe
      show('report-preview-wrap');
      const iframe = el('report-iframe');
      if (iframe) iframe.src = `/api/report?pipeline_id=${pipeline.id}${isLLM ? '&llm=1' : ''}`;
    });
  });
}

// ── Stage: LLM Classification ─────────────────────────────────────────────────

function renderLLMStage(pipeline) {
  return `
    <div class="space-y-4">
      <div class="bg-white rounded-xl border border-gray-200 p-6">
        <h3 class="font-semibold text-lg mb-1">Stage 5: LLM Classification</h3>
        <p class="text-sm text-gray-500 mb-4">Run LLM classification on filtered records via OpenAI Batch API.</p>
        <div id="llm-stats" class="text-sm text-gray-600 mb-4"></div>
        ${pipeline.is_owner ? `
        <div class="flex flex-wrap gap-2 mb-4">
          <button data-phase="estimate" class="llm-phase-btn px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 transition">💰 Estimate Cost</button>
          <button data-phase="prepare" class="llm-phase-btn px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 transition">📄 Prepare</button>
          <button data-phase="submit" class="llm-phase-btn px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 transition">🚀 Submit</button>
          <button data-phase="poll" class="llm-phase-btn px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 transition">🔄 Poll</button>
          <button data-phase="save" class="llm-phase-btn px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 transition">💾 Save Results</button>
          <button data-phase="run" class="llm-phase-btn px-4 py-2 bg-purple-600 text-white rounded-lg text-sm hover:bg-purple-700 transition">▶ Run All</button>
        </div>
        <div class="grid grid-cols-2 gap-3 mb-4">
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Limit (blank = all)</label>
            <input id="llm-limit" type="number" placeholder="e.g. 100" min="1"
                   class="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-purple-400">
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Poll interval (sec)</label>
            <input id="llm-interval" type="number" value="60" min="10"
                   class="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-purple-400">
          </div>
        </div>` : '<p class="text-gray-400 text-sm italic">View only — you are not the owner.</p>'}
      </div>
      <div id="llm-terminal-wrap" class="hidden bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div class="bg-gray-800 px-4 py-2 flex justify-between items-center">
          <span id="llm-job-label" class="text-gray-300 text-sm font-mono"></span>
          <span id="llm-job-status" class="text-xs px-2 py-0.5 rounded bg-gray-700 text-gray-300"></span>
        </div>
        <div id="llm-output" class="terminal bg-gray-900 text-green-400 p-4 h-80 overflow-y-auto whitespace-pre-wrap"></div>
      </div>
    </div>`;
}

function initLLMStage(pipeline) {
  if (!pipeline.is_owner) return;
  document.querySelectorAll('.llm-phase-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const phase = btn.dataset.phase;
      const limit = el('llm-limit').value;
      const interval = el('llm-interval').value || '60';
      const args = {
        '--db': pipeline.output_db_path,
        '--batch-dir': pipeline.batch_state_path ? pipeline.batch_state_path.replace('/state.json', '') : undefined,
      };
      if (limit) args['--limit'] = limit;
      if (['poll', 'run'].includes(phase)) args['--interval'] = interval;

      const res = await apiFetch('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          script: 'llm_classify',
          pipeline_id: pipeline.id,
          args: { [phase]: true, ...args },
        }),
      });
      const job = await res.json();
      if (!res.ok) { alert(job.error); return; }
      startJobStream('llm', job.id, pipeline);
    });
  });
}

// ── Job streaming ─────────────────────────────────────────────────────────────

function startJobStream(prefix, jobId, pipeline, onDone) {
  disconnectSSE();
  S.activeJobId = jobId;
  S.outputLines = [];

  // Poll pipeline status every 3s while job is running
  clearPollTimer();
  S.pollTimer = setInterval(() => loadPipelineStatus(pipeline.id), 3000);

  const termWrapId = `${prefix}-terminal-wrap`;
  const outputId = `${prefix}-output`;
  const labelId = `${prefix}-job-label`;
  const statusId = `${prefix}-job-status`;
  const killBtnId = `btn-kill-${prefix}`;

  show(termWrapId);
  const outputEl = el(outputId);
  if (outputEl) outputEl.textContent = '';
  if (el(labelId)) el(labelId).textContent = `job ${jobId}`;
  if (el(statusId)) el(statusId).textContent = 'running';
  if (el(killBtnId)) show(killBtnId);

  function appendLine(text) {
    if (!outputEl) return;
    outputEl.textContent += stripAnsi(text) + '\n';
    if (S.autoScroll) outputEl.scrollTop = outputEl.scrollHeight;
  }

  function connectSSE(afterSeq = -1) {
    const es = new EventSource(`/api/jobs/${jobId}/stream?after=${afterSeq}`);
    S.sseSource = es;
    let lastSeq = afterSeq;

    es.addEventListener('line', e => {
      try {
        const d = JSON.parse(e.data.replace(/'/g, '"'));
        if (d.seq > lastSeq) {
          appendLine(d.text);
          lastSeq = d.seq;
        }
      } catch (_) {
        appendLine(e.data);
      }
    });

    es.addEventListener('done', e => {
      es.close();
      S.sseSource = null;
      try {
        const d = JSON.parse(e.data.replace(/'/g, '"'));
        const statusEl = el(statusId);
        if (statusEl) {
          statusEl.textContent = d.status;
          statusEl.className = `text-xs px-2 py-0.5 rounded ${d.status === 'done' ? 'bg-green-700 text-green-100' : 'bg-red-700 text-red-100'}`;
        }
      } catch (_) {}
      if (el(killBtnId)) hide(killBtnId);
      clearPollTimer();
      if (pipeline?.id) loadPipelineStatus(pipeline.id);
      if (onDone) onDone();
    });

    es.onerror = () => {
      es.close();
      S.sseSource = null;
      // Check if job is still running, reconnect if so
      setTimeout(async () => {
        const r = await fetch(`/api/jobs/${jobId}`);
        if (r.ok) {
          const j = await r.json();
          if (j.status === 'running') connectSSE(lastSeq);
        }
      }, 3000);
    };
  }

  connectSSE();
}

function disconnectSSE() {
  if (S.sseSource) {
    S.sseSource.close();
    S.sseSource = null;
  }
  S.activeJobId = null;
}

// ── Pipeline status polling ───────────────────────────────────────────────────

function clearPollTimer() {
  if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
}

async function loadPipelineStatus(pipelineId) {
  try {
    const res = await apiFetch(`/api/pipelines/${pipelineId}/status`);
    const status = await res.json();
    S.pipelineStatus = status;
    updateStatusDisplay(status);
  } catch (_) {}
}

function updateStatusDisplay(status) {
  // Update search progress bar if visible
  const progBar = el('search-progress-bar');
  if (progBar && status.search_progress) {
    show('search-progress-bar');
    const p = status.search_progress;
    el('search-prog-label').textContent = `${p.processed} / ${p.total} files processed`;
    el('search-prog-pct').textContent = `${p.percent}%`;
    el('search-prog-fill').style.width = `${p.percent}%`;
  }

  // Update LLM stats if visible
  const llmStats = el('llm-stats');
  if (llmStats && (status.record_count !== undefined)) {
    llmStats.innerHTML = `
      <span><strong>${(status.record_count || 0).toLocaleString()}</strong> total records</span>
      <span class="mx-2">·</span>
      <span><strong>${(status.llm_done || 0).toLocaleString()}</strong> LLM classified</span>`;
  }

  // Update DB stats in review stage
  const dbStats = el('db-stats');
  if (dbStats) {
    dbStats.innerHTML = `<span><strong>${(status.record_count || 0).toLocaleString()}</strong> records · ${status.db_size_mb || 0} MB</span>`;
  }
}

// ── Job Monitor ───────────────────────────────────────────────────────────────

let _jobsRefreshTimer = null;

el('nav-jobs').addEventListener('click', () => showJobsView());

el('btn-refresh-jobs').addEventListener('click', () => loadJobs());

function showJobsView() {
  S.view = 'jobs';
  clearPollTimer();
  disconnectSSE();
  hide('view-home');
  hide('view-pipeline');
  show('view-jobs');
  loadJobs();
  // Auto-refresh every 5s while on this view
  if (_jobsRefreshTimer) clearInterval(_jobsRefreshTimer);
  _jobsRefreshTimer = setInterval(() => {
    if (S.view === 'jobs') loadJobs();
    else { clearInterval(_jobsRefreshTimer); _jobsRefreshTimer = null; }
  }, 5000);
}

async function loadJobs() {
  try {
    const [jobsRes, pipRes] = await Promise.all([
      apiFetch('/api/jobs'),
      apiFetch('/api/pipelines'),
    ]);
    const jobs = await jobsRes.json();
    const pipelines = await pipRes.json();
    renderJobsTable(jobs, pipelines);
    updateJobsBadge(jobs);
  } catch (err) {
    el('jobs-table-wrap').innerHTML = `<div class="text-red-500 p-4">${escHtml(err.message)}</div>`;
  }
}

function updateJobsBadge(jobs) {
  const running = jobs.filter(j => j.status === 'running').length;
  const badge = el('nav-jobs-badge');
  if (running > 0) {
    badge.textContent = running;
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }
}

function renderJobsTable(jobs, pipelines) {
  const wrap = el('jobs-table-wrap');
  const pipelineMap = Object.fromEntries(pipelines.map(p => [p.id, p.name]));

  if (!jobs.length) {
    wrap.innerHTML = `<div class="bg-white rounded-xl border border-gray-200 p-12 text-center text-gray-400">
      <p class="text-lg mb-1">No jobs recorded</p>
      <p class="text-sm">Jobs appear here when pipeline scripts are launched.</p>
    </div>`;
    return;
  }

  const STATUS_CLASSES = {
    running: 'bg-yellow-100 text-yellow-800',
    done:    'bg-green-100 text-green-800',
    error:   'bg-red-100 text-red-800',
    killed:  'bg-gray-100 text-gray-600',
  };

  const rows = jobs.slice().sort((a, b) => (b.started_at || 0) - (a.started_at || 0)).map(job => {
    const pname = job.pipeline_id ? (pipelineMap[job.pipeline_id] || `#${job.pipeline_id}`) : '—';
    const statusCls = STATUS_CLASSES[job.status] || 'bg-gray-100 text-gray-600';
    const elapsed = job.ended_at
      ? `${Math.round(job.ended_at - job.started_at)}s`
      : job.started_at ? `${Math.round(Date.now() / 1000 - job.started_at)}s` : '—';
    const started = job.started_at ? new Date(job.started_at * 1000).toLocaleTimeString() : '—';
    const coresCell = (job.workers && job.workers > 1)
      ? `<span class="text-xs font-medium text-blue-700">${job.workers} cores</span>`
      : `<span class="text-xs text-gray-400">1</span>`;
    const killBtn = job.status === 'running'
      ? `<button class="btn-kill-job text-xs text-red-600 border border-red-200 px-2 py-1 rounded hover:bg-red-50 transition" data-id="${escHtml(job.id)}">Kill</button>`
      : '';
    return `<tr class="border-b border-gray-100 hover:bg-blue-50 cursor-pointer job-log-row" data-jobid="${escHtml(job.id)}">
      <td class="px-4 py-3 text-sm font-mono text-gray-500">${escHtml(job.id)}</td>
      <td class="px-4 py-3 text-sm">${escHtml(pname)}</td>
      <td class="px-4 py-3 text-sm font-medium">${escHtml(job.label || job.script)}</td>
      <td class="px-4 py-3"><span class="text-xs px-2 py-0.5 rounded-full font-medium ${statusCls}">${escHtml(job.status)}</span></td>
      <td class="px-4 py-3 text-sm text-center">${coresCell}</td>
      <td class="px-4 py-3 text-sm text-gray-500">${escHtml(String(job.pid || '—'))}</td>
      <td class="px-4 py-3 text-sm text-gray-500">${started}</td>
      <td class="px-4 py-3 text-sm text-gray-500">${elapsed}</td>
      <td class="px-4 py-3">${killBtn}</td>
    </tr>`;
  }).join('');

  wrap.innerHTML = `
    <div class="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <table class="w-full text-left">
        <thead class="bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-600 uppercase tracking-wide">
          <tr>
            <th class="px-4 py-3">Job ID</th>
            <th class="px-4 py-3">Pipeline</th>
            <th class="px-4 py-3">Script</th>
            <th class="px-4 py-3">Status</th>
            <th class="px-4 py-3 text-center">Cores</th>
            <th class="px-4 py-3">PID</th>
            <th class="px-4 py-3">Started</th>
            <th class="px-4 py-3">Duration</th>
            <th class="px-4 py-3"></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;

  wrap.querySelectorAll('.btn-kill-job').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm('Kill this job?')) return;
      const res = await apiFetch(`/api/jobs/${btn.dataset.id}`, { method: 'DELETE' });
      if (!res.ok) { const d = await res.json(); alert(d.error); return; }
      loadJobs();
    });
  });

  // Job row click → show logs panel
  wrap.querySelectorAll('.job-log-row').forEach(row => {
    row.addEventListener('click', e => {
      if (e.target.closest('button')) return;
      const jobId = row.dataset.jobid;
      showJobLogs(jobId);
    });
  });
}

// ── Job log viewer ────────────────────────────────────────────────────────────

async function showJobLogs(jobId) {
  const output = el('jobs-log-output');
  const title = el('jobs-log-title');
  const statusEl = el('jobs-log-status');
  show('jobs-log-panel');
  output.textContent = 'Loading...';

  // Fetch current job info
  const res = await apiFetch(`/api/jobs/${jobId}`);
  const job = await res.json();
  if (!res.ok) { output.textContent = job.error; return; }
  title.textContent = `${job.label || job.script} (${jobId})`;
  statusEl.textContent = job.status;
  statusEl.className = `text-xs px-2 py-0.5 rounded ${job.status === 'running' ? 'bg-yellow-700 text-yellow-100' : job.status === 'done' ? 'bg-green-700 text-green-100' : 'bg-gray-700 text-gray-300'}`;

  // Use SSE stream to get output
  output.textContent = '';
  if (S.sseSource) { S.sseSource.close(); S.sseSource = null; }
  const es = new EventSource(`/api/jobs/${jobId}/stream?after=-1`);
  S.sseSource = es;
  es.addEventListener('line', e => {
    try {
      const d = JSON.parse(e.data.replace(/'/g, '"'));
      output.textContent += stripAnsi(d.text) + '\n';
      output.scrollTop = output.scrollHeight;
    } catch { output.textContent += e.data + '\n'; }
  });
  es.addEventListener('done', e => {
    es.close(); S.sseSource = null;
    try {
      const d = JSON.parse(e.data.replace(/'/g, '"'));
      statusEl.textContent = d.status;
      statusEl.className = `text-xs px-2 py-0.5 rounded ${d.status === 'done' ? 'bg-green-700 text-green-100' : 'bg-red-700 text-red-100'}`;
    } catch {}
    loadJobs(); // refresh table
  });
  es.onerror = () => { es.close(); S.sseSource = null; };
}

el('btn-close-log').addEventListener('click', () => {
  hide('jobs-log-panel');
  if (S.sseSource) { S.sseSource.close(); S.sseSource = null; }
});

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  try {
    const res = await fetch('/api/auth/me');
    if (res.ok) {
      const me = await res.json();
      S.me = me;
      el('nav-username').textContent = me.username;
      await loadHome();
      show('view-home');
    } else {
      show('login-overlay');
    }
  } catch (_) {
    show('login-overlay');
  }
}

init();
