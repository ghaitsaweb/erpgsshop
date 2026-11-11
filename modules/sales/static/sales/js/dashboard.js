const initializeSalesDashboard = () => {
  const tour = document.querySelector('[data-sales-tour]');
  const openButton = document.querySelector('[data-sales-tour-open]');
  const dialog = document.querySelector('.sales-tour-dialog');
  const spotlight = document.querySelector('[data-sales-tour-spotlight]');
  const title = document.querySelector('[data-sales-tour-title]');
  const copy = document.querySelector('[data-sales-tour-copy]');
  const count = document.querySelector('[data-sales-tour-count]');
  const dots = document.querySelector('[data-sales-tour-dots]');
  const backButton = document.querySelector('[data-sales-tour-back]');
  const nextButton = document.querySelector('[data-sales-tour-next]');
  const branchControls = [...document.querySelectorAll('[data-sales-branch]')];
  const chart = document.querySelector('[data-sales-chart]');
  const chartModeButtons = [...document.querySelectorAll('[data-sales-chart-mode]')];
  let activeStep = 0;
  let highlighted = null;
  let repositionTimer = null;

  const steps = [
    {
      target: 'overview',
      title: 'Rapat Senin yang sudah siap',
      copy: 'Di banyak perusahaan, ada orang yang menghabiskan Senin pagi mengekspor data pesanan hanya untuk menjawab satu pertanyaan: bagaimana penjualan kita? Dashboard ini langsung menjawabnya untuk periode dan cabang yang Anda pilih, sebelum ada yang bertanya.',
    },
    {
      target: 'metrics',
      title: 'Empat angka, lengkap dengan pembandingnya',
      copy: 'Revenue, Sales Orders, Avg. Order Value dan Promo Savings Given, masing-masing dengan perubahan terhadap periode pembanding, yaitu periode yang sama tahun lalu bila datanya tersedia. Pertumbuhan atau perlambatan langsung terlihat tanpa ada yang menyusun pivot table.',
    },
    {
      target: 'promo',
      title: 'Biaya promo yang sebenarnya',
      copy: 'Promo Savings Given menampilkan total potongan yang diberikan pada periode ini di samping revenue, beserta porsinya dari penjualan kotor. Promo menjadi biaya yang terlihat manajemen di layar pertama, bukan kejutan di akhir bulan.',
    },
    {
      target: 'new-order',
      title: 'Dari insight langsung ke tindakan',
      copy: 'Tombol New Sales Order ada di header yang sama. Manajer yang melihat celah bisa langsung membuat pesanan dari sini, dan pesanan itu masuk ke angka yang sama yang dibaca dashboard.',
    },
    {
      target: 'trend',
      title: 'Tahun ini dibanding tahun lalu, dalam satu grafik',
      copy: 'Revenue trend bisa ditampilkan sebagai Bar atau Line. Di tampilan Line, tahun lalu tampil sebagai garis putus-putus di bawah tahun ini, dan hari besar seperti Lebaran ditandai bila masuk rentang, jadi penurunan musiman tidak disangka masalah penjualan.',
    },
    {
      target: 'overview',
      title: 'Artinya bagi Anda',
      copy: 'Manajemen memulai minggu dari angka yang sama dengan yang dilihat sales dan keuangan, dibandingkan secara setara, dengan biaya promo terlihat jelas. Waktu menyusun laporan berkurang, waktu untuk pelanggan bertambah.',
    },
    {
      target: 'overview',
      title: 'Pertanyaan untuk Anda',
      copy: 'Saat direksi menanyakan hasil penjualan minggu lalu, siapa yang menyiapkan jawabannya saat ini, dan berapa lama prosesnya?',
    },
  ];

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
    if (highlighted) highlighted.classList.remove('sales-tour-highlight');
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
    highlighted = document.querySelector(`[data-sales-tour-target="${step.target}"]`)
      || document.querySelector('[data-sales-tour-target="overview"]');
    if (highlighted) {
      highlighted.classList.add('sales-tour-highlight');
      highlighted.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
    count.textContent = `${activeStep + 1} / ${steps.length}`;
    title.textContent = step.title;
    copy.textContent = step.copy;
    backButton.disabled = activeStep === 0;
    backButton.textContent = 'Kembali';
    nextButton.textContent = activeStep === steps.length - 1 ? 'Selesai' : 'Lanjut';
    renderDots();
    window.requestAnimationFrame(positionTour);
    window.clearTimeout(repositionTimer);
    repositionTimer = window.setTimeout(positionTour, 320);
  };

  const closeTour = () => {
    clearHighlight();
    tour.hidden = true;
    document.body.classList.remove('sales-tour-open');
    window.clearTimeout(repositionTimer);
    openButton?.focus();
  };

  const openTour = () => {
    activeStep = 0;
    tour.hidden = false;
    document.body.classList.add('sales-tour-open');
    renderStep();
    nextButton.focus();
  };

  openButton?.addEventListener('click', openTour);
  document.querySelectorAll('[data-sales-tour-close]').forEach((button) => button.addEventListener('click', closeTour));
  backButton?.addEventListener('click', () => {
    if (activeStep > 0) {
      activeStep -= 1;
      renderStep();
    }
  });
  nextButton?.addEventListener('click', () => {
    if (activeStep === steps.length - 1) {
      closeTour();
    } else {
      activeStep += 1;
      renderStep();
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && tour && !tour.hidden) closeTour();
  });
  window.addEventListener('resize', positionTour);
  window.addEventListener('scroll', positionTour, true);

  branchControls.forEach((control) => {
    control.addEventListener('click', () => {
      const branchId = control.dataset.salesBranch;
      branchControls.forEach((candidate) => {
        candidate.classList.toggle('is-active', candidate.dataset.salesBranch === branchId);
      });
    });
  });

  chartModeButtons.forEach((button) => {
    button.addEventListener('click', () => {
      const lineMode = button.dataset.salesChartMode === 'line';
      chart?.classList.toggle('is-line', lineMode);
      chartModeButtons.forEach((candidate) => {
        const active = candidate === button;
        candidate.classList.toggle('is-active', active);
        candidate.setAttribute('aria-pressed', String(active));
      });
    });
  });
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeSalesDashboard, { once: true });
} else {
  initializeSalesDashboard();
}
