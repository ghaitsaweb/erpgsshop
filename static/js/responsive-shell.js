(() => {
  const sidebar = document.querySelector('[data-shell-sidebar]');
  const openButton = document.querySelector('[data-sidebar-open]');
  const closeButtons = document.querySelectorAll('[data-sidebar-close]');
  if (!sidebar || !openButton || !closeButtons.length) return;

  const compactViewport = window.matchMedia('(max-width: 1024px)');
  let returnFocus = null;

  const setOpen = (open, {restoreFocus = false} = {}) => {
    const nextOpen = compactViewport.matches && Boolean(open);
    sidebar.classList.toggle('is-open', nextOpen);
    document.body.classList.toggle('sidebar-open', nextOpen);
    openButton.setAttribute('aria-expanded', String(nextOpen));
    if (nextOpen) {
      returnFocus = document.activeElement;
      window.requestAnimationFrame(() => sidebar.querySelector('[data-sidebar-close]')?.focus());
    } else if (restoreFocus && returnFocus instanceof HTMLElement) {
      returnFocus.focus();
      returnFocus = null;
    }
  };

  openButton.addEventListener('click', () => setOpen(!sidebar.classList.contains('is-open')));
  closeButtons.forEach((button) => button.addEventListener('click', () => setOpen(false, {restoreFocus: true})));

  sidebar.addEventListener('click', (event) => {
    if (!compactViewport.matches) return;
    const link = event.target.closest('a[href]');
    if (link) setOpen(false);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && sidebar.classList.contains('is-open')) {
      event.preventDefault();
      setOpen(false, {restoreFocus: true});
    }
  });

  compactViewport.addEventListener?.('change', () => setOpen(false));
  setOpen(false);
})();
