(() => {
  const companyField = document.querySelector('form select[name="company"]');
  const scopedFields = [...document.querySelectorAll('form select[name="branch"], form select[name="parent"]')];
  const switcher = document.getElementById('organization-switcher');
  if (!companyField || !scopedFields.length || !switcher?.dataset.optionsUrl) return;

  const originalOptions = new Map(
    scopedFields.map((field) => [field, [...field.options].map((option) => option.cloneNode(true))])
  );

  fetch(switcher.dataset.optionsUrl, { headers: { Accept: 'application/json' } })
    .then((response) => {
      if (!response.ok) throw new Error('Organization options are unavailable.');
      return response.json();
    })
    .then((payload) => {
      const branchCompany = new Map();
      (payload.companies || []).forEach((company) => {
        (company.branches || []).forEach((branch) => branchCompany.set(String(branch.id), String(company.id)));
      });
      const selectedBranchIds = (payload.selected_branch_ids || []).map(String);

      const refresh = () => {
        const companyId = String(companyField.value || '');
        scopedFields.forEach((field) => {
          const previous = String(field.value || '');
          field.replaceChildren(...originalOptions.get(field).map((option) => option.cloneNode(true)));
          [...field.options].forEach((option) => {
            if (!option.value) return;
            const visible = branchCompany.get(String(option.value)) === companyId;
            option.hidden = !visible;
            option.disabled = !visible;
          });
          const previousOption = [...field.options].find((option) => option.value === previous && !option.disabled);
          if (previousOption) {
            field.value = previous;
          } else if (field.name === 'branch') {
            field.value = selectedBranchIds.find((id) => branchCompany.get(id) === companyId && [...field.options].some((option) => option.value === id && !option.disabled)) || '';
          } else {
            field.value = '';
          }
        });
      };

      companyField.addEventListener('change', refresh);
      refresh();
    })
    .catch(() => {
      // Server-side querysets and model validation remain authoritative.
    });
})();
