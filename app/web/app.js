/* Shared helpers: request wrapper, toast notifications */
window.api = async function (url, opts = {}) {
  const r = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (r.status === 401 && location.pathname !== '/login') {
    location.href = '/login';
  }
  if (!r.ok) throw Object.assign(new Error(data.error || ('HTTP ' + r.status)), { status: r.status, data });
  return data;
};

let _toastTimer;
window.toast = function (msg, kind = '') {
  let el = document.getElementById('toast');
  if (!el) { el = document.createElement('div'); el.id = 'toast'; el.className = 'toast'; document.body.appendChild(el); }
  el.textContent = msg;
  el.className = 'toast show ' + kind;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = 'toast ' + kind; }, 2600);
};

window.copyText = function (text) {
  navigator.clipboard.writeText(text).then(
    () => toast('Copied to clipboard', 'ok'),
    () => toast('Copy failed', 'err')
  );
};

window.esc = function (s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
};
