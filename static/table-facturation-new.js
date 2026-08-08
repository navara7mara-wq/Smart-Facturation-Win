(() => {
  const toast = document.querySelector('[data-toast]');
  let toastTimer;

  function showToast(message, isError = false) {
    if (!toast) return;
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.toggle('error', isError);
    toast.classList.add('show');
    toastTimer = setTimeout(() => toast.classList.remove('show'), 2300);
  }

  async function saveRow(row, values) {
    const invoiceId = row?.dataset.invoiceId;
    if (!invoiceId) return;
    const body = new URLSearchParams({ invoice_id: invoiceId, ...values });
    const response = await fetch('/table-facturation-new/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
      body,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) throw new Error(payload.error || 'Échec de l’enregistrement');
    return payload;
  }

  const filterForm = document.querySelector('.facturation-filters');
  let searchTimer;
  filterForm?.querySelector('input[name="q"]')?.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => filterForm.requestSubmit(), 420);
  });
  filterForm?.querySelectorAll('select[name], input[type="date"]').forEach((control) => {
    control.addEventListener('change', () => filterForm.requestSubmit());
  });

  document.querySelector('[data-clear-filters]')?.addEventListener('click', () => {
    window.location.href = '/table-facturation-new';
  });

  document.querySelector('[data-per-page]')?.addEventListener('change', (event) => {
    const url = new URL(window.location.href);
    url.searchParams.set('per_page', event.target.value);
    url.searchParams.set('page', '1');
    window.location.href = url.toString();
  });

  document.querySelectorAll('[data-deposit-toggle]').forEach((checkbox) => {
    checkbox.addEventListener('change', async () => {
      const row = checkbox.closest('tr[data-invoice-id]');
      checkbox.disabled = true;
      try {
        const payload = await saveRow(row, { depos: checkbox.checked ? '1' : '0' });
        const date = row.querySelector('[data-deposit-date]');
        const label = row.querySelector('[data-deposit-label]');
        if (date) date.textContent = checkbox.checked ? payload.date : '—';
        if (label) label.textContent = checkbox.checked ? 'Oui' : 'Non';
        showToast('État de dépôt enregistré');
      } catch (error) {
        checkbox.checked = !checkbox.checked;
        showToast(error.message, true);
      } finally {
        checkbox.disabled = false;
      }
    });
  });

  document.querySelectorAll('[data-edit-remark]').forEach((button) => {
    button.addEventListener('click', () => {
      const editor = button.closest('.remark-editor');
      const input = editor.querySelector('[data-remark-input]');
      input.hidden = false;
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    });
  });

  document.querySelectorAll('[data-remark-input]').forEach((input) => {
    let initial = input.value;
    let saving = false;
    const commit = async () => {
      if (saving || input.hidden) return;
      const value = input.value.trim();
      input.hidden = true;
      if (value === initial.trim()) return;
      saving = true;
      const row = input.closest('tr[data-invoice-id]');
      try {
        await saveRow(row, { remarque: value });
        initial = value;
        const text = input.closest('.remark-editor').querySelector('[data-remark-text]');
        text.textContent = value || '—';
        showToast('Remarque enregistrée');
      } catch (error) {
        input.value = initial;
        showToast(error.message, true);
      } finally {
        saving = false;
      }
    };
    input.addEventListener('blur', commit);
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        input.value = initial;
        input.hidden = true;
      }
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        input.blur();
      }
    });
  });
})();


// TABLE SORTING: clicking the visible column title sorts the full server-side result set.
// No template URL placeholders are used; this stays robust even when app.py and HTML are updated separately.
(() => {
  const buttons = [...document.querySelectorAll('.table-sort-button[data-sort-key]')];
  if (!buttons.length) return;

  const currentUrl = new URL(window.location.href);
  const currentSort = (currentUrl.searchParams.get('sort') || 'date').toLowerCase();
  const currentOrder = (currentUrl.searchParams.get('order') || 'desc').toLowerCase();

  buttons.forEach((button) => {
    const key = button.dataset.sortKey;
    const th = button.closest('th');
    const arrow = button.querySelector('.table-sort-arrow');
    const active = key === currentSort;

    if (th) th.setAttribute('aria-sort', active ? (currentOrder === 'asc' ? 'ascending' : 'descending') : 'none');
    if (arrow) arrow.textContent = active ? (currentOrder === 'asc' ? '↑' : '↓') : '';

    button.addEventListener('click', () => {
      const url = new URL(window.location.href);
      const sort = (url.searchParams.get('sort') || 'date').toLowerCase();
      const order = (url.searchParams.get('order') || 'desc').toLowerCase();
      const nextOrder = sort === key && order === 'asc' ? 'desc' : 'asc';
      url.searchParams.set('sort', key);
      url.searchParams.set('order', nextOrder);
      url.searchParams.set('page', '1');
      window.location.assign(url.pathname + '?' + url.searchParams.toString());
    });
  });
})();


// ACTIONS: one compact document picker behind the eye icon.
(() => {
  const triggers = [...document.querySelectorAll('[data-document-menu-trigger]')];

  function closeMenus(except = null) {
    triggers.forEach((trigger) => {
      if (trigger === except) return;
      const menuId = trigger.getAttribute('aria-controls');
      const menu = menuId ? document.getElementById(menuId) : null;
      if (menu) menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
    });
  }

  triggers.forEach((trigger) => {
    trigger.addEventListener('click', (event) => {
      event.stopPropagation();
      const menuId = trigger.getAttribute('aria-controls');
      const menu = menuId ? document.getElementById(menuId) : null;
      if (!menu) return;
      const willOpen = menu.hidden;
      closeMenus(trigger);
      menu.hidden = !willOpen;
      trigger.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    });
  });

  document.addEventListener('click', (event) => {
    if (!event.target.closest('.action-menu-wrap')) closeMenus();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeMenus();
  });
})();
