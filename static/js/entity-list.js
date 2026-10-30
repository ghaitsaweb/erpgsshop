(() => {
  const activateRecordRows = (rows) => rows.forEach((row) => {
    const openRecord = () => {
      if (row.dataset.recordUrl) window.location.assign(row.dataset.recordUrl);
    };
    row.addEventListener('click', (event) => {
      if (event.target.closest('a, button, input, select, textarea, label, summary')) return;
      openRecord();
    });
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openRecord();
      }
    });
  });

  const layoutSwitcher = document.querySelector('[data-layout-switcher]');
  const listLayout = document.querySelector('[data-entity-layout="list"]');
  const kanbanLayout = document.querySelector('[data-entity-layout="kanban"]');
  const layoutInput = document.querySelector('[data-entity-layout-input]');
  const listControls = document.querySelector('[data-list-layout-control]');

  if (layoutSwitcher && listLayout && kanbanLayout) {
    const switchLayout = (nextLayout) => {
      listLayout.hidden = nextLayout !== 'list';
      kanbanLayout.hidden = nextLayout !== 'kanban';
      if (listControls) listControls.hidden = nextLayout !== 'list';
      if (layoutInput) layoutInput.value = nextLayout;
      layoutSwitcher.querySelectorAll('[data-layout-switch]').forEach((button) => {
        const active = button.dataset.layoutSwitch === nextLayout;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
      });
      const url = new URL(window.location.href);
      if (nextLayout === 'list') url.searchParams.delete('layout');
      else url.searchParams.set('layout', nextLayout);
      window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
    };

    layoutSwitcher.querySelectorAll('[data-layout-switch]').forEach((button) => {
      button.addEventListener('click', () => switchLayout(button.dataset.layoutSwitch));
    });
    switchLayout(layoutInput?.value === 'kanban' ? 'kanban' : 'list');
  }

  const table = document.querySelector('[data-entity-list-table]');
  if (!table) {
    activateRecordRows(Array.from(document.querySelectorAll('[data-record-url]')));
    return;
  }

  const body = table.tBodies[0];
  const rows = Array.from(body.querySelectorAll('[data-list-row]'));
  const controls = document.querySelector('[data-list-controls]');
  const noMatch = body.querySelector('[data-list-no-match]');
  const fieldSelect = controls?.querySelector('[data-list-filter-field]');
  const operatorSelect = controls?.querySelector('[data-list-filter-operator]');
  const filterInput = controls?.querySelector('[data-list-filter-value]');
  const groupSelect = controls?.querySelector('[data-list-group-field]');
  const filterCount = controls?.querySelector('[data-list-filter-count]');
  const groupCount = controls?.querySelector('[data-list-group-count]');
  const columns = Array.from(table.tHead?.querySelectorAll('[data-list-field]') || []);
  const columnIndex = Object.fromEntries(columns.map((column) => [column.dataset.listField, column.cellIndex]));
  const selectAll = table.querySelector('[data-select-all]');
  const selectionInputs = Array.from(table.querySelectorAll('[data-select-record]'));
  const bulkDeleteForm = document.querySelector('[data-bulk-delete-form]');
  const selectedCount = bulkDeleteForm?.querySelector('[data-selected-count]');
  const deleteWizard = document.querySelector('[data-delete-wizard]');
  const wizardCount = deleteWizard?.querySelector('[data-delete-wizard-count]');
  const wizardRecords = deleteWizard?.querySelector('[data-delete-wizard-records]');
  const confirmDelete = deleteWizard?.querySelector('[data-confirm-delete]');

  const updateSelection = () => {
    const selected = selectionInputs.filter((input) => input.checked);
    const visible = selectionInputs.filter((input) => !input.closest('[data-list-row]').hidden);
    const selectedVisible = visible.filter((input) => input.checked);
    if (selectedCount) selectedCount.textContent = String(selected.length);
    if (bulkDeleteForm) bulkDeleteForm.hidden = selected.length === 0;
    if (selectAll) {
      selectAll.checked = visible.length > 0 && selectedVisible.length === visible.length;
      selectAll.indeterminate = selectedVisible.length > 0 && selectedVisible.length < visible.length;
    }
  };

  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase();
  const rawValue = (row, field) => {
    const index = columnIndex[field];
    return index === undefined ? '' : String(row.cells[index]?.textContent || '').replace(/\s+/g, ' ').trim();
  };
  const displayValue = (row, field) => normalize(rawValue(row, field));

  const clearGroupRows = () => body.querySelectorAll('[data-list-group-row]').forEach((row) => row.remove());

  const matchesFilter = (row) => {
    const field = fieldSelect?.value || '';
    const value = normalize(filterInput?.value);
    if (!field || !value) return true;
    const cellValue = displayValue(row, field);
    switch (operatorSelect?.value) {
      case 'equals': return cellValue === value;
      case 'not-equals': return cellValue !== value;
      case 'starts-with': return cellValue.startsWith(value);
      case 'ends-with': return cellValue.endsWith(value);
      default: return cellValue.includes(value);
    }
  };

  const applyControls = () => {
    clearGroupRows();
    const visibleRows = rows.filter((row) => {
      const visible = matchesFilter(row);
      row.hidden = !visible;
      const selection = row.querySelector('[data-select-record]');
      if (!visible && selection) selection.checked = false;
      return visible;
    });
    const field = groupSelect?.value || '';
    if (field) {
      const grouped = new Map();
      visibleRows
        .sort((left, right) => displayValue(left, field).localeCompare(displayValue(right, field)))
        .forEach((row) => {
          const label = rawValue(row, field) || 'Not set';
          if (!grouped.has(label)) grouped.set(label, []);
          grouped.get(label).push(row);
        });
      grouped.forEach((groupRows, label) => {
        const groupRow = document.createElement('tr');
        groupRow.className = 'list-client-group-row';
        groupRow.dataset.listGroupRow = 'true';
        const cell = document.createElement('td');
        cell.colSpan = table.tHead.rows[0].cells.length;
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'list-client-group-toggle';
        const marker = document.createElement('span');
        const title = document.createElement('strong');
        const count = document.createElement('small');
        marker.setAttribute('aria-hidden', 'true');
        marker.textContent = '▾';
        title.textContent = label;
        count.textContent = `${groupRows.length} record${groupRows.length === 1 ? '' : 's'}`;
        toggle.append(marker, title, count);
        toggle.addEventListener('click', () => {
          const expanded = toggle.getAttribute('aria-expanded') === 'true';
          const nextExpanded = !expanded;
          toggle.setAttribute('aria-expanded', String(nextExpanded));
          toggle.querySelector('span').textContent = nextExpanded ? '▾' : '▸';
          groupRows.forEach((row) => { row.hidden = !nextExpanded; });
          updateSelection();
        });
        toggle.setAttribute('aria-expanded', 'true');
        cell.appendChild(toggle);
        groupRow.appendChild(cell);
        body.appendChild(groupRow);
        groupRows.forEach((row) => body.appendChild(row));
      });
    } else {
      rows.forEach((row) => body.appendChild(row));
    }
    if (noMatch) noMatch.hidden = visibleRows.length !== 0;
    if (filterCount) filterCount.hidden = !(fieldSelect?.value && filterInput?.value.trim());
    if (groupCount) groupCount.hidden = !field;
    updateSelection();
  };

  selectAll?.addEventListener('change', () => {
    selectionInputs.forEach((input) => {
      if (!input.closest('[data-list-row]').hidden) input.checked = selectAll.checked;
    });
    updateSelection();
  });
  selectionInputs.forEach((input) => input.addEventListener('change', updateSelection));
  bulkDeleteForm?.querySelector('[data-clear-selection]')?.addEventListener('click', () => {
    selectionInputs.forEach((input) => { input.checked = false; });
    const actionMenu = bulkDeleteForm.querySelector('details');
    if (actionMenu) actionMenu.open = false;
    updateSelection();
  });
  bulkDeleteForm?.querySelector('[data-open-delete-wizard]')?.addEventListener('click', () => {
    const selected = selectionInputs.filter((input) => input.checked);
    if (!selected.length || !deleteWizard || !wizardRecords) return;
    wizardRecords.replaceChildren();
    selected.forEach((input) => {
      const row = input.closest('[data-list-row]');
      const item = document.createElement('li');
      const id = document.createElement('span');
      const name = document.createElement('strong');
      id.textContent = `#${input.value}`;
      name.textContent = row?.dataset.devObjectName || `Record ${input.value}`;
      item.append(id, name);
      wizardRecords.appendChild(item);
    });
    if (wizardCount) wizardCount.textContent = String(selected.length);
    const actionMenu = bulkDeleteForm.querySelector('details');
    if (actionMenu) actionMenu.open = false;
    deleteWizard.showModal();
  });
  deleteWizard?.querySelectorAll('[data-close-delete-wizard]').forEach((button) => {
    button.addEventListener('click', () => deleteWizard.close());
  });
  deleteWizard?.addEventListener('click', (event) => {
    if (event.target === deleteWizard) deleteWizard.close();
  });
  bulkDeleteForm?.addEventListener('submit', () => {
    if (confirmDelete) {
      confirmDelete.disabled = true;
      confirmDelete.textContent = 'Deleting…';
    }
  });

  controls?.querySelector('[data-list-filter-clear]')?.addEventListener('click', () => {
    fieldSelect.value = '';
    filterInput.value = '';
    operatorSelect.value = 'contains';
    applyControls();
  });
  controls?.querySelector('[data-list-group-clear]')?.addEventListener('click', () => {
    groupSelect.value = '';
    applyControls();
  });
  [fieldSelect, operatorSelect, filterInput, groupSelect].filter(Boolean).forEach((control) => {
    control.addEventListener(control === filterInput ? 'input' : 'change', applyControls);
  });

  activateRecordRows(rows);
  updateSelection();
})();
