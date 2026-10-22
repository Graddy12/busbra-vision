"use strict";
importScripts("./vendor/ort.wasm.min.js", "./preprocess.js");
ort.env.wasm.wasmPaths = new URL("./vendor/", self.location.href).href;
ort.env.wasm.numThreads = 1;
ort.env.logLevel = "warning";

let manifest, classifier, segmenter;
const statistics = { classificationRuns: 0, segmentationRuns: 0 };
async function verifiedModel(name) {
  const item = manifest.models[name];
  const response = await fetch(item.url);
  if (!response.ok) throw new Error(`Modèle ${name} indisponible. Préparez à nouveau le mode hors ligne.`);
  const data = await response.arrayBuffer();
  const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", data))].map(v => v.toString(16).padStart(2, "0")).join("");
  if (hash !== item.sha256 || data.byteLength !== item.bytes) throw new Error(`Intégrité du modèle ${name} invalide.`);
  return ort.InferenceSession.create(data, { executionProviders: ["wasm"], graphOptimizationLevel: "all" });
}
async function analyze(rgb, width, height, id) {
  if (!classifier) throw new Error("Le moteur n'est pas encore prêt.");
  if (width < 32 || height < 32 || width * height > 4000000 || rgb.length !== width * height * 3) {
    throw new Error("Dimensions non prises en charge par ce prototype (32 px minimum, 4 millions de pixels maximum).");
  }
  OfflinePreprocess.validateUltrasound(rgb, width, height);
  const started = performance.now();
  self.postMessage({ id, progress: "Classification locale…" });
  const pixels = OfflinePreprocess.classificationInput(rgb, width, height, manifest.classifier.size);
  const output = await classifier.run({ image: new ort.Tensor("float32", pixels, [1, 224, 224, 3]) });
  statistics.classificationRuns++;
  const probabilities = [...output.probabilities.data];
  if (probabilities.length !== 3 || probabilities.some(value => !Number.isFinite(value))) {
    throw new Error("Sortie du classificateur invalide.");
  }
  let winner = 0;
  probabilities.forEach((p, i) => { if (p > probabilities[winner]) winner = i; });
  const code = manifest.classes[winner];
  let mask = null, lesionAreaPercent = null;
  if (code === "benign" || code === "malignant") {
    self.postMessage({ id, progress: "Segmentation locale et TTA…" });
    if (!segmenter) segmenter = await verifiedModel("segmentation");
    const input = OfflinePreprocess.segmentationInput(rgb, width, height, manifest.segmenter);
    const result = await segmenter.run({ image: new ort.Tensor("float32", input.data, [1, 3, 384, 384]) });
    statistics.segmentationRuns++;
    const restored = OfflinePreprocess.restoreProbability(result.probability_map.data, input.geometry);
    mask = Uint8Array.from(restored, value => value >= manifest.segmenter.threshold ? 1 : 0);
    if (manifest.segmenter.largest) mask = OfflinePreprocess.largestComponent(mask, width, height);
    let selected = 0;
    for (const pixel of mask) selected += pixel;
    lesionAreaPercent = 100 * selected / mask.length;
  } else if (code !== "normal") {
    throw new Error("Classe inconnue ; aucune segmentation effectuée.");
  }
  return { code, label: manifest.labels[code], probabilities, mask, width, height,
    segmentationPerformed: mask !== null, lesionAreaPercent,
    inferenceMs: Math.round(performance.now() - started), statistics: { ...statistics },
    engine: "ONNX Runtime Web / WebAssembly CPU / FP32", gradcamAvailable: false };
}
self.onmessage = async ({ data }) => {
  const { id, action } = data;
  try {
    if (action === "init") {
      manifest = data.manifest;
      classifier = await verifiedModel("classification");
      self.postMessage({ id, result: { ready: true } });
    } else if (action === "analyze") {
      const result = await analyze(new Uint8Array(data.rgb), data.width, data.height, id);
      self.postMessage({ id, result }, result.mask ? [result.mask.buffer] : []);
    } else throw new Error("Commande inconnue.");
  } catch (error) {
    self.postMessage({ id, error: error.message || String(error) });
  }
};
