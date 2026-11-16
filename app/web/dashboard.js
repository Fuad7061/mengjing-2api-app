/* Console logic */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
let CONFIG = null;

/* ---- tab switching ---- */
$$('.tabs button').forEach(b => b.onclick = () => {
  $$('.tabs button').forEach(x => x.classList.toggle('active', x === b));
  ['overview', 'playground', 'keys', 'settings', 'logs', 'password'].forEach(t =>
    $('#tab-' + t).classList.toggle('hidden', t !== b.dataset.tab));
  if (b.dataset.tab === 'keys') loadKeys();
  if (b.dataset.tab === 'playground') loadStyles();
  if (b.dataset.tab === 'logs') loadLogs();
});

/* ---- overview ---- */
function renderStats(s, pool) {
  const items = [
    ['Requests', s.requests],
    ['Success', s.success],
    ['Errors', s.errors],
    ['Registrations', s.registrations],
    ['Uptime', Math.round(s.uptime_s) + 's'],
    ['Pool alive', pool.pool_alive + ' / ' + pool.pool_size],
  ];
  $('#stats').innerHTML = items.map(([l, v]) =>
    `<div class="stat"><div class="label">${l}</div><div class="value">${v}</div></div>`).join('');
}

function renderPool(pool) {
  $('#pool-badge').textContent = `alive ${pool.pool_alive} · idle ${pool.pool_idle} · credits ${pool.pool_total_credits}`;
  const rows = (pool.accounts || []).map(a => `<tr>
    <td class="mono">${esc(a.id)}</td>
    <td>${esc(a.nickname)}</td>
    <td>${a.credits}</td>
    <td>${a.used}</td>
    <td>${a.busy ? '<span class="badge">busy</span>' : '<span class="badge ok">idle</span>'}</td>
  </tr>`).join('');
  $('#pool-body').innerHTML = rows || '<tr><td colspan="5" class="muted">No accounts yet, auto-registering…</td></tr>';
}

async function loadOverview() {
  const j = await api('/admin/api/config');
  CONFIG = j.config;
  $('#ver').textContent = 'v' + j.version;
  renderStats(j.stats, j.pool);
  renderPool(j.pool);
  if (CONFIG.must_change_password) toast('Please change the default password now', 'err');
}

$('#refill').onclick = async () => {
  try { await api('/admin/api/pool/refill', { method: 'POST' }); toast('Refilling accounts…', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
};

/* ---- playground ---- */
let _stylesLoaded = false;
async function loadStyles() {
  if (_stylesLoaded) return;
  try {
    const j = await api('/v1/styles');
    const list = (j && j.data) || [];
    $('#pg-style').innerHTML = list.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
    _stylesLoaded = list.length > 0;
  } catch (e) {
    $('#pg-style').innerHTML = '<option value="">(unavailable)</option>';
  }
}

$('#pg-run').onclick = async () => {
  const prompt = $('#pg-prompt').value.trim();
  if (!prompt) return toast('Enter a prompt first', 'err');
  const body = {
    prompt,
    style: $('#pg-style').value || undefined,
    aspect_ratio: $('#pg-ratio').value,
    n: parseInt($('#pg-n').value, 10) || 1,
  };
  const btn = $('#pg-run');
  btn.disabled = true;
  $('#pg-results').innerHTML = '';
  $('#pg-elapsed').textContent = '-';
  const started = Date.now();
  const tick = setInterval(() => {
    $('#pg-status').textContent = `Generating… ${Math.round((Date.now() - started) / 1000)}s`;
  }, 500);
  $('#pg-status').textContent = 'Generating… 0s';
  try {
    const j = await api('/admin/api/generate', { method: 'POST', body });
    const imgs = (j.data || []).map(d => d.url || (d.b64_json ? 'data:image/png;base64,' + d.b64_json : ''));
    $('#pg-results').innerHTML = imgs.map(u =>
      `<a href="${esc(u)}" target="_blank" rel="noopener"><img src="${esc(u)}" style="width:100%;border-radius:10px;border:1px solid var(--border)"></a>`).join('');
    $('#pg-elapsed').textContent = j.elapsed_s + 's';
    $('#pg-status').textContent = `Done · task ${esc(j.task_id || '')} · ${imgs.length} image(s)`;
  } catch (e) {
    $('#pg-status').textContent = '';
    toast(e.message, 'err');
  } finally {
    clearInterval(tick);
    btn.disabled = false;
  }
};

/* ---- keys ---- */
async function loadKeys() {
  const j = await api('/admin/api/keys');
  $('#require-key').checked = !!j.require_api_key;
  const rows = (j.keys || []).map(k => `<tr>
    <td>${esc(k.name)}</td>
    <td class="mono">${esc(k.key || '')}</td>
    <td>${k.enabled ? '<span class="badge ok">enabled</span>' : '<span class="badge err">disabled</span>'}</td>
    <td class="row">
      <button class="ghost small" data-copy="${esc(k.key || '')}">Copy</button>
      <button class="ghost small" data-toggle="${esc(k.key || '')}" data-on="${k.enabled ? 0 : 1}">${k.enabled ? 'Disable' : 'Enable'}</button>
      <button class="danger small" data-del="${esc(k.key || '')}">Delete</button>
    </td>
  </tr>`).join('');
  $('#keys-body').innerHTML = rows || '<tr><td colspan="4" class="muted">No keys yet</td></tr>';
  $$('[data-toggle]').forEach(b => b.onclick = async () => {
    await api('/admin/api/keys/update', { method: 'POST', body: { key: b.dataset.toggle, enabled: b.dataset.on === '1' } });
    loadKeys();
  });
  $$('[data-del]').forEach(b => b.onclick = async () => {
    if (!confirm('Delete this key?')) return;
    await api('/admin/api/keys/delete', { method: 'POST', body: { key: b.dataset.del } });
    loadKeys();
  });
  $$('[data-copy]').forEach(b => b.onclick = () => copyText(b.dataset.copy));
}

$('#require-key').onchange = async () => {
  await api('/admin/api/config', { method: 'PUT', body: { require_api_key: $('#require-key').checked } });
  toast('Updated', 'ok');
};

$('#new-key').onclick = async () => {
  const name = prompt('Key name:', 'default');
  if (name === null) return;
  const j = await api('/admin/api/keys', { method: 'POST', body: { name } });
  await loadKeys();
  if (j.key && j.key.key) {
    if (confirm('Key created. Copy it to the clipboard?\n\n' + j.key.key)) copyText(j.key.key);
  }
};

/* ---- settings ---- */
const FIELDS = ['pool.target','pool.min','pool.max','pool.free_per_account','pool.reg_min_interval',
  'pool.refill_interval','pool.session_ttl','pool.acquire_timeout','upstream.host','upstream.user_agent',
  'upstream.timeout','upstream.poll_interval','upstream.poll_max','upstream.max_retries','styles_cache_ttl'];

function fillSettings() {
  if (!CONFIG) return;
  FIELDS.forEach(f => {
    const [sec, leaf] = f.split('.');
    const v = CONFIG[sec] ? CONFIG[sec][leaf] : undefined;
    const el = document.getElementById(f);
    if (el && v !== undefined) el.value = v;
  });
  $('#docs_public').checked = !!CONFIG.docs_public;
}

$('#save-settings').onclick = async () => {
  const body = {};
  FIELDS.forEach(f => {
    const [sec, leaf] = f.split('.');
    const el = document.getElementById(f);
    if (!el) return;
    body[sec] = body[sec] || {};
    body[sec][leaf] = el.value;
  });
  body.docs_public = $('#docs_public').checked;
  try {
    const j = await api('/admin/api/config', { method: 'PUT', body });
    toast(j.errors && j.errors.length ? ('Partial failure: ' + j.errors.join('; ')) : 'Settings saved', j.errors && j.errors.length ? 'err' : 'ok');
    loadOverview();
  } catch (e) { toast(e.message, 'err'); }
};

/* ---- logs ---- */
function fmtTs(ts) {
  const d = new Date(ts * 1000);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function fmtBytes(n) {
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1024 / 1024).toFixed(1) + ' MB';
}

function renderLogs(j) {
  const s = j.stats || {};
  const lv = s.levels || {};
  $('#logs-meta').textContent =
    `${j.total} shown / ${s.total || 0} stored · ${fmtBytes(s.file_bytes || 0)} · retention ${s.retention_days === 0 ? 'off' : (s.retention_days + 'd')}` +
    (lv.ERROR ? ` · ${lv.ERROR} error` : '') + (lv.WARN ? ` · ${lv.WARN} warn` : '');
  const badgeCls = { ERROR: 'err', WARN: 'warn', INFO: 'info', DEBUG: 'dbg' };
  const rows = (j.entries || []).map(e => `<div class="log-row lv-${esc((e.level || 'INFO').toLowerCase())}">
    <span class="log-ts">${esc(fmtTs(e.ts))}</span>
    <span class="badge ${badgeCls[e.level] || 'info'}">${esc(e.level)}</span>
    <span class="log-src">${esc(e.source)}</span>
    <span class="log-msg">${esc(e.msg)}${e.count > 1 ? ` <span class="badge log-count">×${e.count}</span>` : ''}${
      e.detail ? `<details class="log-detail"><summary>details</summary><pre>${esc(e.detail)}</pre></details>` : ''}</span>
  </div>`).join('');
  $('#logs-view').innerHTML = rows || '<div class="log-empty muted">No log entries yet</div>';
}

async function loadLogs() {
  try {
    const level = $('#logs-level').value;
    const params = new URLSearchParams({ limit: '500' });
    if (level) params.set('level', level);
    const j = await api('/admin/api/logs?' + params.toString());
    renderLogs(j);
  } catch (e) {
    $('#logs-view').innerHTML = `<div class="log-empty muted">Failed to load logs: ${esc(e.message)}</div>`;
  }
}

$('#logs-refresh').onclick = loadLogs;
$('#logs-level').onchange = loadLogs;

$('#logs-clear').onclick = async () => {
  if (!confirm('Delete ALL log entries? This cannot be undone.')) return;
  try {
    const j = await api('/admin/api/logs/clear', { method: 'POST' });
    toast(`Deleted ${j.removed} log entries`, 'ok');
    loadLogs();
  } catch (e) { toast(e.message, 'err'); }
};

$('#save-logs-settings').onclick = async () => {
  try {
    const j = await api('/admin/api/config', {
      method: 'PUT',
      body: { logs: {
        retention_days: parseInt(document.getElementById('logs.retention_days').value, 10) || 0,
        max_entries: parseInt(document.getElementById('logs.max_entries').value, 10) || 5000,
      } },
    });
    toast(j.errors && j.errors.length ? ('Save failed: ' + j.errors.join('; ')) : 'Retention settings saved',
      j.errors && j.errors.length ? 'err' : 'ok');
    loadLogs();
  } catch (e) { toast(e.message, 'err'); }
};

function fillLogsSettings() {
  if (!CONFIG || !CONFIG.logs) return;
  const ret = document.getElementById('logs.retention_days');
  const max = document.getElementById('logs.max_entries');
  if (ret && document.activeElement !== ret && CONFIG.logs.retention_days !== undefined) ret.value = CONFIG.logs.retention_days;
  if (max && document.activeElement !== max && CONFIG.logs.max_entries !== undefined) max.value = CONFIG.logs.max_entries;
}

setInterval(() => {
  const tab = $('#tab-logs');
  if (tab && !tab.classList.contains('hidden') && $('#logs-auto').checked) loadLogs();
}, 5000);

/* ---- password ---- */
$('#save-pw').onclick = async () => {
  const cur = $('#pw-current').value, nw = $('#pw-new').value, nw2 = $('#pw-new2').value;
  if (nw !== nw2) return toast('The new passwords do not match', 'err');
  try {
    await api('/admin/api/password', { method: 'POST', body: { current: cur, new: nw } });
    toast('Password updated, please sign in again', 'ok');
    setTimeout(() => location.href = '/login', 1200);
  } catch (e) { toast(e.message, 'err'); }
};

/* ---- sign out ---- */
$('#logout').onclick = async () => {
  await api('/admin/api/logout', { method: 'POST' });
  location.href = '/login';
};

/* ---- init ---- */
loadOverview().then(() => { fillSettings(); fillLogsSettings(); }).catch(e => toast(e.message, 'err'));
setInterval(() => { loadOverview().then(fillLogsSettings).catch(() => {}); }, 5000);
