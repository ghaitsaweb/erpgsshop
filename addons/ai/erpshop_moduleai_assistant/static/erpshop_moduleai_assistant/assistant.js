(() => {
  const workspace = document.querySelector('[data-assistant-workspace]');
  if (!workspace) return;
  const textarea = workspace.querySelector('[data-assistant-textarea]');
  const files = workspace.querySelector('[data-assistant-files]');
  const note = workspace.querySelector('[data-assistant-file-note]');
  const list = workspace.querySelector('[data-assistant-messages]');

  if (textarea) {
    textarea.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        textarea.closest('form').requestSubmit();
      }
    });
  }
  if (files && note) {
    files.addEventListener('change', () => {
      const selected = [...files.files];
      note.textContent = selected.length ? `${selected.length} file(s): ${selected.map((file) => file.name).join(', ')}` : note.dataset.defaultNote || note.textContent;
    });
  }
  if (list) list.scrollTop = list.scrollHeight;

  const stateUrl = workspace.dataset.stateUrl;
  if (!stateUrl) return;
  let pollCount = 0;
  const poll = async () => {
    try {
      const response = await fetch(stateUrl, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
      const state = await response.json();
      if (state.pending) {
        pollCount += 1;
        window.setTimeout(poll, Math.min(5000, 1200 + pollCount * 300));
      } else if (pollCount > 0) {
        window.location.reload();
      }
    } catch (_) {
      // Retry a transient error without interrupting the user composing a message.
      pollCount += 1;
      window.setTimeout(poll, Math.min(30000, 1200 * (2 ** Math.min(pollCount, 5))));
    }
  };
  if (workspace.querySelector('.hashy-message.state-pending')) poll();

  workspace.querySelectorAll('[data-feedback-for] form').forEach((form) => {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const response = await fetch(form.action, {
        method: 'POST',
        headers: {'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value},
        body: new FormData(form),
      });
      if (response.ok) {
        form.parentElement.querySelectorAll('button').forEach((button) => button.classList.remove('selected'));
        form.querySelector('button').classList.add('selected');
      }
    });
  });
})();
