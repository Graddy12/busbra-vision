// Role/navigation regression check. Isolated JSON data; no model inference needed.
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright");

async function main() {
  const root = path.resolve(__dirname, "..");
  const data = fs.mkdtempSync(path.join(os.tmpdir(), "busbra-admin-"));
  const output = path.join(root, "output", "qa");
  fs.mkdirSync(output, { recursive: true });
  const source = "from cancer_app import create_app; from types import SimpleNamespace; app=create_app(predictor=SimpleNamespace(status=lambda: {'loaded': True})); app.extensions['store'].manage_recovery('create', 'BrowserRecovery!2026-Long'); app.run(host='127.0.0.1', port=5082, use_reloader=False)";
  const server = spawn(path.join(root, ".venv", "Scripts", "python.exe"), ["-u", "-c", source], {
    cwd: root, windowsHide: true,
    env: { ...process.env, BUSBRA_DATA_DIR: data, BUSBRA_ADMIN_PASSWORD: "AdminCheck!2026" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let logs = "";
  server.stdout.on("data", (chunk) => { logs += chunk; });
  server.stderr.on("data", (chunk) => { logs += chunk; });
  let browser;
  try {
    let ready = false;
    for (let index = 0; index < 60; index++) {
      if (server.exitCode !== null) throw new Error(logs);
      try {
        if ((await fetch("http://127.0.0.1:5082/api/health", { signal: AbortSignal.timeout(1000) })).ok) { ready = true; break; }
      } catch {}
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    assert.ok(ready, logs);
    browser = await chromium.launch({ executablePath: path.join(process.env["ProgramFiles(x86)"], "Microsoft", "Edge", "Application", "msedge.exe"), headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("http://127.0.0.1:5082/login?next=/workspace");
    await page.locator('[name="username"]').fill("admin");
    await page.locator('[name="password"]').fill("AdminCheck!2026");
    await page.getByRole("button", { name: "Se connecter" }).click();
    await page.waitForURL("http://127.0.0.1:5082/admin");
    assert.equal(await page.locator("[data-admin-shell]").count(), 1);
    assert.equal(await page.getByRole("navigation", { name: "Navigation principale", exact: true }).count(), 0);
    await page.screenshot({ path: path.join(output, "admin-separated-desktop.png"), fullPage: true });

    await page.getByRole("link", { name: "Journal des actions", exact: false }).click();
    await page.waitForURL("http://127.0.0.1:5082/admin/audit");
    assert.equal(await page.locator("[data-admin-shell]").count(), 1);
    await page.getByRole("link", { name: "Utilisateurs", exact: false }).click();

    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(output, "admin-separated-mobile.png"), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });

    const form = page.locator("form.stack-form");
    await form.locator('[name="name"]').fill("Dr Test administration");
    await form.locator('[name="username"]').fill("medecin_test");
    await form.locator('[name="password"]').fill("DoctorCheck!2026");
    await form.getByRole("button", { name: "Créer l'utilisateur" }).click();
    await page.getByRole("cell", { name: "Dr Test administration", exact: false }).waitFor();
    await page.locator('form[action="/logout"] button').click();
    await page.locator('[name="username"]').fill("medecin_test");
    await page.locator('[name="password"]').fill("DoctorCheck!2026");
    await page.getByRole("button", { name: "Se connecter" }).click();
    await page.waitForURL("http://127.0.0.1:5082/");
    assert.equal(await page.locator("[data-admin-shell]").count(), 0);
    assert.equal(await page.getByRole("navigation", { name: "Navigation principale", exact: true }).count(), 1);
    // UI-only regression: positive -> normal -> positive, including stale views.
    // The actual inference/API policy is independently checked on raw BUSI images.
    const imagePath = path.join(root, "cancer_seins", "normal", "normal (92).png");
    const png = `data:image/png;base64,${fs.readFileSync(imagePath).toString("base64")}`;
    const classes = ["malignant", "normal", "benign"];
    let requestIndex = 0;
    await page.route("**/api/analyze", async (route) => {
      const code = classes[requestIndex++];
      const segmented = code !== "normal";
      const label = { normal: "Normale", benign: "Bénigne", malignant: "Maligne" }[code];
      await route.fulfill({ json: {
        classification: { code, label, confidence_percent: 95, probabilities: [{ code, label, percent: 95 }] },
        segmentation: { performed: segmented, lesion_area_percent: segmented ? 10 : null, mask_data_url: segmented ? png : null, overlay_data_url: segmented ? png : null },
        gradcam: { available: true, overlay_data_url: png },
        meta: { device: "cpu", inference_ms: 1 },
        analysis: { original_url: png, history_url: "/history/test", report_url: "/history/test/report" },
      } });
    });
    for (const code of classes) {
      await page.locator("#image-input").setInputFiles(imagePath);
      const response = page.waitForResponse((response) => response.url().endsWith("/api/analyze"));
      await page.locator("#analyze-button").click();
      await response;
      await page.locator("#results").waitFor({ state: "visible" });
      assert.equal(await page.locator("#error-state").isVisible(), false);
      assert.equal(await page.locator('[data-view="mask"]').isVisible(), code !== "normal");
      assert.equal(await page.locator('[data-view="overlay"]').isVisible(), code !== "normal");
      assert.equal(await page.locator("#lesion-area-wrap").isVisible(), code !== "normal");
      if (code === "normal") {
        assert.equal(await page.locator('[data-view="original"]').getAttribute("class"), "view-toggle active");
        assert.equal(await page.locator("#download-result").getAttribute("download"), "echographie-originale.png");
        await page.evaluate(() => setResultView("mask"));
        assert.equal(await page.locator("#overlay-image").getAttribute("alt"), "Échographie originale sans masque");
        await page.screenshot({ path: path.join(output, "normal-no-mask.png"), fullPage: true });
      } else {
        await page.locator('[data-view="mask"]').click();
        assert.equal(await page.locator("#download-result").getAttribute("download"), "masque-segmentation.png");
      }
      await page.locator("#remove-image").click();
    }
    await page.locator('form[action="/logout"] button').click();
    await page.locator('[name="username"]').fill("secours");
    await page.locator('[name="password"]').fill("BrowserRecovery!2026-Long");
    await page.getByRole("button", { name: "Se connecter" }).click();
    await page.waitForURL("http://127.0.0.1:5082/admin");
    assert.equal(await page.locator("tbody tr").count(), 2);
    assert.equal(await page.getByRole("cell", { name: "Administrateur de secours", exact: false }).count(), 0);
    assert.equal(await page.getByRole("status").filter({ hasText: "Mode secours" }).count(), 1);
    const recoveryForm = page.locator("form.stack-form");
    await recoveryForm.locator('[name="name"]').fill("Dr Créé par secours");
    await recoveryForm.locator('[name="username"]').fill("medecin_secours");
    await recoveryForm.locator('[name="password"]').fill("DoctorCreated!2026");
    await recoveryForm.getByRole("button", { name: "Créer l'utilisateur" }).click();
    await page.getByRole("cell", { name: "Dr Créé par secours", exact: false }).waitFor();
    assert.equal(await page.locator("tbody tr").count(), 3);
    await page.screenshot({ path: path.join(output, "admin-recovery.png"), fullPage: true });
    await page.getByRole("link", { name: "Journal des actions", exact: false }).click();
    assert.ok((await page.locator("tbody").innerText()).includes("Connexion de secours"));
    assert.ok((await page.locator("tbody").innerText()).includes("medecin_secours"));
    assert.deepEqual(errors, []);
    console.log("PASS: admin lands on /admin; dedicated desktop/mobile navigation; separate audit page; doctor lands in medical workspace");
    console.log("PASS: malignant -> normal -> benign; no mask controls, no area, no stale overlay for normal");
    console.log("PASS: emergency admin authenticates, stays out of the directory, creates accounts and leaves an audit trail");
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
