/**
 * OpenAlex Dashboard — Frontend Application
 * Single-page app: tab switching, SSE job streaming, DB browser, log viewer.
 */

// ═══════════════════════════════════════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════════════════════════════════════

const S = {
  activeTab: 'dashboard',
  // Active database (rel_path from BASE_DIR, '' = default)
  activeDb: '',
  // Job streaming
  activeJobId: null,
  sseSource: null,
  outputLines: [],   // { seq, text }
  autoScroll: true,
  elapsedTimer: null,
  // DB browser
  db: { page: 1, sort: 'publication_year', dir: 'desc', search: '', total: 0 },
  sqlResult: null,   // { columns, rows } — last query result for CSV export
  // Log viewer
  logTimer: null,
  // Dashboard polling
  dashTimer: null,
};

// ═══════════════════════════════════════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  if (!window.__AUTHENTICATED__) {
    document.getElementById('login-overlay').classList.remove('hidden');
    document.getElementById('login-password').focus();
    return;
  }
  boot();
});

function boot() {
  bindScrollDetection();
  loadDbList();
  showTab('dashboard');
  startDashPolling();
}

// ═══════════════════════════════════════════════════════════════════════════════
// LOGIN
// ═══════════════════════════════════════════════════════════════════════════════

async function doLogin() {
  const pw = document.getElementById('login-password').value;
  const err = document.getElementById('login-error');
  err.classList.add('hidden');

  const res = await fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: pw }),
  });
  if (res.ok) {
    document.getElementById('login-overlay').classList.add('hidden');
    window.__AUTHENTICATED__ = true;
    boot();
  } else {
    err.classList.remove('hidden');
    document.getElementById('login-password').select();
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// TABS
// ═══════════════════════════════════════════════════════════════════════════════

function showTab(name) {
  S.activeTab = name;

  // Toggle tab content visibility
  for (const el of document.querySelectorAll('[id^="tab-"]')) {
    el.classList.add('hidden');
  }
  document.getElementById(`tab-${name}`).classList.remove('hidden');

  // Update nav button styles
  for (const btn of document.querySelectorAll('.tab-btn')) {
    btn.classList.toggle('tab-active', btn.dataset.tab === name);
  }

  // Load data for the active tab
  if (name === 'dashboard') loadDashboard();
  if (name === 'database')  loadDbRecords(S.db.page);
  if (name === 'report')    /* iframe loads itself */ void 0;
}

// ═══════════════════════════════════════════════════════════════════════════════
// DATABASE SELECTOR
// ═══════════════════════════════════════════════════════════════════════════════

async function loadDbList() {
  const dbs = await apiFetch('/api/db/list').catch(() => []);
  const sel = document.getElementById('db-selector');
  if (!sel) return;
  if (!dbs.length) {
    sel.innerHTML = '<option value="">No databases found</option>';
    return;
  }
  sel.innerHTML = dbs.map(db =>
    `<option value="${esc(db.rel_path)}"${db.rel_path === S.activeDb ? ' selected' : ''}>` +
    `${esc(db.name)} (${db.size_mb} MB)</option>`
  ).join('');
  // Auto-select default DB if nothing chosen yet
  if (!S.activeDb && dbs.length) {
    S.activeDb = dbs[0].rel_path;
    sel.value = S.activeDb;
  }
}

function selectActiveDb(relPath) {
  S.activeDb = relPath;
  S.db.page = 1;
  if (S.activeTab === 'dashboard') loadDashboard();
  if (S.activeTab === 'database')  loadDbRecords(1);
}

function _dbParam() {
  return S.activeDb ? `db=${encodeURIComponent(S.activeDb)}` : '';
}

function _appendDb(url) {
  const p = _dbParam();
  if (!p) return url;
  return url + (url.includes('?') ? '&' : '?') + p;
}

// ═══════════════════════════════════════════════════════════════════════════════
// DASHBOARD
// ═══════════════════════════════════════════════════════════════════════════════

async function loadDashboard() {
  try {
    const data = await apiFetch(_appendDb('/api/status'));
    renderStatusCards(data);
    renderPipelineState(data.pipeline_state);
    renderBatchState(data.batch_state);
    renderActiveJobs(data.jobs || []);
  } catch (_) {}
}

function startDashPolling() {
  clearInterval(S.dashTimer);
  S.dashTimer = setInterval(() => {
    if (S.activeTab === 'dashboard') loadDashboard();
    refreshJobPills();
  }, 8000);
}

function renderStatusCards(data) {
  const db = data.db || {};
  document.getElementById('sc-records').textContent =
    db.record_count != null ? db.record_count.toLocaleString() : '—';
  document.getElementById('sc-llm').textContent =
    db.llm_processed_count != null ? db.llm_processed_count.toLocaleString() : '—';
  document.getElementById('sc-dbsize').textContent =
    db.size_mb != null ? `${db.size_mb} MB` : '—';
  document.getElementById('sc-diskfree').textContent =
    data.disk_free_gb != null ? `${data.disk_free_gb} GB` : '—';
}

function renderPipelineState(ps) {
  const el = document.getElementById('pipeline-state-content');
  if (!ps || Object.keys(ps).length === 0) {
    el.innerHTML = '<span class="text-gray-400">No pipeline_state.json found</span>';
    return;
  }
  const done = ps.files_done || ps.files_processed || 0;
  const total = ps.total_files || ps.files_total || 0;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const seen = ps.records_seen || ps.total_seen || 0;
  const matched = ps.records_matched || ps.total_matched || 0;

  el.innerHTML = `
    <div class="mb-2">
      <div class="flex justify-between text-xs text-gray-500 mb-1">
        <span>${done.toLocaleString()} / ${total.toLocaleString()} files</span>
        <span>${pct}%</span>
      </div>
      <div class="w-full bg-gray-200 rounded-full h-2">
        <div class="bg-blue-500 h-2 rounded-full" style="width:${pct}%"></div>
      </div>
    </div>
    <div class="grid grid-cols-2 gap-2 text-xs mt-3">
      <div><span class="text-gray-400">Records seen:</span> <b>${seen.toLocaleString()}</b></div>
      <div><span class="text-gray-400">Matched:</span> <b class="text-green-600">${matched.toLocaleString()}</b></div>
    </div>
    ${ps.last_hit ? `<div class="mt-2 text-xs text-gray-400 truncate" title="${esc(ps.last_hit)}">Last hit: ${esc(ps.last_hit.substring(0, 80))}</div>` : ''}
  `;
}

function renderBatchState(bs) {
  const el = document.getElementById('batch-state-content');
  if (!bs || Object.keys(bs).length === 0) {
    el.innerHTML = '<span class="text-gray-400">No batch state found</span>';
    return;
  }
  const statusColor = {
    completed: 'text-green-600', failed: 'text-red-600',
    in_progress: 'text-blue-600 pulse', pending: 'text-yellow-600',
  }[bs.status] || 'text-gray-600';

  el.innerHTML = `
    <div class="text-xs space-y-1.5">
      <div><span class="text-gray-400">Status:</span>
           <span class="font-semibold ${statusColor} ml-1">${bs.status || '—'}</span></div>
      <div><span class="text-gray-400">Batch ID:</span>
           <code class="ml-1 text-xs bg-gray-100 px-1 rounded">${bs.batch_id ? bs.batch_id.substring(0, 24) + '…' : '—'}</code></div>
      <div><span class="text-gray-400">Records:</span> <b class="ml-1">${(bs.record_count || 0).toLocaleString()}</b></div>
      ${bs.prepared_at ? `<div><span class="text-gray-400">Prepared:</span> <span class="ml-1">${bs.prepared_at.substring(0, 16)}</span></div>` : ''}
      ${bs.completed_at ? `<div><span class="text-gray-400">Completed:</span> <span class="ml-1">${bs.completed_at.substring(0, 16)}</span></div>` : ''}
    </div>
  `;
}

function renderActiveJobs(jobs) {
  const el = document.getElementById('active-jobs-content');
  const active = jobs.filter(j => j.status === 'running');
  if (active.length === 0) {
    el.innerHTML = '<span class="text-gray-400 text-sm">No active jobs</span>';
    return;
  }
  el.innerHTML = `
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead><tr class="text-xs text-gray-500 text-left">
          <th class="pb-2 pr-4">Script</th>
          <th class="pb-2 pr-4">PID</th>
          <th class="pb-2 pr-4">Elapsed</th>
          <th class="pb-2"></th>
        </tr></thead>
        <tbody>
          ${active.map(j => `
            <tr class="border-t">
              <td class="py-2 pr-4 font-mono text-xs text-gray-800 max-w-xs truncate">${esc(j.label)}</td>
              <td class="py-2 pr-4 text-gray-500">${j.pid}</td>
              <td class="py-2 pr-4 text-gray-500">${fmtElapsed(j.started_at)}</td>
              <td class="py-2">
                <button onclick="showTab('scripts'); selectJob('${j.id}')"
                        class="text-xs text-blue-600 hover:underline mr-3">View Output</button>
              </td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>
  `;
}

// ═══════════════════════════════════════════════════════════════════════════════
// SCRIPTS — ACCORDION
// ═══════════════════════════════════════════════════════════════════════════════

function toggleAccordion(btn) {
  const body = btn.nextElementSibling;
  const isOpen = !body.classList.contains('hidden');
  body.classList.toggle('hidden', isOpen);
  btn.closest('.accordion-item').classList.toggle('accordion-open', !isOpen);
}

function updateBatchFields() {
  const selected = document.querySelector('#batch-subcommand-group input[name="subcommand"]:checked');
  if (!selected) return;
  const val = selected.value;
  const limitField = document.getElementById('batch-limit-field');
  const intervalField = document.getElementById('batch-interval-field');
  limitField.style.opacity    = ['prepare', 'run'].includes(val) ? '1' : '0.35';
  intervalField.style.opacity = ['poll', 'run'].includes(val)    ? '1' : '0.35';
}

// ═══════════════════════════════════════════════════════════════════════════════
// SCRIPTS — LAUNCH
// ═══════════════════════════════════════════════════════════════════════════════

function handleScriptSubmit(event) {
  event.preventDefault();
  const form = event.target;
  const scriptKey = form.dataset.script;
  const args = {};

  // Collect form inputs
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.name === 'subcommand') {
      if (el.type === 'radio' && el.checked) args['subcommand'] = el.value;
    } else if (el.name.startsWith('--')) {
      if (el.type === 'checkbox') {
        args[el.name] = el.checked ? 'true' : 'false';
      } else {
        args[el.name] = el.value;
      }
    }
  }

  launchJob(scriptKey, args);
}

async function launchJob(scriptKey, args) {
  try {
    const data = await apiFetch('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ script: scriptKey, args }),
    });
    if (data.error) {
      toast(data.error, 'red');
      return;
    }
    toast(`Launched: ${data.label}`, 'green');
    selectJob(data.id);
    refreshJobPills();
  } catch (err) {
    toast(`Launch failed: ${err.message}`, 'red');
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// SCRIPTS — JOB OUTPUT & SSE
// ═══════════════════════════════════════════════════════════════════════════════

function selectJob(jobId) {
  if (S.sseSource) {
    S.sseSource.close();
    S.sseSource = null;
  }
  clearInterval(S.elapsedTimer);
  S.activeJobId = jobId;
  S.outputLines = [];
  S.autoScroll = true;

  const outputEl = document.getElementById('job-output');
  outputEl.textContent = '';

  connectSSE(jobId, -1);
}

function connectSSE(jobId, afterSeq) {
  const url = `/api/jobs/${jobId}/stream?after=${afterSeq}`;
  const es = new EventSource(url);
  S.sseSource = es;

  es.addEventListener('line', (e) => {
    const d = JSON.parse(e.data);
    appendOutputLine(d.seq, d.text);
    updateJobHeader(jobId);
  });

  es.addEventListener('done', (e) => {
    const d = JSON.parse(e.data);
    es.close();
    S.sseSource = null;
    clearInterval(S.elapsedTimer);
    updateJobHeaderFinal(jobId, d);
    refreshJobPills();
    toast(`Job ${d.status}: ${jobId}`, d.status === 'done' ? 'green' : 'red');
  });

  es.onerror = () => {
    es.close();
    S.sseSource = null;
    // Reconnect after 3s if job might still be running
    const lastSeq = S.outputLines.length ? S.outputLines[S.outputLines.length - 1].seq : afterSeq;
    setTimeout(async () => {
      const job = await apiFetch(`/api/jobs/${jobId}`).catch(() => null);
      if (job && job.status === 'running') {
        connectSSE(jobId, lastSeq);
      }
    }, 3000);
  };

  // Start elapsed timer
  clearInterval(S.elapsedTimer);
  S.elapsedTimer = setInterval(() => updateJobHeader(jobId), 1000);

  updateJobHeader(jobId);
  document.getElementById('job-header').classList.remove('hidden');
}

function appendOutputLine(seq, text) {
  const stripped = stripAnsi(text);
  S.outputLines.push({ seq, text: stripped });

  const pre = document.getElementById('job-output');
  const line = document.createTextNode(stripped + '\n');
  pre.appendChild(line);

  if (S.autoScroll) {
    const container = document.getElementById('job-output-container');
    container.scrollTop = container.scrollHeight;
  }
}

async function updateJobHeader(jobId) {
  const job = await apiFetch(`/api/jobs/${jobId}`).catch(() => null);
  if (!job) return;

  document.getElementById('job-label').textContent = job.label;
  document.getElementById('job-pid').textContent = `PID ${job.pid}`;

  const badge = document.getElementById('job-status-badge');
  const statusClasses = {
    running: 'status-running', done: 'status-done',
    error: 'status-error',     killed: 'status-killed',
  };
  badge.className = statusClasses[job.status] || '';
  badge.textContent = job.status === 'running' ? '⬤ running' : job.status;

  const elapsed = job.started_at ? fmtElapsed(job.started_at) : '';
  document.getElementById('job-elapsed').textContent = elapsed;

  const killBtn = document.getElementById('job-kill-btn');
  killBtn.classList.toggle('hidden', job.status !== 'running');
}

function updateJobHeaderFinal(jobId, doneData) {
  const badge = document.getElementById('job-status-badge');
  const statusClasses = { done: 'status-done', error: 'status-error', killed: 'status-killed' };
  badge.className = statusClasses[doneData.status] || '';
  badge.textContent = doneData.status;
  document.getElementById('job-elapsed').textContent =
    doneData.elapsed != null ? `(${fmtSecs(doneData.elapsed)})` : '';
  document.getElementById('job-kill-btn').classList.add('hidden');
}

async function killActiveJob() {
  if (!S.activeJobId) return;
  if (!confirm('Kill this process and all its children?')) return;
  try {
    await apiFetch(`/api/jobs/${S.activeJobId}`, { method: 'DELETE' });
    toast('Kill signal sent', 'orange');
  } catch (err) {
    toast(`Kill failed: ${err.message}`, 'red');
  }
}

// ── Recent job pills ──────────────────────────────────────────────────────────

async function refreshJobPills() {
  const jobs = await apiFetch('/api/jobs').catch(() => []);
  const list = document.getElementById('recent-jobs-list');
  if (!list) return;

  const recent = jobs.slice(-10).reverse();
  list.innerHTML = recent.map(j => {
    const color = {
      running: 'bg-green-100 text-green-800 border-green-300',
      done:    'bg-blue-100 text-blue-800 border-blue-300',
      error:   'bg-red-100 text-red-800 border-red-300',
      killed:  'bg-orange-100 text-orange-800 border-orange-300',
    }[j.status] || 'bg-gray-100 text-gray-700 border-gray-300';
    const icon = { running: '⬤', done: '✓', error: '✕', killed: '⊘' }[j.status] || '?';
    const active = j.id === S.activeJobId ? ' ring-2 ring-offset-1 ring-blue-400' : '';
    return `<button onclick="selectJob('${j.id}')"
             class="border rounded-full px-3 py-1 text-xs font-medium ${color}${active} hover:opacity-80 transition max-w-[200px] truncate"
             title="${esc(j.label)}">
              ${icon} ${esc(j.script)}
            </button>`;
  }).join('');
}

// ═══════════════════════════════════════════════════════════════════════════════
// DATABASE BROWSER
// ═══════════════════════════════════════════════════════════════════════════════

async function loadDbRecords(page) {
  if (page != null) S.db.page = page;
  S.db.search = document.getElementById('db-search').value.trim();
  S.db.sort   = document.getElementById('db-sort').value;
  S.db.dir    = document.getElementById('db-dir').value;

  const params = new URLSearchParams({
    page: S.db.page, sort: S.db.sort, dir: S.db.dir, q: S.db.search,
  });
  if (S.activeDb) params.set('db', S.activeDb);

  const data = await apiFetch(`/api/db/records?${params}`).catch(e => ({ error: e.message }));
  if (data.error) { toast(data.error, 'red'); return; }

  S.db.total = data.total;
  renderDbTable(data);
  renderDbPagination(data);
}

function renderDbTable(data) {
  const thead = document.getElementById('db-table-head');
  const tbody = document.getElementById('db-table-body');

  const colLabels = {
    openalex_id: 'ID', doi: 'DOI', title: 'Title',
    publication_year: 'Year', fwci: 'FWCI',
    primary_location_source_display_name: 'Journal',
    authors_countries: 'Countries',
    matched_fuel_terms: 'Fuel Terms',
    matched_impact_terms: 'Impact Terms',
    llm_fuel_classification: 'LLM Class.',
    llm_is_hydrocarbon: 'Hydrocarbon',
    llm_confidence: 'LLM Conf.',
  };

  thead.innerHTML = data.columns.map(c =>
    `<th class="px-3 py-2.5 text-xs font-semibold text-gray-600 text-left whitespace-nowrap cursor-pointer hover:text-blue-700 select-none"
         onclick="sortDb('${c}')">${colLabels[c] || c}</th>`
  ).join('') + '<th class="px-3 py-2.5"></th>';

  if (!data.rows || data.rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="13" class="text-center py-12 text-gray-400">No records found</td></tr>';
    return;
  }

  tbody.innerHTML = data.rows.map((row, ri) => {
    const oaId = row[0]; // openalex_id is first column
    const cells = row.map((val, ci) =>
      `<td class="px-3 py-2 max-w-xs">${renderCell(data.columns[ci], val)}</td>`
    ).join('');
    return `<tr class="hover:bg-blue-50 cursor-pointer transition" onclick="showRecordModal('${esc(oaId)}')">
              ${cells}
              <td class="px-3 py-2 text-xs text-blue-500">›</td>
            </tr>`;
  }).join('');
}

function renderCell(col, val) {
  if (val === null || val === undefined) return '<span class="text-gray-300">—</span>';
  const s = String(val);

  if (col === 'openalex_id') {
    const short = s.replace('https://openalex.org/', '');
    return `<a href="${esc(s)}" target="_blank" onclick="event.stopPropagation()"
               class="text-blue-600 hover:underline text-xs font-mono">${esc(short)}</a>`;
  }
  if (col === 'doi') {
    return s ? `<a href="https://doi.org/${esc(s)}" target="_blank" onclick="event.stopPropagation()"
                   class="text-blue-600 hover:underline text-xs">${esc(s.substring(0, 30))}</a>` : '—';
  }
  if (col === 'title') {
    return `<span class="text-sm" title="${esc(s)}">${esc(s.substring(0, 70))}${s.length > 70 ? '…' : ''}</span>`;
  }
  if (col === 'fwci') {
    return s && val !== null ? `<span class="font-mono text-xs">${parseFloat(val).toFixed(2)}</span>` : '—';
  }
  if (col === 'llm_is_hydrocarbon') {
    if (val === 1) return '<span class="text-green-600 font-semibold text-xs">Yes</span>';
    if (val === 0) return '<span class="text-red-500 text-xs">No</span>';
    return '<span class="text-gray-300">—</span>';
  }
  if (['matched_fuel_terms','matched_impact_terms','authors_countries','llm_fuel_classification'].includes(col)) {
    try {
      const arr = JSON.parse(s);
      if (Array.isArray(arr) && arr.length) {
        const shown = arr.slice(0, 3).join(', ');
        return `<span class="text-xs" title="${esc(arr.join(', '))}">${esc(shown)}${arr.length > 3 ? ` +${arr.length - 3}` : ''}</span>`;
      }
    } catch(_) {}
    return `<span class="text-xs text-gray-500">${esc(s.substring(0, 50))}</span>`;
  }
  if (col === 'primary_location_source_display_name') {
    return `<span class="text-xs" title="${esc(s)}">${esc(s.substring(0, 40))}${s.length > 40 ? '…' : ''}</span>`;
  }
  return `<span class="text-xs">${esc(s.substring(0, 60))}</span>`;
}

function sortDb(col) {
  if (S.db.sort === col) {
    S.db.dir = S.db.dir === 'desc' ? 'asc' : 'desc';
  } else {
    S.db.sort = col;
    S.db.dir = 'desc';
  }
  document.getElementById('db-sort').value = col;
  document.getElementById('db-dir').value = S.db.dir;
  loadDbRecords(1);
}

function renderDbPagination(data) {
  const totalPages = Math.max(1, Math.ceil(data.total / data.per_page));
  const from = (data.page - 1) * data.per_page + 1;
  const to   = Math.min(data.page * data.per_page, data.total);
  document.getElementById('db-pagination-info').textContent =
    data.total > 0 ? `${from}–${to} of ${data.total.toLocaleString()} records` : 'No records';

  document.getElementById('db-prev-btn').disabled = data.page <= 1;
  document.getElementById('db-next-btn').disabled = data.page >= totalPages;
}

function dbChangePage(delta) {
  S.db.page = Math.max(1, S.db.page + delta);
  loadDbRecords(S.db.page);
}

// ── Record detail modal ───────────────────────────────────────────────────────

async function showRecordModal(rawId) {
  const id = rawId.startsWith('http') ? rawId : `https://openalex.org/${rawId}`;
  const data = await apiFetch(_appendDb(`/api/db/records/${encodeURIComponent(id)}`)).catch(() => null);
  if (!data || data.error) { toast('Could not load record', 'red'); return; }

  const content = document.getElementById('record-modal-content');
  const rows = Object.entries(data).map(([k, v]) => {
    let display;
    if (v === null || v === undefined) {
      display = '<span class="text-gray-400">null</span>';
    } else if (typeof v === 'string' && v.startsWith('[')) {
      try {
        const arr = JSON.parse(v);
        display = `<code class="text-xs bg-gray-100 px-1 rounded">${esc(JSON.stringify(arr, null, 2))}</code>`;
      } catch(_) {
        display = esc(v);
      }
    } else {
      const sv = String(v);
      if (k === 'doi') {
        display = `<a href="https://doi.org/${esc(sv)}" target="_blank" class="text-blue-600 hover:underline">${esc(sv)}</a>`;
      } else if (k === 'openalex_id' && sv.startsWith('http')) {
        display = `<a href="${esc(sv)}" target="_blank" class="text-blue-600 hover:underline">${esc(sv)}</a>`;
      } else if (k === 'open_access_oa_url' || k === 'best_oa_location_pdf_url') {
        display = sv ? `<a href="${esc(sv)}" target="_blank" class="text-blue-600 hover:underline text-xs">${esc(sv.substring(0,60))}…</a>` : '—';
      } else if (k === 'abstract_text' || k === 'full_text') {
        display = `<div class="max-h-40 overflow-y-auto text-xs text-gray-600 border rounded p-2 bg-gray-50">${esc(sv)}</div>`;
      } else {
        display = `<span class="text-xs">${esc(sv.substring(0, 300))}</span>`;
      }
    }
    return `<tr class="border-b last:border-0">
      <td class="py-1.5 pr-4 text-xs font-mono text-gray-500 whitespace-nowrap align-top w-64">${esc(k)}</td>
      <td class="py-1.5 text-gray-800 align-top break-words">${display}</td>
    </tr>`;
  }).join('');

  content.innerHTML = `<table class="w-full"><tbody>${rows}</tbody></table>`;
  document.getElementById('record-modal').classList.remove('hidden');
}

function closeRecordModal() {
  document.getElementById('record-modal').classList.add('hidden');
}

// Close modal on backdrop click
document.addEventListener('click', (e) => {
  if (e.target.id === 'record-modal') closeRecordModal();
});

// ── SQL query ─────────────────────────────────────────────────────────────────

async function runSqlQuery() {
  const sql = document.getElementById('sql-input').value.trim();
  if (!sql) return;

  const errEl = document.getElementById('sql-error');
  const resultEl = document.getElementById('sql-results');
  errEl.classList.add('hidden');
  resultEl.innerHTML = '<p class="text-gray-400 text-sm">Running…</p>';
  document.getElementById('export-csv-btn').disabled = true;

  const data = await apiFetch(_appendDb('/api/db/query'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sql }),
  }).catch(e => ({ error: e.message }));

  if (data.error) {
    errEl.textContent = data.error;
    errEl.classList.remove('hidden');
    resultEl.innerHTML = '';
    return;
  }

  S.sqlResult = data;
  document.getElementById('export-csv-btn').disabled = false;

  if (!data.rows || data.rows.length === 0) {
    resultEl.innerHTML = '<p class="text-gray-400 text-sm">No rows returned.</p>';
    return;
  }

  const truncNote = data.truncated ? `<p class="text-yellow-600 text-xs mb-2">Results truncated to ${data.count.toLocaleString()} rows</p>` : '';
  const cols = data.columns.map(c => `<th class="px-3 py-2 text-xs font-semibold text-gray-600 text-left whitespace-nowrap border-b">${esc(c)}</th>`).join('');
  const rows = data.rows.map(r =>
    `<tr class="hover:bg-gray-50 border-b">${r.map(v =>
      `<td class="px-3 py-1.5 text-xs max-w-xs truncate">${v !== null ? esc(String(v).substring(0, 200)) : '<span class="text-gray-300">null</span>'}</td>`
    ).join('')}</tr>`
  ).join('');

  resultEl.innerHTML = `
    ${truncNote}
    <div class="overflow-x-auto max-h-96 overflow-y-auto border rounded-lg">
      <table class="w-full text-sm">
        <thead class="bg-gray-50 sticky top-0"><tr>${cols}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <p class="text-xs text-gray-400 mt-2">${data.count.toLocaleString()} row${data.count !== 1 ? 's' : ''}</p>`;
}

function exportQueryCsv() {
  if (!S.sqlResult) return;
  const { columns, rows } = S.sqlResult;
  const csvRows = [columns.join(','), ...rows.map(r => r.map(v => csvCell(v)).join(','))];
  const blob = new Blob([csvRows.join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `openalex_query_${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function csvCell(v) {
  if (v === null || v === undefined) return '';
  const s = String(v);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

// ═══════════════════════════════════════════════════════════════════════════════
// LOGS
// ═══════════════════════════════════════════════════════════════════════════════

async function loadLog() {
  const logName = document.getElementById('log-file').value;
  const lines   = document.getElementById('log-lines').value;
  const data = await apiFetch(`/api/log?log=${logName}&lines=${lines}`).catch(e => ({ error: e.message }));

  const el = document.getElementById('log-output');
  if (data.error) {
    el.textContent = `Error: ${data.error}`;
    return;
  }
  el.textContent = data.lines.join('\n') || '(empty)';
  document.getElementById('log-meta').textContent =
    `${data.lines.length} lines shown • ${fmtBytes(data.total_bytes)} total`;
  // Scroll to bottom
  el.parentElement.scrollTop = el.parentElement.scrollHeight;
}

function toggleLogRefresh() {
  const checked = document.getElementById('log-autorefresh').checked;
  clearInterval(S.logTimer);
  if (checked) {
    loadLog();
    S.logTimer = setInterval(loadLog, 10000);
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// REPORT
// ═══════════════════════════════════════════════════════════════════════════════

function reloadReport() {
  const iframe = document.getElementById('report-iframe');
  iframe.src = iframe.src; // force reload
}

async function launchGenerateReport() {
  showTab('scripts');
  await launchJob('generate_report', {
    '--db': 'openalex_works_filtered.sqlite',
    '--output': 'report.html',
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// SCROLL DETECTION
// ═══════════════════════════════════════════════════════════════════════════════

function bindScrollDetection() {
  const container = document.getElementById('job-output-container');
  if (!container) return;
  container.addEventListener('scroll', () => {
    const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 30;
    S.autoScroll = atBottom;
    document.getElementById('scroll-btn').classList.toggle('hidden', atBottom);
  });
}

function scrollToBottom() {
  const container = document.getElementById('job-output-container');
  container.scrollTop = container.scrollHeight;
  S.autoScroll = true;
  document.getElementById('scroll-btn').classList.add('hidden');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TOAST NOTIFICATIONS
// ═══════════════════════════════════════════════════════════════════════════════

let _toastTimer = null;
function toast(msg, color = 'gray') {
  const el = document.getElementById('toast');
  const colorMap = {
    green: 'bg-green-700', red: 'bg-red-700', orange: 'bg-orange-600', gray: 'bg-gray-800',
  };
  el.className = `fixed top-4 right-4 z-50 px-5 py-3 rounded-lg shadow-lg text-white text-sm font-medium ${colorMap[color] || colorMap.gray}`;
  document.getElementById('toast-msg').textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.add('hidden'), 4000);
}

// ═══════════════════════════════════════════════════════════════════════════════
// UTILITIES
// ═══════════════════════════════════════════════════════════════════════════════

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  if (res.status === 401) {
    document.getElementById('login-overlay').classList.remove('hidden');
    throw new Error('Session expired — please log in again');
  }
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch (_) {
    return text;
  }
}

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function stripAnsi(str) {
  return str
    .replace(/\x1b\[[0-9;]*[A-Za-z]/g, '')
    .replace(/\x1b[()][A-Z]/g, '')
    .replace(/\x1b\][^\x07\x1b]*(\x07|\x1b\\)/g, '')
    .replace(/\r/g, '');
}

function fmtElapsed(startedAt) {
  if (!startedAt) return '';
  const secs = Math.floor(Date.now() / 1000 - startedAt);
  return fmtSecs(secs);
}

function fmtSecs(secs) {
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  return `${h}h ${m}m`;
}

function fmtBytes(bytes) {
  if (!bytes) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}
