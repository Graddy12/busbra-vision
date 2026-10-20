const form = document.querySelector("#analysis-form");
const input = document.querySelector("#image-input");
const dropZone = document.querySelector("#drop-zone");
const previewWrap = document.querySelector("#preview-wrap");
const preview = document.querySelector("#source-preview");
const removeButton = document.querySelector("#remove-image");
const analyzeButton = document.querySelector("#analyze-button");
const emptyState = document.querySelector("#empty-state");
const loadingState = document.querySelector("#loading-state");
const errorState = document.querySelector("#error-state");
const results = document.querySelector("#results");

let selectedFile = null;
let resultImages = { original: "", overlay: "", mask: "", gradcam: "" };
let isAnalyzing = false;

function humanSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} Ko`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
}

function setFile(file) {
  if (!file || isAnalyzing) return;
  if (file.size > 16 * 1024 * 1024) {
    showError("Le fichier dépasse la limite de 16 Mo.");
    return;
  }
  if (preview.src) URL.revokeObjectURL(preview.src);
  selectedFile = file;
  preview.src = URL.createObjectURL(file);
  document.querySelector("#file-name").textContent = file.name;
  document.querySelector("#file-size").textContent = humanSize(file.size);
  dropZone.hidden = true;
  previewWrap.hidden = false;
  analyzeButton.disabled = false;
  hideError();
}

function clearFile() {
  if (isAnalyzing) return;
  if (preview.src) URL.revokeObjectURL(preview.src);
  selectedFile = null;
  input.value = "";
  preview.removeAttribute("src");
  previewWrap.hidden = true;
  dropZone.hidden = false;
  analyzeButton.disabled = true;
}

function hideError() {
  errorState.hidden = true;
  document.querySelector("#error-message").textContent = "";
}

function showError(message) {
  emptyState.hidden = true;
  loadingState.hidden = true;
  results.hidden = true;
  errorState.hidden = false;
  document.querySelector("#error-message").textContent = message;
}

function displayResults(data) {
  const classification = data.classification;
  const segmentation = data.segmentation;
  const confidence = classification.confidence_percent;
  const hasSegmentation = classification.code !== "normal" && segmentation.performed === true;

  document.querySelector("#predicted-label").textContent = classification.label;
  document.querySelector("#confidence-value").textContent = `${confidence.toFixed(1)}%`;
  document.querySelector("#confidence-ring").style.setProperty("--confidence", `${confidence * 3.6}deg`);
  document.querySelector("#lesion-area").textContent = hasSegmentation ? `${segmentation.lesion_area_percent.toFixed(2)}%` : "—";
  document.querySelector("#lesion-area-wrap").hidden = !hasSegmentation;
  document.querySelector("#segmentation-title").textContent = hasSegmentation ? "Région segmentée" : "Classe prédite normale";
  const segmentationStatus = document.querySelector("#segmentation-status");
  segmentationStatus.hidden = hasSegmentation;
  segmentationStatus.textContent = hasSegmentation ? "" : "Segmentation non appliquée : aucun masque pour la classe prédite normale.";
  document.querySelectorAll('[data-view="overlay"], [data-view="mask"]').forEach((button) => {
    button.hidden = !hasSegmentation;
    button.disabled = !hasSegmentation;
  });
  document.querySelector("#inference-meta").textContent = `Analyse sur ${data.meta.device.toUpperCase()} · ${data.meta.inference_ms} ms`;

  const probabilityList = document.querySelector("#probability-list");
  probabilityList.replaceChildren();
  classification.probabilities.forEach((item) => {
    const row = document.createElement("div");
    row.className = "probability-row";
    row.innerHTML = `
      <div><span>${item.label}</span><strong>${item.percent.toFixed(1)}%</strong></div>
      <div class="probability-track"><span style="width: ${item.percent}%"></span></div>
    `;
    probabilityList.appendChild(row);
  });

  resultImages = {
    original: data.analysis.original_url,
    overlay: hasSegmentation ? segmentation.overlay_data_url : null,
    mask: hasSegmentation ? segmentation.mask_data_url : null,
    gradcam: data.gradcam.overlay_data_url,
  };
  document.querySelector("#history-link").href = data.analysis.history_url;
  document.querySelector("#report-link").href = data.analysis.report_url;
  document.querySelector("#gradcam-help").textContent = data.gradcam.available
    ? "Grad-CAM : zones qui contribuent à la classification, distinctes du masque de segmentation."
    : "Grad-CAM : aucune activation positive significative pour cette classe. La superposition reste neutre.";
  setResultView(hasSegmentation ? "overlay" : "original");

  errorState.hidden = true;
  loadingState.hidden = true;
  emptyState.hidden = true;
  results.hidden = false;
}

function setResultView(view) {
  if (!resultImages[view]) return;
  const image = document.querySelector("#overlay-image");
  const download = document.querySelector("#download-result");
  image.src = resultImages[view];
  download.href = resultImages[view];
  download.download = view === "original" ? "echographie-originale.png" : view === "mask" ? "masque-segmentation.png" : view === "gradcam" ? "gradcam.png" : "superposition-segmentation.png";
  image.alt = view === "original" ? "Échographie originale sans masque" : view === "gradcam" ? "Grad-CAM du classificateur, pas un masque de lésion" : view === "mask" ? "Masque binaire" : "Superposition du masque";
  document.querySelectorAll(".view-toggle").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
}

input.addEventListener("change", () => setFile(input.files[0]));
removeButton.addEventListener("click", clearFile);

["dragenter", "dragover"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragging");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
  });
});

dropZone.addEventListener("drop", (event) => setFile(event.dataTransfer.files[0]));

document.querySelectorAll(".view-toggle").forEach((button) => {
  button.addEventListener("click", () => setResultView(button.dataset.view));
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedFile || isAnalyzing) return;

  isAnalyzing = true;
  hideError();
  emptyState.hidden = true;
  results.hidden = true;
  loadingState.hidden = false;
  analyzeButton.disabled = true;
  removeButton.disabled = true;

  const body = new FormData();
  body.append("image", selectedFile, selectedFile.name);
  body.append("reference", document.querySelector("#exam-reference").value);

  try {
    const response = await fetch("/api/analyze", {
      method: "POST", body,
      headers: { "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content },
    });
    if (!response.headers.get("content-type")?.includes("application/json")) {
      throw new Error(`Réponse inattendue du serveur (HTTP ${response.status}). Relancez l'application puis actualisez la page.`);
    }
    const data = await response.json();
    if (response.status === 401 && data.login_url) {
      window.location.assign(data.login_url);
      return;
    }
    if (!response.ok) throw new Error(data.detail || data.error || "Erreur inconnue.");
    displayResults(data);
  } catch (error) {
    showError(error instanceof TypeError
      ? "Impossible de contacter le serveur. Lancez app.py et laissez son terminal ouvert."
      : error.message || "L'analyse a échoué.");
  } finally {
    isAnalyzing = false;
    analyzeButton.disabled = !selectedFile;
    removeButton.disabled = false;
  }
});
