const initializeBranchDashboard = () => {
  const branchControls = [...document.querySelectorAll('[data-branch-dashboard-branch]')];
  branchControls.forEach((control) => {
    control.addEventListener('click', () => {
      const branchId = control.dataset.branchDashboardBranch;
      branchControls.forEach((candidate) => {
        candidate.classList.toggle('is-active', candidate.dataset.branchDashboardBranch === branchId);
      });
    });
  });

  const tour = document.querySelector('[data-branch-tour]');
  const openButton = document.querySelector('[data-branch-tour-open]');
  const dialog = document.querySelector('.purchase-tour-dialog');
  const spotlight = document.querySelector('[data-branch-tour-spotlight]');
  const title = document.querySelector('[data-branch-tour-title]');
  const copy = document.querySelector('[data-branch-tour-copy]');
  const count = document.querySelector('[data-branch-tour-count]');
  const dots = document.querySelector('[data-branch-tour-dots]');
  const backButton = document.querySelector('[data-branch-tour-back]');
  const nextButton = document.querySelector('[data-branch-tour-next]');
  const stepsElement = document.getElementById('branch-dashboard-tour-steps');
  if (!tour || !openButton || !dialog || !spotlight || !stepsElement) return;
  const steps = JSON.parse(stepsElement.textContent || '[]');
  if (!steps.length) return;

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
    const gap = 18;
    let top = gap;
    if (rect.top >= dialogHeight + gap * 2) top = rect.top - dialogHeight - gap;
    else if (window.innerHeight - rect.bottom >= dialogHeight + gap) top = rect.bottom + gap;
    const centeredLeft = rect.left + rect.width / 2 - dialogWidth / 2;
    dialog.style.top = `${clamp(top, gap, window.innerHeight - dialogHeight - gap)}px`;
    dialog.style.left = `${clamp(centeredLeft, gap, window.innerWidth - dialogWidth - gap)}px`;
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
    highlighted = document.querySelector(`[data-branch-tour-target="${step.target}"]`) || document.querySelector('[data-branch-tour-target="overview"]');
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
  document.querySelectorAll('[data-branch-tour-close]').forEach((button) => button.addEventListener('click', closeTour));
  backButton.addEventListener('click', () => { if (activeStep > 0) { activeStep -= 1; renderStep(); } });
  nextButton.addEventListener('click', () => { if (activeStep === steps.length - 1) closeTour(); else { activeStep += 1; renderStep(); } });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !tour.hidden) closeTour(); });
  window.addEventListener('resize', positionTour);
  window.addEventListener('scroll', positionTour, true);
};

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initializeBranchDashboard, { once: true });
else initializeBranchDashboard();
