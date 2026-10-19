(() => {
  const switcher = document.getElementById('organization-switcher');
  const trigger = document.querySelector('[data-organization-open]');
  if (!switcher || !trigger) return;

  const closeButtons = switcher.querySelectorAll('[data-organization-close]');
  const companyList = switcher.querySelector('[data-company-list]');
  const branchList = switcher.querySelector('[data-branch-list]');
  const companySearch = switcher.querySelector('[data-company-search]');
  const selectionTitle = switcher.querySelector('[data-selection-title]');
  const selectionSubtitle = switcher.querySelector('[data-selection-subtitle]');
  const saveButton = switcher.querySelector('[data-organization-save]');
  const feedback = switcher.querySelector('[data-organization-feedback]');
  const optionsUrl = switcher.dataset.optionsUrl;
  const selectUrl = switcher.dataset.selectUrl;
  let companies = [];
  let selectedCompanyIds = new Set();
  let selectedBranchIds = new Set();
  let activeCompanyId = null;
  let activeBranchId = null;
  let returnFocus = null;

  const asNumber = (value) => value === null || value === undefined || value === '' ? null : Number(value);
  const csrfToken = () => document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
  const companyById = (id) => companies.find((company) => Number(company.id) === Number(id));
  const branchCompany = (branchId) => companies.find((company) => company.branches.some((branch) => Number(branch.id) === Number(branchId)));
  const escapeHtml = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

  const selectedCompanies = () => companies.filter((company) => selectedCompanyIds.has(Number(company.id)));
  const selectedBranchesFor = (company) => company.branches.filter((branch) => selectedBranchIds.has(Number(branch.id)));

  const ensurePrimary = () => {
    if (!selectedCompanyIds.has(Number(activeCompanyId))) {
      activeCompanyId = selectedCompanyIds.values().next().value ?? null;
    }
    const activeCompany = companyById(activeCompanyId);
    const selectedForActive = activeCompany ? selectedBranchesFor(activeCompany) : [];
    if (!selectedForActive.some((branch) => Number(branch.id) === Number(activeBranchId))) {
      activeBranchId = selectedForActive[0]?.id ?? null;
    }
  };

  const selectDefaultBranch = (company) => {
    if (!company?.branches.length || selectedBranchesFor(company).length) return;
    const branch = company.branches.find((item) => item.is_main) || company.branches[0];
    selectedBranchIds.add(Number(branch.id));
  };

  const renderCompanies = () => {
    const query = companySearch.value.trim().toLowerCase();
    const visible = companies.filter((company) => `${company.code} ${company.name}`.toLowerCase().includes(query));
    if (!visible.length) {
      companyList.innerHTML = '<div class="organization-empty">No accessible company found.</div>';
      return;
    }
    companyList.innerHTML = visible.map((company) => {
      const id = Number(company.id);
      const selected = selectedCompanyIds.has(id);
      const active = Number(activeCompanyId) === id;
      return `
        <div class="organization-option ${selected ? 'selected' : ''} ${active ? 'primary' : ''}">
          <button type="button" class="organization-option-select" data-company-id="${id}" aria-pressed="${selected}">
            <span class="organization-option-check">${selected ? '☑' : '☐'}</span>
            <span><strong>${escapeHtml(company.name)}</strong><small>${escapeHtml(company.code)} · ${company.branches.length} ${company.branches.length === 1 ? 'branch' : 'branches'}</small></span>
          </button>
          <button type="button" class="organization-primary ${active ? 'active' : ''}" data-company-primary-id="${id}" ${selected ? '' : 'disabled'}>${active ? 'ACTIVE' : 'Set active'}</button>
        </div>`;
    }).join('');
  };

  const renderBranches = () => {
    ensurePrimary();
    const scopedCompanies = selectedCompanies();
    const availableBranches = scopedCompanies.flatMap((company) => company.branches.map((branch) => ({ company, branch })));
    const missingBranchCompany = scopedCompanies.some((company) => company.branches.length && !selectedBranchesFor(company).length);
    selectionTitle.textContent = `${scopedCompanies.length} ${scopedCompanies.length === 1 ? 'company' : 'companies'} selected`;
    selectionSubtitle.textContent = activeCompanyId
      ? `${companyById(activeCompanyId)?.name || ''} is active. Select one or more branches for each company.`
      : 'Select at least one company.';

    if (!scopedCompanies.length) {
      branchList.innerHTML = '<div class="organization-empty">Select one or more companies first.</div>';
    } else if (!availableBranches.length) {
      branchList.innerHTML = '<div class="organization-empty">The selected companies have no active branches.</div>';
    } else {
      branchList.innerHTML = availableBranches.map(({ company, branch }) => {
        const id = Number(branch.id);
        const selected = selectedBranchIds.has(id);
        const active = Number(activeBranchId) === id && Number(activeCompanyId) === Number(company.id);
        return `
          <div class="branch-option ${selected ? 'selected' : ''} ${active ? 'primary' : ''}">
            <button type="button" class="branch-option-select" data-branch-id="${id}" aria-pressed="${selected}">
              <span><strong>${escapeHtml(branch.name)}</strong><small>${escapeHtml(company.code)} · ${escapeHtml(branch.code)}${branch.is_main ? ' · Main' : ''}</small></span>
              <i>${selected ? '☑' : '☐'}</i>
            </button>
            ${Number(activeCompanyId) === Number(company.id) ? `<button type="button" class="organization-primary ${active ? 'active' : ''}" data-branch-primary-id="${id}" ${selected ? '' : 'disabled'}>${active ? 'DEFAULT' : 'Set default'}</button>` : ''}
          </div>`;
      }).join('');
    }

    saveButton.disabled = !selectedCompanyIds.size || missingBranchCompany;
    feedback.textContent = `${companies.length} accessible · ${selectedCompanyIds.size} selected companies · ${selectedBranchIds.size} selected branches`;
    feedback.className = 'organization-feedback';
  };

  const render = () => {
    ensurePrimary();
    renderCompanies();
    renderBranches();
  };

  const loadOptions = async () => {
    feedback.textContent = 'Loading organization access…';
    feedback.className = 'organization-feedback';
    try {
      const response = await fetch(optionsUrl, { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error('Unable to load organization access.');
      const payload = await response.json();
      companies = payload.companies || [];
      selectedCompanyIds = new Set((payload.selected_company_ids || [payload.active_company_id]).filter(Boolean).map(Number));
      selectedBranchIds = new Set((payload.selected_branch_ids || [payload.active_branch_id]).filter(Boolean).map(Number));
      activeCompanyId = asNumber(payload.active_company_id);
      activeBranchId = asNumber(payload.active_branch_id);
      if (!selectedCompanyIds.size && companies[0]) {
        selectedCompanyIds.add(Number(companies[0].id));
        activeCompanyId = Number(companies[0].id);
        selectDefaultBranch(companies[0]);
      }
      render();
    } catch (error) {
      feedback.textContent = error.message;
      feedback.className = 'organization-feedback error';
    }
  };

  const open = () => {
    returnFocus = document.activeElement;
    switcher.hidden = false;
    document.body.classList.add('organization-switcher-open');
    companySearch.value = '';
    loadOptions();
    requestAnimationFrame(() => companySearch.focus());
  };

  const close = () => {
    switcher.hidden = true;
    document.body.classList.remove('organization-switcher-open');
    returnFocus?.focus();
  };

  trigger.addEventListener('click', open);
  closeButtons.forEach((button) => button.addEventListener('click', close));
  companySearch.addEventListener('input', renderCompanies);
  companyList.addEventListener('click', (event) => {
    const primary = event.target.closest('[data-company-primary-id]');
    if (primary) {
      activeCompanyId = Number(primary.dataset.companyPrimaryId);
      const company = companyById(activeCompanyId);
      selectDefaultBranch(company);
      activeBranchId = selectedBranchesFor(company)[0]?.id ?? null;
      render();
      return;
    }
    const toggle = event.target.closest('[data-company-id]');
    if (!toggle) return;
    const id = Number(toggle.dataset.companyId);
    const company = companyById(id);
    if (selectedCompanyIds.has(id)) {
      selectedCompanyIds.delete(id);
      company.branches.forEach((branch) => selectedBranchIds.delete(Number(branch.id)));
    } else {
      selectedCompanyIds.add(id);
      selectDefaultBranch(company);
      if (!activeCompanyId) activeCompanyId = id;
    }
    render();
  });
  branchList.addEventListener('click', (event) => {
    const primary = event.target.closest('[data-branch-primary-id]');
    if (primary) {
      activeBranchId = Number(primary.dataset.branchPrimaryId);
      render();
      return;
    }
    const toggle = event.target.closest('[data-branch-id]');
    if (!toggle) return;
    const id = Number(toggle.dataset.branchId);
    if (selectedBranchIds.has(id)) selectedBranchIds.delete(id);
    else selectedBranchIds.add(id);
    const company = branchCompany(id);
    if (Number(company?.id) === Number(activeCompanyId) && !activeBranchId) activeBranchId = id;
    render();
  });
  saveButton.addEventListener('click', async () => {
    ensurePrimary();
    saveButton.disabled = true;
    saveButton.textContent = 'Applying…';
    feedback.textContent = 'Validating selected company and branch scope…';
    try {
      const response = await fetch(selectUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          'X-CSRFToken': csrfToken(),
        },
        body: JSON.stringify({
          company_ids: [...selectedCompanyIds],
          branch_ids: [...selectedBranchIds],
          active_company_id: activeCompanyId,
          active_branch_id: activeBranchId,
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.success) throw new Error(payload.detail || 'Unable to apply organization scope.');
      window.location.reload();
    } catch (error) {
      feedback.textContent = error.message;
      feedback.className = 'organization-feedback error';
      saveButton.disabled = false;
      saveButton.textContent = 'Apply context';
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !switcher.hidden) close();
  });
})();
