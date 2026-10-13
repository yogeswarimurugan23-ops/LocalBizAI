(() => {
  const sidebar = document.querySelector('#site-sidebar');
  const menuToggle = document.querySelector('[data-menu-toggle]');
  const scrim = document.querySelector('[data-sidebar-scrim]');

  const setMenu = (open) => {
    if (!sidebar || !menuToggle || !scrim) return;
    sidebar.classList.toggle('open', open);
    scrim.classList.toggle('visible', open);
    menuToggle.setAttribute('aria-expanded', String(open));
    menuToggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
    document.body.style.overflow = open ? 'hidden' : '';
  };

  menuToggle?.addEventListener('click', () => {
    setMenu(menuToggle.getAttribute('aria-expanded') !== 'true');
  });
  scrim?.addEventListener('click', () => setMenu(false));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') setMenu(false);
  });

  const fileInput = document.querySelector('#invoice-file');
  const fileLabel = document.querySelector('[data-file-name]');
  fileInput?.addEventListener('change', () => {
    const file = fileInput.files?.[0];
    if (fileLabel) fileLabel.textContent = file ? file.name : '';
  });
  const dropZone = fileInput?.closest('.drop-zone');
  if (dropZone) {
    ['dragenter', 'dragover'].forEach((name) => dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      dropZone.classList.add('dragover');
    }));
    ['dragleave', 'drop'].forEach((name) => dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      dropZone.classList.remove('dragover');
    }));
    dropZone.addEventListener('drop', (event) => {
      const files = event.dataTransfer?.files;
      if (files?.length && fileInput) {
        fileInput.files = files;
        fileInput.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
  }

  const generatedMessage = document.querySelector('[data-generated-message]');
  const charCount = document.querySelector('[data-character-count]');
  const updateCount = () => {
    if (generatedMessage && charCount) {
      const count = generatedMessage.value.length;
      charCount.textContent = `${count} ${count === 1 ? 'character' : 'characters'}`;
    }
  };
  generatedMessage?.addEventListener('input', updateCount);
  updateCount();

  const copyButton = document.querySelector('[data-copy-message]');
  copyButton?.addEventListener('click', async () => {
    if (!generatedMessage) return;
    const label = copyButton.querySelector('[data-copy-label]');
    try {
      await navigator.clipboard.writeText(generatedMessage.value);
      if (label) label.textContent = 'Copied';
      window.setTimeout(() => { if (label) label.textContent = 'Copy'; }, 1800);
    } catch {
      generatedMessage.focus();
      generatedMessage.select();
      document.execCommand('copy');
      if (label) label.textContent = 'Copied';
      window.setTimeout(() => { if (label) label.textContent = 'Copy'; }, 1800);
    }
  });

  document.querySelectorAll('[data-confirm]').forEach((element) => {
    element.addEventListener('click', (event) => {
      const message = element.getAttribute('data-confirm') || 'Are you sure?';
      if (!window.confirm(message)) event.preventDefault();
    });
  });
})();
