'use strict';
/* RHD e-Registry front end: a small single-page app with no build step. */

// ---------------- utilities ----------------
class Raw { constructor(s) { this.s = s; } }
const raw = (s) => new Raw(s);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const val = (v) => (v instanceof Raw ? v.s : Array.isArray(v) ? v.map(val).join('') : v == null || v === false ? '' : esc(v));
function html(strings, ...vals) {
  let out = '';
  strings.forEach((s, i) => { out += s; if (i < vals.length) out += val(vals[i]); });
  return raw(out);
}
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function fmtDate(iso, withTime = true) {
  if (!iso) return '';
  const d = new Date(iso.length === 10 ? iso + 'T00:00:00' : iso);
  const date = `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  return withTime ? `${date}, ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` : date;
}
function ago(iso) {
  if (!iso) return '';
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} h`;
  const days = Math.floor(hrs / 24);
  return `${days} day${days === 1 ? '' : 's'}`;
}
function agoText(iso) { const a = ago(iso); return a === 'just now' ? a : `${a} ago`; }
function fmtSize(n) { return n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`; }

let toastTimer;
function toast(msg, isError) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = ''; }, isError ? 5000 : 2800);
}

async function api(method, url, body) {
  const hasBody = method !== 'GET';
  // Relative to the page, so the app also works when installed in a sub-folder.
  const res = await fetch(url.replace(/^\//, ''), {
    method,
    headers: hasBody ? { 'Content-Type': 'application/json' } : {},
    body: hasBody ? JSON.stringify(body || {}) : undefined,
    credentials: 'same-origin',
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (res.status === 401 && url !== '/api/login') {
    state.me = null;
    render();
    throw new Error('Please sign in');
  }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

function formData(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === 'checkbox') out[el.name] = el.checked;
    else if (el.type !== 'file') out[el.name] = el.value;
  }
  return out;
}

function readFileB64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(',')[1]);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

async function uploadFiles(letterId, files) {
  let last = null;
  for (const f of files) {
    if (f.size > 10 * 1024 * 1024) { toast(`${f.name} is larger than 10 MB`, true); continue; }
    last = await api('POST', `/api/letters/${letterId}/attachments`, { filename: f.name, mime: f.type, data: await readFileB64(f) });
  }
  return last;
}

// ---------------- state & routing ----------------
const state = { me: null, meta: null, unread: 0, desk: 0 };

function route() {
  const h = location.hash.replace(/^#\/?/, '');
  const [p, qs] = h.split('?');
  return { parts: p ? p.split('/') : [], query: Object.fromEntries(new URLSearchParams(qs || '')) };
}
const go = (hash) => { if (location.hash === hash) render(); else location.hash = hash; };

window.addEventListener('hashchange', render);
window.addEventListener('DOMContentLoaded', boot);

async function boot() {
  try {
    await refreshMe();
    state.meta = await api('GET', '/api/meta');
  } catch { /* not signed in */ }
  render();
  setInterval(async () => {
    if (!state.me) return;
    const before = state.unread;
    try { await refreshMe(); } catch { return; }
    renderBadges();
    if (state.unread > before) toast('You have a new notification');
  }, 60000);
}

async function refreshMe() {
  const r = await api('GET', '/api/me');
  state.me = r.user; state.unread = r.unread; state.desk = r.desk;
}

const roleLabel = (code) => state.meta?.roles[code]?.label || code;
const facilities = () => state.meta?.facilities || [];
const facilityName = (id) => facilities().find((f) => f.id === Number(id))?.name || '';
const LEVEL_ORDER = ['REGION', 'DHD', 'HOSPITAL'];

function facilityOptions(selected, { exclude, includeInactive, blank } = {}) {
  return html`${blank ? html`<option value="">${blank}</option>` : ''}${LEVEL_ORDER.map((lvl) => {
    const fs = facilities().filter((f) => f.level === lvl && f.id !== exclude && (includeInactive || f.active));
    if (!fs.length) return '';
    return html`<optgroup label="${state.meta.levels[lvl]}s">${fs.map((f) => html`<option value="${f.id}" ${Number(selected) === f.id ? raw('selected') : ''}>${f.name}</option>`)}</optgroup>`;
  })}`;
}

// ---------------- icons & avatars ----------------
const ICONS = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  mail: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 6L2 7"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  pen: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  incoming: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  checkCircle: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  arrow: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  back: '<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>',
  printer: '<path d="M6 9V2h12v7"/><rect x="6" y="14" width="12" height="8" rx="1"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>',
  clip: '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  sms: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  building: '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4"/><path d="M8 6h.01M12 6h.01M16 6h.01M8 10h.01M12 10h.01M16 10h.01M8 14h.01M12 14h.01M16 14h.01"/>',
  activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  route: '<circle cx="6" cy="19" r="3"/><path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15"/><circle cx="18" cy="5" r="3"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  file: '<path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><path d="M14 2v6h6"/>',
  sparkle: '<path d="M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2z"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
};
function icon(name, size = 18) {
  return raw(`<svg class="ic" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`);
}
function initials(name) {
  const words = String(name || '').replace(/\(.*?\)/g, '').split(/[\s,]+/).filter((w) => /^[A-Za-z]/.test(w));
  return (words.slice(0, 2).map((w) => w[0].toUpperCase()).join('') || '?');
}
function avatar(name, cls = '') {
  let h = 0;
  for (const ch of String(name || '')) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return raw(`<span class="av ${cls}" style="--h:${h}" aria-hidden="true">${esc(initials(name))}</span>`);
}

// ---------------- colour theme ----------------
function themeToggle() {
  return html`<button type="button" class="theme-toggle" data-theme-toggle title="Switch between dark and light mode" aria-label="Switch between dark and light mode"><span class="moon">${icon('moon', 17)}</span><span class="sun">${icon('sun', 17)}</span></button>`;
}
function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('ereg-theme', theme); } catch { /* storage blocked: lasts for this visit only */ }
  const meta = document.querySelector('meta[name=theme-color]');
  if (meta) meta.content = theme === 'light' ? '#F2F5FA' : '#0B0F15';
}
document.addEventListener('click', (e) => {
  if (!e.target.closest('[data-theme-toggle]')) return;
  setTheme(document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light');
});

// ---------------- layout ----------------
function navLink(hash, label, ic, badge, active) {
  return html`<a href="${hash}" class="${active ? 'active' : ''}">${icon(ic)}<span class="lbl">${label}</span>${badge ? html`<span class="badge" data-badge="${badge.key}">${badge.n || ''}</span>` : ''}</a>`;
}

function shell(content) {
  const me = state.me;
  const r = route().parts[0] || '';
  return html`
  <div class="shell" id="shell">
    <aside class="side" aria-label="Main navigation">
      <div class="brand">
        <span class="mark logo"><img src="ghs-logo.png" alt="Ghana Health Service"></span>
        <div><b>RHD e-Registry</b><span>${state.meta?.settings?.region_name || 'Regional Health Directorate'}</span></div>
      </div>
      <nav class="nav">
        <div class="nav-label">Workspace</div>
        ${navLink('#/', 'Dashboard', 'dashboard', null, r === '')}
        ${navLink('#/desk', 'On my desk', 'inbox', { key: 'desk', n: state.desk }, r === 'desk')}
        ${navLink('#/letters', 'All letters', 'folder', null, r === 'letters')}
        ${navLink('#/notifications', 'Notifications', 'bell', { key: 'unread', n: state.unread }, r === 'notifications')}
        ${me.can_create || me.can_record_incoming ? html`<div class="nav-label">Create</div>` : ''}
        ${me.can_create ? navLink('#/new', 'New letter', 'pen', null, r === 'new' && !route().query.incoming) : ''}
        ${me.can_record_incoming ? navLink('#/new?incoming=1', 'Record incoming', 'incoming', null, r === 'new' && route().query.incoming) : ''}
        ${me.is_admin ? html`<div class="nav-label">System</div>${navLink('#/admin', 'Administration', 'shield', null, r === 'admin')}` : ''}
      </nav>
      <div class="me">
        <a class="me-card" href="#/account" title="My account">
          ${avatar(me.name, 'lg')}
          <span class="me-text"><b>${me.name}</b><span>${me.role_short || me.role_label}</span><span class="fac">${me.facility_name}</span></span>
        </a>
        ${themeToggle()}
        <button class="signout" id="logout" title="Sign out" aria-label="Sign out">${icon('logout')}</button>
      </div>
    </aside>
    <div class="content">
      <div class="topbar"><span class="brand-sm"><span class="mark logo"><img src="ghs-logo.png" alt=""></span>RHD e-Registry</span><span class="tb-actions">${themeToggle()}<button id="menu" aria-label="Open menu">${icon('menu', 20)}</button></span></div>
      <main class="main" id="view">${content}</main>
    </div>
  </div>`;
}

function renderBadges() {
  for (const b of $$('[data-badge]')) b.textContent = state[b.dataset.badge] || '';
}

function bindShell() {
  $('#logout').onclick = async () => { await api('POST', '/api/logout'); state.me = null; location.hash = '#/'; render(); };
  $('#menu').onclick = () => $('#shell').classList.toggle('menu-open');
  $$('.nav a').forEach((a) => a.addEventListener('click', () => $('#shell').classList.remove('menu-open')));
  $('.side').addEventListener('click', (e) => { if (e.target === e.currentTarget) $('#shell').classList.remove('menu-open'); });
}

// ---------------- render ----------------
let renderSeq = 0;
async function render() {
  const app = $('#app');
  if (!state.me) { renderLogin(app); return; }
  if (!state.meta) state.meta = await api('GET', '/api/meta');
  const { parts, query } = route();
  if (state.me.must_change_password && parts[0] !== 'account') { location.hash = '#/account'; return; }
  const seq = ++renderSeq;
  app.innerHTML = shell(html`<div class="loading"><span></span><span></span><span></span></div>`).s;
  bindShell();
  const view = $('#view');
  try {
    const views = { '': viewDashboard, desk: viewDesk, letters: viewLetters, new: viewNew, letter: viewLetter, notifications: viewNotifications, admin: viewAdmin, account: viewAccount };
    const fn = views[parts[0] || ''] || viewNotFound;
    await fn(view, parts, query, () => seq !== renderSeq);
    view.classList.remove('enter'); void view.offsetWidth; view.classList.add('enter');
    window.scrollTo(0, 0);
  } catch (e) {
    if (seq === renderSeq && state.me) view.innerHTML = html`<div class="notice error">${e.message}</div>`.s;
  }
}

function viewNotFound(view) { view.innerHTML = html`<div class="empty big">${icon('search', 48)}<h1>Page not found</h1><p><a class="btn primary" href="#/">Back to the dashboard</a></p></div>`.s; }

// ---------------- login ----------------
function renderLogin(app) {
  app.innerHTML = html`
  <div class="login">
    <div class="login-theme">${themeToggle()}</div>
    <section class="hero">
      <div class="orb o1"></div><div class="orb o2"></div><div class="grid-bg"></div>
      <div class="hero-in">
        <div class="brand-lg"><span class="mark logo"><img src="ghs-logo.png" alt="Ghana Health Service"></span><span>RHD e-Registry<small>Ghana Health Service</small></span></div>
        <h1>Every letter, <em>every desk,</em> always in sight.</h1>
        <p>One platform linking the Regional Health Directorate, District Health Directorates and District Hospitals to send, route and track letters and communiqués.</p>
        <div class="route-demo" aria-hidden="true">
          ${['RDHS', 'Secretary', 'Registry', 'HR/Admin', 'DDHS'].map((s, i) => html`${i ? html`<span class="rd-line"></span>` : ''}<span class="rd-node" style="--d:${i * 0.35}s"><i>${i + 1}</i>${s}</span>`)}
        </div>
        <ul class="perks">
          <li>${icon('file')}<span><b>Digital registry</b>Reference numbers and scanned copies for every letter</span></li>
          <li>${icon('route')}<span><b>Tracked routing</b>Each desk is recorded with time and officer</span></li>
          <li>${icon('bell')}<span><b>SMS alerts</b>Officers are alerted, and overdue letters flagged</span></li>
        </ul>
      </div>
    </section>
    <section class="form">
      <form class="login-card" id="loginForm">
        <div class="lc-icon">${icon('lock', 22)}</div>
        <h2>Welcome back</h2>
        <p class="muted">Sign in to your e-Registry account</p>
        <div id="loginErr"></div>
        <div class="field"><label for="u">Username</label><div class="input-ic">${icon('user')}<input id="u" name="username" autocomplete="username" required autofocus placeholder="Your username"></div></div>
        <div class="field"><label for="p">Password</label><div class="input-ic">${icon('lock')}<input id="p" name="password" type="password" autocomplete="current-password" required placeholder="••••••••"></div></div>
        <button class="btn primary block" type="submit">Sign in ${icon('arrow')}</button>
      </form>
      <p class="foot small">Ghana Health Service · Regional Health Directorate</p>
    </section>
  </div>`.s;
  $('#loginForm').onsubmit = async (e) => {
    e.preventDefault();
    const btn = $('button[type=submit]', e.target);
    btn.disabled = true;
    try {
      await api('POST', '/api/login', formData(e.target));
      await refreshMe();
      state.meta = await api('GET', '/api/meta');
      if (!location.hash || location.hash === '#/login') location.hash = '#/';
      render();
    } catch (err) {
      btn.disabled = false;
      $('#loginErr').innerHTML = html`<div class="notice error">${err.message}</div>`.s;
      const card = $('.login-card');
      card.classList.remove('shake'); void card.offsetWidth; card.classList.add('shake');
    }
  };
}

// ---------------- page header ----------------
function pageHead(title, sub, ic, actions = '') {
  return html`<div class="page-head">
    <div class="ph-title">${ic ? html`<span class="ph-icon">${icon(ic, 22)}</span>` : ''}<div><h1>${title}</h1>${sub ? html`<p>${sub}</p>` : ''}</div></div>
    ${actions ? html`<div class="btn-row">${actions}</div>` : ''}
  </div>`;
}

// ---------------- letter table ----------------
const STATUS_ICON = { Drafted: 'pen', Dispatched: 'send', Received: 'inbox', Minuted: 'sparkle', Actioned: 'activity', Closed: 'check' };
function statusPill(s) { return html`<span class="pill st-${s}"><i></i>${s}</span>`; }

const shortDate = (iso) => { const d = new Date(iso); return `${String(d.getDate()).padStart(2, '0')} ${MONTHS[d.getMonth()]}`; };

// Letters as a feed: date rail on the left, subject and reference, chips, and the current desk.
function letterTable(rows, { emptyText = 'No letters found.' } = {}) {
  if (!rows.length) return html`<div class="empty">${icon('inbox', 40)}<p>${emptyText}</p></div>`;
  return html`
  <ul class="feed-list">${rows.map((l) => html`
    <li class="fi ${l.overdue ? 'is-overdue' : ''} ${l.status === 'Closed' ? 'is-closed' : ''}" data-href="#/letter/${l.id}">
      <div class="fi-date"><b>${l.overdue ? html`<i class="dot"></i>` : ''}${shortDate(l.updated_at)}</b>
        <span>${l.status === 'Closed' ? 'closed' : l.desk_since ? `${ago(l.desk_since)}${l.overdue ? ' late' : ''}` : ''}</span></div>
      <div class="fi-main">
        <a class="fi-title" href="#/letter/${l.id}">${l.subject}</a>
        <div class="fi-meta">${l.ref_no || 'NOT YET LOGGED'} · ${l.from_name} → ${l.dest_name}${l.due_date ? ` · Due ${fmtDate(l.due_date, false)}` : ''}</div>
        <div class="chips">
          ${l.overdue ? html`<span class="chip bad">Overdue</span>` : ''}${statusPill(l.status)}
          ${l.priority !== 'Normal' ? html`<span class="chip ${l.priority === 'Urgent' ? 'bad' : 'violet'}">${l.priority}</span>` : ''}
          <span class="chip">${l.category}</span>
          ${l.attachment_count ? html`<span class="chip">${icon('clip', 12)} ${l.attachment_count}</span>` : ''}
        </div>
      </div>
      <div class="fi-side">${l.holder_name ? html`<span class="who">${avatar(l.holder_name)}<span>${l.holder_name}<span class="sub">${l.holder_role_label}</span></span></span>` : html`<span class="muted small">No desk · closed</span>`}</div>
    </li>`)}
  </ul>`;
}

function bindRows(root) {
  $$('tr[data-href], li.fi[data-href]', root).forEach((el) => el.addEventListener('click', (e) => { if (!e.target.closest('a')) location.hash = el.dataset.href; }));
}

// ---------------- dashboard ----------------
const STATUS_COLORS = { Drafted: '#8B95A7', Dispatched: '#6EA8FE', Received: '#2DD4BF', Minuted: '#F0B84A', Actioned: '#A3E635', Closed: '#4ADE80' };
const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

function donut(byStatus) {
  const total = byStatus.reduce((a, s) => a + s.n, 0);
  let acc = 0;
  const segs = total ? byStatus.filter((s) => s.n).map((s) => {
    const pct = (s.n / total) * 100;
    const seg = `<circle r="15.9155" cx="21" cy="21" fill="none" stroke="${STATUS_COLORS[s.status]}" stroke-width="5.2" stroke-dasharray="${Math.max(pct - 0.8, 0.1)} ${100 - Math.max(pct - 0.8, 0.1)}" stroke-dashoffset="${25 - acc}"><title>${s.status}: ${s.n}</title></circle>`;
    acc += pct;
    return seg;
  }).join('') : '';
  return raw(`<svg viewBox="0 0 42 42" class="donut" role="img" aria-label="Letters by status, ${total} in total">
    <circle r="15.9155" cx="21" cy="21" fill="none" stroke="var(--track)" stroke-width="5.2"/>${segs}
    <text x="21" y="21.2" class="d-n">${total}</text><text x="21" y="26.4" class="d-l">letters</text></svg>`);
}

async function viewDashboard(view, parts, query, stale) {
  const d = await api('GET', '/api/dashboard');
  if (stale()) return;
  const me = state.me;
  const now = new Date();
  const hr = now.getHours();
  const first = me.name.split(' ')[0];
  const maxFac = Math.max(1, ...d.by_facility.map((f) => f.open));
  view.innerHTML = html`
  <section class="hero-banner">
    <div class="hb-orb"></div><div class="hb-orb two"></div>
    <div class="hb-text">
      <span class="hb-date">${icon('clock', 14)} ${DAYS[now.getDay()]}, ${fmtDate(now.toISOString(), false)}</span>
      <h1>Good ${hr < 12 ? 'morning' : hr < 17 ? 'afternoon' : 'evening'}, ${first}</h1>
      <p>${d.kpi.my_desk ? html`You have <b>${d.kpi.my_desk} letter${d.kpi.my_desk === 1 ? '' : 's'}</b> waiting on your desk${d.kpi.my_overdue ? html`, <b class="hb-warn">${d.kpi.my_overdue} overdue</b>` : ''}.` : html`Your desk is clear. ${icon('sparkle', 16)}`}</p>
      <div class="btn-row">
        ${d.kpi.my_desk ? html`<a class="btn gold" href="#/desk">${icon('inbox')} Open my desk</a>` : ''}
        ${me.can_create ? html`<a class="btn glass" href="#/new">${icon('pen')} New letter</a>` : ''}
        ${me.can_record_incoming ? html`<a class="btn glass" href="#/new?incoming=1">${icon('incoming')} Record incoming</a>` : ''}
      </div>
    </div>
    <div class="hb-who">${avatar(me.name, 'xl')}<b>${me.role_short || me.role_label}</b><span>${me.facility_name}</span></div>
  </section>
  <div class="kpis">
    <a class="kpi k-red" href="#/letters?overdue=1"><div class="n">${d.kpi.overdue}</div><div class="l">Overdue · ${d.sla_days}+ days on a desk</div></a>
    <a class="kpi k-amber" href="#/desk"><div class="n">${d.kpi.my_desk}</div><div class="l">On my desk${d.kpi.my_overdue ? ` · ${d.kpi.my_overdue} late` : ''}</div></a>
    <a class="kpi k-blue" href="#/letters?open=1"><div class="n">${d.kpi.open}</div><div class="l">Open letters</div></a>
    <a class="kpi k-green" href="#/letters?status=Closed"><div class="n">${d.kpi.closed_30}</div><div class="l">Closed · last 30 days</div></a>
  </div>
  <div class="dash-grid">
    <div class="col">
      <div class="card">
        <div class="card-head"><h2>${icon('activity')} Letters by status</h2></div>
        <div class="donut-wrap">
          ${donut(d.by_status)}
          <ul class="legend">${d.by_status.map((s) => html`<li><a href="#/letters?status=${s.status}"><i style="background:${STATUS_COLORS[s.status]}"></i><span>${s.status}</span><b>${s.n}</b></a></li>`)}</ul>
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h2>${icon('building')} Where open letters are sitting</h2></div>
        ${d.by_facility.length ? html`<ul class="bars">${d.by_facility.map((f) => html`
          <li class="click" data-href="#/letters?open=1&holder_facility_id=${f.id}">
            <div class="bar-top"><span>${f.name}</span><span><b>${f.open}</b> open${f.overdue ? html` · <span class="overdue">${f.overdue} overdue</span>` : ''}</span></div>
            <div class="bar"><span style="width:${(f.open / maxFac) * 100}%"></span>${f.overdue ? html`<span class="od" style="width:${(f.overdue / maxFac) * 100}%"></span>` : ''}</div>
          </li>`)}</ul>` : html`<div class="empty">${icon('checkCircle', 36)}<p>No open letters.</p></div>`}
      </div>
      <div class="card">
        <div class="card-head"><h2>${icon('user')} Officers holding open letters</h2></div>
        ${d.by_officer.length ? html`<div class="table-wrap"><table class="responsive">
          <thead><tr><th>Officer</th><th class="right">Open</th><th class="right">Overdue</th><th>Longest wait</th></tr></thead>
          <tbody>${d.by_officer.map((o) => html`<tr class="click" data-href="#/letters?open=1&holder_id=${o.id}">
            <td><span class="who">${avatar(o.name)}<span>${o.name}<span class="sub">${o.role_label}, ${o.facility}</span></span></span></td>
            <td class="right"><b>${o.open}</b></td><td class="right">${o.overdue ? html`<span class="pill st-Failed">${o.overdue}</span>` : html`<span class="muted">—</span>`}</td><td class="small muted">${ago(o.oldest)}</td></tr>`)}</tbody>
        </table></div>` : html`<div class="empty">${icon('checkCircle', 36)}<p>No open letters.</p></div>`}
      </div>
    </div>
    <div class="card feed">
      <div class="card-head"><h2>${icon('clock')} Recent activity</h2></div>
      ${d.recent.length ? html`<ul class="activity">${d.recent.map((e) => html`
        <li>${avatar(e.actor_name)}
          <div><div><b>${e.actor_name}</b> ${e.action.toLowerCase()}${e.to_name ? html` → <b>${e.to_name}</b>` : ''}</div>
          <a href="#/letter/${e.letter_id}" class="small">${e.ref_no ? html`<span class="ref">${e.ref_no}</span> ` : ''}${e.subject}</a>
          <div class="t">${agoText(e.at)} · ${e.actor_role_label}</div></div></li>`)}</ul>` : html`<div class="empty">${icon('clock', 36)}<p>Nothing yet.</p></div>`}
    </div>
  </div>`.s;
  bindRows(view);
  $$('li[data-href]', view).forEach((li) => li.addEventListener('click', () => { location.hash = li.dataset.href; }));
}

// ---------------- desk ----------------
async function viewDesk(view, parts, query, stale) {
  const r = await api('GET', '/api/letters?box=desk&open=1');
  if (stale()) return;
  view.innerHTML = html`
  ${pageHead('On my desk', 'Letters waiting for you to act. Open one to forward, dispatch, minute or close it.', 'inbox')}
  <div class="card">${letterTable(r.rows, { emptyText: 'Your desk is clear.' })}</div>`.s;
  bindRows(view);
}

// ---------------- search / all letters ----------------
async function viewLetters(view, parts, query, stale) {
  const qs = new URLSearchParams(query).toString();
  const r = await api('GET', `/api/letters?${qs}`);
  if (stale()) return;
  const m = state.meta;
  const sel = (name, opts, blank) => html`<select name="${name}" aria-label="${blank}"><option value="">${blank}</option>${opts.map((o) => html`<option ${query[name] === o ? raw('selected') : ''}>${o}</option>`)}</select>`;
  const box = query.box || '';
  const boxes = [['', 'All I can see'], ['desk', 'On my desk'], ['sent', 'Created by me'], ['handled', 'Handled by me']];
  view.innerHTML = html`
  ${pageHead('Letters', 'Search by reference, subject, sender or officer.', 'folder', html`<a class="btn" href="api/letters.csv?${qs}">${icon('download')} Export to Excel</a>`)}
  <div class="tabs">${boxes.map(([k, l]) => html`<a href="#/letters?${new URLSearchParams({ ...query, box: k }).toString()}" class="${box === k ? 'active' : ''}">${l}</a>`)}</div>
  <form id="filters" class="card">
    <div class="filters">
      <div class="input-ic">${icon('search')}<input name="q" placeholder="Search reference, subject, sender…" value="${query.q || ''}" aria-label="Search"></div>
      ${sel('status', m.statuses, 'Any status')}
      <select name="facility_id" aria-label="Directorate or hospital">${facilityOptions(query.facility_id, { blank: 'Any directorate / hospital', includeInactive: true })}</select>
      ${sel('category', m.categories, 'Any type')}
      ${sel('priority', m.priorities, 'Any priority')}
    </div>
    <div class="filters2">
      <label class="check">From <input type="date" name="from" value="${query.from || ''}"></label>
      <label class="check">To <input type="date" name="to" value="${query.to || ''}"></label>
      <label class="check"><input type="checkbox" name="open" ${query.open === '1' ? raw('checked') : ''}> Open only</label>
      <label class="check"><input type="checkbox" name="overdue" ${query.overdue === '1' ? raw('checked') : ''}> Overdue only</label>
      <div class="btn-row"><button class="btn primary" type="submit">${icon('search')} Search</button><a class="btn" href="#/letters">Clear</a></div>
    </div>
  </form>
  ${query.holder_id || query.holder_facility_id ? html`<div class="notice info">Filtered to letters currently held ${query.holder_id ? 'by one officer' : `at ${facilityName(query.holder_facility_id)}`}. <a href="#/letters">Clear</a></div>` : ''}
  <div class="card"><div class="card-head"><h2>${r.total} letter${r.total === 1 ? '' : 's'}</h2>${r.total > r.rows.length ? html`<span class="muted small">Showing the latest ${r.rows.length}</span>` : ''}</div>
    ${letterTable(r.rows)}</div>`.s;
  bindRows(view);
  $('#filters').onsubmit = (e) => {
    e.preventDefault();
    const f = formData(e.target);
    const q = {};
    for (const [k, v] of Object.entries(f)) if (v === true) q[k] = '1'; else if (v) q[k] = v;
    if (box) q.box = box;
    go('#/letters?' + new URLSearchParams(q).toString());
  };
}

// ---------------- new letter ----------------
async function viewNew(view, parts, query, stale) {
  const incoming = query.incoming === '1';
  const m = state.meta;
  const me = state.me;
  let parent = null;
  if (query.reply) {
    parent = (await api('GET', `/api/letters/${query.reply}`)).letter;
    if (stale()) return;
  }
  const replyDest = parent ? (parent.origin_facility_id || '') : '';
  view.innerHTML = html`
  ${pageHead(incoming ? 'Record an incoming letter' : parent ? 'Reply to a letter' : 'New letter',
    incoming
      ? 'For paper letters that arrive from outside the system (for example from the Ministry). The letter is logged with a reference number and placed on your desk.'
      : 'The letter starts on your desk as a draft. Send it to your secretary to log and dispatch.',
    incoming ? 'incoming' : 'pen')}
  <form id="newForm" class="card form-card">
    <div id="formErr"></div>
    ${parent ? html`<div class="notice info">Replying to <a href="#/letter/${parent.id}">${parent.ref_no || ''} ${parent.subject}</a> from ${parent.from_name}.</div>` : ''}
    ${incoming ? html`
      <div class="grid2">
        <div class="field"><label for="sender">From (sender)</label><input id="sender" name="external_sender" required placeholder="e.g. Ministry of Health"></div>
        <div class="field"><label for="xref">Their reference</label><input id="xref" name="external_ref"></div>
      </div>
      <div class="field" style="max-width:240px"><label for="ld">Date on the letter</label><input id="ld" type="date" name="letter_date"></div>`
    : html`
      <div class="field"><label for="dest">To</label>
        <select id="dest" name="dest_facility_id" required>${facilityOptions(replyDest, { exclude: me.facility_id, blank: 'Choose a directorate or hospital…' })}</select>
        <div class="hint">From: ${me.facility_name}</div></div>`}
    <div class="field"><label for="subj">Subject</label><input id="subj" name="subject" required maxlength="300" value="${parent ? `RE: ${parent.subject.replace(/^RE:\s*/i, '')}` : ''}"></div>
    <div class="grid3">
      <div class="field"><label for="cat">Type</label><select id="cat" name="category">${m.categories.map((c) => html`<option ${parent && parent.category === c ? raw('selected') : ''}>${c}</option>`)}</select></div>
      <div class="field"><label for="pri">Priority</label><select id="pri" name="priority">${m.priorities.map((c) => html`<option>${c}</option>`)}</select></div>
      <div class="field"><label for="due">Response due by</label><input id="due" type="date" name="due_date"></div>
    </div>
    <div class="field"><label for="sum">Summary</label><textarea id="sum" name="summary" placeholder="What the letter is about and what is required"></textarea></div>
    <div class="field"><label for="files">Scanned copy / attachments</label><input id="files" type="file" multiple accept=".pdf,.jpg,.jpeg,.png,.webp,.doc,.docx,.xls,.xlsx">
      <div class="hint">PDF, image, Word or Excel, up to 10 MB each.</div></div>
    <div class="btn-row"><button class="btn primary" type="submit">${incoming ? 'Record and log' : 'Create letter'}</button><a class="btn" href="#/">Cancel</a></div>
  </form>`.s;
  $('#newForm').onsubmit = async (e) => {
    e.preventDefault();
    const btn = $('button[type=submit]', e.target);
    btn.disabled = true;
    try {
      const body = formData(e.target);
      if (incoming) body.incoming = true;
      if (parent) body.parent_id = parent.id;
      const { id } = await api('POST', '/api/letters', body);
      const files = $('#files').files;
      if (files.length) await uploadFiles(id, files);
      await refreshMe();
      toast(incoming ? 'Incoming letter recorded' : 'Letter created');
      go(`#/letter/${id}`);
    } catch (err) {
      $('#formErr').innerHTML = html`<div class="notice error">${err.message}</div>`.s;
      btn.disabled = false;
    }
  };
}

// ---------------- letter detail ----------------
const ACTION_INFO = {
  forward: { label: 'Forward', help: 'Send the letter to another officer at your directorate or hospital.' },
  dispatch: { label: 'Dispatch', help: 'Send the letter to the destination. A reference number is assigned if it does not have one.' },
  receive: { label: 'Acknowledge receipt', help: 'Confirm the letter has arrived. You can send it straight on to the next desk.' },
  minute: { label: 'Minute', help: 'Write an instruction and assign the letter to an officer for action.' },
  actioned: { label: 'Mark as actioned', help: 'Record what was done in response to the minute.' },
  close: { label: 'Close', help: 'Close the letter. No further action will be expected.' },
  reply: { label: 'Reply', help: '' },
};

async function viewLetter(view, parts, query, stale) {
  const data = await api('GET', `/api/letters/${parts[1]}`);
  if (stale()) return;
  drawLetter(view, data);
}

function drawLetter(view, data) {
  const { letter: l, events, attachments, actions, suggestion, parent, replies, can_attach } = data;
  const si = state.meta.statuses.indexOf(l.status);
  view.innerHTML = html`
  <div class="print-brand"><img src="ghs-logo.png" alt=""><div><b>Ghana Health Service</b><span>${state.meta.settings.region_name || "Regional Health Directorate"} · RHD e-Registry letter tracking slip</span></div></div>
  <section class="letter-hero">
    <div class="lh-top">
      <div class="lh-meta"><span class="ref chip">${l.ref_no || 'Not yet logged'}</span><span class="tag">${l.category}</span>${l.priority !== 'Normal' ? html`<span class="tag ${l.priority}">${l.priority}</span>` : ''}${l.overdue ? html`<span class="pill st-Failed"><i></i>Overdue</span>` : ''}</div>
      <div class="btn-row no-print"><button class="btn ghost" id="back">${icon('back')} Back</button><button class="btn ghost" id="print">${icon('printer')} Print slip</button></div>
    </div>
    <h1>${l.subject}</h1>
    <div class="lh-flow"><span>${icon('building', 16)} ${l.from_name}</span><span class="lh-arrow">${icon('arrow', 16)}</span><span>${icon('building', 16)} ${l.dest_name}</span></div>
    <ol class="stepper" aria-label="Status">${state.meta.statuses.map((s, i) => { const done = i < si || (i === si && s === 'Closed'); return html`<li class="${done ? 'done' : i === si ? 'now' : ''}"><span class="dot">${done ? icon('check', 14) : icon(STATUS_ICON[s], 14)}</span><span class="st-l">${s}</span></li>`; })}</ol>
  </section>
  <div class="detail">
    <div>
      <div class="card">
        <div class="card-head"><h2>${icon('file')} Details</h2>${statusPill(l.status)}</div>
        <dl class="info">
          <dt>From</dt><dd>${l.from_name}${l.external_ref ? html` <span class="muted">(their ref ${l.external_ref})</span>` : ''}</dd>
          <dt>To</dt><dd>${l.dest_name}</dd>
          <dt>Created by</dt><dd>${l.creator_name} <span class="muted">(${l.creator_role_label})</span>, ${fmtDate(l.created_at)}</dd>
          ${l.letter_date ? html`<dt>Date on letter</dt><dd>${fmtDate(l.letter_date, false)}</dd>` : ''}
          <dt>Current desk</dt><dd>${l.holder_name ? html`<span class="who">${avatar(l.holder_name)}<span><b>${l.holder_name}</b> <span class="muted">(${l.holder_role_label}, ${l.holder_facility})</span><br><span class="small muted">since ${fmtDate(l.desk_since)} (${ago(l.desk_since)})</span></span></span>` : html`<span class="muted">None, the letter is closed</span>`}</dd>
          ${l.due_date ? html`<dt>Response due</dt><dd>${fmtDate(l.due_date, false)}</dd>` : ''}
          ${l.dispatched_at ? html`<dt>Dispatched</dt><dd>${fmtDate(l.dispatched_at)}</dd>` : ''}
          ${l.received_at ? html`<dt>Received</dt><dd>${fmtDate(l.received_at)}</dd>` : ''}
          ${l.closed_at ? html`<dt>Closed</dt><dd>${fmtDate(l.closed_at)}</dd>` : ''}
          ${parent ? html`<dt>In reply to</dt><dd><a href="#/letter/${parent.id}">${parent.ref_no || ''} ${parent.subject}</a></dd>` : ''}
          ${replies.length ? html`<dt>Replies</dt><dd>${replies.map((r) => html`<div><a href="#/letter/${r.id}">${r.ref_no || 'Draft'} ${r.subject}</a> ${statusPill(r.status)}</div>`)}</dd>` : ''}
        </dl>
        ${l.summary ? html`<div class="summary">${l.summary}</div>` : ''}
      </div>
      <div class="card">
        <div class="card-head"><h2>${icon('clip')} Attachments</h2><span class="muted small">${attachments.length || ''}</span></div>
        ${attachments.length ? html`<ul class="files">${attachments.map((a) => html`<li><a class="file" href="api/attachments/${a.id}" target="_blank" rel="noopener"><span class="f-ic">${icon('file', 18)}</span>${a.filename}</a><span class="muted small nowrap">${fmtSize(a.size)} · ${a.uploaded_by_name}</span></li>`)}</ul>` : html`<p class="muted">No files attached.</p>`}
        ${can_attach ? html`<div class="btn-row no-print"><input type="file" id="attach" multiple accept=".pdf,.jpg,.jpeg,.png,.webp,.doc,.docx,.xls,.xlsx" style="max-width:320px"><button class="btn sm" id="attachBtn">${icon('download')} Upload</button></div>` : ''}
      </div>
      <div class="card">
        <div class="card-head"><h2>${icon('lock')} Audit trail</h2><span class="small muted">Locked · cannot be edited</span></div>
        <ul class="timeline">${events.map((e) => html`
          <li class="k-${e.action}">
            ${avatar(e.actor_name)}
            <div class="t">${fmtDate(e.at)} · ${e.status}</div>
            <div><b>${e.actor_name}</b> <span class="muted">(${e.actor_role_label}, ${e.actor_facility})</span> · ${e.action}${e.to_name ? html` → <b>${e.to_name}</b> <span class="muted">(${e.to_role_label})</span>` : ''}</div>
            ${e.note ? html`<div class="note">${e.note}</div>` : ''}
          </li>`)}</ul>
      </div>
    </div>
    <div>${actions.length ? deskPanel(data) : html`<div class="card no-print watch"><span class="watch-ic">${icon(l.status === 'Closed' ? 'checkCircle' : 'clock', 26)}</span><h2>${l.status === 'Closed' ? 'Closed' : 'Following this letter'}</h2><p class="muted">${l.status === 'Closed' ? 'This letter is closed.' : `This letter is with ${l.holder_name || 'another officer'}. You can follow its progress here.`}</p></div>`}</div>
  </div>`.s;

  $('#print').onclick = () => window.print();
  $('#back').onclick = () => (history.length > 1 ? history.back() : go('#/'));
  const ab = $('#attachBtn');
  if (ab) ab.onclick = async () => {
    const files = $('#attach').files;
    if (!files.length) return toast('Choose a file first', true);
    ab.disabled = true;
    try { const d = await uploadFiles(l.id, files); if (d) { toast('File attached'); drawLetter(view, d); } } catch (e) { toast(e.message, true); ab.disabled = false; }
  };
  if (actions.length) bindDesk(view, data);
}

function deskPanel(data) {
  const { actions, suggestion } = data;
  return html`
  <div class="desk no-print">
    <div class="desk-head"><span class="pulse"></span><h2>This letter is on your desk</h2></div>
    ${suggestion ? html`<div class="suggest"><span class="sg-k">${icon('sparkle', 14)} Suggested next step</span><b>${suggestion.why}</b>
      <button class="btn primary" id="doSuggest">${ACTION_INFO[suggestion.action].label}${suggestion.to_name ? ` → ${suggestion.to_name}` : ''}</button></div>` : ''}
    <div class="act-buttons">${actions.map((a) => html`<button class="btn sm" data-act="${a}">${ACTION_INFO[a].label}</button>`)}</div>
    <form id="actForm" class="hidden"></form>
  </div>`;
}

function officerSelect(list, selected, name = 'to_user_id', blank) {
  return html`<select name="${name}" ${blank ? '' : raw('required')}>${blank ? html`<option value="">${blank}</option>` : html`<option value="">Choose an officer…</option>`}${list.map((u) => html`<option value="${u.id}" ${u.id === selected ? raw('selected') : ''}>${u.name} — ${u.role_label}</option>`)}</select>`;
}

function bindDesk(view, data) {
  const { letter: l, suggestion, colleagues, destOfficers } = data;
  const form = $('#actForm');
  const open = (act, preset) => {
    if (act === 'reply') { go(`#/new?reply=${l.id}`); return; }
    $$('[data-act]').forEach((b) => b.classList.toggle('on', b.dataset.act === act));
    const info = ACTION_INFO[act];
    const sugTo = preset && preset.to_user_id;
    let fields = '';
    if (act === 'forward') fields = html`<div class="field"><label>Send to</label>${officerSelect(colleagues, sugTo)}</div><div class="field"><label>Note (optional)</label><textarea name="note"></textarea></div>`;
    if (act === 'dispatch') fields = html`<div class="field"><label>Receiving officer at ${l.dest_name}</label>${officerSelect(destOfficers, sugTo)}</div><div class="field"><label>Dispatch note (optional)</label><textarea name="note"></textarea></div>`;
    if (act === 'receive') fields = html`<div class="field"><label>Then send to</label>${officerSelect(colleagues, sugTo, 'to_user_id', 'Keep it on my desk')}</div><div class="field"><label>Note (optional)</label><textarea name="note"></textarea></div>`;
    if (act === 'minute') fields = html`<div class="field"><label>Minute to</label>${officerSelect(colleagues, sugTo)}</div><div class="field"><label>Minute (instruction)</label><textarea name="note" required placeholder="e.g. DDHR: please review and advise by Friday."></textarea></div>`;
    if (act === 'actioned') fields = html`<div class="field"><label>Action taken</label><textarea name="note" required></textarea></div>`;
    if (act === 'close') fields = html`<div class="field"><label>Closing note (optional)</label><textarea name="note"></textarea></div>`;
    form.innerHTML = html`<p class="small">${info.help}</p>${fields}<div class="btn-row"><button class="btn primary" type="submit">${info.label}</button><button class="btn" type="button" id="cancelAct">Cancel</button></div>`.s;
    form.classList.remove('hidden');
    form.dataset.act = act;
    $('#cancelAct').onclick = () => { form.classList.add('hidden'); $$('[data-act]').forEach((b) => b.classList.remove('on')); };
    const first = $('select, textarea', form);
    if (first) first.focus();
  };
  $$('[data-act]').forEach((b) => { b.onclick = () => open(b.dataset.act); });
  const ds = $('#doSuggest');
  if (ds) ds.onclick = () => open(suggestion.action, suggestion);
  form.onsubmit = async (e) => {
    e.preventDefault();
    const body = { type: form.dataset.act, ...formData(form) };
    if (body.type === 'close' && !confirm('Close this letter?')) return;
    const btn = $('button[type=submit]', form);
    btn.disabled = true;
    try {
      const d = await api('POST', `/api/letters/${l.id}/actions`, body);
      await refreshMe();
      renderBadges();
      toast(`${ACTION_INFO[body.type].label}: done`);
      drawLetter(view, d);
    } catch (err) {
      toast(err.message, true);
      btn.disabled = false;
    }
  };
}

// ---------------- notifications ----------------
async function viewNotifications(view, parts, query, stale) {
  const { rows } = await api('GET', '/api/notifications');
  if (stale()) return;
  view.innerHTML = html`
  <div class="page-head"><div class="ph-title"><span class="ph-icon">${icon('bell', 22)}</span><div><h1>Notifications</h1><p>Alerts when a letter reaches your desk, is received, minuted or closed.</p></div></div>
    ${rows.some((n) => !n.is_read) ? html`<button class="btn" id="readAll">Mark all as read</button>` : ''}</div>
  <div class="card">${rows.length ? html`<ul class="notes">${rows.map((n) => html`
    <li class="${n.is_read ? '' : 'unread'}" data-id="${n.id}" data-letter="${n.letter_id || ''}" tabindex="0">
      <span class="msg">${n.message}</span><span class="small muted nowrap">${fmtDate(n.at)}</span></li>`)}</ul>` : html`<div class="empty">No notifications yet.</div>`}</div>`.s;
  const ra = $('#readAll');
  if (ra) ra.onclick = async () => { await api('POST', '/api/notifications/read', {}); await refreshMe(); render(); };
  $$('.notes li').forEach((li) => {
    const openIt = async () => {
      if (li.classList.contains('unread')) { await api('POST', '/api/notifications/read', { id: Number(li.dataset.id) }); await refreshMe(); }
      if (li.dataset.letter) go(`#/letter/${li.dataset.letter}`); else render();
    };
    li.onclick = openIt;
    li.onkeydown = (e) => { if (e.key === 'Enter') openIt(); };
  });
}

// ---------------- account ----------------
async function viewAccount(view) {
  const me = state.me;
  view.innerHTML = html`
  <div class="profile-head">${avatar(me.name, 'xl')}<div><h1>${me.name}</h1><p>${me.role_label} · ${me.facility_name}</p></div></div>
  ${me.must_change_password ? html`<div class="notice warn">Please choose a new password before continuing.</div>` : ''}
  <div class="grid2">
    <form class="card" id="pwForm">
      <h2>Change password</h2>
      <div class="field"><label>Current password</label><input type="password" name="current" required autocomplete="current-password"></div>
      <div class="field"><label>New password</label><input type="password" name="password" required minlength="8" autocomplete="new-password"><div class="hint">At least 8 characters.</div></div>
      <div class="field"><label>Repeat new password</label><input type="password" name="password2" required minlength="8" autocomplete="new-password"></div>
      <button class="btn primary">Change password</button>
    </form>
    <form class="card" id="profForm">
      <h2>Contact details for alerts</h2>
      <div class="field"><label>Username</label><input value="${me.username}" disabled></div>
      <div class="field"><label>Mobile number (for SMS alerts)</label><input name="phone" value="${me.phone || ''}" placeholder="e.g. 0244 000 000"></div>
      <div class="field"><label>Email</label><input type="email" name="email" value="${me.email || ''}"></div>
      <button class="btn primary">Save</button>
    </form>
  </div>`.s;
  $('#pwForm').onsubmit = async (e) => {
    e.preventDefault();
    const f = formData(e.target);
    if (f.password !== f.password2) return toast('The new passwords do not match', true);
    try { await api('POST', '/api/me/password', f); toast('Password changed'); await refreshMe(); e.target.reset(); if (!state.me.must_change_password) go('#/'); } catch (err) { toast(err.message, true); }
  };
  $('#profForm').onsubmit = async (e) => {
    e.preventDefault();
    try { await api('POST', '/api/me/profile', formData(e.target)); await refreshMe(); toast('Saved'); } catch (err) { toast(err.message, true); }
  };
}

// ---------------- administration ----------------
async function viewAdmin(view, parts, query, stale) {
  if (!state.me.is_admin) return viewNotFound(view);
  const tab = parts[1] || 'users';
  const tabs = [['users', 'Users'], ['facilities', 'Directorates & hospitals'], ['settings', 'Settings'], ['sms', 'SMS alerts']];
  const head = html`${pageHead('Administration', 'Manage officers, facilities and system settings.', 'shield')}
    <div class="tabs">${tabs.map(([k, l]) => html`<a href="#/admin/${k}" class="${tab === k ? 'active' : ''}">${l}</a>`)}</div>`;
  if (tab === 'users') return adminUsers(view, head, stale);
  if (tab === 'facilities') return adminFacilities(view, head);
  if (tab === 'settings') return adminSettings(view, head);
  if (tab === 'sms') return adminSms(view, head, stale, query);
  return viewNotFound(view);
}

function roleOptions(selected, level) {
  const roles = state.meta.roles;
  return html`<option value="">Choose a role…</option>${['REGION', 'DHD', 'HOSPITAL', 'ANY'].map((lvl) => {
    const list = Object.entries(roles).filter(([, r]) => r.level === lvl && (!level || lvl === level || lvl === 'ANY'));
    if (!list.length) return '';
    return html`<optgroup label="${lvl === 'ANY' ? 'System' : state.meta.levels[lvl]}">${list.map(([code, r]) => html`<option value="${code}" ${selected === code ? raw('selected') : ''}>${r.label}</option>`)}</optgroup>`;
  })}`;
}

async function adminUsers(view, head, stale) {
  const { rows } = await api('GET', '/api/admin/users');
  if (stale()) return;
  view.innerHTML = html`${head}
  <div class="card" id="userFormCard"></div>
  <div class="card">
    <div class="card-head"><h2>${rows.length} users</h2><input id="userFilter" placeholder="Filter…" style="max-width:240px" aria-label="Filter users"></div>
    <div class="table-wrap"><table class="responsive">
      <thead><tr><th>Name</th><th>Username</th><th>Role</th><th>Directorate / hospital</th><th>Status</th><th></th></tr></thead>
      <tbody>${rows.map((u) => html`<tr data-search="${(u.name + ' ' + u.username + ' ' + u.role_label + ' ' + u.facility_name).toLowerCase()}">
        <td><span class="who">${avatar(u.name)}<span>${u.name}${u.is_admin ? html` <span class="tag">admin</span>` : ''}</span></span></td><td class="ref">${u.username}</td><td>${u.role_label}</td><td>${u.facility_name}</td>
        <td>${u.active ? html`<span class="pill st-Received"><i></i>Active</span>` : html`<span class="pill st-Drafted"><i></i>Inactive</span>`}</td>
        <td class="nowrap"><button class="btn sm" data-edit="${u.id}">Edit</button> <button class="btn sm" data-pw="${u.id}">Reset password</button></td></tr>`)}</tbody>
    </table></div>
  </div>`.s;

  const card = $('#userFormCard');
  const drawForm = (u) => {
    const isNew = !u;
    u = u || { active: true };
    card.innerHTML = html`<form id="userForm">
      <div class="card-head"><h2>${isNew ? 'Add an officer' : `Edit ${u.name}`}</h2>${!isNew ? html`<button type="button" class="btn sm" id="newUser">Add new instead</button>` : ''}</div>
      <div class="grid3">
        <div class="field"><label>Full name</label><input name="name" required value="${u.name || ''}"></div>
        <div class="field"><label>Username</label><input name="username" required value="${u.username || ''}" ${isNew ? '' : raw('disabled')} pattern="[a-zA-Z0-9._\\-]{3,40}"></div>
        ${isNew ? html`<div class="field"><label>Initial password</label><input name="password" required minlength="8"><div class="hint">They must change it at first sign-in.</div></div>` : html`<div></div>`}
        <div class="field"><label>Directorate / hospital</label><select name="facility_id" id="uf" required>${facilityOptions(u.facility_id, { blank: 'Choose…', includeInactive: !isNew })}</select></div>
        <div class="field"><label>Role</label><select name="role" id="ur" required></select></div>
        <div class="field"><label>Mobile (SMS alerts)</label><input name="phone" value="${u.phone || ''}"></div>
        <div class="field"><label>Email</label><input type="email" name="email" value="${u.email || ''}"></div>
      </div>
      <div class="btn-row" style="margin-bottom:.8rem"><label class="check"><input type="checkbox" name="is_admin" ${u.is_admin ? raw('checked') : ''}> System administrator</label>
        ${!isNew ? html`<label class="check"><input type="checkbox" name="active" ${u.active ? raw('checked') : ''}> Active</label>` : ''}</div>
      <button class="btn primary">${isNew ? 'Add officer' : 'Save changes'}</button>
    </form>`.s;
    const uf = $('#uf'), ur = $('#ur');
    const fillRoles = () => {
      const f = facilities().find((x) => x.id === Number(uf.value));
      ur.innerHTML = roleOptions(ur.value || u.role, f ? f.level : null).s;
    };
    uf.onchange = fillRoles;
    fillRoles();
    const nu = $('#newUser');
    if (nu) nu.onclick = () => drawForm(null);
    $('#userForm').onsubmit = async (e) => {
      e.preventDefault();
      const body = formData(e.target);
      if (isNew) body.active = true;
      try {
        if (isNew) await api('POST', '/api/admin/users', body);
        else await api('PUT', `/api/admin/users/${u.id}`, body);
        toast(isNew ? 'Officer added' : 'Saved');
        render();
      } catch (err) { toast(err.message, true); }
    };
  };
  drawForm(null);

  $$('[data-edit]').forEach((b) => { b.onclick = () => { drawForm(rows.find((u) => u.id === Number(b.dataset.edit))); card.scrollIntoView({ behavior: 'smooth' }); }; });
  $$('[data-pw]').forEach((b) => {
    b.onclick = async () => {
      const u = rows.find((x) => x.id === Number(b.dataset.pw));
      const pw = prompt(`New temporary password for ${u.name} (at least 8 characters). They will be asked to change it.`);
      if (!pw) return;
      try { await api('POST', `/api/admin/users/${u.id}/password`, { password: pw }); toast('Password reset'); } catch (err) { toast(err.message, true); }
    };
  });
  $('#userFilter').oninput = (e) => {
    const q = e.target.value.toLowerCase();
    $$('tr[data-search]').forEach((tr) => tr.classList.toggle('hidden', !!q && !tr.dataset.search.includes(q)));
  };
}

async function adminFacilities(view, head) {
  state.meta = await api('GET', '/api/meta');
  const fs = facilities();
  const dhds = fs.filter((f) => f.level === 'DHD');
  view.innerHTML = html`${head}
  <form class="card" id="facForm">
    <h2>Add a directorate or hospital</h2>
    <div class="grid3">
      <div class="field"><label>Name</label><input name="name" required placeholder="e.g. Tolon District Health Directorate"></div>
      <div class="field"><label>Code</label><input name="code" required placeholder="e.g. TOLDHD" maxlength="12"><div class="hint">Used in reference numbers, e.g. CODE/26/0001.</div></div>
      <div class="field"><label>Level</label><select name="level" required>${LEVEL_ORDER.map((l) => html`<option value="${l}">${state.meta.levels[l]}</option>`)}</select></div>
      <div class="field"><label>Parent district (hospitals)</label><select name="parent_id"><option value="">None</option>${dhds.map((d) => html`<option value="${d.id}">${d.name}</option>`)}</select></div>
    </div>
    <button class="btn primary">Add</button>
  </form>
  <div class="card"><div class="table-wrap"><table class="responsive">
    <thead><tr><th>Name</th><th>Code</th><th>Level</th><th>District</th><th>Active</th><th></th></tr></thead>
    <tbody>${fs.map((f) => html`<tr data-id="${f.id}">
      <td><input name="name" value="${f.name}" aria-label="Name"></td><td class="ref">${f.code}</td><td>${state.meta.levels[f.level]}</td>
      <td>${f.level === 'HOSPITAL' ? html`<select name="parent_id" aria-label="District"><option value="">None</option>${dhds.map((d) => html`<option value="${d.id}" ${f.parent_id === d.id ? raw('selected') : ''}>${d.name}</option>`)}</select>` : html`<span class="muted">—</span>`}</td>
      <td><input type="checkbox" name="active" ${f.active ? raw('checked') : ''} aria-label="Active"></td>
      <td><button class="btn sm" data-save="${f.id}">Save</button></td></tr>`)}</tbody>
  </table></div></div>`.s;
  $('#facForm').onsubmit = async (e) => {
    e.preventDefault();
    try { await api('POST', '/api/admin/facilities', formData(e.target)); toast('Added'); render(); } catch (err) { toast(err.message, true); }
  };
  $$('[data-save]').forEach((b) => {
    b.onclick = async () => {
      const tr = b.closest('tr');
      const body = { name: $('[name=name]', tr).value, active: $('[name=active]', tr).checked, parent_id: $('[name=parent_id]', tr)?.value || null };
      try { await api('PUT', `/api/admin/facilities/${b.dataset.save}`, body); state.meta = await api('GET', '/api/meta'); toast('Saved'); } catch (err) { toast(err.message, true); }
    };
  });
}

async function adminSettings(view, head) {
  state.meta = await api('GET', '/api/meta');
  const s = state.meta.settings;
  view.innerHTML = html`${head}
  <form class="card" id="setForm" style="max-width:640px">
    <div class="field"><label>Region / directorate name</label><input name="region_name" value="${s.region_name}"><div class="hint">Shown under the system name, e.g. "Northern Regional Health Directorate".</div></div>
    <div class="field" style="max-width:220px"><label>Days on one desk before a letter is overdue</label><input type="number" name="sla_days" min="1" max="60" value="${s.sla_days}"></div>
    <div class="field"><label class="check"><input type="checkbox" name="email_enabled" ${s.email_enabled === '1' ? raw('checked') : ''}> Queue email alerts</label>
      <div class="hint">Emails are queued only; a mail server is not yet connected. SMS alerts are set up in the "SMS alerts" tab.</div></div>
    <button class="btn primary">Save settings</button>
  </form>`.s;
  $('#setForm').onsubmit = async (e) => {
    e.preventDefault();
    try { await api('PUT', '/api/admin/settings', formData(e.target)); state.meta = await api('GET', '/api/meta'); toast('Settings saved'); render(); } catch (err) { toast(err.message, true); }
  };
}

const SMS_STATUS = {
  pending: ['Waiting to send', 'st-Drafted'], retry: ['Will retry', 'st-Minuted'], sent: ['Sent', 'st-Received'], failed: ['Failed', 'st-Failed'],
};
const DELIVERY_OK = ['delivered', 'test'];
const DELIVERY_BAD = ['failed', 'no-route-found', 'dnc'];

async function adminSms(view, head, stale, query) {
  const [st, box] = await Promise.all([api('GET', '/api/admin/sms'), api('GET', `/api/admin/outbox?channel=sms${query.status ? `&status=${encodeURIComponent(query.status)}` : ''}`)]);
  if (stale()) return;
  const c = st.config;
  const mode = !c.sms_enabled ? ['Off', 'notice warn', 'SMS alerts are switched off. Officers still get alerts inside the e-Registry.']
    : c.sms_provider === 'test' ? ['Test mode', 'notice warn', 'SMS alerts are on in test mode: messages are recorded below but not sent to phones.']
    : st.problems.length ? ['Not ready', 'notice error', 'SMS alerts are on, but the gateway is not fully set up.']
    : ['Live', 'notice info', 'SMS alerts are live through Fish Africa (LetsFish).'];
  const filters = [['', 'All'], ['pending', 'Waiting'], ['retry', 'Retrying'], ['failed', 'Failed'], ['sent', 'Sent']];
  view.innerHTML = html`${head}
  <div class="${mode[1]}"><b>${mode[0]}.</b> ${mode[2]}
    ${st.problems.length && c.sms_enabled ? html`<ul style="margin:.3rem 0 0">${st.problems.map((p) => html`<li>${p}</li>`)}</ul>` : ''}
    ${st.provider_error ? html`<div style="margin-top:.3rem"><b>Last gateway error</b> (${fmtDate(st.provider_error.at)}): ${st.provider_error.error}${st.paused_until ? html`. Sending is paused until ${fmtDate(st.paused_until)}; saving the settings below retries now.` : ''}</div>` : ''}
  </div>
  <div class="kpis">
    <div class="kpi k-green"><div class="n">${st.sent_24h}</div><div class="l">Sent in the last 24 hours</div></div>
    <a class="kpi k-amber" href="#/admin/sms?status=pending"><div class="n">${(st.counts.pending || 0) + (st.counts.retry || 0)}</div><div class="l">Waiting or retrying</div></a>
    <a class="kpi k-red" href="#/admin/sms?status=failed"><div class="n">${st.counts.failed || 0}</div><div class="l">Failed</div></a>
    <a class="kpi k-blue" href="#/admin/users"><div class="n">${st.users_without_phone}</div><div class="l">Officers with no mobile number (they get no SMS)</div></a>
  </div>
  <div class="dash-grid">
    <form class="card" id="smsForm">
      <h2>Set-up</h2>
      <div class="field"><label class="check"><input type="checkbox" name="sms_enabled" ${c.sms_enabled ? raw('checked') : ''}> Send SMS alerts</label>
        <div class="hint">An officer gets an SMS when a letter is sent, dispatched or minuted to them, plus a weekday reminder for overdue letters. Other updates stay in the app.</div></div>
      <div class="field"><label for="prov">SMS gateway</label>
        <select id="prov" name="sms_provider">
          <option value="test" ${c.sms_provider === 'test' ? raw('selected') : ''}>Test mode (nothing is sent)</option>
          <option value="letsfish" ${c.sms_provider === 'letsfish' ? raw('selected') : ''}>Fish Africa (LetsFish)</option>
        </select></div>
      <div id="lfFields">
        <p class="small muted">Find these in the <a href="https://letsfish.africa" target="_blank" rel="noopener">Fish Africa dashboard</a> under Settings → Applications → Default App.</p>
        ${c.credentials_from_env ? html`<div class="notice info small">The App ID and Secret come from the server's environment variables (LETSFISH_APP_ID and LETSFISH_APP_SECRET).</div>` : html`
        <div class="grid2">
          <div class="field"><label for="appid">App ID</label><input id="appid" name="sms_app_id" autocomplete="off" placeholder="${c.sms_app_id_set ? `Saved (${c.sms_app_id_hint}); leave blank to keep` : ''}"></div>
          <div class="field"><label for="appsec">App Secret</label><input id="appsec" name="sms_app_secret" type="password" autocomplete="new-password" placeholder="${c.sms_app_secret_set ? 'Saved; leave blank to keep' : ''}"></div>
        </div>
        ${c.sms_app_id_set || c.sms_app_secret_set ? html`<label class="check small" style="margin:-.4rem 0 .8rem"><input type="checkbox" name="clear_credentials"> Remove the saved App ID and Secret</label>` : ''}`}
        <div class="field" style="max-width:260px"><label for="sid">Sender ID</label><input id="sid" name="sms_sender_id" maxlength="11" value="${c.sms_sender_id}" placeholder="e.g. GHS-RHD">
          <div class="hint">The sender name that phones display. Up to 11 characters; it must be approved on your Fish Africa account.</div></div>
      </div>
      <div class="grid2">
        <div class="field"><label for="cc">Country code</label><input id="cc" name="sms_country_code" value="${c.sms_country_code}" inputmode="numeric">
          <div class="hint">Used to convert local numbers such as 0244123456.</div></div>
        <div class="field"><label for="url">System address (optional)</label><input id="url" name="app_url" value="${c.app_url || ''}" placeholder="https://eregistry.example.gov.gh">
          <div class="hint">If set, each SMS ends with a link to the letter.</div></div>
      </div>
      <div class="field"><label class="check"><input type="checkbox" name="sms_overdue_reminders" ${c.sms_overdue_reminders ? raw('checked') : ''}> Send a weekday reminder for overdue letters</label>
        <div class="check small" style="margin-top:.3rem">at <input type="number" name="sms_overdue_hour" min="0" max="23" value="${c.sms_overdue_hour}" style="width:5rem" aria-label="Reminder hour"> :00, Monday to Friday, one message per officer</div></div>
      <button class="btn primary">Save SMS settings</button>
    </form>
    <div class="col">
    <form class="card" id="smsTest">
      <h2>Send a test SMS</h2>
      <p class="small muted">Sends one message straight away using the saved settings, so save any changes first.</p>
      <div class="field"><label for="tphone">Mobile number</label><input id="tphone" name="phone" required placeholder="0244123456" value="${state.me.phone || ''}"></div>
      <button class="btn">Send test</button>
      <div id="testResult" style="margin-top:.8rem"></div>
    </form>
    <div class="card">
      <h2>${icon('clock')} Keep alerts running on shared hosting</h2>
      <p class="small muted">Shared hosting puts the app to sleep when nobody is using it, which pauses SMS sending and the morning reminder.
        In cPanel → <b>Cron Jobs</b>, add this command to run <b>every 5 minutes</b>:</p>
      <div><input id="cronCmd" class="ref" readonly value="${`curl -fsS "${new URL(st.cron_path, location.href.split('#')[0]).href}" > /dev/null 2>&1`}" aria-label="Cron command"></div>
      <div class="btn-row" style="margin-top:.6rem"><button class="btn sm" type="button" id="copyCron">${icon('file')} Copy command</button>
        <span class="small muted">Database: ${st.database === 'mysql' ? 'MySQL / MariaDB' : `SQLite (${st.database})`}</span></div>
      <p class="small muted" style="margin-top:.6rem">Keep this link private: it contains a secret key.</p>
    </div>
    </div>
  </div>
  <div class="card">
    <div class="card-head"><h2>Messages</h2>
      <div class="tabs" style="margin:0;border:0">${filters.map(([k, l]) => html`<a href="#/admin/sms${k ? `?status=${k}` : ''}" class="${(query.status || '') === k ? 'active' : ''}">${l}</a>`)}</div></div>
    ${box.rows.length ? html`<div class="table-wrap"><table class="responsive">
      <thead><tr><th>Queued</th><th>To</th><th>Message</th><th>Status</th><th>Delivery</th><th></th></tr></thead>
      <tbody>${box.rows.map((r) => {
        const [label, cls] = SMS_STATUS[r.status] || [r.status, 'st-Drafted'];
        const d = r.delivery_status;
        return html`<tr>
          <td class="small nowrap">${fmtDate(r.created_at)}</td>
          <td>${r.user_name || 'Test message'}<span class="sub ref">${r.recipient}</span></td>
          <td class="small">${r.message}${r.last_error && r.status !== 'sent' ? html`<span class="sub overdue">${r.last_error}</span>` : ''}</td>
          <td><span class="pill ${cls}">${label}</span><span class="sub">${r.status === 'sent' ? fmtDate(r.sent_at) : r.status === 'retry' ? `next try ${fmtDate(r.next_attempt_at)}` : r.attempts ? `${r.attempts} attempt${r.attempts > 1 ? 's' : ''}` : ''}</span></td>
          <td>${d ? html`<span class="${DELIVERY_BAD.includes(d) ? 'overdue' : DELIVERY_OK.includes(d) ? '' : 'muted'}">${d === 'test' ? 'Test mode' : d}</span>` : html`<span class="muted">—</span>`}</td>
          <td>${['failed', 'retry'].includes(r.status) ? html`<button class="btn sm" data-retry="${r.id}">Retry now</button>` : ''}</td></tr>`;
      })}</tbody></table></div>`
      : html`<div class="empty">No messages${query.status ? ' with this status' : ' yet'}.</div>`}
  </div>`.s;

  const prov = $('#prov');
  const showLf = () => $('#lfFields').classList.toggle('hidden', prov.value !== 'letsfish');
  prov.onchange = showLf;
  showLf();
  $('#smsForm').onsubmit = async (e) => {
    e.preventDefault();
    const body = formData(e.target);
    if (body.clear_credentials && !confirm('Remove the saved App ID and Secret?')) return;
    try { await api('PUT', '/api/admin/sms', body); toast('SMS settings saved'); render(); } catch (err) { toast(err.message, true); }
  };
  $('#smsTest').onsubmit = async (e) => {
    e.preventDefault();
    const btn = $('button', e.target);
    const out = $('#testResult');
    btn.disabled = true;
    out.innerHTML = html`<span class="muted">Sending…</span>`.s;
    try {
      const r = await api('POST', '/api/admin/sms/test', formData(e.target));
      out.innerHTML = html`<div class="notice info">${r.status === 'test' ? `Test mode: the message to ${r.to} was recorded but not sent to the phone.` : `The gateway accepted the message for ${r.to} (reference ${r.reference || 'n/a'}, status ${r.status}). Check the phone.`}</div>`.s;
    } catch (err) {
      out.innerHTML = html`<div class="notice error">${err.message}</div>`.s;
    }
    btn.disabled = false;
  };
  $('#copyCron').onclick = async () => {
    const box = $('#cronCmd');
    try { await navigator.clipboard.writeText(box.value); toast('Command copied'); } catch { box.select(); toast('Press Ctrl+C to copy'); }
  };
  $$('[data-retry]').forEach((b) => {
    b.onclick = async () => {
      try { await api('POST', `/api/admin/outbox/${b.dataset.retry}/retry`); toast('Queued to retry'); render(); } catch (err) { toast(err.message, true); }
    };
  });
}
