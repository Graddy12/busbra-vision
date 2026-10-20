// Regression check for the actual startup, upload, drag/drop and both models.
// Requires Playwright and Microsoft Edge; the server is stopped after the check.
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const testPort = process.env.BROWSER_TEST_PORT || "5081";
const baseUrl = `http://127.0.0.1:${testPort}`;
const useExistingServer = process.env.BROWSER_TEST_USE_EXISTING === "1";
const adminPassword = process.env.BROWSER_TEST_ADMIN_PASSWORD || "BrowserAdmin!2026";

async function main() {
  const testData = useExistingServer ? null : fs.mkdtempSync(path.join(os.tmpdir(), "busbra-portal-"));
  const artifacts = path.join(root, "output", "qa");
  fs.mkdirSync(artifacts, { recursive: true });
  fs.mkdirSync(path.join(root, "output", "pdf"), { recursive: true });
  const server = useExistingServer ? null : spawn(path.join(root, ".venv", "Scripts", "python.exe"), ["-u", "app.py"], {
    cwd: root,
    env: { ...process.env, KERAS_BACKEND: "torch", INFERENCE_DEVICE: "cpu", FLASK_HOST: "127.0.0.1", FLASK_PORT: testPort, BUSBRA_DATA_DIR: testData, BUSBRA_ADMIN_PASSWORD: adminPassword },
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let serverOutput = "";
  let spawnError;
  server?.on("error", (error) => { spawnError = error; });
  server?.stdout.on("data", (chunk) => { serverOutput += chunk; });
  server?.stderr.on("data", (chunk) => { serverOutput += chunk; });

  let browser;
  try {
    const deadline = Date.now() + 120_000;
    let health;
    while (Date.now() < deadline) {
      if (spawnError) throw spawnError;
      if (server && server.exitCode !== null) throw new Error(serverOutput);
      try {
        const response = await fetch(`${baseUrl}/api/health`, { signal: AbortSignal.timeout(2000) });
        if (response.ok) {
          health = await response.json();
          break;
        }
      } catch {}
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    assert.equal(health?.models?.loaded, true, `Server not ready:\n${serverOutput}`);
    console.log("PASS: the actual app.py starts with both models loaded");

    browser = await chromium.launch({
      executablePath: path.join(process.env["ProgramFiles(x86)"], "Microsoft", "Edge", "Application", "msedge.exe"),
      headless: true,
    });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.route("https://fonts.googleapis.com/**", (route) => route.abort());
    await page.goto(baseUrl);
    assert.ok(page.url().includes("/login"));
    await page.screenshot({ path: path.join(artifacts, "login.png"), fullPage: true });
    async function login(username, password) {
      await page.locator('[name="username"]').fill(username);
      await page.locator('[name="password"]').fill(password);
      await page.getByRole("button", { name: "Se connecter" }).click();
      await page.waitForURL(`${baseUrl}${username === "admin" ? "/admin" : "/"}`);
    }
    await login("admin", adminPassword);
    await page.goto(`${baseUrl}/admin`);
    const createForm = page.locator("form.stack-form");
    await createForm.locator('[name="name"]').fill("Dr Démonstration");
    await createForm.locator('[name="username"]').fill("demo_medecin");
    await createForm.locator('[name="specialty"]').fill("Oncologie");
    await createForm.locator('[name="password"]').fill("DoctorDemo!2026");
    await createForm.getByRole("button", { name: "Créer l'utilisateur" }).click();
    await page.getByRole("cell", { name: "Dr Démonstration", exact: false }).waitFor();
    await page.screenshot({ path: path.join(artifacts, "admin.png"), fullPage: true });
    await page.locator('form[action="/logout"] button').click();
    await login("demo_medecin", "DoctorDemo!2026");
    assert.equal((await page.goto(`${baseUrl}/admin`)).status(), 403);
    await page.goto(baseUrl);
    console.log("PASS: admin creates a doctor; doctor cannot access administration");

    async function analyze(expected) {
      const responsePromise = page.waitForResponse(
        (response) => response.url().endsWith("/api/analyze") && response.request().method() === "POST",
        { timeout: 60_000 },
      );
      await page.locator("#analyze-button").click();
      const response = await responsePromise;
      const payload = await response.json();
      assert.equal(response.status(), 200, JSON.stringify(payload));
      assert.equal(payload.classification.code, expected);
      if (expected === "normal") {
        assert.equal(payload.segmentation.performed, false);
        assert.equal(payload.segmentation.mask_data_url, null);
        assert.equal(payload.segmentation.overlay_data_url, null);
      } else {
        assert.equal(payload.segmentation.performed, true);
        assert.ok(payload.segmentation.mask_data_url.startsWith("data:image/png;base64,"));
      }
      assert.ok(payload.gradcam.overlay_data_url.startsWith("data:image/png;base64,"));
      await page.locator("#results").waitFor({ state: "visible" });
      assert.equal(await page.locator("#predicted-label").innerText(), payload.classification.label);
      if (expected === "normal") {
        assert.equal(await page.locator('[data-view="mask"]').isVisible(), false);
        assert.equal(await page.locator('[data-view="overlay"]').isVisible(), false);
        assert.equal(await page.locator("#lesion-area-wrap").isVisible(), false);
        assert.equal(await page.locator("#overlay-image").getAttribute("src"), payload.analysis.original_url);
        assert.equal(await page.locator("#download-result").getAttribute("download"), "echographie-originale.png");
      } else {
        await page.locator('[data-view="mask"]').click();
        assert.equal(await page.locator("#overlay-image").getAttribute("src"), payload.segmentation.mask_data_url);
        assert.equal(await page.locator("#download-result").getAttribute("download"), "masque-segmentation.png");
      }
      await page.locator('[data-view="gradcam"]').click();
      assert.equal(await page.locator("#overlay-image").getAttribute("src"), payload.gradcam.overlay_data_url);
      console.log(`PASS: ${expected}, confidence ${payload.classification.confidence_percent}%, segmentation=${payload.segmentation.performed}`);
      return payload;
    }

    await page.locator("#image-input").setInputFiles(path.join(root, "cancer_seins", "malignant", "malignant (95).png"));
    await page.locator("#exam-reference").fill("DEMO-MALIGNANT-001");
    const first = await analyze("malignant");
    await page.screenshot({ path: path.join(artifacts, "analysis.png"), fullPage: true });

    await page.goto(`${baseUrl}${first.analysis.history_url}`);
    await page.locator('[name="note"]').fill("Examen de démonstration. Images, segmentation et Grad-CAM examinés. Observation humaine distincte du résultat automatique.");
    await page.locator('[name="status"]').selectOption("validated");
    await page.getByRole("button", { name: "Enregistrer la revue" }).click();
    await page.getByText("Revue enregistrée.", { exact: true }).waitFor();
    await page.goto(`${baseUrl}${first.analysis.report_url}`);
    await page.evaluate(() => Promise.all([...document.images].map((image) => image.decode())));
    if (process.env.BROWSER_SKIP_PDF !== "1") {
      await page.pdf({ path: path.join(root, "output", "pdf", "rapport-demonstration.pdf"), format: "A4", printBackground: true, preferCSSPageSize: true });
    }
    console.log("PASS: human review and printable PDF with real images and final interpretation");

    await page.goto(`${baseUrl}${first.analysis.history_url}`);
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "Supprimer l'analyse" }).click();
    await page.waitForURL(/\/history\?/);
    assert.equal((await page.request.get(`${baseUrl}/history/${first.analysis.id}/assets/original`)).status(), 404);
    await page.goto(`${baseUrl}/history?trash=1`);
    await page.getByRole("button", { name: "Restaurer" }).click();
    await page.goto(`${baseUrl}/history`);
    await page.getByRole("link", { name: "Consulter", exact: true }).waitFor();
    await page.screenshot({ path: path.join(artifacts, "history.png"), fullPage: true });
    console.log("PASS: persisted history, soft delete, protected assets and restore");
    await page.goto(baseUrl);

    const normalPath = path.join(root, "cancer_seins", "normal", "normal (92).png");
    await page.evaluate(({ bytes, name }) => {
      const transfer = new DataTransfer();
      transfer.items.add(new File([new Uint8Array(bytes)], name, { type: "image/png" }));
      document.querySelector("#drop-zone").dispatchEvent(
        new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: transfer }),
      );
    }, { bytes: [...fs.readFileSync(normalPath)], name: path.basename(normalPath) });
    await analyze("normal");
    console.log("PASS: drag and drop submits without native form-validation blocking");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${baseUrl}/history`);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(artifacts, "mobile-history.png"), fullPage: true });
    assert.deepEqual(pageErrors, []);
  } finally {
    if (browser) await browser.close();
    server?.kill();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
