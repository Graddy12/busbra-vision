const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const p = require("../offline_poc/preprocess.js");
const root = path.resolve(__dirname, "..");

test("domain screening rejects color photos, blank images, gradients, graphics and noise", () => {
  for (const kind of ["color", "black", "white", "flat", "gradient", "graphic", "noise"]) {
    const rgb = new Uint8Array(128 * 128 * 3);
    let seed = 19;
    for (let y = 0; y < 128; y++) for (let x = 0; x < 128; x++) {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      const value = kind === "white" ? 255 : kind === "flat" ? 100
        : kind === "gradient" ? x * 2 : kind === "graphic" ? ((x >> 3) + (y >> 3)) % 2 * 255
        : kind === "noise" ? seed >>> 24 : 0;
      const i = (y * 128 + x) * 3;
      rgb[i] = value; rgb[i + 1] = kind === "color" ? 140 : value;
      rgb[i + 2] = kind === "color" ? 230 : value;
    }
    assert.throws(() => p.validateUltrasound(rgb, 128, 128), /Image refusée/, kind);
  }
});

test("textured grayscale input passes domain screening", () => {
  const rgb = new Uint8Array(64 * 64 * 3);
  let seed = 19;
  for (let i = 0; i < rgb.length; i += 3) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    rgb.fill(35 + seed % 90, i, i + 3);
  }
  assert.ok(p.validateUltrasound(rgb, 64, 64).entropy > 4.5);
});

test("rounding matches Python at positive and negative half-integers", () => {
  assert.deepEqual([-.5, -1.5, -2.5, .5, 1.5, 2.5, 3.5].map(p.roundEven), [0, -2, -2, 0, 2, 2, 4]);
});
test("normalization and identity resizing preserve pixels", () => {
  const source = new Uint8Array([0, 64, 255, 127, 2, 7, 4, 40, 230, 255, 255, 255]);
  assert.deepEqual(p.resizePillow(source, 2, 2, 2, 2), source);
  assert.deepEqual(p.resizeLinearRGB(source, 2, 2, 2, 2), source);
});
test("segmentation and classification have deliberately distinct padding", () => {
  assert.equal(p.geometry(384, 129, 384).top, 127);
  assert.equal(p.geometry(384, 129, 384, true).top, 128);
});
test("largest component uses eight-connectivity; an empty mask stays empty", () => {
  const input = new Uint8Array([1, 0, 0, 0, 1, 0, 0, 0, 1]);
  assert.deepEqual(p.largestComponent(input, 3, 3), input);
  assert.deepEqual(p.largestComponent(new Uint8Array(9), 3, 3), new Uint8Array(9));
  assert.deepEqual(p.largestComponent(new Uint8Array([1, 1, 0, 0, 1]), 5, 1), new Uint8Array([1, 1, 0, 0, 0]));
});
test("all exported public-sample tensors match Python exactly", { skip: !fs.existsSync(path.join(root, "output/offline_poc/python_reference.json")) }, () => {
  const reference = JSON.parse(fs.readFileSync(path.join(root, "output/offline_poc/python_reference.json")));
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "offline_poc/assets-manifest.json")));
  for (const sample of reference.samples) {
    const base = path.join(root, "output/offline_poc", sample.id);
    const rgb = new Uint8Array(fs.readFileSync(base + "_rgb.bin"));
    assert.doesNotThrow(() => p.validateUltrasound(rgb, sample.width, sample.height), sample.source);
    const expectedClass = fs.readFileSync(base + "_classification_input.bin");
    const actualClass = p.classificationInput(rgb, sample.width, sample.height);
    assert.equal(Buffer.compare(Buffer.from(actualClass.buffer), expectedClass), 0, sample.source);
    if (sample.predicted !== "normal") {
      const expectedSegmentation = fs.readFileSync(base + "_segmentation_input.bin");
      const actualSegmentation = p.segmentationInput(rgb, sample.width, sample.height, manifest.segmenter).data;
      assert.equal(Buffer.compare(Buffer.from(actualSegmentation.buffer), expectedSegmentation), 0, sample.source);
    }
  }
});
