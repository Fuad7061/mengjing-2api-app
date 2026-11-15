/* 控制台逻辑 */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
let CONFIG = null;

/* ---- 标签切换 ---- */
$$('.tabs button').forEach(b => b.onclick = () => {
  $$('.tabs button').forEach(x => x.classList.toggle('active', x === b));
  ['overview', 'keys', 'settings', 'password'].forEach(t =>
    $('#tab-' + t).classList.toggle('hidden', t !== b.dataset.tab));
  if (b.dataset.tab === 'keys') loadKeys();
});

/* ---- 概览 ---- */
function renderStats(s, pool) {
  const items = [
    ['累计请求', s.requests],
    ['成功', s.success],
    ['失败', s.errors],
    ['注册账号', s.registrations],
    ['运行时长', Math.round(s.uptime_s) + 's'],
    ['池内可用', pool.pool_alive + ' / ' + pool.pool_size],
  ];
  $('#stats').innerHTML = items.map(([l, v]) =>
    `<div class="stat"><div class="label">${l}</div><div class="value">${v}</div></div>`).join('');
}

function renderPool(pool) {
  $('#pool-badge').textContent = `可用 ${pool.pool_alive} · 空闲 ${pool.pool_idle} · 额度 ${pool.pool_total_credits}`;
  const rows = (pool.accounts || []).map(a => `<tr>
    <td class="mono">${esc(a.id)}</td>
    <td>${esc(a.nickname)}</td>
    <td>${a.credits}</td>
    <td>${a.used}</td>
    <td>${a.busy ? '<span class="badge">使用中</span>' : '<span class="badge ok">空闲</span>'}</td>
  </tr>`).join('');
  $('#pool-body').innerHTML = rows || '<tr><td colspan="5" class="muted">暂无账号，正在自动注册…</td></tr>';
}

async function loadOverview() {
  const j = await api('/admin/api/config');
  CONFIG = j.config;
  $('#ver').textContent = 'v' + j.version;
  renderStats(j.stats, j.pool);
  renderPool(j.pool);
  if (CONFIG.must_change_password) toast('建议立即修改默认密码', 'err');
}

$('#refill').onclick = async () => {
  try { await api('/admin/api/pool/refill', { method: 'POST' }); toast('正在补充账号…', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
};

/* ---- 密钥 ---- */
async function loadKeys() {
  const j = await api('/admin/api/keys');
  $('#require-key').checked = !!j.require_api_key;
  const rows = (j.keys || []).map(k => `<tr>
    <td>${esc(k.name)}</td>
    <td class="mono">${esc(k.key || '')}</td>
    <td>${k.enabled ? '<span class="badge ok">启用</span>' : '<span class="badge err">停用</span>'}</td>
    <td class="row">
      <button class="ghost small" data-copy="${esc(k.key || '')}">复制</button>
      <button class="ghost small" data-toggle="${esc(k.key || '')}" data-on="${k.enabled ? 0 : 1}">${k.enabled ? '停用' : '启用'}</button>
      <button class="danger small" data-del="${esc(k.key || '')}">删除</button>
    </td>
  </tr>`).join('');
  $('#keys-body').innerHTML = rows || '<tr><td colspan="4" class="muted">暂无密钥</td></tr>';
  $$('[data-toggle]').forEach(b => b.onclick = async () => {
    await api('/admin/api/keys/update', { method: 'POST', body: { key: b.dataset.toggle, enabled: b.dataset.on === '1' } });
    loadKeys();
  });
  $$('[data-del]').forEach(b => b.onclick = async () => {
    if (!confirm('确定删除该密钥？')) return;
    await api('/admin/api/keys/delete', { method: 'POST', body: { key: b.dataset.del } });
    loadKeys();
  });
  $$('[data-copy]').forEach(b => b.onclick = () => copyText(b.dataset.copy));
}

$('#require-key').onchange = async () => {
  await api('/admin/api/config', { method: 'PUT', body: { require_api_key: $('#require-key').checked } });
  toast('已更新', 'ok');
};

$('#new-key').onclick = async () => {
  const name = prompt('密钥名称：', 'default');
  if (name === null) return;
  const j = await api('/admin/api/keys', { method: 'POST', body: { name } });
  await loadKeys();
  if (j.key && j.key.key) {
    if (confirm('密钥已生成，是否复制到剪贴板？\n\n' + j.key.key)) copyText(j.key.key);
  }
};

/* ---- 设置 ---- */
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
    toast(j.errors && j.errors.length ? ('部分失败: ' + j.errors.join('; ')) : '设置已保存', j.errors && j.errors.length ? 'err' : 'ok');
    loadOverview();
  } catch (e) { toast(e.message, 'err'); }
};

/* ---- 密码 ---- */
$('#save-pw').onclick = async () => {
  const cur = $('#pw-current').value, nw = $('#pw-new').value, nw2 = $('#pw-new2').value;
  if (nw !== nw2) return toast('两次输入的新密码不一致', 'err');
  try {
    await api('/admin/api/password', { method: 'POST', body: { current: cur, new: nw } });
    toast('密码已更新，请重新登录', 'ok');
    setTimeout(() => location.href = '/login', 1200);
  } catch (e) { toast(e.message, 'err'); }
};

/* ---- 退出 ---- */
$('#logout').onclick = async () => {
  await api('/admin/api/logout', { method: 'POST' });
  location.href = '/login';
};

/* ---- 初始化 ---- */
loadOverview().then(fillSettings).catch(e => toast(e.message, 'err'));
setInterval(() => { loadOverview().catch(() => {}); }, 5000);
