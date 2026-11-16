(() => {
  const workspace = document.querySelector('[data-allocation-workspace]');
  if (!workspace) return;

  const currency = workspace.dataset.currency || 'IDR';
  const formatter = new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency,
    minimumFractionDigits: currency === 'IDR' ? 0 : 2,
    maximumFractionDigits: currency === 'IDR' ? 0 : 2,
  });
  const lines = [...workspace.querySelectorAll('[data-allocation-line]')];
  const balancedCount = workspace.querySelector('[data-balanced-count]');

  const refresh = () => {
    let balanced = 0;
    lines.forEach((line) => {
      const required = Number(line.dataset.required || 0);
      const inputs = [...line.querySelectorAll('[data-allocation-input]')];
      const allocated = inputs.reduce((total, input) => total + (Number(input.value) || 0), 0);
      inputs.forEach((input) => {
        const subtotal = (Number(input.value) || 0) * (Number(input.dataset.unitPrice) || 0);
        const cell = input.closest('tr')?.querySelector('[data-allocation-subtotal]');
        if (cell) cell.textContent = formatter.format(subtotal);
      });
      const isBalanced = Math.abs(allocated - required) < 0.0005;
      line.classList.toggle('is-balanced', isBalanced);
      line.classList.toggle('is-unbalanced', !isBalanced);
      const total = line.querySelector('[data-line-allocated]');
      const status = line.querySelector('[data-line-status]');
      if (total) total.textContent = allocated.toLocaleString(undefined, { maximumFractionDigits: 3 });
      if (status) status.textContent = isBalanced ? 'Balanced' : 'Adjust allocation';
      if (isBalanced) balanced += 1;
    });
    if (balancedCount) balancedCount.textContent = String(balanced);
  };

  workspace.addEventListener('input', (event) => {
    if (event.target.matches('[data-allocation-input]')) refresh();
  });
  refresh();
})();
