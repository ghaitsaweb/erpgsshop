(() => {
  const debounce = (callback, wait = 180) => {
    let timer = null;
    return (...args) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => callback(...args), wait);
    };
  };

  const fetchResults = async (hiddenInput, query, more = false, recordId = '') => {
    const params = new URLSearchParams();
    if (recordId) params.set('id', recordId);
    else if (query) params.set('q', query);
    if (more) params.set('more', '1');
    const response = await fetch(`${hiddenInput.dataset.relationSearchUrl}?${params}`, {
      headers: {'Accept': 'application/json'},
    });
    if (!response.ok) throw new Error(`Relation search failed (${response.status})`);
    return (await response.json()).results || [];
  };

  const resultButton = (record, selectRecord) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'relation-selector-result';
    button.textContent = record.text;
    button.addEventListener('click', () => selectRecord(record));
    return button;
  };

  const enhance = (hiddenInput) => {
    if (hiddenInput.dataset.relationSelectorReady === 'true') return;
    hiddenInput.dataset.relationSelectorReady = 'true';

    const wrapper = document.createElement('div');
    const searchInput = document.createElement('input');
    const dropdown = document.createElement('div');
    const resultList = document.createElement('div');
    const moreButton = document.createElement('button');
    const dialog = document.createElement('dialog');
    const dialogPanel = document.createElement('div');
    const dialogHeader = document.createElement('header');
    const dialogTitle = document.createElement('strong');
    const dialogClose = document.createElement('button');
    const dialogSearch = document.createElement('input');
    const dialogResults = document.createElement('div');
    let activeRequest = 0;

    wrapper.className = 'relation-selector';
    searchInput.type = 'search';
    searchInput.className = 'form-control relation-selector-input';
    searchInput.placeholder = hiddenInput.dataset.relationSearchPlaceholder || 'Search...';
    searchInput.autocomplete = 'off';
    searchInput.value = hiddenInput.dataset.initialLabel || '';
    searchInput.readOnly = hiddenInput.disabled;
    searchInput.required = hiddenInput.required;
    searchInput.dataset.selectedLabel = hiddenInput.value ? searchInput.value : '';
    searchInput.setAttribute('role', 'combobox');
    searchInput.setAttribute('aria-expanded', 'false');

    dropdown.className = 'relation-selector-dropdown';
    dropdown.hidden = true;
    resultList.className = 'relation-selector-results';
    moreButton.type = 'button';
    moreButton.className = 'relation-selector-more';
    moreButton.textContent = 'Search More…';
    dropdown.append(resultList, moreButton);
    wrapper.append(searchInput, dropdown);
    hiddenInput.insertAdjacentElement('afterend', wrapper);

    dialog.className = 'relation-search-dialog';
    dialogPanel.className = 'relation-search-dialog-panel';
    dialogHeader.className = 'relation-search-dialog-head';
    dialogTitle.textContent = `Search ${hiddenInput.dataset.relationSearchPlaceholder?.replace(/^Search\s+|\.\.\.$/g, '') || 'records'}`;
    dialogClose.type = 'button';
    dialogClose.className = 'relation-search-dialog-close';
    dialogClose.setAttribute('aria-label', 'Close');
    dialogClose.textContent = '×';
    dialogSearch.type = 'search';
    dialogSearch.className = 'form-control';
    dialogSearch.placeholder = 'Type to search records...';
    dialogSearch.autocomplete = 'off';
    dialogResults.className = 'relation-search-dialog-results';
    dialogHeader.append(dialogTitle, dialogClose);
    dialogPanel.append(dialogHeader, dialogSearch, dialogResults);
    dialog.append(dialogPanel);
    document.body.append(dialog);

    const closeDropdown = () => {
      dropdown.hidden = true;
      searchInput.setAttribute('aria-expanded', 'false');
    };
    const openDropdown = () => {
      if (hiddenInput.disabled) return;
      dropdown.hidden = false;
      searchInput.setAttribute('aria-expanded', 'true');
    };
    const closeDialog = () => {
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
    };
    const selectRecord = (record) => {
      hiddenInput.value = String(record.id);
      searchInput.value = record.text;
      searchInput.dataset.selectedLabel = record.text;
      searchInput.setCustomValidity('');
      hiddenInput.dispatchEvent(new Event('change', {bubbles: true}));
      closeDropdown();
      closeDialog();
    };
    const render = (container, records) => {
      container.replaceChildren();
      if (!records.length) {
        const empty = document.createElement('p');
        empty.className = 'relation-selector-empty';
        empty.textContent = 'No matching records.';
        container.append(empty);
        return;
      }
      records.forEach((record) => container.append(resultButton(record, selectRecord)));
    };
    const loadSuggestions = async () => {
      const requestId = ++activeRequest;
      try {
        const records = await fetchResults(hiddenInput, searchInput.value.trim());
        if (requestId !== activeRequest) return;
        render(resultList, records);
        openDropdown();
      } catch (error) {
        if (requestId === activeRequest) render(resultList, []);
      }
    };
    const loadDialog = async () => {
      try {
        render(dialogResults, await fetchResults(hiddenInput, dialogSearch.value.trim(), true));
      } catch (error) {
        render(dialogResults, []);
      }
    };
    const validate = () => {
      if (searchInput.required && !hiddenInput.value) {
        searchInput.setCustomValidity('Select a record from the search results.');
        return false;
      }
      searchInput.setCustomValidity('');
      return true;
    };

    searchInput.addEventListener('focus', loadSuggestions);
    searchInput.addEventListener('input', debounce(() => {
      if (searchInput.value !== searchInput.dataset.selectedLabel) hiddenInput.value = '';
      searchInput.setCustomValidity('');
      loadSuggestions();
    }));
    searchInput.addEventListener('blur', validate);
    searchInput.form?.addEventListener('submit', (event) => {
      if (!validate()) {
        event.preventDefault();
        searchInput.reportValidity();
      }
    });
    moreButton.addEventListener('click', () => {
      closeDropdown();
      dialogSearch.value = searchInput.value;
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
      loadDialog();
      window.requestAnimationFrame(() => dialogSearch.focus());
    });
    dialogClose.addEventListener('click', closeDialog);
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) closeDialog();
    });
    dialogSearch.addEventListener('input', debounce(loadDialog));
    document.addEventListener('click', (event) => {
      if (!wrapper.contains(event.target)) closeDropdown();
    });

    if (hiddenInput.value && !searchInput.value) {
      fetchResults(hiddenInput, '', false, hiddenInput.value)
        .then((records) => { if (records[0]) selectRecord(records[0]); })
        .catch(() => {});
    }
  };

  const enhanceAll = (root = document) => {
    root.querySelectorAll?.('input[data-relation-search-url]').forEach(enhance);
  };
  enhanceAll();
  new MutationObserver((mutations) => {
    mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
      if (node.nodeType === Node.ELEMENT_NODE) enhanceAll(node);
    }));
  }).observe(document.body, {childList: true, subtree: true});
})();
