// Shared helpers: API client, toasts, modals, formatting, range tiles, pager.
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const state = { me: null, csrf: '', timers: [] };

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

// ---------------------------------------------------------------- API
export async function api(path, { method = 'GET', body, params, form } = {}) {
  let url = '/admin/api' + path;
  if (params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, v);
    const s = q.toString();
    if (s) url += '?' + s;
  }
  const opts = { method, credentials: 'same-origin', headers: { 'X-CSRF-Token': state.csrf } };
  if (form) opts.body = form;
  else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(url, opts); } catch { throw new Error('Network error. Check your connection.'); }
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (res.status === 401) { location.href = '/login'; throw new Error('Session expired'); }
  if (!res.ok) throw new Error((data && (data.error || data.message)) || `Request failed (${res.status})`);
  return data;
}

// ---------------------------------------------------------------- formatting
const TZ = 'Asia/Kolkata';
export const fmtDate = (ts) => !ts ? '—' : new Date(ts * 1000).toLocaleString('en-IN', {
  timeZone: TZ, day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
export const fmtMoney = (n) => '₹' + Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 });
export const fmtNum = (n) => Number(n || 0).toLocaleString('en-IN');
export function ago(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}
export function until(ts) {
  const s = ts - Date.now() / 1000;
  if (s <= 0) return 'expired';
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h left` : h ? `${h}h ${m}m left` : `${m}m left`;
}
export const mmss = (s) => { s = Math.max(0, Math.floor(s)); return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0'); };
export const today = () => new Date().toLocaleDateString('en-CA', { timeZone: TZ });
export const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

export function avatar(url, name, cls = '') {
  const letter = esc((name || '?').trim().charAt(0).toUpperCase() || '?');
  if (url) return `<img class="avatar ${cls}" src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-fallback="${letter}">`;
  return `<div class="avatar ${cls}">${letter}</div>`;
}
// <img> failures (dead URLs) fall back to the letter avatar. Capture phase because error events don't bubble.
document.addEventListener('error', (e) => {
  const t = e.target;
  if (t && t.tagName === 'IMG' && t.dataset.fallback !== undefined) {
    const d = document.createElement('div');
    d.className = t.className; d.textContent = t.dataset.fallback;
    t.replaceWith(d);
  }
}, true);

export function statusBadge(s, extra = '') {
  const map = { active: ['ok', '🟢 Active'], expired: ['bad', '🔴 Expired'], revoked: ['warn', '⛔ Revoked'],
    pending: ['warn', '⏳ Pending'], verification: ['info', '🕒 In verification'], success: ['ok', '✅ Success'],
    failed: ['bad', '❌ Failed'] };
  const [c, l] = map[s] || ['', esc(s)];
  return `<span class="badge ${c}">${l}${extra ? ' · ' + esc(extra) : ''}</span>`;
}

// ---------------------------------------------------------------- toast / modal
export function toast(msg, type = 'ok') {
  const el = document.createElement('div');
  el.className = 'toast' + (type === 'err' ? ' err' : '');
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), type === 'err' ? 6000 : 3200);
}
export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast('Copied ✨'); }
  catch { toast('Copy failed — select and copy manually', 'err'); }
}

export function openModal({ title, body, submit = 'Save', cancel = 'Cancel', onSubmit, wide = false, danger = false, noFoot = false }) {
  const root = $('#modalRoot');
  const back = document.createElement('div');
  back.className = 'modal-back';
  back.innerHTML = `<div class="modal${wide ? ' wide' : ''}" role="dialog" aria-modal="true">
    <div class="head"><h2>${esc(title)}</h2><button class="icon-btn" data-x aria-label="Close">✕</button></div>
    <form novalidate>${body}${noFoot ? '' : `<div class="foot"><button type="button" class="btn" data-x>${esc(cancel)}</button>
    ${onSubmit ? `<button type="submit" class="btn ${danger ? 'danger' : 'primary'}">${esc(submit)}</button>` : ''}</div>`}</form></div>`;
  root.appendChild(back);
  const form = $('form', back);
  const close = () => { back.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  back.addEventListener('click', (e) => { if (e.target.closest('[data-x]')) close(); });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!onSubmit) return;
    const btn = $('button[type=submit]', form);
    btn.disabled = true;
    try { const r = await onSubmit(Object.fromEntries(new FormData(form)), form, close); if (r !== false) close(); }
    catch (err) { toast(err.message, 'err'); }
    finally { btn.disabled = false; }
  });
  const first = $('input:not([type=hidden]),select,textarea', form);
  if (first) setTimeout(() => first.focus(), 60);
  return { el: back, form, close };
}
export function confirmBox(title, text, okLabel = 'Confirm', danger = true) {
  return new Promise((resolve) => {
    let done = false;
    const m = openModal({ title, body: `<p class="muted">${text}</p>`, submit: okLabel, danger,
      onSubmit: async () => { done = true; resolve(true); } });
    new MutationObserver((_, o) => { if (!document.body.contains(m.el)) { if (!done) resolve(false); o.disconnect(); } })
      .observe($('#modalRoot'), { childList: true });
  });
}
export function lightbox(src) {
  openModal({ title: 'Screenshot', body: `<div class="lightbox"><img src="${esc(src)}" alt="screenshot"></div>`, noFoot: true, wide: true });
}

// ---------------------------------------------------------------- UI pieces
export function pager(el, meta, go) {
  if (!meta || meta.pages <= 1) { el.innerHTML = meta && meta.total ? `<span class="tiny">${fmtNum(meta.total)} total</span>` : ''; return; }
  el.className = 'pager';
  el.innerHTML = `<button class="btn sm" data-p="${meta.page - 1}" ${meta.page <= 1 ? 'disabled' : ''}>← Prev</button>
    <span>Page <b>${meta.page}</b> of ${meta.pages} · ${fmtNum(meta.total)} total</span>
    <button class="btn sm" data-p="${meta.page + 1}" ${meta.page >= meta.pages ? 'disabled' : ''}>Next →</button>`;
  el.onclick = (e) => { const b = e.target.closest('[data-p]'); if (b && !b.disabled) go(+b.dataset.p); };
}
export const skeleton = (n = 4) => '<div>' + '<div class="skel"></div>'.repeat(n) + '</div>';
export const empty = (icon, text) => `<div class="empty"><div class="big">${icon}</div>${esc(text)}</div>`;

export function userCell(u, size = '') {
  return `${avatar(u.profile_image_url, u.full_name, size)}<div class="meta"><b>${esc(u.full_name || '—')}</b>
    <small>${u.username ? '@' + esc(u.username) : 'no username'} · <span class="mono">${esc(u.chat_id)}</span></small></div>`;
}

/** Four stat tiles (today / yesterday / all time / custom) per block, with one shared custom date range. */
export function rangeBlocks(root, { fetch, build, after }) {
  const custom = { start: '', end: '' };
  const tile = (label, value, sub, hot) => `<div class="tile${hot ? ' hot' : ''}"><div class="lbl">${label}</div><div class="val">${value}</div><div class="sub">${sub || ''}</div></div>`;
  function draw(data) {
    const blocks = build(data);
    root.innerHTML = blocks.map((b) => `<div class="section-title"><h2>${b.title}</h2></div>
      <div class="grid cols-4 keep2">
        ${tile('Today', b.fmt(b.data.today), b.sub?.today, true)}${tile('Yesterday', b.fmt(b.data.yesterday), b.sub?.yesterday)}
        ${tile('All time', b.fmt(b.data.all), b.sub?.all)}
        <div class="tile custom"><div class="lbl">Custom range</div>
          <div class="val">${b.data.custom == null ? '—' : b.fmt(b.data.custom)}</div>
          <div class="ranges"><input type="date" data-from value="${esc(custom.start)}" aria-label="From"><input type="date" data-to value="${esc(custom.end)}" aria-label="To">
          <button class="btn sm primary" data-apply>Apply</button></div></div>
      </div>`).join('');
    if (after) after(data);
  }
  async function load() {
    try { draw(await fetch(custom.start, custom.end)); } catch (e) { toast(e.message, 'err'); }
  }
  root.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-apply]');
    if (!btn) return;
    const t = btn.closest('.tile');
    custom.start = $('[data-from]', t).value; custom.end = $('[data-to]', t).value;
    if (!custom.start || !custom.end) return toast('Pick both dates first', 'err');
    load();
  });
  return load();
}

export function tick(fn, ms = 1000) { const id = setInterval(fn, ms); state.timers.push(id); return id; }
export function clearTimers() { state.timers.forEach(clearInterval); state.timers = []; }
