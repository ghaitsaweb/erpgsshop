(() => {
  'use strict';

  const root = document.body;
  const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
  const apiRoot = '/database-manager/api';
  const state = {
    unlocked: root.dataset.unlocked === 'true',
    databases: [],
    backups: [],
    current: root.dataset.currentDatabase,
    activeOperation: null,
  };

  const $ = (selector, parent = document) => parent.querySelector(selector);
  const $$ = (selector, parent = document) => Array.from(parent.querySelectorAll(selector));
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[char]));

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if ((options.method || 'GET').toUpperCase() !== 'GET') headers.set('X-CSRFToken', csrfToken);
    headers.set('X-Requested-With', 'XMLHttpRequest');
    const response = await fetch(`${apiRoot}/${path.replace(/^\//, '')}`, { ...options, headers });
    let data = {};
    try { data = await response.json(); } catch (_) { data = { success: false, message: 'The server returned an invalid response.' }; }
    if (!response.ok) {
      const error = new Error(data.message || data.detail || `Request failed (${response.status}).`);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function toast(message, type = '') {
    const item = document.createElement('div');
    item.className = `dbm-toast ${type}`;
    item.textContent = message;
    $('#toast-stack').appendChild(item);
    setTimeout(() => item.remove(), 4600);
  }

  function setOperatorState(unlocked, username = '') {
    state.unlocked = unlocked;
    root.dataset.unlocked = String(unlocked);
    $('#operator-state').textContent = unlocked ? `Unlocked${username ? ` · ${username}` : ''}` : 'Operator locked';
    $('#operator-hint').textContent = unlocked ? 'Protected controls available' : 'Unlock management controls';
    if (unlocked) {
      loadBackups();
      loadOperations();
    } else {
      $('#backup-table').innerHTML = '<tr><td colspan="5" class="dbm-table-message">Unlock operator controls to view the backup vault.</td></tr>';
      $('#activity-table').innerHTML = '<tr><td colspan="5" class="dbm-table-message">Unlock operator controls to view activity.</td></tr>';
    }
  }

  function openDialog(id) {
    if (!state.unlocked && id !== 'unlock-dialog') {
      $('#unlock-dialog').showModal();
      return;
    }
    const dialog = document.getElementById(id);
    if (!dialog) return;
    const error = $('[data-form-error]', dialog);
    if (error) error.textContent = '';
    populateDatabaseSelects();
    if (id === 'restore-dialog') loadBackups();
    dialog.showModal();
  }

  function switchTab(name) {
    $$('.dbm-tabs [data-tab]').forEach(button => button.classList.toggle('active', button.dataset.tab === name));
    $$('[data-panel]').forEach(panel => {
      const active = panel.dataset.panel === name;
      panel.classList.toggle('active', active);
      panel.hidden = !active;
    });
    if (name === 'backups' && state.unlocked) loadBackups();
    if (name === 'activity' && state.unlocked) loadOperations();
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (!value) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
    return `${(value / Math.pow(1024, index)).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
  }

  function formatDate(value) {
    if (!value) return '—';
    return new Intl.DateTimeFormat('id-ID', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
  }

  function databaseCard(database) {
    const selected = database.name === state.current ? ' selected' : '';
    const offline = database.status !== 'ready' ? ' offline' : '';
    const symbol = database.is_primary ? 'G' : (database.label || database.name).slice(0, 1).toUpperCase();
    const action = database.status === 'ready'
      ? `<button class="dbm-open-db" type="button" data-choose-database="${escapeHtml(database.name)}">Open workspace <span>→</span></button>`
      : '<span class="dbm-unavailable">Database unavailable</span>';
    return `<article class="dbm-database-card${selected}${offline}" data-database-card data-name="${escapeHtml(database.name)}" data-label="${escapeHtml((database.label || '').toLowerCase())}">
      <header><span class="dbm-db-symbol">${escapeHtml(symbol)}</span><span class="dbm-status ${escapeHtml(database.status)}"><i></i>${escapeHtml(database.status)}</span>${database.is_protected ? '<span class="dbm-protected" title="Protected database">◆</span>' : ''}</header>
      <div class="dbm-card-title"><small>${database.is_primary ? 'Primary database' : 'Managed database'}</small><h3>${escapeHtml(database.label)}</h3><code>${escapeHtml(database.name)}</code></div>
      <dl><div><dt>Storage</dt><dd>${escapeHtml(database.size)}</dd></div><div><dt>Connections</dt><dd>${escapeHtml(database.connections)}</dd></div><div><dt>PostgreSQL</dt><dd>${escapeHtml(database.version)}</dd></div></dl>
      <footer>${action}</footer>
    </article>`;
  }

  async function loadDatabases() {
    try {
      const data = await request('databases/');
      state.databases = data.databases || [];
      $('#database-grid').innerHTML = state.databases.map(databaseCard).join('') || '<div class="dbm-empty"><span>!</span><h3>No database is available</h3></div>';
      $('#database-count').textContent = state.databases.length;
      $('#protected-count').textContent = state.databases.filter(item => item.is_protected).length;
      populateDatabaseSelects();
      renderProtection();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  function populateDatabaseSelects() {
    $$('[data-database-select]').forEach(select => {
      const current = select.value;
      const excludePrimary = select.hasAttribute('data-exclude-primary');
      const options = state.databases
        .filter(item => item.status === 'ready' && (!excludePrimary || !item.is_primary))
        .map(item => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.label)} · ${escapeHtml(item.name)}</option>`)
        .join('');
      select.innerHTML = options || '<option value="">No database available</option>';
      if (state.databases.some(item => item.name === current)) select.value = current;
    });
  }

  function renderProtection() {
    const protectedItems = state.databases.filter(item => item.is_protected);
    $('#protection-summary').textContent = `${protectedItems.length} of ${state.databases.length} protected`;
    $('#protection-list').innerHTML = protectedItems.length
      ? protectedItems.map(item => `<span class="dbm-protection-pill">◆ ${escapeHtml(item.label)} <code>${escapeHtml(item.name)}</code></span>`).join('')
      : '<p class="dbm-muted">No database protection is enabled.</p>';
  }

  async function chooseDatabase(name) {
    const body = new FormData();
    body.append('database', name);
    try {
      const data = await request('choose/', { method: 'POST', body });
      toast(`Opening ${name}…`, 'success');
      window.location.assign(data.redirect || '/login/');
    } catch (error) { toast(error.message, 'error'); }
  }

  async function loadBackups() {
    if (!state.unlocked) return;
    const tbody = $('#backup-table');
    try {
      const data = await request('backups/');
      state.backups = data.backups || [];
      $$('[data-backup-select]').forEach(select => {
        select.innerHTML = '<option value="">Or upload a backup below</option>' + state.backups.map(item => `<option value="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</option>`).join('');
      });
      tbody.innerHTML = state.backups.length ? state.backups.map(item => `<tr>
        <td><strong>${escapeHtml(item.filename)}</strong></td><td><code>${escapeHtml(item.database)}</code></td><td>${formatDate(item.created_at)}</td><td>${formatBytes(item.size)}</td>
        <td><div class="dbm-table-actions"><a class="dbm-mini-btn" href="${apiRoot}/backups/${encodeURIComponent(item.filename)}/download/">Download</a><button class="dbm-mini-btn danger" type="button" data-delete-backup="${escapeHtml(item.filename)}">Delete</button></div></td>
      </tr>`).join('') : '<tr><td colspan="5" class="dbm-table-message">No backups are stored yet.</td></tr>';
    } catch (error) {
      if (error.status === 401) setOperatorState(false);
      tbody.innerHTML = `<tr><td colspan="5" class="dbm-table-message">${escapeHtml(error.message)}</td></tr>`;
    }
  }

  async function loadOperations() {
    if (!state.unlocked) return;
    const tbody = $('#activity-table');
    try {
      const data = await request('operations/');
      const operations = data.operations || [];
      tbody.innerHTML = operations.length ? operations.map(item => `<tr><td><strong>${escapeHtml(item.kind_label)}</strong><br><small>${escapeHtml(item.message || '')}</small></td><td><code>${escapeHtml(item.target || item.database || '—')}</code></td><td>${escapeHtml(item.requested_by)}</td><td>${formatDate(item.started_at || item.created_at)}</td><td><span class="dbm-badge ${escapeHtml(item.status)}">${escapeHtml(item.status)} · ${escapeHtml(item.progress)}%</span></td></tr>`).join('') : '<tr><td colspan="5" class="dbm-table-message">No database activity has been recorded.</td></tr>';
    } catch (error) {
      if (error.status === 401) setOperatorState(false);
      tbody.innerHTML = `<tr><td colspan="5" class="dbm-table-message">${escapeHtml(error.message)}</td></tr>`;
    }
  }

  function showProgress(operation) {
    state.activeOperation = operation.id;
    $('#operation-progress').hidden = false;
    $('#progress-title').textContent = operation.kind_label || 'Database operation';
    updateProgress(operation);
    pollOperation(operation.id);
  }

  function updateProgress(operation) {
    const progress = Number(operation.progress || 0);
    $('#progress-message').textContent = operation.message || 'Working…';
    $('#progress-percent').textContent = `${progress}%`;
    $('#progress-bar').style.width = `${progress}%`;
  }

  async function pollOperation(id) {
    if (state.activeOperation !== id) return;
    try {
      const data = await request(`operations/${id}/`);
      updateProgress(data.operation);
      if (data.operation.status === 'success') {
        toast(`${data.operation.kind_label} completed.`, 'success');
        state.activeOperation = null;
        setTimeout(() => { $('#operation-progress').hidden = true; }, 1700);
        await Promise.all([loadDatabases(), loadBackups(), loadOperations()]);
        return;
      }
      if (data.operation.status === 'failed') {
        toast(data.operation.message || 'Database operation failed.', 'error');
        state.activeOperation = null;
        setTimeout(() => { $('#operation-progress').hidden = true; }, 2600);
        loadOperations();
        return;
      }
      setTimeout(() => pollOperation(id), 1800);
    } catch (error) {
      toast(error.message, 'error');
      setTimeout(() => pollOperation(id), 3000);
    }
  }

  async function submitOperation(form) {
    const button = form.querySelector('[type="submit"]');
    const errorBox = $('[data-form-error]', form);
    errorBox.textContent = '';
    button.disabled = true;
    try {
      const data = await request(`${form.dataset.endpoint}/`, { method: 'POST', body: new FormData(form) });
      form.closest('dialog').close();
      form.reset();
      if (data.operation) showProgress(data.operation);
      else {
        toast('Changes applied.', 'success');
        await Promise.all([loadDatabases(), loadOperations()]);
      }
    } catch (error) {
      errorBox.textContent = error.message;
      if (error.status === 401) {
        form.closest('dialog').close();
        setOperatorState(false);
        openDialog('unlock-dialog');
      }
    } finally { button.disabled = false; }
  }

  async function deleteBackup(filename) {
    const password = window.prompt(`Confirm operator password to delete ${filename}:`);
    if (!password) return;
    const body = new FormData();
    body.append('operator_password', password);
    try {
      await request(`backups/${encodeURIComponent(filename)}/delete/`, { method: 'POST', body });
      toast('Backup deleted.', 'success');
      loadBackups();
      loadOperations();
    } catch (error) { toast(error.message, 'error'); }
  }

  $$('.dbm-tabs [data-tab]').forEach(button => button.addEventListener('click', () => switchTab(button.dataset.tab)));
  $$('[data-tab-target]').forEach(button => button.addEventListener('click', () => switchTab(button.dataset.tabTarget)));
  $$('[data-dialog]').forEach(button => button.addEventListener('click', () => openDialog(button.dataset.dialog)));
  $$('[data-close-dialog]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  $$('.dbm-dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); }));
  $$('[data-operation-form], [data-simple-form]').forEach(form => form.addEventListener('submit', event => { event.preventDefault(); submitOperation(form); }));

  $('#operator-toggle').addEventListener('click', async () => {
    if (!state.unlocked) return openDialog('unlock-dialog');
    try {
      await request('operator/lock/', { method: 'POST', body: new FormData() });
      setOperatorState(false);
      toast('Operator controls locked.');
    } catch (error) { toast(error.message, 'error'); }
  });

  $('#unlock-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const errorBox = $('[data-form-error]', form);
    errorBox.textContent = '';
    try {
      const data = await request('operator/unlock/', { method: 'POST', body: new FormData(form) });
      form.closest('dialog').close();
      form.reset();
      setOperatorState(true, data.operator);
      toast('Protected controls unlocked.', 'success');
    } catch (error) { errorBox.textContent = error.message; }
  });

  $('#database-search').addEventListener('input', event => {
    const query = event.target.value.trim().toLowerCase();
    let visible = 0;
    $$('[data-database-card]').forEach(card => {
      const match = !query || `${card.dataset.name} ${card.dataset.label}`.includes(query);
      card.hidden = !match;
      if (match) visible += 1;
    });
    $('#database-no-results').hidden = visible !== 0;
  });

  document.addEventListener('click', event => {
    const choose = event.target.closest('[data-choose-database]');
    if (choose) chooseDatabase(choose.dataset.chooseDatabase);
    const remove = event.target.closest('[data-delete-backup]');
    if (remove) deleteBackup(remove.dataset.deleteBackup);
  });
  $('#refresh-activity').addEventListener('click', loadOperations);

  request('worker/').then(data => { $('#worker-label').textContent = data.worker ? 'Online' : 'Offline'; }).catch(() => { $('#worker-label').textContent = 'Unknown'; });
  loadDatabases();
  if (state.unlocked) {
    loadBackups();
    loadOperations();
  }
})();
