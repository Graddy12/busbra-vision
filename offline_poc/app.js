"use strict";
const CACHE = "busbra-offline-poc-v10";
const $ = selector => document.querySelector(selector);
const pending = new Map();
let worker, manifest, requestId = 0, busy = false, ready = false, selection = null, lastResult = null;
let databasePromise, installPrompt = null;

function networkStatus() {
  const offline = !navigator.onLine;
  $("#network").textContent = offline ? "Hors ligne · calcul local" : "Mode local · aucune synchronisation";
  $("#network").classList.toggle("offline", offline);
}
addEventListener("online", networkStatus);
addEventListener("offline", networkStatus);
addEventListener("beforeinstallprompt", event => {
  event.preventDefault();
  installPrompt = event;
  $("#install-app").hidden = false;
});
$("#install-app").addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  $("#install-app").hidden = true;
});
networkStatus();

function status(message) { $("#status").textContent = message; }
function error(message) { $("#error").textContent = message; $("#error").hidden = !message; }
function controls() {
  $("#prepare").disabled = busy;
  $("#image").disabled = busy || !ready;
  $("#analyze").disabled = busy || !ready || !selection;
  $("#remove-image").disabled = busy;
}
function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("Impossible de lire l'historique local."));
  });
}
function openDatabase() {
  if (databasePromise) return databasePromise;
  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open("busbra-offline-history", 1);
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore("analyses", { keyPath: "hash" });
      store.createIndex("updatedAt", "updatedAt");
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("L'historique local n'est pas disponible."));
  });
  return databasePromise;
}
async function readRecord(hash) {
  const db = await openDatabase();
  return requestResult(db.transaction("analyses", "readonly").objectStore("analyses").get(hash));
}
async function readRecords() {
  const db = await openDatabase();
  return requestResult(db.transaction("analyses", "readonly").objectStore("analyses").getAll());
}
async function saveRecord(record) {
  const db = await openDatabase();
  await new Promise((resolve, reject) => {
    const transaction = db.transaction("analyses", "readwrite");
    transaction.objectStore("analyses").put(record);
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(transaction.error || new Error("Impossible d'enregistrer dans l'historique local."));
    transaction.onabort = () => reject(transaction.error || new Error("L'enregistrement local a été annulé."));
  });
}
function formatDate(value) {
  return new Intl.DateTimeFormat("fr-FR", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}
async function renderHistory() {
  try {
    const records = (await readRecords()).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    const rows = $("#history-rows");
    rows.replaceChildren();
    $("#history-count").textContent = records.length;
    $("#history-empty").hidden = records.length > 0;
    records.forEach(record => {
      const row = document.createElement("tr");
      const file = document.createElement("td");
      const name = document.createElement("strong");
      const timestamp = document.createElement("small");
      name.textContent = record.filename;
      timestamp.textContent = `${record.reference || "Sans référence"} · Empreinte ${record.hash.slice(0, 12)}…`;
      file.append(name, timestamp);
      const prediction = document.createElement("td");
      prediction.textContent = `${record.label} · ${record.confidence.toFixed(1)} %`;
      const date = document.createElement("td");
      date.textContent = formatDate(record.updatedAt);
      const feedback = document.createElement("td");
      if (record.feedback) {
        const detail = record.feedback.isIncorrect
          ? `Incorrecte · ${manifest.labels[record.feedback.correctedClass]}`
          : "Prédiction correcte";
        feedback.textContent = `Retour · ${detail}`;
        if (record.feedback.note) {
          const note = document.createElement("small");
          note.textContent = record.feedback.note;
          feedback.append(note);
        }
      } else {
        feedback.textContent = "Aucun retour";
      }
      const actions = document.createElement("td");
      actions.className = "table-actions";
      const openButton = document.createElement("button");
      openButton.className = "btn small secondary";
      openButton.type = "button";
      openButton.dataset.recordAction = "detail";
      openButton.dataset.hash = record.hash;
      openButton.textContent = "Ouvrir";
      const reportButton = document.createElement("button");
      reportButton.className = "btn small secondary";
      reportButton.type = "button";
      reportButton.dataset.recordAction = "report";
      reportButton.dataset.hash = record.hash;
      reportButton.textContent = "Rapport";
      actions.append(openButton, reportButton);
      row.append(file, prediction, date, feedback, actions);
      rows.append(row);
    });
  } catch (exception) {
    error(`Historique local indisponible : ${exception.message}`);
  }
}
$("#history-rows").addEventListener("click", event => {
  const button = event.target.closest("[data-record-action]");
  if (button) openRecord(button.dataset.hash, button.dataset.recordAction);
});
$("#detail-report").addEventListener("click", () => showPage("report"));
$("#print-report").addEventListener("click", () => window.print());
function appendProbabilities(container, values) {
  container.replaceChildren();
  for (const [index, code] of manifest.classes.entries()) {
    const probability = values?.[index];
    if (!Number.isFinite(probability)) continue;
    const item = document.createElement("span");
    item.textContent = `${manifest.labels[code]} : ${(probability * 100).toFixed(2)} %`;
    container.append(item);
  }
}
function drawStoredCanvas(id, width, height, pixels) {
  const canvas = $(`#${id}`);
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  const image = context.createImageData(width, height);
  image.data.set(pixels);
  context.putImageData(image, 0, 0);
}
async function drawArchivedImages(record, prefix) {
  const figureIds = ["original", "overlay", "mask"];
  if (!record.imageBlob) {
    figureIds.forEach(name => { $(`#${prefix}-${name}-figure`).hidden = true; });
    return false;
  }
  const bitmap = await createImageBitmap(record.imageBlob);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(bitmap, 0, 0);
  bitmap.close();
  const source = context.getImageData(0, 0, canvas.width, canvas.height);
  drawStoredCanvas(`${prefix}-${figureIds[0]}`, canvas.width, canvas.height, source.data);
  $(`#${prefix}-original-figure`).hidden = false;
  const maskBytes = record.mask ? new Uint8Array(record.mask) : null;
  const validMask = record.segmentationPerformed && maskBytes?.length === canvas.width * canvas.height;
  $(`#${prefix}-overlay-figure`).hidden = !validMask;
  $(`#${prefix}-mask-figure`).hidden = !validMask;
  if (validMask) {
    const overlay = new Uint8ClampedArray(source.data);
    const mask = new Uint8ClampedArray(maskBytes.length * 4);
    for (let index = 0; index < maskBytes.length; index++) {
      const value = maskBytes[index] ? 255 : 0;
      mask[index * 4] = mask[index * 4 + 1] = mask[index * 4 + 2] = value;
      mask[index * 4 + 3] = 255;
      if (maskBytes[index]) {
        overlay[index * 4] = Math.round(overlay[index * 4] * .48 + 255 * .52);
        overlay[index * 4 + 1] = Math.round(overlay[index * 4 + 1] * .48 + 96 * .52);
        overlay[index * 4 + 2] = Math.round(overlay[index * 4 + 2] * .48 + 72 * .52);
      }
    }
    drawStoredCanvas(`${prefix}-overlay`, canvas.width, canvas.height, overlay);
    drawStoredCanvas(`${prefix}-mask`, canvas.width, canvas.height, mask);
  }
  return true;
}
function feedbackSummary(record) {
  if (!record.feedback) return { label: "Aucune revue humaine enregistrée.", note: "Aucune observation saisie.", date: "" };
  const feedback = record.feedback;
  const label = feedback.isIncorrect
    ? `Prédiction signalée incorrecte · classe corrigée : ${manifest.labels[feedback.correctedClass] || "non précisée"}.`
    : "Prédiction indiquée comme correcte.";
  return {
    label,
    note: feedback.note || "Aucune observation saisie.",
    date: feedback.reviewedAt ? `Retour enregistré le ${formatDate(feedback.reviewedAt)}.` : "",
  };
}
async function renderArchivedRecord(record) {
  const reference = record.reference || `EX-${record.hash.slice(0, 8).toUpperCase()}`;
  const confidence = record.confidence.toFixed(1);
  const date = formatDate(record.updatedAt);
  const hasSegmentation = Boolean(record.segmentationPerformed);
  const segmentation = hasSegmentation
    ? `Surface segmentée : ${Number(record.lesionAreaPercent || 0).toFixed(2)} % des pixels.`
    : "Segmentation non appliquée pour la classe prédite normale. Cela ne prouve pas l'absence de lésion.";
  $("#detail-title").textContent = reference;
  $("#detail-meta").textContent = `${date} · ${record.filename}`;
  $("#detail-prediction").textContent = record.label;
  $("#detail-confidence").textContent = `${confidence} %`;
  $("#detail-segmentation").textContent = hasSegmentation ? "Appliquée" : "Non appliquée";
  $("#detail-segmentation-note").textContent = segmentation;
  $("#report-reference").textContent = reference;
  $("#report-date").textContent = date;
  $("#report-filename").textContent = record.filename;
  $("#report-prediction").textContent = record.label;
  $("#report-confidence").textContent = `${confidence} %`;
  $("#report-segmentation").textContent = segmentation;
  const feedback = feedbackSummary(record);
  $("#detail-feedback").textContent = feedback.label;
  $("#detail-feedback-note").textContent = feedback.note;
  $("#detail-feedback-date").textContent = feedback.date;
  $("#report-feedback").textContent = feedback.label;
  $("#report-feedback-note").textContent = feedback.note;
  $("#report-feedback-date").textContent = feedback.date;
  $("#report-review-status").textContent = record.feedback ? "Revue enregistrée localement" : "À examiner";
  appendProbabilities($("#detail-probabilities"), record.probabilities);
  appendProbabilities($("#report-probabilities"), record.probabilities);
  const imageAvailable = await drawArchivedImages(record, "detail");
  await drawArchivedImages(record, "report");
  $("#detail-archive-warning").hidden = imageAvailable;
  $("#detail-archive-warning").textContent = imageAvailable
    ? ""
    : "Cette ancienne analyse ne contient pas l'image source dans l'historique local. Son rapport affiche les métadonnées disponibles ; réanalysez le fichier pour archiver les visuels.";
}
async function renderDashboard() {
  try {
    const records = await readRecords();
    $("#dashboard-total").textContent = records.length;
    $("#dashboard-reviewed").textContent = records.filter(record => record.feedback).length;
    $("#dashboard-pending").textContent = records.filter(record => !record.feedback).length;
    const distribution = $("#dashboard-distribution");
    distribution.replaceChildren();
    for (const code of manifest.classes) {
      const count = records.filter(record => record.code === code).length;
      const item = document.createElement("div");
      const heading = document.createElement("p");
      const label = document.createElement("span");
      const value = document.createElement("strong");
      label.textContent = manifest.labels[code];
      value.textContent = count;
      heading.append(label, value);
      const track = document.createElement("div");
      track.className = "probability-track";
      const bar = document.createElement("span");
      bar.style.width = `${records.length ? count / records.length * 100 : 0}%`;
      track.append(bar);
      item.append(heading, track);
      distribution.append(item);
    }
  } catch (exception) {
    error(`Tableau de bord local indisponible : ${exception.message}`);
  }
}
let activeRecord = null;
function showPage(page) {
  const pages = ["analysis", "dashboard", "history", "detail", "report"];
  pages.forEach(name => { $(`#${name}-page`).hidden = name !== page; });
  document.querySelectorAll(".nav-link").forEach(button => {
    button.classList.toggle("active", button.dataset.page === page
      || (["detail", "report"].includes(page) && button.dataset.page === "history"));
  });
  if (page === "history") renderHistory();
  if (page === "dashboard") renderDashboard();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
async function openRecord(hash, page = "detail") {
  try {
    activeRecord = await readRecord(hash);
    if (!activeRecord) throw new Error("Cette analyse n'existe plus dans l'historique local.");
    await renderArchivedRecord(activeRecord);
    showPage(page);
  } catch (exception) {
    error(`Impossible d'ouvrir l'analyse : ${exception.message}`);
    showPage("history");
  }
}
document.querySelectorAll("[data-page]").forEach(button => {
  button.addEventListener("click", () => showPage(button.dataset.page));
});
function callWorker(action, values = {}, transfer = []) {
  const id = ++requestId;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      pending.delete(id);
      reject(new Error("Le calcul dépasse deux minutes sur cet appareil. Réessayez sur un appareil plus puissant."));
    }, 120000);
    pending.set(id, { resolve, reject, timeout });
    worker.postMessage({ id, action, ...values }, transfer);
  });
}
async function startEngine() {
  if (worker) worker.terminate();
  for (const item of pending.values()) {
    clearTimeout(item.timeout);
    item.reject(new Error("Moteur redémarré."));
  }
  pending.clear();
  worker = new Worker("./inference-worker.js");
  worker.onmessage = ({ data }) => {
    if (data.progress) { status(data.progress); return; }
    const item = pending.get(data.id);
    if (!item) return;
    clearTimeout(item.timeout);
    pending.delete(data.id);
    if (data.error) item.reject(new Error(data.error));
    else item.resolve(data.result);
  };
  worker.onerror = event => {
    for (const item of pending.values()) {
      clearTimeout(item.timeout);
      item.reject(new Error(event.message || "Le moteur local a échoué."));
    }
    pending.clear();
    ready = false;
    controls();
  };
  status("Chargement du classificateur sur cet appareil…");
  await callWorker("init", { manifest });
  ready = true;
  status("Prêt · analyse locale activée");
}
async function prepared() {
  const cache = await caches.open(CACHE);
  const marker = await cache.match("./__offline_ready__");
  if (!marker) return false;
  const previous = await marker.json();
  if (previous.hashes !== Object.values(manifest.models).map(model => model.sha256).join(":")) return false;
  const urls = ["./", "./index.html", "./styles.css", "./app.js", "./preprocess.js",
    "./inference-worker.js", "./manifest.webmanifest", "./icon.svg", "./assets-manifest.json",
    "./vendor/ort.wasm.min.js", "./vendor/ort-wasm-simd-threaded.mjs",
    "./vendor/ort-wasm-simd-threaded.wasm", ...Object.values(manifest.models).map(model => model.url)];
  for (const url of urls) if (!await cache.match(url)) return false;
  return true;
}
async function prepare() {
  busy = true;
  ready = false;
  controls();
  error("");
  const progress = $("#progress");
  progress.hidden = false;
  progress.value = 0;
  try {
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) {
      await new Promise(resolve => navigator.serviceWorker.addEventListener("controllerchange", resolve, { once: true }));
    }
    const response = await fetch("./assets-manifest.json", { cache: "reload" });
    if (!response.ok) throw new Error("Manifest des modèles indisponible.");
    manifest = await response.json();
    const cache = await caches.open(CACHE);
    let done = 0;
    const assets = ["./", "./index.html", "./styles.css", "./app.js", "./preprocess.js",
      "./inference-worker.js", "./manifest.webmanifest", "./icon.svg", "./assets-manifest.json",
      "./vendor/ort.wasm.min.js", "./vendor/ort-wasm-simd-threaded.mjs",
      "./vendor/ort-wasm-simd-threaded.wasm", ...Object.values(manifest.models).map(model => model.url)];
    for (const url of assets) {
      status(`Préparation ${done + 1}/${assets.length} · ${url.replace("./", "")}`);
      const assetResponse = await fetch(url, { cache: "reload" });
      if (!assetResponse.ok) throw new Error(`Téléchargement impossible : ${url}`);
      const model = Object.values(manifest.models).find(item => item.url === url);
      if (model) {
        const bytes = await assetResponse.clone().arrayBuffer();
        const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
          .map(byte => byte.toString(16).padStart(2, "0")).join("");
        if (hash !== model.sha256 || bytes.byteLength !== model.bytes) {
          throw new Error(`Fichier incomplet ou altéré : ${url}`);
        }
      }
      await cache.put(url, assetResponse);
      progress.value = ++done / assets.length * 100;
    }
    await cache.put("./__offline_ready__", new Response(JSON.stringify({
      hashes: Object.values(manifest.models).map(model => model.sha256).join(":"),
      date: new Date().toISOString(),
    })));
    if (navigator.storage?.persist) await navigator.storage.persist();
    await startEngine();
  } catch (exception) {
    ready = false;
    status("Préparation incomplète · rétablir la connexion et réessayer");
    error(exception.message);
  } finally {
    busy = false;
    controls();
  }
}
$("#prepare").addEventListener("click", prepare);

async function selectImage(file) {
  selection = null;
  lastResult = null;
  $("#results").hidden = true;
  $("#empty-state").hidden = false;
  $("#duplicate-notice").hidden = true;
  $("#duplicate-notice").textContent = "";
  $("#preview-wrap").hidden = true;
  $("#drop-zone").hidden = false;
  error("");
  controls();
  if (!file) return;
  try {
    if (!["image/png", "image/jpeg"].includes(file.type)) throw new Error("Sélectionner un fichier PNG ou JPEG.");
    if (file.size > 16 * 1024 * 1024) throw new Error("Le fichier dépasse 16 Mo.");
    const bitmap = await createImageBitmap(file, { colorSpaceConversion: "none", premultiplyAlpha: "none" });
    const width = bitmap.width, height = bitmap.height;
    if (width < 32 || height < 32 || width * height > 4000000) {
      bitmap.close();
      throw new Error("Image trop petite ou trop grande : 32 px minimum et 4 millions de pixels maximum.");
    }
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    context.drawImage(bitmap, 0, 0);
    bitmap.close();
    const rgba = context.getImageData(0, 0, width, height);
    const rgb = new Uint8Array(width * height * 3);
    for (let i = 0; i < width * height; i++) {
      if (rgba.data[i * 4 + 3] !== 255) throw new Error("Ce prototype exige une image opaque, sans transparence.");
      rgb.set(rgba.data.subarray(i * 4, i * 4 + 3), i * 3);
    }
    OfflinePreprocess.validateUltrasound(rgb, width, height);
    const fileBytes = await file.arrayBuffer();
    const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", fileBytes))]
      .map(byte => byte.toString(16).padStart(2, "0")).join("");
    selection = { rgb, width, height, rgba, hash, filename: file.name, imageBlob: file.slice(0, file.size, file.type) };
    $("#source-preview").src = URL.createObjectURL(file);
    $("#filename").textContent = file.name;
    $("#file-size").textContent = `${(file.size / 1024 / 1024).toFixed(1)} Mo`;
    $("#preview-wrap").hidden = false;
    $("#drop-zone").hidden = true;
    const previous = await readRecord(hash);
    if (previous) {
      $("#duplicate-notice").textContent = `Cette image a déjà été analysée le ${formatDate(previous.updatedAt)} : ${previous.label} (${previous.confidence.toFixed(1)} %). Vous pouvez l'analyser à nouveau si nécessaire.`;
      $("#duplicate-notice").hidden = false;
    }
  } catch (exception) {
    error(exception.message || "Impossible de lire cette image.");
    $("#image").value = "";
  }
  controls();
}
$("#image").addEventListener("change", event => selectImage(event.target.files[0]));
$("#remove-image").addEventListener("click", () => {
  selection = null;
  lastResult = null;
  $("#image").value = "";
  $("#preview-wrap").hidden = true;
  $("#drop-zone").hidden = false;
  $("#duplicate-notice").hidden = true;
  $("#results").hidden = true;
  $("#empty-state").hidden = false;
  controls();
});
const dropZone = $("#drop-zone");
["dragenter", "dragover"].forEach(name => dropZone.addEventListener(name, event => {
  event.preventDefault();
  dropZone.classList.add("dragging");
}));
["dragleave", "drop"].forEach(name => dropZone.addEventListener(name, event => {
  event.preventDefault();
  dropZone.classList.remove("dragging");
}));
dropZone.addEventListener("drop", event => selectImage(event.dataTransfer.files[0]));

function drawCanvas(name, pixels) {
  const canvas = $("#" + name);
  canvas.width = selection.width;
  canvas.height = selection.height;
  canvas.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(pixels), selection.width, selection.height), 0, 0);
}
function setResultView(view) {
  const canvas = view === "original" ? "original" : view;
  ["original", "overlay", "mask"].forEach(name => { $("#" + name).hidden = name !== canvas; });
  const activeCanvas = $("#" + canvas);
  $("#download-result").href = activeCanvas.toDataURL("image/png");
  $("#download-result").download = view === "original" ? "echographie-originale.png"
    : view === "mask" ? "masque-segmentation.png" : "superposition-segmentation.png";
  document.querySelectorAll(".view-toggle").forEach(button => {
    button.classList.toggle("active", button.dataset.view === view);
    button.disabled = view !== "original" && !lastResult?.segmentationPerformed;
  });
}
function paint(result) {
  const confidence = result.probabilities[manifest.classes.indexOf(result.code)] * 100;
  $("#prediction").textContent = result.label;
  $("#confidence-value").textContent = `${confidence.toFixed(1)}%`;
  $("#confidence-ring").style.setProperty("--confidence", `${confidence * 3.6}deg`);
  const probabilities = $("#probabilities");
  probabilities.replaceChildren();
  manifest.classes.forEach((code, index) => {
    const row = document.createElement("div");
    row.className = "probability-row";
    const label = document.createElement("div");
    const name = document.createElement("span");
    const value = document.createElement("strong");
    name.textContent = manifest.labels[code];
    value.textContent = `${(result.probabilities[index] * 100).toFixed(1)}%`;
    label.append(name, value);
    const track = document.createElement("div");
    track.className = "probability-track";
    const bar = document.createElement("span");
    bar.style.width = `${result.probabilities[index] * 100}%`;
    track.append(bar);
    row.append(label, track);
    probabilities.append(row);
  });
  $("#segmentation-title").textContent = result.segmentationPerformed ? "Région segmentée" : "Classe prédite normale";
  $("#lesion-area-wrap").hidden = !result.segmentationPerformed;
  $("#lesion-area").textContent = result.segmentationPerformed ? `${result.lesionAreaPercent.toFixed(2)}%` : "—";
  $("#segmentation-status").textContent = result.segmentationPerformed
    ? "Masque prédit · seuil 0,5 · plus grande composante conservée."
    : "Segmentation non appliquée pour une classe prédite normale. Cela ne prouve pas l'absence de lésion.";
  drawCanvas("original", selection.rgba.data);
  if (result.mask) {
    const source = new Uint8ClampedArray(selection.rgba.data);
    const mask = new Uint8ClampedArray(selection.width * selection.height * 4);
    for (let i = 0; i < result.mask.length; i++) {
      const value = result.mask[i] ? 255 : 0;
      mask[i * 4] = mask[i * 4 + 1] = mask[i * 4 + 2] = value;
      mask[i * 4 + 3] = 255;
      if (result.mask[i]) {
        source[i * 4] = Math.round(source[i * 4] * .48 + 255 * .52);
        source[i * 4 + 1] = Math.round(source[i * 4 + 1] * .48 + 96 * .52);
        source[i * 4 + 2] = Math.round(source[i * 4 + 2] * .48 + 72 * .52);
      }
    }
    drawCanvas("overlay", source);
    drawCanvas("mask", mask);
  }
  $("#timing").textContent = `${(result.inferenceMs / 1000).toFixed(2)} s · calcul sur cet appareil`;
  $("#results").hidden = false;
  $("#empty-state").hidden = true;
  setResultView(result.segmentationPerformed ? "overlay" : "original");
}
document.querySelectorAll(".view-toggle").forEach(button => {
  button.addEventListener("click", () => setResultView(button.dataset.view));
});

async function analyze() {
  if (!ready || busy || !selection) return;
  busy = true;
  lastResult = null;
  controls();
  error("");
  $("#empty-state").hidden = true;
  $("#results").hidden = true;
  $("#loading-state").hidden = false;
  try {
    const { rgb, width, height } = selection;
    const transferred = rgb.slice();
    const result = await callWorker("analyze", { rgb: transferred.buffer, width, height }, [transferred.buffer]);
    lastResult = result;
    paint(result);
    status("Analyse terminée · résultat et empreinte enregistrés localement");
    const existing = await readRecord(selection.hash);
    const confidence = result.probabilities[manifest.classes.indexOf(result.code)] * 100;
    const now = new Date();
    const reference = $("#exam-reference").value.trim()
      || `EX-${now.toISOString().slice(0, 10).replaceAll("-", "")}-${selection.hash.slice(0, 6).toUpperCase()}`;
    await saveRecord({
      hash: selection.hash,
      filename: selection.filename,
      reference,
      code: result.code,
      label: result.label,
      confidence,
      probabilities: [...result.probabilities],
      segmentationPerformed: result.segmentationPerformed,
      lesionAreaPercent: result.lesionAreaPercent || 0,
      inferenceMs: result.inferenceMs,
      width: selection.width,
      height: selection.height,
      imageBlob: selection.imageBlob,
      mask: result.mask ? new Uint8Array(result.mask) : null,
      updatedAt: now.toISOString(),
      feedback: existing?.feedback || null,
    });
    await renderHistory();
  } catch (exception) {
    error(exception.message || "L'analyse ou l'enregistrement local a échoué.");
    status("Analyse interrompue · aucun résultat complet disponible");
  } finally {
    busy = false;
    $("#loading-state").hidden = true;
    controls();
  }
}
$("#analyze").addEventListener("click", analyze);
$("#feedback-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!selection || !lastResult) return;
  const assessment = document.querySelector('input[name="assessment"]:checked')?.value;
  const correctedClass = $("#corrected-class").value;
  const note = $("#feedback-note").value.trim();
  if (!assessment) {
    $("#feedback-status").textContent = "Indiquer si la prédiction est correcte ou incorrecte.";
    return;
  }
  if (assessment === "incorrect" && !correctedClass) {
    $("#feedback-status").textContent = "Choisir la classe corrigée pour signaler une erreur.";
    return;
  }
  try {
    const record = await readRecord(selection.hash);
    if (!record) throw new Error("Analyse introuvable dans l'historique local. Relancez l'analyse.");
    record.feedback = {
      isIncorrect: assessment === "incorrect",
      correctedClass: assessment === "incorrect" ? correctedClass : "",
      note,
      reviewedAt: new Date().toISOString(),
    };
    await saveRecord(record);
    $("#feedback-status").textContent = "Retour enregistré uniquement sur cet appareil. Il n'est pas transmis à l'application web.";
    await renderHistory();
  } catch (exception) {
    $("#feedback-status").textContent = exception.message;
  }
});
document.querySelectorAll('input[name="assessment"]').forEach(input => {
  input.addEventListener("change", () => {
    const incorrect = input.value === "incorrect" && input.checked;
    $("#correction-wrap").hidden = !incorrect;
    $("#corrected-class").required = incorrect;
    if (!incorrect) $("#corrected-class").value = "";
  });
});

async function initialize() {
  busy = true;
  controls();
  try {
    if (!isSecureContext || !("serviceWorker" in navigator)) {
      throw new Error("Utiliser HTTPS ou l’adresse locale 127.0.0.1 avec un navigateur compatible.");
    }
    const response = await fetch("./assets-manifest.json");
    if (!response.ok) throw new Error("Les modèles n’ont pas encore été exportés.");
    manifest = await response.json();
    $("#download-size").textContent = `Modèles · ${(Object.values(manifest.models).reduce((sum, model) => sum + model.bytes, 0) / 1e6).toFixed(1)} Mo, plus le moteur.`;
    await renderHistory();
    await navigator.serviceWorker.register("./sw.js");
    await navigator.serviceWorker.ready;
    if (await prepared()) {
      await startEngine();
    } else {
      status("Appareil non préparé · téléchargez les modèles avant de passer hors ligne");
    }
  } catch (exception) {
    error(exception.message);
    status("Mode hors ligne non prêt");
  } finally {
    busy = false;
    controls();
  }
}
initialize();
