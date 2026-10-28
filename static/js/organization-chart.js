const initializeOrganizationChart = () => {
  const form = document.getElementById('organization-chart-move');
  if (!form) return;

  const employeeInput = form.querySelector('[name="employee_id"]');
  const managerInput = form.querySelector('[name="supervisor_id"]');
  const editToggle = document.querySelector('[data-org-edit-toggle]');
  const editorStatus = document.querySelector('[data-org-editor-status]');
  const cards = [...document.querySelectorAll('[data-org-card]')];
  const dropZones = [...document.querySelectorAll('[data-org-drop-zone]')];
  let draggedEmployee = null;
  let selectedCard = null;
  let editing = false;

  const clearDropTargets = () => dropZones.forEach((target) => {
    target.classList.remove('is-drop-target');
  });

  const clearSelection = () => {
    if (selectedCard) selectedCard.classList.remove('is-selected-for-move');
    selectedCard = null;
  };

  const canMoveTo = (source, target) => (
    source
    && target
    && source.dataset.orgEmployee !== target.dataset.orgEmployee
    && source.dataset.orgCompany === target.dataset.orgCompany
  );

  const submitMove = async (source, target) => {
    if (!canMoveTo(source, target)) return;
    employeeInput.value = source.dataset.orgEmployee;
    managerInput.value = target.dataset.orgManager || '';
    clearSelection();
    if (editToggle) editToggle.disabled = true;
    if (editorStatus) editorStatus.textContent = 'Saving reporting line…';
    try {
      const response = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest', Accept: 'application/json' },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) throw new Error(payload.message || 'The reporting line could not be saved.');
      if (editorStatus) editorStatus.textContent = `${payload.employee} now reports to ${payload.destination}. Refreshing chart…`;
      window.setTimeout(() => window.location.reload(), 180);
    } catch (error) {
      if (editorStatus) editorStatus.textContent = error.message || 'The reporting line could not be saved.';
      window.alert(error.message || 'The reporting line could not be saved.');
      if (editToggle) editToggle.disabled = false;
    }
  };

  const setEditing = (enabled) => {
    editing = enabled;
    clearSelection();
    clearDropTargets();
    document.querySelectorAll('.org-chart-company').forEach((company) => company.classList.toggle('is-editing', enabled));
    cards.forEach((card) => {
      card.draggable = enabled;
      card.setAttribute('aria-grabbed', enabled ? 'false' : 'undefined');
    });
    if (editToggle) editToggle.setAttribute('aria-pressed', String(enabled));
    if (editorStatus) editorStatus.textContent = enabled ? 'Editing enabled' : 'Editing disabled';
  };

  setEditing(form.dataset.editing === 'true');

  cards.forEach((card) => {
    card.addEventListener('dragstart', (event) => {
      if (!editing) {
        event.preventDefault();
        return;
      }
      draggedEmployee = card.dataset.orgEmployee;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', draggedEmployee);
      card.classList.add('is-dragging');
    });
    card.addEventListener('dragend', () => {
      draggedEmployee = null;
      card.classList.remove('is-dragging');
      clearDropTargets();
    });
    card.addEventListener('click', (event) => {
      if (!editing || event.target.closest('a')) return;
      if (selectedCard === card) {
        clearSelection();
      } else if (selectedCard) {
        submitMove(selectedCard, card);
      } else {
        selectedCard = card;
        card.classList.add('is-selected-for-move');
        if (editorStatus) editorStatus.textContent = `Selected ${card.querySelector('strong')?.textContent || 'employee'} — choose a manager or Top level`;
      }
    });
  });

  dropZones.forEach((target) => {
    target.addEventListener('dragover', (event) => {
      if (!editing) return;
      if (!draggedEmployee || target.dataset.orgEmployee === draggedEmployee) return;
      const source = document.querySelector(`[data-org-card][data-org-employee="${draggedEmployee}"]`);
      if (!source || source.dataset.orgCompany !== target.dataset.orgCompany) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      target.classList.add('is-drop-target');
    });
    target.addEventListener('dragleave', () => target.classList.remove('is-drop-target'));
    target.addEventListener('drop', (event) => {
      event.preventDefault();
      target.classList.remove('is-drop-target');
      if (!editing) return;
      if (!draggedEmployee || target.dataset.orgEmployee === draggedEmployee) return;
      const source = document.querySelector(`[data-org-card][data-org-employee="${draggedEmployee}"]`);
      submitMove(source, target);
    });
    target.addEventListener('click', () => {
      if (!editing || !selectedCard || target.dataset.orgEmployee) return;
      submitMove(selectedCard, target);
    });
  });
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeOrganizationChart, { once: true });
} else {
  initializeOrganizationChart();
}
