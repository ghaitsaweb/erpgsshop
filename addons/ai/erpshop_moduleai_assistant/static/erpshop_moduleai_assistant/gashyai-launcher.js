(() => {
  const root = document.querySelector('[data-gashyai-shell]');
  if (!root || root.dataset.ready === 'true') return;
  root.dataset.ready = 'true';

  const launcher = root.querySelector('[data-gashyai-launcher]');
  const panel = root.querySelector('[data-gashyai-panel]');
  const closeButton = root.querySelector('[data-gashyai-close]');
  const newChatButton = root.querySelector('[data-gashyai-new-chat]');
  const sessionsNode = root.querySelector('[data-gashyai-sessions]');
  const messagesNode = root.querySelector('[data-gashyai-messages]');
  const feedbackNode = root.querySelector('[data-gashyai-feedback]');
  const composer = root.querySelector('[data-gashyai-composer]');
  const textarea = composer.querySelector('textarea');
  const sendButton = composer.querySelector('button[type="submit"]');
  const workspaceLink = root.querySelector('[data-gashyai-workspace-link]');
  const storageKey = 'gashyai_launcher_position_ratio';
  const launcherSize = 68;
  const launcherMargin = 28;
  const dragThreshold = 5;
  let activeSessionId = null;
  let activeSendUrl = '';
  let dragState = null;
  let suppressClick = false;
  let refreshFrame = null;
  let pollTimer = null;

  const viewportRect = () => {
    const viewport = window.visualViewport;
    return viewport ? {
      left: viewport.offsetLeft || 0,
      top: viewport.offsetTop || 0,
      width: viewport.width || window.innerWidth,
      height: viewport.height || window.innerHeight,
    } : {left: 0, top: 0, width: window.innerWidth, height: window.innerHeight};
  };

  const clampPosition = (left, top) => {
    const viewport = viewportRect();
    const horizontalMargin = Math.min(launcherMargin, Math.max(0, viewport.width - launcherSize));
    const verticalMargin = Math.min(launcherMargin, Math.max(0, viewport.height - launcherSize));
    const minLeft = viewport.left + horizontalMargin;
    const minTop = viewport.top + verticalMargin;
    const maxLeft = Math.max(minLeft, viewport.left + viewport.width - launcherSize - horizontalMargin);
    const maxTop = Math.max(minTop, viewport.top + viewport.height - launcherSize - verticalMargin);
    return {
      left: Math.min(Math.max(left, minLeft), maxLeft),
      top: Math.min(Math.max(top, minTop), maxTop),
    };
  };

  const parseRatio = (value) => {
    try {
      const parsed = typeof value === 'string' ? JSON.parse(value) : value;
      if (!parsed || !Number.isFinite(Number(parsed.xRatio)) || !Number.isFinite(Number(parsed.yRatio))) return null;
      return {
        xRatio: Math.min(Math.max(Number(parsed.xRatio), 0), 1),
        yRatio: Math.min(Math.max(Number(parsed.yRatio), 0), 1),
      };
    } catch (_) {
      return null;
    }
  };

  const loadRatio = () => parseRatio(window.localStorage?.getItem(storageKey)) || {xRatio: 1, yRatio: 1};
  let positionRatio = loadRatio();

  const applyPosition = (left, top) => {
    const position = clampPosition(left, top);
    launcher.style.right = 'auto';
    launcher.style.bottom = 'auto';
    launcher.style.left = `${position.left}px`;
    launcher.style.top = `${position.top}px`;
    return position;
  };

  const applyRatio = () => {
    const viewport = viewportRect();
    applyPosition(
      viewport.left + viewport.width * positionRatio.xRatio - launcherSize / 2,
      viewport.top + viewport.height * positionRatio.yRatio - launcherSize / 2,
    );
  };

  const savePosition = () => {
    const viewport = viewportRect();
    const position = clampPosition(parseFloat(launcher.style.left), parseFloat(launcher.style.top));
    positionRatio = parseRatio({
      xRatio: (position.left + launcherSize / 2 - viewport.left) / viewport.width,
      yRatio: (position.top + launcherSize / 2 - viewport.top) / viewport.height,
    }) || {xRatio: 1, yRatio: 1};
    try { window.localStorage?.setItem(storageKey, JSON.stringify(positionRatio)); } catch (_) { /* private mode */ }
  };

  const schedulePositionRefresh = () => {
    if (refreshFrame) return;
    refreshFrame = window.requestAnimationFrame(() => {
      refreshFrame = null;
      if (!dragState) applyRatio();
    });
  };

  launcher.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    launcher.setPointerCapture?.(event.pointerId);
    dragState = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      startLeft: parseFloat(launcher.style.left),
      startTop: parseFloat(launcher.style.top),
      moved: false,
    };
    launcher.classList.add('is-dragging');
  });

  launcher.addEventListener('pointermove', (event) => {
    if (!dragState || dragState.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - dragState.clientX;
    const deltaY = event.clientY - dragState.clientY;
    if (Math.max(Math.abs(deltaX), Math.abs(deltaY)) >= dragThreshold) dragState.moved = true;
    applyPosition(dragState.startLeft + deltaX, dragState.startTop + deltaY);
  });

  const finishDrag = (event) => {
    if (!dragState || dragState.pointerId !== event.pointerId) return;
    suppressClick = dragState.moved;
    if (dragState.moved) savePosition();
    launcher.releasePointerCapture?.(event.pointerId);
    dragState = null;
    launcher.classList.remove('is-dragging');
    applyRatio();
  };
  launcher.addEventListener('pointerup', finishDrag);
  launcher.addEventListener('pointercancel', finishDrag);

  const setFeedback = (message = '', isError = false) => {
    feedbackNode.textContent = message;
    feedbackNode.classList.toggle('is-error', isError);
    feedbackNode.hidden = !message;
  };

  const createElement = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };

  const renderSessions = (sessions) => {
    sessionsNode.replaceChildren();
    sessions.forEach((session) => {
      const button = createElement('button', `gashyai-session-chip${session.id === activeSessionId ? ' is-active' : ''}`, session.title);
      button.type = 'button';
      button.title = session.title;
      button.addEventListener('click', () => loadState(session.id));
      sessionsNode.appendChild(button);
    });
  };

  const renderMessages = (messages, pending) => {
    messagesNode.replaceChildren();
    if (!messages.length) {
      const welcome = createElement('div', 'gashyai-welcome');
      const mark = createElement('span', 'gashyai-welcome-mark');
      const icon = root.querySelector('.gashyai-mark img').cloneNode();
      icon.alt = '';
      mark.appendChild(icon);
      welcome.append(mark, createElement('h2', '', 'Mulai percakapan baru'), createElement('p', '', 'Tanyakan ringkasan, analisis, atau panduan berdasarkan scope ERP yang sedang aktif.'));
      messagesNode.appendChild(welcome);
    }
    messages.forEach((message) => {
      const article = createElement('article', `gashyai-message${message.role === 'user' ? ' is-user' : ''}${message.state === 'failed' ? ' is-failed' : ''}`);
      const header = createElement('header');
      header.append(createElement('span', '', message.role === 'assistant' ? 'GashyAI' : message.role === 'user' ? 'Anda' : 'System'), createElement('time', '', message.time));
      article.append(header, createElement('p', '', message.content || (message.state === 'pending' ? 'Menyiapkan respons…' : '')));
      messagesNode.appendChild(article);
    });
    if (pending) {
      const thinking = createElement('div', 'gashyai-thinking');
      thinking.setAttribute('aria-label', 'GashyAI sedang berpikir');
      thinking.append(createElement('i'), createElement('i'), createElement('i'));
      messagesNode.appendChild(thinking);
    }
    messagesNode.scrollTop = messagesNode.scrollHeight;
  };

  const schedulePoll = (pending) => {
    window.clearTimeout(pollTimer);
    pollTimer = null;
    if (pending && !panel.hidden && activeSessionId) {
      pollTimer = window.setTimeout(() => loadState(activeSessionId, true), 1500);
    }
  };

  const loadState = async (sessionId = null, quiet = false) => {
    if (!quiet) setFeedback('Memuat percakapan…');
    const url = new URL(root.dataset.stateUrl, window.location.origin);
    if (sessionId) url.searchParams.set('session', sessionId);
    try {
      const response = await fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || 'Percakapan tidak dapat dimuat.');
      activeSessionId = payload.selected_session_id;
      activeSendUrl = payload.send_url || '';
      workspaceLink.href = payload.workspace_url || root.dataset.workspaceUrl;
      renderSessions(payload.sessions || []);
      renderMessages(payload.messages || [], payload.pending);
      textarea.disabled = !activeSessionId;
      sendButton.disabled = !activeSessionId;
      setFeedback(payload.configuration_ready ? '' : 'Provider belum aktif. Anda tetap dapat menyiapkan percakapan; admin perlu melengkapi konfigurasi.');
      schedulePoll(payload.pending);
    } catch (error) {
      setFeedback(error.message || 'GashyAI tidak dapat dimuat.', true);
    }
  };

  const csrfToken = () => composer.querySelector('[name=csrfmiddlewaretoken]').value;

  const createSession = async () => {
    newChatButton.disabled = true;
    setFeedback('Membuat chat baru…');
    const data = new FormData();
    data.set('title', 'New conversation');
    data.set('csrfmiddlewaretoken', csrfToken());
    try {
      const response = await fetch(root.dataset.newSessionUrl, {
        method: 'POST',
        headers: {'X-Requested-With': 'XMLHttpRequest'},
        body: data,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || 'Chat baru tidak dapat dibuat.');
      await loadState(payload.session_id);
      textarea.focus();
    } catch (error) {
      setFeedback(error.message || 'Chat baru tidak dapat dibuat.', true);
    } finally {
      newChatButton.disabled = false;
    }
  };

  const sendMessage = async () => {
    const content = textarea.value.trim();
    if (!content || !activeSendUrl) return;
    sendButton.disabled = true;
    const data = new FormData();
    data.set('content', content);
    data.set('csrfmiddlewaretoken', csrfToken());
    try {
      const response = await fetch(activeSendUrl, {
        method: 'POST',
        headers: {'X-Requested-With': 'XMLHttpRequest'},
        body: data,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || 'Pesan tidak dapat dikirim.');
      textarea.value = '';
      await loadState(activeSessionId, true);
    } catch (error) {
      setFeedback(error.message || 'Pesan tidak dapat dikirim.', true);
    } finally {
      sendButton.disabled = false;
    }
  };

  const openPanel = () => {
    panel.hidden = false;
    launcher.setAttribute('aria-expanded', 'true');
    loadState(activeSessionId);
    window.requestAnimationFrame(() => textarea.focus());
  };

  const closePanel = () => {
    panel.hidden = true;
    launcher.setAttribute('aria-expanded', 'false');
    window.clearTimeout(pollTimer);
    pollTimer = null;
    launcher.focus();
  };

  launcher.addEventListener('click', () => {
    if (suppressClick) { suppressClick = false; return; }
    if (panel.hidden) openPanel(); else closePanel();
  });
  closeButton.addEventListener('click', closePanel);
  newChatButton.addEventListener('click', createSession);
  composer.addEventListener('submit', (event) => { event.preventDefault(); sendMessage(); });
  textarea.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); composer.requestSubmit(); }
  });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !panel.hidden) closePanel(); });
  window.addEventListener('resize', schedulePositionRefresh);
  window.visualViewport?.addEventListener('resize', schedulePositionRefresh);
  window.visualViewport?.addEventListener('scroll', schedulePositionRefresh);
  applyRatio();
})();
