/* Deterministic preprocessing, without canvas resizing or external libraries.
 * Pillow bilinear uses a separable antialiasing filter with 22-bit coefficients.
 * OpenCV INTER_LINEAR uses half-pixel coordinates and 11-bit RGB coefficients.
 * Input screening is heuristic, not a clinically validated domain detector.
 */
(function (scope) {
  "use strict";
  const f32 = Math.fround;
  function validateUltrasound(rgb, width, height) {
    const rejection = "Image refusée : contenu incompatible avec une échographie mammaire en niveaux de gris (mode B), ou qualité insuffisante. Importez l'image échographique originale, sans graphique ni capture d'écran.";
    if (width < 32 || height < 32 || width / height < .4 || width / height > 2.5
        || rgb.length !== width * height * 3) throw new Error(rejection);
    // Same central crop, sampling and thresholds as cancer_app/image_validation.py.
    const step = Math.max(1, Math.ceil(Math.max(width, height) / 512));
    const left = Math.floor(width / 8), top = Math.floor(height / 8);
    const columns = Math.ceil((width - 2 * left) / step);
    const rows = Math.ceil((height - 2 * top) / step);
    const gray = new Uint8Array(columns * rows), hist = new Uint32Array(256);
    let count = 0, color = 0, sum = 0, squares = 0, midtones = 0;
    for (let y = top; y < height - top; y += step) for (let x = left; x < width - left; x += step) {
      const i = (y * width + x) * 3, r = rgb[i], g = rgb[i + 1], b = rgb[i + 2];
      const value = Math.floor((r + g + b) / 3);
      gray[count++] = value; hist[value]++; sum += value; squares += value * value;
      if (Math.max(r, g, b) - Math.min(r, g, b) > 18) color++;
      if (value > 20 && value < 235) midtones++;
    }
    let entropy = 0, textured = 0, edges = 0;
    for (const frequency of hist) if (frequency) {
      const probability = frequency / count;
      entropy -= probability * Math.log2(probability);
    }
    for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) {
      const i = y * columns + x, value = gray[i];
      if (x && Math.abs(value - gray[i - 1]) > 60) edges++;
      if (y && Math.abs(value - gray[i - columns]) > 60) edges++;
      if (x && y && x < columns - 1 && y < rows - 1
          && Math.abs(4 * value - gray[i - 1] - gray[i + 1] - gray[i - columns] - gray[i + columns]) > 12) textured++;
    }
    const features = { color_fraction: color / count,
      std: Math.sqrt(Math.max(0, squares / count - (sum / count) ** 2)), entropy,
      midtone_fraction: midtones / count, texture_fraction: textured / ((rows - 2) * (columns - 2)),
      edge_fraction: edges / ((rows - 1) * columns + rows * (columns - 1)) };
    if (features.color_fraction > .08 || features.std < 12 || features.entropy < 4.5
        || features.midtone_fraction < .25 || features.texture_fraction < .08
        || features.edge_fraction > .18) throw new Error(rejection);
    return features;
  }
  function roundEven(value) {
    const base = Math.floor(value), fraction = value - base;
    return fraction === .5 ? base + Math.abs(base % 2) : Math.round(value);
  }
  function geometry(width, height, size, pillow = false) {
    const scale = Math.min(size / width, size / height);
    const newWidth = Math.max(1, Math.min(size, roundEven(width * scale)));
    const newHeight = Math.max(1, Math.min(size, roundEven(height * scale)));
    return { width, height, newWidth, newHeight,
      left: pillow ? roundEven((size - newWidth) / 2) : Math.floor((size - newWidth) / 2),
      top: pillow ? roundEven((size - newHeight) / 2) : Math.floor((size - newHeight) / 2), size };
  }
  function pillowCoefficients(inputSize, outputSize) {
    const scale = inputSize / outputSize, support = Math.max(1, scale), result = [];
    for (let i = 0; i < outputSize; i++) {
      const center = (i + .5) * scale;
      const start = Math.max(0, Math.trunc(center - support + .5));
      const end = Math.min(inputSize, Math.trunc(center + support + .5));
      const weights = [];
      let total = 0;
      for (let k = start; k < end; k++) {
        const weight = Math.max(0, 1 - Math.abs((k - center + .5) / support));
        weights.push(weight); total += weight;
      }
      result.push({ start, weights: weights.map(w => Math.floor(w / total * 4194304 + .5)) });
    }
    return result;
  }
  function resizePillow(rgb, width, height, targetWidth, targetHeight) {
    const hx = pillowCoefficients(width, targetWidth), hy = pillowCoefficients(height, targetHeight);
    const horizontal = new Uint8Array(targetWidth * height * 3);
    for (let y = 0; y < height; y++) for (let x = 0; x < targetWidth; x++) {
      const coefficients = hx[x];
      for (let c = 0; c < 3; c++) {
        let sum = 2097152;
        for (let k = 0; k < coefficients.weights.length; k++) {
          sum += rgb[(y * width + coefficients.start + k) * 3 + c] * coefficients.weights[k];
        }
        horizontal[(y * targetWidth + x) * 3 + c] = Math.floor(sum / 4194304);
      }
    }
    const result = new Uint8Array(targetWidth * targetHeight * 3);
    for (let y = 0; y < targetHeight; y++) for (let x = 0; x < targetWidth; x++) {
      const coefficients = hy[y];
      for (let c = 0; c < 3; c++) {
        let sum = 2097152;
        for (let k = 0; k < coefficients.weights.length; k++) {
          sum += horizontal[((coefficients.start + k) * targetWidth + x) * 3 + c] * coefficients.weights[k];
        }
        result[(y * targetWidth + x) * 3 + c] = Math.floor(sum / 4194304);
      }
    }
    return result;
  }
  function linearCoordinates(inputSize, outputSize, floatCoordinates = false) {
    return Array.from({ length: outputSize }, (_, index) => {
      const value = (index + .5) * inputSize / outputSize - .5;
      const coordinate = floatCoordinates ? f32(value) : value;
      const start = Math.floor(coordinate);
      return { a: Math.max(0, Math.min(inputSize - 1, start)),
        b: Math.max(0, Math.min(inputSize - 1, start + 1)), fraction: coordinate - start };
    });
  }
  function resizeLinearRGB(rgb, width, height, targetWidth, targetHeight) {
    const hx = linearCoordinates(width, targetWidth, true), hy = linearCoordinates(height, targetHeight, true);
    const result = new Uint8Array(targetWidth * targetHeight * 3);
    for (let y = 0; y < targetHeight; y++) for (let x = 0; x < targetWidth; x++) {
      const h = hx[x], v = hy[y];
      const a0 = roundEven(f32(1 - h.fraction) * 2048), a1 = roundEven(f32(h.fraction) * 2048);
      const b0 = roundEven(f32(1 - v.fraction) * 2048), b1 = roundEven(f32(v.fraction) * 2048);
      for (let c = 0; c < 3; c++) {
        const upper = rgb[(v.a * width + h.a) * 3 + c] * a0 + rgb[(v.a * width + h.b) * 3 + c] * a1;
        const lower = rgb[(v.b * width + h.a) * 3 + c] * a0 + rgb[(v.b * width + h.b) * 3 + c] * a1;
        // OpenCV's optimized 8-bit vertical pass rounds these two terms separately.
        result[(y * targetWidth + x) * 3 + c] = (((b0 * (upper >> 4)) >> 16) + ((b1 * (lower >> 4)) >> 16) + 2) >> 2;
      }
    }
    return result;
  }
  function classificationInput(rgb, width, height, size = 224) {
    const g = geometry(width, height, size, true);
    const resized = resizePillow(rgb, width, height, g.newWidth, g.newHeight);
    const data = new Float32Array(size * size * 3);
    for (let y = 0; y < g.newHeight; y++) {
      const start = y * g.newWidth * 3;
      data.set(resized.subarray(start, start + g.newWidth * 3), ((y + g.top) * size + g.left) * 3);
    }
    return data;
  }
  function segmentationInput(rgb, width, height, config) {
    const size = config.size, g = geometry(width, height, size);
    const resized = resizeLinearRGB(rgb, width, height, g.newWidth, g.newHeight);
    const area = size * size, data = new Float32Array(area * 3);
    for (let c = 0; c < 3; c++) {
      data.fill(f32(f32(0 - config.mean[c]) / config.std[c]), c * area, (c + 1) * area);
      for (let y = 0; y < g.newHeight; y++) for (let x = 0; x < g.newWidth; x++) {
        const value = resized[(y * g.newWidth + x) * 3 + c];
        data[c * area + (y + g.top) * size + x + g.left] = f32(f32(f32(value / 255) - config.mean[c]) / config.std[c]);
      }
    }
    return { data, geometry: g };
  }
  function restoreProbability(probabilities, g) {
    const hx = linearCoordinates(g.newWidth, g.width), hy = linearCoordinates(g.newHeight, g.height);
    const result = new Float32Array(g.width * g.height);
    for (let y = 0; y < g.height; y++) for (let x = 0; x < g.width; x++) {
      const h = hx[x], v = hy[y];
      const at = (xx, yy) => probabilities[(yy + g.top) * g.size + xx + g.left];
      const top = at(h.a, v.a) * (1 - h.fraction) + at(h.b, v.a) * h.fraction;
      const bottom = at(h.a, v.b) * (1 - h.fraction) + at(h.b, v.b) * h.fraction;
      result[y * g.width + x] = Math.max(0, Math.min(1, top * (1 - v.fraction) + bottom * v.fraction));
    }
    return result;
  }
  function largestComponent(mask, width, height) {
    const visited = new Uint8Array(mask.length), queue = new Int32Array(mask.length);
    let bestStart = -1, bestSize = 0;
    const flood = (seed, emit) => {
      let head = 0, tail = 1;
      queue[0] = seed; visited[seed] = 1;
      while (head < tail) {
        const position = queue[head++], y = Math.floor(position / width), x = position % width;
        if (emit) emit(position);
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const xx = x + dx, yy = y + dy, next = yy * width + xx;
          if (xx >= 0 && xx < width && yy >= 0 && yy < height && mask[next] && !visited[next]) {
            visited[next] = 1; queue[tail++] = next;
          }
        }
      }
      return tail;
    };
    for (let i = 0; i < mask.length; i++) if (mask[i] && !visited[i]) {
      const count = flood(i);
      if (count > bestSize) { bestSize = count; bestStart = i; }
    }
    const result = new Uint8Array(mask.length);
    if (bestStart >= 0) { visited.fill(0); flood(bestStart, position => { result[position] = 1; }); }
    return result;
  }
  const api = { validateUltrasound, roundEven, geometry, resizePillow, resizeLinearRGB, classificationInput,
    segmentationInput, restoreProbability, largestComponent };
  scope.OfflinePreprocess = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof self !== "undefined" ? self : globalThis);
