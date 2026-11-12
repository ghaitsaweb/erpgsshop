const initializePurchaseDashboard = () => {
  const branchControls = [...document.querySelectorAll('[data-purchase-branch]')];
  branchControls.forEach((control) => {
    control.addEventListener('click', () => {
      const branchId = control.dataset.purchaseBranch;
      branchControls.forEach((candidate) => {
        candidate.classList.toggle('is-active', candidate.dataset.purchaseBranch === branchId);
      });
    });
  });

  const tour = document.querySelector('[data-purchase-tour]');
  const openButton = document.querySelector('[data-purchase-tour-open]');
  const dialog = document.querySelector('.purchase-tour-dialog');
  const spotlight = document.querySelector('[data-purchase-tour-spotlight]');
  const title = document.querySelector('[data-purchase-tour-title]');
  const copy = document.querySelector('[data-purchase-tour-copy]');
  const count = document.querySelector('[data-purchase-tour-count]');
  const dots = document.querySelector('[data-purchase-tour-dots]');
  const backButton = document.querySelector('[data-purchase-tour-back]');
  const nextButton = document.querySelector('[data-purchase-tour-next]');
  if (!tour || !openButton || !dialog || !spotlight || !title || !copy || !count || !dots || !backButton || !nextButton) return;

  const steps = [
    {
      target: 'overview',
      title: 'Kontrol pembelian dimulai dari satu layar',
      copy: 'Dashboard ini menyatukan Purchase Order, status approval, penerimaan barang, dan outstanding value untuk periode serta cabang yang Anda pilih. Tim tidak perlu menggabungkan spreadsheet PO dan gudang secara manual.',
    },
    {
      target: 'network',
      title: 'Belanja setiap cabang terlihat di peta',
      copy: 'Branch network menampilkan nilai Purchase Order per cabang di Indonesia. Klik pin atau ranking cabang untuk menyorot lokasi yang sama dan melihat kontribusinya terhadap total pembelian.',
    },
    {
      target: 'metrics',
      title: 'Empat KPI yang langsung bisa ditindaklanjuti',
      copy: 'Nilai PO, nilai barang yang sudah diterima, outstanding line value, dan jumlah Goods Receipt posted dihitung langsung dari transaksi dalam scope akses Anda.',
    },
    {
      target: 'flow',
      title: 'Dari PO sampai penerimaan barang',
      copy: 'Pipeline memperlihatkan berapa PO yang masih Draft atau Sent, menunggu approval, belum diterima, baru diterima sebagian, atau sudah fully received.',
    },
    {
      target: 'receiving',
      title: 'Progress receipt tanpa membuka PO satu per satu',
      copy: 'Ring progress membandingkan ordered line value dengan received line value. Kuantitas accepted dan rejected membantu purchasing menindaklanjuti selisih dengan gudang dan vendor.',
    },
    {
      target: 'orders',
      title: 'Daftar tindak lanjut operasional',
      copy: 'Purchase Order terbaru menampilkan vendor, nilai, persentase receipt, jumlah Goods Receipt posted, dan status. Klik nomor PO untuk langsung membuka dokumennya.',
    },
    {
      target: 'overview',
      title: 'Pertanyaan untuk tim Anda',
      copy: 'Saat manajemen menanyakan PO mana yang belum diterima dan cabang mana yang paling besar nilai pembeliannya, apakah jawabannya sekarang sudah tersedia tanpa ekspor manual?',
    },
  ];

  let activeStep = 0;
  let highlighted = null;
  let repositionTimer = null;
  const clamp = (value, minimum, maximum) => Math.min(Math.max(value, minimum), maximum);

  const positionTour = () => {
    if (!highlighted || tour.hidden) return;
    const rect = highlighted.getBoundingClientRect();
    const padding = 8;
    spotlight.style.top = `${Math.max(4, rect.top - padding)}px`;
    spotlight.style.left = `${Math.max(4, rect.left - padding)}px`;
    spotlight.style.width = `${Math.min(window.innerWidth - 8, rect.width + padding * 2)}px`;
    spotlight.style.height = `${Math.min(window.innerHeight - 8, rect.height + padding * 2)}px`;

    const dialogRect = dialog.getBoundingClientRect();
    const dialogWidth = dialogRect.width || 344;
    const dialogHeight = dialogRect.height || 250;
    const viewportGap = 18;
    let top;
    if (rect.top >= dialogHeight + viewportGap * 2) {
      top = rect.top - dialogHeight - viewportGap;
    } else if (window.innerHeight - rect.bottom >= dialogHeight + viewportGap) {
      top = rect.bottom + viewportGap;
    } else {
      top = viewportGap;
    }
    const centeredLeft = rect.left + rect.width / 2 - dialogWidth / 2;
    const left = clamp(centeredLeft, viewportGap, window.innerWidth - dialogWidth - viewportGap);
    dialog.style.top = `${clamp(top, viewportGap, window.innerHeight - dialogHeight - viewportGap)}px`;
    dialog.style.left = `${left}px`;
    dialog.style.transform = 'none';
  };

  const clearHighlight = () => {
    if (highlighted) highlighted.classList.remove('purchase-tour-highlight');
    highlighted = null;
  };

  const renderDots = () => {
    dots.replaceChildren(...steps.map((_step, index) => {
      const dot = document.createElement('i');
      dot.classList.toggle('active', index === activeStep);
      return dot;
    }));
  };

  const renderStep = () => {
    const step = steps[activeStep];
    clearHighlight();
    highlighted = document.querySelector(`[data-purchase-tour-target="${step.target}"]`)
      || document.querySelector('[data-purchase-tour-target="overview"]');
    if (highlighted) {
      highlighted.classList.add('purchase-tour-highlight');
      highlighted.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
    count.textContent = `${activeStep + 1} / ${steps.length}`;
    title.textContent = step.title;
    copy.textContent = step.copy;
    backButton.disabled = activeStep === 0;
    nextButton.textContent = activeStep === steps.length - 1 ? 'Selesai' : 'Lanjut';
    renderDots();
    window.requestAnimationFrame(positionTour);
    window.clearTimeout(repositionTimer);
    repositionTimer = window.setTimeout(positionTour, 320);
  };

  const closeTour = () => {
    clearHighlight();
    tour.hidden = true;
    document.body.classList.remove('purchase-tour-open');
    window.clearTimeout(repositionTimer);
    openButton.focus();
  };

  const openTour = () => {
    activeStep = 0;
    tour.hidden = false;
    document.body.classList.add('purchase-tour-open');
    renderStep();
    nextButton.focus();
  };

  openButton.addEventListener('click', openTour);
  document.querySelectorAll('[data-purchase-tour-close]').forEach((button) => button.addEventListener('click', closeTour));
  backButton.addEventListener('click', () => {
    if (activeStep > 0) {
      activeStep -= 1;
      renderStep();
    }
  });
  nextButton.addEventListener('click', () => {
    if (activeStep === steps.length - 1) closeTour();
    else {
      activeStep += 1;
      renderStep();
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !tour.hidden) closeTour();
  });
  window.addEventListener('resize', positionTour);
  window.addEventListener('scroll', positionTour, true);
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializePurchaseDashboard, { once: true });
} else {
  initializePurchaseDashboard();
}
