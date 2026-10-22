"use strict";
const CACHE = "busbra-offline-poc-v1";
const ASSETS = ["./", "./index.html", "./styles.css", "./app.js", "./preprocess.js", "./inference-worker.js",
  "./manifest.webmanifest", "./icon.svg", "./assets-manifest.json", "./vendor/ort.wasm.min.js",
  "./vendor/ort-wasm-simd-threaded.mjs", "./vendor/ort-wasm-simd-threaded.wasm"];
const $ = selector => document.querySelector(selector);
const pending = new Map();
let worker, manifest, requestId = 0, busy = false, ready = false, selection = null, lastResult = null;

function networkStatus() {
  const offline = !navigator.onLine;
  $("#network").textContent = offline ? "Hors ligne · calcul local" : "Connexion disponible";
  $("#network").classList.toggle("offline", offline);
}
addEventListener("online", networkStatus); addEventListener("offline", networkStatus); networkStatus();
function status(message) { $("#status").textContent = message; }
function error(message) { $("#error").textContent = message; $("#error").hidden = !message; }
function controls() {
  $("#prepare").disabled = busy;
  $("#image").disabled = busy || !ready;
  $("#analyze").disabled = busy || !ready || !selection;
}
function callWorker(action, values = {}, transfer = []) {
  const id = ++requestId;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { pending.delete(id); reject(new Error("Le calcul dépasse deux minutes sur cet appareil. Réessayez sur un appareil plus puissant.")); }, 120000);
    pending.set(id, { resolve, reject, timeout });
    worker.postMessage({ id, action, ...values }, transfer);
  });
}
async function startEngine() {
  if (worker) worker.terminate();
  for (const item of pending.values()) { clearTimeout(item.timeout); item.reject(new Error("Moteur redémarré.")); }
  pending.clear();
  worker = new Worker("./inference-worker.js");
  worker.onmessage = ({ data }) => {
    if (data.progress) { status(data.progress); return; }
    const item = pending.get(data.id);
    if (!item) return;
    clearTimeout(item.timeout); pending.delete(data.id);
    if (data.error) item.reject(new Error(data.error)); else item.resolve(data.result);
  };
  worker.onerror = event => {
    for (const item of pending.values()) { clearTimeout(item.timeout); item.reject(new Error(event.message || "Le moteur local a échoué.")); }
    pending.clear(); ready = false; controls();
  };
  status("Chargement du classificateur sur cet appareil…");
  await callWorker("init", { manifest });
  ready = true;
  status("Prêt : vous pouvez couper la connexion, recharger la page et analyser une nouvelle image.");
}
async function prepared() {
  const cache = await caches.open(CACHE), marker = await cache.match("./__offline_ready__");
  if (!marker) return false;
  const previous = await marker.json();
  if (previous.hashes !== Object.values(manifest.models).map(m => m.sha256).join(":")) return false;
  const urls = [...ASSETS, ...Object.values(manifest.models).map(model => model.url)];
  for (const url of urls) if (!await cache.match(url)) return false;
  return true;
}
async function prepare() {
  busy = true; ready = false; controls(); error("");
  const progress = $("#progress"); progress.hidden = false; progress.value = 0;
  try {
    const registration = await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) {
      await new Promise(resolve => navigator.serviceWorker.addEventListener("controllerchange", resolve, { once: true }));
    }
    const response = await fetch("./assets-manifest.json", { cache: "reload" });
    if (!response.ok) throw new Error("Manifest des modèles indisponible.");
    manifest = await response.json();
    const cache = await caches.open(CACHE);
    let done = 0;
    const list = [...ASSETS, ...Object.values(manifest.models).map(model => model.url)];
    for (const url of list) {
      status(`Préparation ${done + 1}/${list.length} : ${url.replace("./", "")}`);
      const response = await fetch(url, { cache: "reload" });
      if (!response.ok) throw new Error(`Téléchargement impossible : ${url}`);
      const model = Object.values(manifest.models).find(model => model.url === url);
      if (model) {
        const data = await response.clone().arrayBuffer();
        const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", data))].map(b => b.toString(16).padStart(2, "0")).join("");
        if (hash !== model.sha256 || data.byteLength !== model.bytes) throw new Error(`Fichier incomplet ou altéré : ${url}`);
      }
      await cache.put(url, response);
      progress.value = ++done / list.length * 100;
    }
    await cache.put("./__offline_ready__", new Response(JSON.stringify({ hashes: Object.values(manifest.models).map(m => m.sha256).join(":"), date: new Date().toISOString() })));
    if (navigator.storage?.persist) await navigator.storage.persist();
    await startEngine();
  } catch (exception) {
    ready = false;
    status("Préparation incomplète. Rétablir la connexion et réessayer.");
    error(exception.message);
  } finally { busy = false; controls(); }
}
$("#prepare").addEventListener("click", prepare);

async function selectImage(file) {
  selection = null; lastResult = null; $("#results").hidden = true; error(""); controls();
  if (!file) return;
  try {
    if (!["image/png", "image/jpeg"].includes(file.type)) throw new Error("Sélectionner un fichier PNG ou JPEG.");
    if (file.size > 16 * 1024 * 1024) throw new Error("Le fichier dépasse 16 Mo.");
    const bitmap = await createImageBitmap(file, { colorSpaceConversion: "none", premultiplyAlpha: "none" });
    const width = bitmap.width, height = bitmap.height;
    if (width < 32 || height < 32 || width * height > 4000000) {
      bitmap.close(); throw new Error("Image trop petite ou trop grande : 32 px minimum et 4 millions de pixels maximum.");
    }
    const canvas = $("#original"); canvas.width = width; canvas.height = height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    context.clearRect(0, 0, width, height); context.drawImage(bitmap, 0, 0); bitmap.close();
    const rgba = context.getImageData(0, 0, width, height), rgb = new Uint8Array(width * height * 3);
    for (let i = 0; i < width * height; i++) {
      if (rgba.data[i * 4 + 3] !== 255) throw new Error("Ce prototype exige une image opaque, sans transparence.");
      rgb.set(rgba.data.subarray(i * 4, i * 4 + 3), i * 3);
    }
    selection = { rgb, width, height, rgba };
    $("#filename").textContent = `${file.name} · ${width} × ${height} pixels`;
  } catch (exception) { error(exception.message || "Impossible de lire cette image."); }
  controls();
}
$("#image").addEventListener("change", event => { selectImage(event.target.files[0]); });
function paint(result) {
  $("#prediction").textContent = `${result.label} · ${(result.probabilities[manifest.classes.indexOf(result.code)] * 100).toFixed(1)} %`;
  $("#timing").textContent = `${(result.inferenceMs / 1000).toFixed(2)} s · calcul sur l’appareil`;
  const probabilities = $("#probabilities"); probabilities.replaceChildren();
  manifest.classes.forEach((code, i) => {
    const item = document.createElement("div"), strong = document.createElement("strong");
    item.textContent = manifest.labels[code]; strong.textContent = `${(result.probabilities[i] * 100).toFixed(2)} %`;
    item.append(strong); probabilities.append(item);
  });
  $("#mask-figure").hidden = $("#overlay-figure").hidden = !result.segmentationPerformed;
  $("#segmentation-status").textContent = result.segmentationPerformed
    ? `Surface segmentée : ${result.lesionAreaPercent.toFixed(2)} % des pixels. TTA activée ; plus grande composante conservée.`
    : "Segmentation non appliquée : classe prédite normale. Aucun masque n’a été calculé.";
  for (const name of ["mask", "overlay"]) {
    const canvas = $("#" + name); canvas.width = result.width; canvas.height = result.height;
    const context = canvas.getContext("2d"); context.clearRect(0, 0, canvas.width, canvas.height);
    if (!result.mask) continue;
    const data = new Uint8ClampedArray(selection.rgba.data);
    for (let i = 0; i < result.mask.length; i++) {
      if (name === "mask") data[i * 4] = data[i * 4 + 1] = data[i * 4 + 2] = result.mask[i] * 255;
      else if (result.mask[i]) {
        data[i * 4] = Math.round(data[i * 4] * .48 + 255 * .52);
        data[i * 4 + 1] = Math.round(data[i * 4 + 1] * .48 + 96 * .52);
        data[i * 4 + 2] = Math.round(data[i * 4 + 2] * .48 + 72 * .52);
      }
    }
    context.putImageData(new ImageData(data, result.width, result.height), 0, 0);
  }
  $("#results").hidden = false;
}
async function analyze() {
  if (!ready || busy || !selection) return;
  busy = true; lastResult = null; controls(); error(""); $("#results").hidden = true;
  try {
    const { rgb, width, height } = selection;
    // Transfer a copy; the original remains available only in this page's memory.
    const transferred = rgb.slice();
    const result = await callWorker("analyze", { rgb: transferred.buffer, width, height }, [transferred.buffer]);
    lastResult = result; paint(result);
    status("Analyse terminée localement. Aucune image n’a été envoyée au serveur.");
  } catch (exception) { error(exception.message); status("Analyse interrompue ; aucun nouveau résultat disponible."); }
  finally { busy = false; controls(); }
}
$("#analyze").addEventListener("click", analyze);
// Observable state for the automated proof; no precomputed predictions are loaded.
window.offlineDemo = { get ready() { return ready; }, get lastResult() { return lastResult; },
  get selected() { return Boolean(selection); }, analyze, selectImage };
(async () => {
  busy = true; controls();
  try {
    if (!isSecureContext || !("serviceWorker" in navigator)) throw new Error("Utiliser HTTPS ou l’adresse locale 127.0.0.1 avec un navigateur compatible.");
    const response = await fetch("./assets-manifest.json");
    if (!response.ok) throw new Error("Les modèles n’ont pas encore été exportés.");
    manifest = await response.json();
    $("#download-size").textContent = `Modèles : ${(Object.values(manifest.models).reduce((n, model) => n + model.bytes, 0) / 1e6).toFixed(1)} Mo, plus le moteur.`;
    await navigator.serviceWorker.register("./sw.js");
    await navigator.serviceWorker.ready;
    if (await prepared()) await startEngine();
    else status("Appareil non préparé : télécharger les modèles avec le bouton ci-dessus.");
  } catch (exception) { error(exception.message); status("Le mode hors ligne n’est pas encore disponible."); }
  finally { busy = false; controls(); }
})();
