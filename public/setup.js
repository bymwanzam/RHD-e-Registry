'use strict';
/* Set-up page: connects the MySQL database (shown only until the e-Registry is set up). */
(function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const notice = (kind, text) => `<div class="notice ${kind}">${esc(text)}</div>`;

  $('#themeBtn').addEventListener('click', () => {
    const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('ereg-theme', next); } catch { /* storage blocked */ }
  });

  $('#app_url').value = location.origin + location.pathname.replace(/\/(setup(\.html)?)?$/, '');

  async function call(method, url, body) {
    const res = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
    let data = {};
    try { data = await res.json(); } catch { /* not JSON */ }
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  }

  call('GET', 'api/setup/status').then((s) => {
    if (s.reason === 'connection-failed' && s.error) {
      $('#reason').innerHTML = notice('error', `The saved database details no longer work: ${s.error}`);
    }
    if (s.key_source === 'env') $('#keyHint').innerHTML = 'Enter the <code>SETUP_KEY</code> you set in cPanel → Setup Python App → Environment variables.';
    else if (s.key_file) $('#keyHint').innerHTML = `In cPanel → File Manager, open <code>${esc(s.key_file)}</code> and copy the key (the first line, or the text after "set-up key:"). This proves you manage the hosting.`;
    const v = s.values || {};
    if (v.database) $('#database').value = v.database;
    if (v.user) $('#user').value = v.user;
    if (v.host) $('#host').value = v.host;
    if (v.port) $('#port').value = v.port;
    if (v.app_url) $('#app_url').value = v.app_url;
    if (s.locked && s.locked.length) {
      $('#reason').innerHTML += notice('warn', `Some database settings are fixed in cPanel → Setup Python App → Environment variables: ${s.locked.join(', ')}. If they are wrong, correct or delete them there and restart the app.`);
    }
  }).catch(() => {
    // Already set up: go to the app.
    location.href = './';
  });

  const values = () => ({
    key: $('#key').value, database: $('#database').value, user: $('#user').value, password: $('#password').value,
    host: $('#host').value, port: $('#port').value, app_url: $('#app_url').value,
  });
  const busy = (on) => { $('#testBtn').disabled = on; $('#saveBtn').disabled = on; };

  $('#testBtn').addEventListener('click', async () => {
    if (!$('#setupForm').reportValidity()) return;
    busy(true);
    $('#result').innerHTML = notice('info', 'Testing the connection…');
    try {
      const r = await call('POST', 'api/setup/test', values());
      $('#result').innerHTML = notice('info', `✓ ${r.message} Click "Save and start".`);
    } catch (e) {
      $('#result').innerHTML = notice('error', e.message);
    }
    busy(false);
  });

  $('#setupForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    busy(true);
    $('#result').innerHTML = notice('info', 'Connecting and creating the tables. This can take a few seconds…');
    try {
      await call('POST', 'api/setup/save', values());
      $('#setupForm').classList.add('hidden');
      $('#done').classList.remove('hidden');
    } catch (err) {
      $('#result').innerHTML = notice('error', err.message);
      busy(false);
    }
  });
})();
