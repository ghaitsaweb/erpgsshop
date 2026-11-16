(() => {
  const form = document.querySelector('form[data-dev-entity="purchase-tenders"]');
  if (!form) return;
  const evaluationWeightInputs = [
    'evaluation_price_weight',
    'evaluation_delivery_weight',
    'evaluation_compliance_weight',
    'evaluation_term_weight',
    'evaluation_history_weight',
  ].map((name) => form.querySelector(`#id_${name}`)).filter(Boolean);
  if (evaluationWeightInputs.length) {
    const weightContainer = evaluationWeightInputs[0].closest('.document-header-grid');
    const totalCard = document.createElement('aside');
    totalCard.className = 'tender-evaluation-total';
    totalCard.setAttribute('aria-live', 'polite');
    weightContainer?.append(totalCard);
    const updateEvaluationWeights = () => {
      const total = evaluationWeightInputs.reduce(
        (sum, input) => sum + (Number.parseFloat(input.value) || 0),
        0,
      );
      for (const input of evaluationWeightInputs) {
        input.min = '0';
        input.max = '100';
        input.inputMode = 'decimal';
        input.closest('.field')?.classList.add('tender-evaluation-weight-card');
        input.closest('.field')?.style.setProperty(
          '--tender-weight-percent',
          `${Math.min(Math.max(Number.parseFloat(input.value) || 0, 0), 100)}%`,
        );
      }
      totalCard.classList.toggle('is-valid', total === 100);
      totalCard.innerHTML = `<span>Evaluation total</span><strong>${total}%</strong><small>${
        total === 100 ? 'Ready for scoring' : 'Weights must total exactly 100%'
      }</small>`;
    };
    evaluationWeightInputs.forEach((input) => input.addEventListener('input', updateEvaluationWeights));
    updateEvaluationWeights();
  }
  const templateSelect = form.querySelector('#id_order_template');
  const companySelect = form.querySelector('#id_company');
  const orderTypeSelect = form.querySelector('#id_order_type');
  const expiryInput = form.querySelector('#id_expiry_date');
  const warehouseSelect = form.querySelector('#id_warehouse');
  const lineSection = form.querySelector('[data-line-section]');
  const lineBody = lineSection?.querySelector('[data-document-line-body]');
  const addLineButton = lineSection?.querySelector('[data-add-document-line]');
  const onchangeUrl = templateSelect?.dataset.tenderOnchangeUrl;
  if (!lineSection || !lineBody || !addLineButton || !onchangeUrl) return;

  const lineField = (row, suffix) => row.querySelector(`[name$="-${suffix}"]`);
  const activeRows = () => [...lineBody.querySelectorAll('.document-line-row')]
    .filter((row) => !row.hidden && !lineField(row, 'DELETE')?.checked);
  const headerValue = (name) => form.querySelector(`#id_${name}`)?.value || '';
  const setIfBlank = (input, value) => {
    if (input && !input.value && value) input.value = value;
  };

  const applyHeaderDefaults = (row) => {
    setIfBlank(lineField(row, 'expiry_date'), expiryInput?.value);
    setIfBlank(lineField(row, 'destination_warehouse'), warehouseSelect?.value);
    for (const field of [
      'cost_center',
      'cost_center_mode',
      'cost_center_distribution_ids',
      'cost_center_distribution_data',
      'cost_center_analytic_tag_ids',
    ]) {
      setIfBlank(lineField(row, field), headerValue(field));
    }
  };

  const clearRows = () => {
    activeRows().forEach((row) => row.querySelector('.line-remove')?.click());
  };

  const addTemplateRow = (values) => {
    addLineButton.click();
    const row = lineBody.lastElementChild;
    if (!row) return;
    const assignments = {
      product: values.product_id,
      description: values.description,
      quantity: values.quantity,
      target_unit_price: values.target_unit_price,
    };
    Object.entries(assignments).forEach(([field, value]) => {
      const input = lineField(row, field);
      if (input) input.value = value ?? '';
    });
    applyHeaderDefaults(row);
  };

  templateSelect.addEventListener('change', async () => {
    if (!templateSelect.value) return;
    const params = new URLSearchParams({
      order_template_id: templateSelect.value,
      company_id: companySelect?.value || '',
    });
    try {
      const response = await fetch(`${onchangeUrl}?${params}`, {headers: {Accept: 'application/json'}});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || 'Unable to apply Order Template.');
      if (payload.order_type_id && orderTypeSelect) {
        orderTypeSelect.value = String(payload.order_type_id);
      }
      if (payload.lines?.length) {
        clearRows();
        payload.lines.forEach(addTemplateRow);
      }
    } catch (error) {
      window.alert(error.message);
    }
  });

  lineBody.addEventListener('change', async (event) => {
    const input = event.target;
    const row = input.closest('.document-line-row');
    if (!row) return;
    if (input.matches('[name$="-product"]') && input.value) {
      const endpoint = input.dataset.tenderProductOnchangeUrl || onchangeUrl;
      const params = new URLSearchParams({
        product_id: input.value,
        company_id: companySelect?.value || '',
      });
      try {
        const response = await fetch(`${endpoint}?${params}`, {headers: {Accept: 'application/json'}});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Unable to apply Product defaults.');
        const description = lineField(row, 'description');
        if (description) description.value = payload.product.description || '';
        applyHeaderDefaults(row);
      } catch (error) {
        window.alert(error.message);
      }
    }
    if (input.matches('[name$="-expiry_date"]') && !expiryInput?.value) {
      expiryInput.value = input.value;
    }
    if (input.matches('[name$="-destination_warehouse"]') && !warehouseSelect?.value) {
      warehouseSelect.value = input.value;
    }
  });

  for (const input of [expiryInput, warehouseSelect]) {
    input?.addEventListener('change', () => activeRows().forEach(applyHeaderDefaults));
  }
  for (const name of [
    'cost_center',
    'cost_center_mode',
    'cost_center_distribution_ids',
    'cost_center_distribution_data',
    'cost_center_analytic_tag_ids',
  ]) {
    form.querySelector(`#id_${name}`)?.addEventListener(
      'change',
      () => activeRows().forEach(applyHeaderDefaults),
    );
  }
  activeRows().forEach(applyHeaderDefaults);
})();
