(() => {
  document.querySelectorAll('[data-smart-actions-drawer]').forEach((drawer) => {
    const layout = drawer.closest('.form-layout');
    const openButton = drawer.querySelector('[data-smart-actions-drawer-open]');
    const closeButton = drawer.querySelector('[data-smart-actions-drawer-close]');
    const content = drawer.querySelector('[data-smart-actions-drawer-content]');
    if (!layout || !openButton || !closeButton || !content) return;

    const setExpanded = (expanded) => {
      layout.classList.toggle('smart-actions-expanded', expanded);
      openButton.hidden = expanded;
      content.hidden = !expanded;
      openButton.setAttribute('aria-expanded', String(expanded));
      if (expanded) closeButton.focus();
      else openButton.focus();
    };

    openButton.addEventListener('click', () => setExpanded(true));
    closeButton.addEventListener('click', () => setExpanded(false));
  });

  document.querySelectorAll('[data-smart-actions]').forEach((container) => {
    const dialog = container.querySelector('[data-smart-actions-dialog]');
    const openButton = container.querySelector('[data-smart-actions-open]');
    const closeButton = container.querySelector('[data-smart-actions-close]');
    if (!dialog || !openButton || !closeButton) return;

    const closeDialog = () => {
      dialog.close();
      openButton.focus();
    };

    openButton.addEventListener('click', () => {
      dialog.showModal();
      closeButton.focus();
    });
    closeButton.addEventListener('click', closeDialog);
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) closeDialog();
    });
  });
})();
