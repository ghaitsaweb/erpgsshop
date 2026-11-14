(() => {
  document.querySelectorAll('[data-image-field-preview]').forEach((preview) => {
    const field = preview.closest('.field');
    const input = field?.querySelector('input[type="file"]');
    const image = preview.querySelector('[data-image-field-preview-image]');
    const empty = preview.querySelector('[data-image-field-preview-empty]');
    const clear = field?.querySelector('input[type="checkbox"][name$="-clear"]');
    if (!input || !image || !empty) return;

    const savedSource = image.dataset.savedSrc || '';
    let objectUrl = '';

    const releaseObjectUrl = () => {
      if (!objectUrl) return;
      URL.revokeObjectURL(objectUrl);
      objectUrl = '';
    };
    const showImage = (source) => {
      image.src = source;
      image.hidden = false;
      empty.hidden = true;
    };
    const showEmpty = (message = 'No image selected') => {
      image.removeAttribute('src');
      image.hidden = true;
      empty.textContent = message;
      empty.hidden = false;
    };

    input.addEventListener('change', () => {
      releaseObjectUrl();
      const [file] = input.files || [];
      if (!file) {
        if (clear?.checked || !savedSource) showEmpty();
        else showImage(savedSource);
        return;
      }
      if (!file.type.startsWith('image/')) {
        showEmpty('Image preview unavailable');
        return;
      }
      if (clear) clear.checked = false;
      objectUrl = URL.createObjectURL(file);
      showImage(objectUrl);
    });

    clear?.addEventListener('change', () => {
      releaseObjectUrl();
      if (clear.checked) {
        input.value = '';
        showEmpty();
      } else if (savedSource) {
        showImage(savedSource);
      }
    });

    image.addEventListener('error', () => showEmpty('Image preview unavailable'));
    window.addEventListener('pagehide', releaseObjectUrl, { once: true });
  });
})();
