(() => {
  const root = document.querySelector("[data-coworker-state-url]");
  if (!root) return;
  const poll = async () => {
    try {
      const response = await fetch(root.dataset.coworkerStateUrl, {headers: {"X-Requested-With": "XMLHttpRequest"}});
      if (!response.ok) return;
      const state = await response.json();
      if (state.terminal) window.location.reload();
    } catch (_error) {
      // A later poll can recover from transient network failures.
    }
  };
  window.setInterval(poll, 4000);
})();
