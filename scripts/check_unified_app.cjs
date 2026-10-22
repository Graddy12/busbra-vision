// Isolated Flask API test double + real ONNX/WASM offline inference in Edge.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const { spawn } = require("node:child_process");
const { chromium } = require("playwright");
const root = path.resolve(__dirname, "..");
const artifacts = path.join(root, "output", "unified_app");

async function main() {
  const probe = net.createServer();
  await new Promise(resolve => probe.listen(0, "127.0.0.1", resolve));
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  const base = `http://127.0.0.1:${port}`;
  const server = spawn(path.join(root, ".venv/Scripts/python.exe"),
    ["-u", "tests/unified_server_fixture.py", "--port", String(port)],
    { cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let log = "", browser;
  server.stdout.on("data", data => { log += data; });
  server.stderr.on("data", data => { log += data; });
  try {
    let healthy = false;
    for (let attempt = 0; attempt < 40; attempt++) {
      try { healthy = (await (await fetch(base + "/api/health")).json()).models.backend === "browser-test-double"; } catch {}
      if (healthy) break;
      await new Promise(resolve => setTimeout(resolve, 300));
    }
    assert(healthy, log);
    fs.mkdirSync(artifacts, { recursive: true });
    browser = await chromium.launch({ executablePath: path.join(process.env["ProgramFiles(x86)"], "Microsoft/Edge/Application/msedge.exe"), headless: true });
    const context = await browser.newContext({ viewport: { width: 1365, height: 950 }, serviceWorkers: "allow" });
    const page = await context.newPage();
    const pageErrors = [];
    let analysisPosts = 0;
    page.on("pageerror", error => pageErrors.push(error.message));
    context.on("request", request => {
      if (request.method() === "POST" && request.url().endsWith("/api/analyze")) analysisPosts++;
    });
    await page.goto(base + "/login");
    await page.locator('[name="username"]').fill("admin");
    await page.locator('[name="password"]').fill("BrowserTestOnly!2026");
    await page.getByRole("button", { name: "Se connecter" }).click();
    await page.waitForURL(base + "/admin");
    assert(await page.locator('a[href="/workspace/"]').isVisible());
    // Recreate the previous deployment's cached model locations, then enter
    // through the old installed worker. No old auth page or patient data cached.
    await page.evaluate(async () => {
      const manifest = await (await fetch("/workspace/assets-manifest.json")).json();
      const previous = await caches.open("busbra-offline-poc-v11");
      for (const item of Object.values(manifest.models)) {
        const suffix = item.url.replace("./", "");
        const response = await fetch("/workspace/" + suffix);
        if (!response.ok) throw new Error("Fixture model missing");
        await previous.put("/offline/" + suffix, response);
      }
      await previous.put("/offline/__offline_ready__", new Response(JSON.stringify({
        hashes: Object.values(manifest.models).map(item => item.sha256).join(":"),
      })));
      const registration = await navigator.serviceWorker.register("/offline/sw.js", { scope: "/offline/" });
      const worker = registration.installing || registration.waiting || registration.active;
      if (worker.state !== "activated") await new Promise(resolve => {
        worker.addEventListener("statechange", () => { if (worker.state === "activated") resolve(); });
      });
    });
    await page.goto(base + "/offline/");
    await page.waitForURL(base + "/workspace/");
    await page.waitForFunction(() => !document.querySelector("#image").disabled);
    assert(await page.locator("#error").isHidden(), await page.locator("#error").textContent());
    assert.match(await page.locator("#status").textContent(), /^Prêt/);
    await page.locator("#engine-mode").selectOption("server");
    await page.locator("#image").setInputFiles(path.join(root, "cancer_seins/benign/benign (92).png"));
    await page.locator("#analyze").click();
    await page.locator("#results").waitFor({ state: "visible" });
    assert(await page.locator("#error").isHidden(), await page.locator("#error").textContent());
    assert.equal(analysisPosts, 1);
    assert.equal(await page.locator("#history-count").textContent(), "0");
    assert(await page.locator("#server-result-links").isVisible());
    assert(await page.locator("#feedback-form").isHidden());
    assert(await page.locator('[data-view="gradcam"]').isVisible());
    await page.locator('[data-view="gradcam"]').click();
    assert(await page.locator("#gradcam").isVisible());
    const reportUrl = await page.locator("#server-report").getAttribute("href");
    assert((await context.request.get(base + reportUrl)).ok());
    await page.screenshot({ path: path.join(artifacts, "unified-server.png"), fullPage: true });
    console.log("PASS: same-origin login, admin, server analysis UI, Grad-CAM and protected report; no local copy");

    const badImage = await page.evaluate(() => {
      const canvas = document.createElement("canvas"); canvas.width = canvas.height = 128;
      const ctx = canvas.getContext("2d"); ctx.fillStyle = "red"; ctx.fillRect(0, 0, 128, 128);
      return canvas.toDataURL("image/png").split(",")[1];
    });
    await page.locator("#image").setInputFiles({ name: "echographie.png", mimeType: "image/png", buffer: Buffer.from(badImage, "base64") });
    await page.locator("#error").waitFor({ state: "visible" });
    assert.match(await page.locator("#error").textContent(), /Image refusée/);
    assert(await page.locator("#results").isHidden());
    assert.equal(analysisPosts, 1);
    // The migrated cache must be usable without clicking Prepare again.
    const cached = await page.evaluate(async () => {
      const result = [];
      for (const name of await caches.keys()) for (const request of await (await caches.open(name)).keys()) result.push(new URL(request.url).pathname);
      return result;
    });
    assert(cached.includes("/workspace/models/classification.onnx"));
    assert(cached.includes("/workspace/models/segmentation.onnx"));
    assert(!cached.some(value => !value.startsWith("/workspace/") || /api|login|admin|history|instance/.test(value)));
    const workerScope = await page.evaluate(async () => (await navigator.serviceWorker.ready).scope);
    assert.equal(workerScope, base + "/workspace/");
    server.kill();
    await new Promise(resolve => {
      if (server.exitCode !== null) resolve(); else { server.once("exit", resolve); setTimeout(resolve, 3000); }
    });
    let serverStopped = false;
    try { await fetch(base, { signal: AbortSignal.timeout(1500) }); } catch { serverStopped = true; }
    assert(serverStopped);
    await context.setOffline(true);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => document.querySelector("#status").textContent.startsWith("Prêt") || !document.querySelector("#error").hidden, null, { timeout: 120000 });
    assert(await page.locator("#error").isHidden(), await page.locator("#error").textContent());
    assert.equal(await page.evaluate(() => navigator.onLine), false);
    assert(await page.locator("#server-user").isHidden());
    assert(await page.locator("#server-history").isHidden());
    await page.locator("#image").setInputFiles(path.join(root, "cancer_seins/normal/normal (92).png"));
    await page.locator("#analyze").click();
    await page.locator("#results").waitFor({ state: "visible", timeout: 120000 });
    await page.waitForFunction(() => !document.querySelector("#image").disabled);
    assert.equal(await page.locator("#prediction").textContent(), "Normale");
    assert(await page.locator('[data-view="mask"]').isDisabled());
    assert(await page.locator('[data-view="gradcam"]').isHidden());
    assert.equal(await page.locator("#history-count").textContent(), "1");
    await page.locator("#engine-mode").selectOption("local");
    await page.locator("#image").setInputFiles(path.join(root, "cancer_seins/benign/benign (92).png"));
    await page.locator("#analyze").click();
    await page.locator("#results").waitFor({ state: "visible", timeout: 120000 });
    await page.waitForFunction(() => !document.querySelector("#image").disabled);
    assert.equal(await page.locator("#prediction").textContent(), "Bénigne");
    assert(await page.locator('[data-view="mask"]').isEnabled());
    assert(await page.locator("#server-result-links").isHidden());
    assert(await page.locator("#feedback-form").isVisible());
    assert.equal(await page.locator("#history-count").textContent(), "2");
    assert.equal(analysisPosts, 1);
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(artifacts, "unified-mobile-offline.png"), fullPage: true });
    assert.deepEqual(pageErrors, []);
    fs.writeFileSync(path.join(artifacts, "validation.json"), JSON.stringify({
      status: "PASS", serverBackend: "test double (UI/API/storage integration only)",
      offlineBackend: "real ONNX FP32 / WASM", serverStopped, browserOffline: true,
      legacyUrlRedirected: true, modelCacheMigratedWithoutPreparation: true,
      analysisPosts, cachedAuthOrHistory: false, normalMaskSkipped: true,
      benignSegmented: true, mobileViewport: true, physicalPhoneTested: false, pageErrors,
    }, null, 2));
    console.log("PASS: cached /workspace/ shell, server stopped, auto local mode, real normal/benign inference, no extra POST, mobile layout");
  } finally {
    if (browser) await browser.close();
    if (server.exitCode === null) server.kill();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
