(() => {
  const workspace = document.querySelector('[data-tender-comparison]');
  if (!workspace) return;

  const search = workspace.querySelector('[data-comparison-search]');
  const rows = [...workspace.querySelectorAll('[data-comparison-row]')];
  const empty = workspace.querySelector('[data-comparison-empty]');
  const table = workspace.querySelector('[data-comparison-table]');

  const filterRows = () => {
    const query = (search?.value || '').trim().toLowerCase().replace(/\s+/g, ' ');
    const terms = query.split(' ').filter(Boolean);
    let visible = 0;
    rows.forEach((row) => {
      const haystack = (row.dataset.search || '').toLowerCase();
      const matches = terms.every((term) => haystack.includes(term));
      row.hidden = !matches;
      if (matches) visible += 1;
    });
    if (empty) empty.hidden = visible !== 0 || rows.length === 0;
  };

  search?.addEventListener('input', filterRows);

  workspace.querySelector('[data-export-pdf]')?.addEventListener('click', () => window.print());

  workspace.querySelector('[data-export-excel]')?.addEventListener('click', () => {
    if (!table) return;
    const exportTable = table.cloneNode(true);
    exportTable.querySelectorAll('[hidden]').forEach((node) => node.remove());
    const title = workspace.dataset.exportName || 'tender-bid-comparison';
    const html = `<!doctype html><html><head><meta charset="utf-8"><style>
      body{font-family:Arial,sans-serif;color:#203047}h1{font-size:18px}
      table{border-collapse:collapse;width:100%}th,td{border:1px solid #dfe5ec;padding:8px;text-align:left}
      thead th,tfoot th,tfoot td{background:#edf4fc;font-weight:700}.is-best{background:#effaf4}
    </style></head><body><h1>${title.replaceAll('-', ' ')}</h1>${exportTable.outerHTML}</body></html>`;
    const blob = new Blob([html], { type: 'application/vnd.ms-excel;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${title}.xls`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  });
})();
