(function () {
  'use strict';

  const durations = { success: 5000, warning: 7000, error: 10000 };

  function inferType(message) {
    const value = String(message || '').toLocaleLowerCase('fr');
    if (/erreur|invalide|introuvable|impossible|interdite|refus|échou|echou/.test(value)) return 'error';
    if (/attention|session|expir|verrouill|obligatoire|sélectionnez|selectionnez/.test(value)) return 'warning';
    return 'success';
  }

  function toastRegion() {
    let region = document.querySelector('.app-toast-region');
    if (!region) {
      region = document.createElement('div');
      region.className = 'app-toast-region';
      region.setAttribute('aria-live', 'polite');
      region.setAttribute('aria-label', 'Notifications');
      document.body.appendChild(region);
    }
    return region;
  }

  function showAppToast(message, requestedType) {
    if (!message) return null;
    const type = requestedType || inferType(message);
    const toast = document.createElement('section');
    toast.className = `app-toast app-toast-${type}`;
    toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
    toast.innerHTML = '<span class="app-toast-indicator" aria-hidden="true"></span><span class="app-toast-message"></span><button type="button" class="app-toast-close" aria-label="Fermer la notification">&times;</button>';
    toast.querySelector('.app-toast-message').textContent = message;
    toastRegion().appendChild(toast);

    let remaining = durations[type] || durations.success;
    let startedAt = Date.now();
    let timer = null;
    const remove = () => {
      clearTimeout(timer);
      toast.classList.add('is-leaving');
      window.setTimeout(() => toast.remove(), 180);
    };
    const start = () => {
      startedAt = Date.now();
      timer = window.setTimeout(remove, remaining);
    };
    const pause = () => {
      clearTimeout(timer);
      remaining = Math.max(0, remaining - (Date.now() - startedAt));
    };
    toast.addEventListener('mouseenter', pause);
    toast.addEventListener('mouseleave', start);
    toast.querySelector('.app-toast-close').addEventListener('click', remove);
    window.requestAnimationFrame(() => toast.classList.add('is-visible'));
    start();
    return toast;
  }

  window.showAppToast = showAppToast;

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.alert:not([data-inline-alert]), .sapta-alert:not([data-inline-alert])').forEach(source => {
      const message = source.textContent.trim();
      const type = source.dataset.toastType || inferType(message);
      source.remove();
      showAppToast(message, type);
    });
  });
}());
