// DeviceAdmin UI helpers: select-all, loading states, confirmations, mobile menu.
document.addEventListener("DOMContentLoaded", () => {
  // "Select all" checkbox controls every checkbox with the same data-group.
  document.querySelectorAll("[data-select-all]").forEach((master) => {
    master.addEventListener("change", () => {
      document.querySelectorAll(`input[name="${master.dataset.selectAll}"]`).forEach((cb) => (cb.checked = master.checked));
      updateBulkBar();
    });
  });
  document.querySelectorAll("input[data-bulk-item]").forEach((cb) => cb.addEventListener("change", updateBulkBar));

  // Bulk action bar shows how many rows are selected and enables its buttons.
  function updateBulkBar() {
    const n = document.querySelectorAll("input[data-bulk-item]:checked").length;
    document.querySelectorAll("[data-bulk-count]").forEach((el) => (el.textContent = n));
    document.querySelectorAll("[data-bulk-action]").forEach((btn) => (btn.disabled = n === 0));
  }
  updateBulkBar();

  // Confirmation prompts for risky actions: <button data-confirm="Are you sure?">
  document.addEventListener("submit", (event) => {
    const submitter = event.submitter;
    const message = submitter && submitter.dataset.confirm;
    if (message && !window.confirm(message)) {
      event.preventDefault();
      return;
    }
    // Loading state: disable the submit button and show a spinner; slow operations show a full overlay.
    if (submitter && !event.defaultPrevented) {
      if (submitter.dataset.overlay !== undefined) document.getElementById("busy").hidden = false;
      setTimeout(() => {
        submitter.disabled = true;
        submitter.dataset.label = submitter.innerHTML;
        submitter.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>Working…';
      }, 0);
    }
  });

  // Browsers restore the page from cache on Back; make sure nothing is stuck in "Working…".
  window.addEventListener("pageshow", () => {
    document.getElementById("busy").hidden = true;
    document.querySelectorAll("button[data-label]").forEach((b) => {
      b.disabled = false;
      b.innerHTML = b.dataset.label;
    });
  });

  const toggle = document.getElementById("menuToggle");
  if (toggle) toggle.addEventListener("click", () => document.getElementById("sidebar").classList.toggle("open"));

  // Fetch-and-show JSON (used by "Pull config").
  document.querySelectorAll("[data-fetch-json]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const target = document.querySelector(btn.dataset.target);
      target.hidden = false;
      target.textContent = "Loading…";
      try {
        const res = await fetch(btn.dataset.fetchJson);
        const body = await res.json();
        target.textContent = res.ok ? JSON.stringify(body.config, null, 2) : `Error: ${body.message}`;
      } catch (err) {
        target.textContent = `Request failed: ${err}`;
      }
    });
  });
});
