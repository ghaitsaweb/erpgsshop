(() => {
  const body = document.body;
  if (body.dataset.developerMode !== 'true') return;

  const toolbar = document.querySelector('[data-developer-toolbar]');
  const inspectButton = toolbar?.querySelector('[data-developer-inspect]');
  const inspectionStorageKey = 'gsshop.developer.inspector-active';
  const inspector = document.createElement('aside');
  inspector.className = 'developer-inspector';
  inspector.setAttribute('role', 'status');
  inspector.innerHTML = '<strong>UI Inspector</strong><div class="developer-inspector-details"></div><small class="developer-inspector-hint">Hover to inspect · Stop inspecting or Esc to close</small>';
  document.body.appendChild(inspector);

  const details = inspector.querySelector('.developer-inspector-details');
  let inspecting = false;
  let current = null;

  const storedInspectionState = () => {
    try {
      return window.sessionStorage.getItem(inspectionStorageKey) === 'true';
    } catch (error) {
      return false;
    }
  };

  const persistInspectionState = (enabled) => {
    try {
      if (enabled) window.sessionStorage.setItem(inspectionStorageKey, 'true');
      else window.sessionStorage.removeItem(inspectionStorageKey);
    } catch (error) {
      // Private browsing may deny storage; inspection still works on this page.
    }
  };

  const escapeValue = (value) => String(value || '').trim();
  const data = (element, key) => escapeValue(element?.dataset?.[key]);
  const firstValue = (...values) => values.map(escapeValue).find(Boolean) || '';

  const labelFor = (input) => {
    if (!input?.id) return '';
    return Array.from(document.querySelectorAll('label')).find((label) => label.htmlFor === input.id)?.textContent?.trim() || '';
  };

  const elementLabel = (element) => {
    const aria = element.getAttribute?.('aria-label');
    return firstValue(aria, element.getAttribute?.('title'), element.textContent?.replace(/\s+/g, ' ').slice(0, 80));
  };

  const componentName = (element) => {
    if (element.id) return `#${element.id}`;
    const classes = Array.from(element.classList || []).filter((name) => !name.startsWith('developer-')).slice(0, 2);
    return `${element.tagName.toLowerCase()}${classes.length ? `.${classes.join('.')}` : ''}`;
  };

  const targetFor = (source) => {
    if (!(source instanceof Element) || source.closest('.developer-toolbar, .developer-inspector')) return null;
    return source.closest('[data-dev-field]')
      || source.closest('input, select, textarea, button, a')
      || source.closest('[data-dev-object], [data-dev-model], form, table, [data-menu-item], [data-application-card], [data-company-id], [data-branch-id]');
  };

  const technicalInfo = (element) => {
    const field = element.closest('[data-dev-field]');
    const input = element.matches('input, select, textarea') ? element : element.querySelector?.('input, select, textarea');
    const record = element.closest('[data-dev-object]');
    const modelCarrier = element.closest('[data-dev-model]') || record || body;
    const form = element.closest('form');
    const link = element.closest('a');
    const model = firstValue(data(field, 'devModel'), data(record, 'devModel'), data(modelCarrier, 'devModel'), data(body, 'currentModel'));
    const table = firstValue(data(field, 'devTable'), data(record, 'devTable'), data(modelCarrier, 'devTable'), data(body, 'currentTable'));
    const entity = firstValue(data(field, 'devEntity'), data(record, 'devEntity'), data(modelCarrier, 'devEntity'), data(body, 'currentEntity'));
    const fieldName = firstValue(data(field, 'devField'), input?.name);
    const fieldLabel = firstValue(data(field, 'devFieldLabel'), labelFor(input), elementLabel(element));
    const objectId = firstValue(data(record, 'devObjectId'), data(body, 'currentObjectId'));
    const objectName = data(record, 'devObjectName');
    const href = firstValue(link?.getAttribute('href'), element.getAttribute?.('href'));
    const rows = [
      ['Component', componentName(element)],
      ['Route', body.dataset.currentRoute || '—'],
      ['Application', body.dataset.currentApp || '—'],
    ];
    if (entity) rows.push(['Entity', entity]);
    if (model) rows.push(['Model', model]);
    if (table) rows.push(['Table', table]);
    if (objectId) rows.push(['Object', `${objectName ? `${objectName} · ` : ''}#${objectId}`]);
    if (fieldName) rows.push(['Field', fieldName]);
    if (fieldLabel) rows.push(['Label', fieldLabel]);
    if (data(field, 'devFieldColumn')) rows.push(['DB column', data(field, 'devFieldColumn')]);
    if (data(field, 'devFieldType')) rows.push(['Field type', data(field, 'devFieldType')]);
    if (data(field, 'devFieldRelation')) rows.push(['Relation', data(field, 'devFieldRelation')]);
    if (data(field, 'devFieldKind') === 'virtual') rows.push(['Source', 'Virtual form field']);
    if (data(field, 'devRequired')) rows.push(['Required', data(field, 'devRequired')]);
    if (input) rows.push(['Widget', `${input.tagName.toLowerCase()}${input.type ? ` · ${input.type}` : ''}`]);
    if (form) rows.push(['Form action', `${(form.method || 'get').toUpperCase()} ${form.getAttribute('action') || window.location.pathname}`]);
    if (href) rows.push(['URL', href]);
    if (element.id) rows.push(['DOM ID', element.id]);
    if (element.dataset.search) rows.push(['Search key', element.dataset.search]);
    return rows;
  };

  const showDetails = (element, event) => {
    if (!element) return;
    current?.classList.remove('developer-inspected');
    current = element;
    current.classList.add('developer-inspected');
    details.replaceChildren(...technicalInfo(element).map(([label, value]) => {
      const row = document.createElement('p');
      const name = document.createElement('span');
      const content = document.createElement('code');
      name.textContent = label;
      content.textContent = value;
      row.append(name, content);
      return row;
    }));
    inspector.classList.add('visible');
    position(event);
  };

  const position = (event) => {
    if (!event) return;
    const left = Math.min(event.clientX + 16, window.innerWidth - inspector.offsetWidth - 14);
    const top = Math.min(event.clientY + 16, window.innerHeight - inspector.offsetHeight - 14);
    inspector.style.left = `${Math.max(12, left)}px`;
    inspector.style.top = `${Math.max(12, top)}px`;
  };

  const setInspecting = (enabled) => {
    inspecting = enabled;
    persistInspectionState(enabled);
    inspector.querySelector('.developer-inspector-hint').textContent = 'Hover to inspect · Stop inspecting or Esc to close';
    body.classList.toggle('developer-inspecting', enabled);
    toolbar?.classList.toggle('is-inspecting', enabled);
    if (inspectButton) {
      inspectButton.textContent = enabled ? 'Stop inspecting' : 'Inspect UI';
      inspectButton.setAttribute('aria-pressed', String(enabled));
    }
    if (!enabled) {
      current?.classList.remove('developer-inspected');
      current = null;
      inspector.classList.remove('visible');
    }
  };

  inspectButton?.addEventListener('click', () => setInspecting(!inspecting));

  document.addEventListener('mousemove', (event) => {
    if (!inspecting) return;
    const target = targetFor(event.target);
    if (target) showDetails(target, event);
  });

  document.addEventListener('click', (event) => {
    if (!inspecting) return;
    const target = targetFor(event.target);
    if (!target) return;
    showDetails(target, event);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !inspecting) return;
    event.preventDefault();
    setInspecting(false);
  });

  document.querySelectorAll('[data-developer-mode-disable], [data-developer-mode-toggle]').forEach((form) => {
    form.addEventListener('submit', () => {
      if (form.querySelector('[name="enabled"]')?.value === '0') persistInspectionState(false);
    });
  });

  if (storedInspectionState()) setInspecting(true);
})();
