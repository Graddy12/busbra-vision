"use strict";

function dismissToast(toast) {
  if (!toast || toast.classList.contains("is-leaving")) return;
  toast.classList.add("is-leaving");
  setTimeout(() => toast.remove(), 240);
}

function activateToast(toast) {
  requestAnimationFrame(() => toast.classList.add("is-visible"));
  toast.querySelector(".flash-close")?.addEventListener("click", () => dismissToast(toast));
  setTimeout(() => dismissToast(toast), toast.classList.contains("error") ? 8000 : 5600);
}

document.querySelectorAll("[data-toast]").forEach(activateToast);

window.busbraNotify = (message, category = "success") => {
  let region = document.querySelector(".flash-wrap");
  if (!region) {
    region = document.createElement("div");
    region.className = "flash-wrap no-print";
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    document.body.append(region);
  }
  const toast = document.createElement("div");
  toast.className = `flash ${category}`;
  toast.dataset.toast = "";
  const icon = document.createElement("span");
  icon.className = "flash-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = category === "error" ? "!" : "✓";
  const text = document.createElement("span");
  text.className = "flash-message";
  text.textContent = message;
  const close = document.createElement("button");
  close.className = "flash-close";
  close.type = "button";
  close.setAttribute("aria-label", "Fermer la notification");
  close.textContent = "×";
  toast.append(icon, text, close);
  region.append(toast);
  activateToast(toast);
};

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (form.dataset.asyncForm !== undefined) return;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    if (event.defaultPrevented || !form.checkValidity()) return;
    const button = event.submitter || form.querySelector("button[type='submit'], button:not([type])");
    if (!button || button.dataset.noLoading !== undefined) return;
    button.dataset.originalText = button.textContent;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    button.disabled = true;
    button.textContent = button.dataset.loadingText
      || (form.method.toLowerCase() === "get" ? "Chargement…" : "Enregistrement…");
  });
});

addEventListener("pageshow", () => {
  document.querySelectorAll("button.is-loading").forEach((button) => {
    button.classList.remove("is-loading");
    button.removeAttribute("aria-busy");
    button.disabled = false;
    if (button.dataset.originalText) button.textContent = button.dataset.originalText;
  });
});

document.querySelectorAll("[data-print]").forEach((button) => {
  button.addEventListener("click", async () => {
    await Promise.all([...document.images].map((image) => image.decode().catch(() => {})));
    await document.fonts.ready;
    window.print();
  });
});
