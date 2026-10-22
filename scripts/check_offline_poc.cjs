// Real-browser proof: cache code + models, kill the server, disconnect, reload,
// then select previously unseen local files and execute both models in WASM.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { chromium } = require("playwright");
const root = path.resolve(__dirname, "..");
const artifacts = path.join(root, "output", "offline_poc");
const baseUrl = "http://127.0.0.1:5092";
const reference = JSON.parse(fs.readFileSync(path.join(artifacts, "python_reference.json")));

async function main() {
  const server = spawn(path.join(root, ".venv", "Scripts", "python.exe"), ["-u", path.join(root, "offline_poc", "server.py"), "--port", "5092"], {
    cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  server.stdout.on("data", data => { output += data; }); server.stderr.on("data", data => { output += data; });
  let browser;
  try {
    let serverReady = false;
    for (let i = 0; i < 30; i++) {
      try { serverReady = (await fetch(baseUrl)).ok; } catch {}
      if (serverReady) break;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    assert(serverReady, output);
    browser = await chromium.launch({ executablePath: path.join(process.env["ProgramFiles(x86)"], "Microsoft", "Edge", "Application", "msedge.exe"), headless: true });
    const context = await browser.newContext({ viewport: { width: 1365, height: 950 }, serviceWorkers: "allow" });
    const page = await context.newPage();
    const errors = [], forbiddenRequests = [];
    page.on("pageerror", error => errors.push(error.message));
    context.on("request", request => {
      if (request.method() !== "GET" || request.url().includes("/api/") || !request.url().startsWith(baseUrl)) forbiddenRequests.push(request.url());
    });
    await page.goto(baseUrl);
    await page.getByRole("button", { name: "Préparer le mode hors ligne" }).waitFor({ state: "visible" });
    await page.waitForFunction(() => !document.querySelector("#prepare").disabled);
    await page.getByRole("button", { name: "Préparer le mode hors ligne" }).click();
    await page.waitForFunction(() => window.offlineDemo?.ready || !document.querySelector("#error").hidden, null, { timeout: 120000 });
    assert(await page.evaluate(() => window.offlineDemo.ready), await page.locator("#error").textContent());
    console.log("PASS: static files, WASM and both ONNX models cached; classifier started");
    const cached = await page.evaluate(async () => (await (await caches.open("busbra-offline-poc-v1")).keys()).map(request => new URL(request.url).pathname));
    assert(cached.includes("/models/classification.onnx") && cached.includes("/models/segmentation.onnx"));
    assert(!cached.some(url => /cancer_seins|sample_|reference|history|instance/.test(url)));
    // Remove the inference server, not merely its network connection.
    server.kill();
    await new Promise(resolve => { if (server.exitCode !== null) resolve(); else { server.once("exit", resolve); setTimeout(resolve, 3000); } });
    let serverStopped = false;
    try { await fetch(baseUrl, { signal: AbortSignal.timeout(2000) }); } catch { serverStopped = true; }
    assert(serverStopped, "The server is still reachable");
    await context.setOffline(true);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.offlineDemo?.ready || !document.querySelector("#error").hidden, null, { timeout: 120000 });
    assert(await page.evaluate(() => window.offlineDemo.ready), await page.locator("#error").textContent());
    assert.equal(await page.evaluate(() => navigator.onLine), false);
    console.log("PASS: page + inference worker restarted with server stopped and browser offline");
    const rows = [];
    for (const sample of reference.samples) {
      await page.locator("#image").setInputFiles(path.join(root, sample.source));
      await page.waitForFunction(() => window.offlineDemo.selected || !document.querySelector("#error").hidden);
      assert(await page.evaluate(() => window.offlineDemo.selected), await page.locator("#error").textContent());
      await page.getByRole("button", { name: "Analyser sur cet appareil" }).click();
      await page.waitForFunction(() => window.offlineDemo.lastResult || !document.querySelector("#error").hidden, null, { timeout: 120000 });
      const summary = await page.evaluate(() => {
        const result = window.offlineDemo.lastResult;
        if (!result) return { error: document.querySelector("#error").textContent };
        return { code: result.code, probabilities: result.probabilities, inferenceMs: result.inferenceMs,
          segmentationPerformed: result.segmentationPerformed, lesionAreaPercent: result.lesionAreaPercent,
          statistics: result.statistics, maskIsNull: result.mask === null };
      });
      assert(!summary.error, summary.error);
      assert.equal(summary.code, sample.predicted, sample.source);
      const classError = Math.max(...summary.probabilities.map((value, i) => Math.abs(value - sample.probabilities[i])));
      assert(classError < 1e-4, `${sample.source}: probability drift ${classError}`);
      let maskAgreement = null;
      if (summary.code === "normal") {
        assert.equal(summary.segmentationPerformed, false);
        assert.equal(summary.maskIsNull, true);
        assert.equal(summary.lesionAreaPercent, null);
        assert.equal(summary.statistics.segmentationRuns, rows.at(-1)?.statistics.segmentationRuns || 0);
        assert(await page.locator("#mask-figure").isHidden());
      } else {
        const expected = fs.readFileSync(path.join(artifacts, sample.id + "_mask.bin"));
        maskAgreement = await page.evaluate(expected => {
          const actual = window.offlineDemo.lastResult.mask;
          let intersection = 0, total = 0, equal = 0;
          for (let i = 0; i < actual.length; i++) {
            intersection += actual[i] && expected[i] ? 1 : 0; total += actual[i] + expected[i];
            equal += actual[i] === expected[i] ? 1 : 0;
          }
          return { diceBetweenImplementations: total ? 2 * intersection / total : 1, equalPixels: equal / actual.length };
        }, [...expected]);
        assert(maskAgreement.diceBetweenImplementations > .999, `${sample.source}: mask conversion drift`);
      }
      const row = { source: sample.source, ...summary, classMaxError: classError, maskAgreement };
      rows.push(row);
      console.log(`PASS: OFFLINE ${sample.source} -> ${summary.code}, ${summary.inferenceMs} ms, mask ${maskAgreement?.diceBetweenImplementations ?? "not applied"}`);
      if (sample.id === "sample_00") await page.screenshot({ path: path.join(artifacts, "offline-benign-desktop.png"), fullPage: true });
      if (sample.id === "sample_05") {
        await page.setViewportSize({ width: 390, height: 844 });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await page.screenshot({ path: path.join(artifacts, "offline-malignant-mobile.png"), fullPage: true });
        await page.setViewportSize({ width: 1365, height: 950 });
      }
      if (sample.id === "sample_10") await page.screenshot({ path: path.join(artifacts, "offline-normal-no-mask.png"), fullPage: true });
    }
    // Invalid uploads must not leave an old medical-looking result on screen.
    await page.locator("#image").setInputFiles({ name: "not-an-image.txt", mimeType: "text/plain", buffer: Buffer.from("not an image") });
    await page.getByRole("alert").waitFor({ state: "visible" });
    assert(await page.locator("#results").isHidden());
    assert.equal(await page.evaluate(() => window.offlineDemo.lastResult), null);
    assert.deepEqual(forbiddenRequests, []);
    assert.deepEqual(errors, []);
    const report = { generatedAt: new Date().toISOString(), status: "PASS", serverStopped, browserOffline: true,
      sampleImagesCached: false, modelExecution: "ONNX Runtime Web / WebAssembly CPU / FP32 / single thread",
      physicalPhoneTested: false, viewportMobileTested: true, samples: rows,
      note: "Numerical parity against the original Python models on public demo samples; NOT a clinical evaluation. Dice here compares two implementations, not a predicted mask to ground truth." };
    fs.writeFileSync(path.join(artifacts, "browser_offline_validation.json"), JSON.stringify(report, null, 2));
    console.log("PASS: no upload/API/external requests, normal gate enforced, invalid files rejected, mobile layout checked");
  } finally { if (browser) await browser.close(); server.kill(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
