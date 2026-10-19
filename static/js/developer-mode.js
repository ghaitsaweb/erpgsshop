(() => {
  if (document.body.dataset.developerMode !== 'true') return;

  document.body.classList.add('developer-mode-active');
  const inspector = document.createElement('aside');
  inspector.className = 'developer-inspector';
  inspector.setAttribute('role', 'status');
  inspector.innerHTML = '<strong>Developer Inspector</strong><div></div>';
  document.body.appendChild(inspector);
  const details = inspector.querySelector('div');
  let current = null;

  const selector = 'a, button, input, select, textarea, form, table, [data-menu-item], [data-application-card], [data-company-id], [data-branch-id]';
  const technicalInfo = (element) => {
    const href = element.getAttribute('href') || element.closest('a')?.getAttribute('href') || '';
    const entityMatch = href.match(/\/app\/([^/]+)\//);
    const rows = [
      ['Element', element.tagName.toLowerCase()],
      ['Route', document.body.dataset.currentRoute || '—'],
      ['Application', document.body.dataset.currentApp || '—'],
    ];
    if (entityMatch) rows.push(['Entity', entityMatch[1]]);
    if (element.getAttribute('name')) rows.push(['Field', element.getAttribute('name')]);
    if (element.id) rows.push(['DOM ID', element.id]);
    if (href) rows.push(['URL', href]);
    if (element.dataset.search) rows.push(['Search key', element.dataset.search]);
    return rows;
  };

  const position = (event) => {
    const left = Math.min(event.clientX + 16, window.innerWidth - inspector.offsetWidth - 14);
    const top = Math.min(event.clientY + 16, window.innerHeight - inspector.offsetHeight - 14);
    inspector.style.left = `${Math.max(12, left)}px`;
    inspector.style.top = `${Math.max(12, top)}px`;
  };

  document.addEventListener('mouseover', (event) => {
    const target = event.target.closest(selector);
    if (!target || target.closest('.developer-inspector')) return;
    current?.classList.remove('developer-inspected');
    current = target;
    current.classList.add('developer-inspected');
    details.innerHTML = technicalInfo(target)
      .map(([label, value]) => `<p><span>${label}</span><code>${String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;')}</code></p>`)
      .join('');
    inspector.classList.add('visible');
    position(event);
  });
  document.addEventListener('mousemove', (event) => {
    if (inspector.classList.contains('visible')) position(event);
  });
  document.addEventListener('mouseout', (event) => {
    if (current && !current.contains(event.relatedTarget)) {
      current.classList.remove('developer-inspected');
      current = null;
      inspector.classList.remove('visible');
    }
  });
})();
