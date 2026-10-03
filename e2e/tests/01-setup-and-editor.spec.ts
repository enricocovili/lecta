import { expect, test } from "@playwright/test";
import { login, openCourse, PASSWORD, USER, watchConsole } from "./helpers";

// 404s for probing private URLs anonymously are expected in the console.
const EXPECTED = [/status of 404/];

test.describe.serial("workspace", () => {
  test("setup wizard creates the admin account", async ({ page }) => {
    const con = watchConsole(page);
    await page.goto("/");
    await page.waitForURL("**/setup");
    await page.getByLabel("Codice di configurazione").fill(process.env.SETUP_CODE!);
    await page.getByRole("button", { name: "Continua" }).click();
    await page.getByLabel("Nome utente amministratore").fill(USER);
    await page.getByLabel(/^Password/).fill(PASSWORD);
    await page.getByLabel("Ripeti la password").fill(PASSWORD);
    await page.getByRole("button", { name: "Crea account amministratore" }).click();
    await page.waitForURL("**/admin");
    await expect(page.getByRole("heading", { name: "Home" })).toBeVisible();
    con.assertClean(EXPECTED);
  });

  test("anonymous visitors get 404 on private pages", async ({ browser }) => {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    for (const url of ["/admin", "/admin/courses", "/admin/courses/1", "/admin/settings", "/admin/editor/1"]) {
      const r = await page.goto(url);
      expect(r?.status(), url).toBe(404);
    }
    await ctx.close();
  });

  test("create a course: the workspace opens with the outline and the draft", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await page.goto("/admin/courses?new=1");
    await page.getByLabel("Nome").fill("Algebra Lineare");
    await page.getByLabel(/^Capitoli/).fill("Spazi vettoriali\nMatrici");
    await page.getByRole("button", { name: "Crea materia" }).click();
    await page.waitForURL(/\/admin\/courses\/\d+(\/testo)?$/);
    await expect(page.getByRole("heading", { name: "Algebra Lineare" })).toBeVisible();

    // The outline on the left and the draft in the middle both know the chapters.
    const outline = page.getByRole("navigation", { name: "Struttura del documento" });
    await expect(outline.getByText("Spazi vettoriali")).toBeVisible();
    await expect(outline.getByText("Matrici")).toBeVisible();
    const doc = page.getByTestId("doc-preview");
    await expect(doc.getByRole("heading", { name: "Spazi vettoriali" })).toBeVisible();
    // A course with nothing written yet invites you to start.
    await expect(page.getByText("Questa materia è ancora vuota")).toBeVisible();
    await expect(page.locator(".doc-empty").getByRole("link", { name: "Nuova lezione" })).toBeVisible();
    // The assistant panel is there, ready.
    await expect(page.getByTestId("ai-panel")).toBeVisible();
    await expect(page.getByTestId("ai-composer")).toBeVisible();
    con.assertClean(EXPECTED);
  });

  test("structure actions need no AI: add, rename, delete a chapter", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await openCourse(page, /Algebra Lineare/);
    const outline = page.getByRole("navigation", { name: "Struttura del documento" });

    await outline.getByRole("button", { name: "Aggiungi capitolo" }).click();
    await outline.getByLabel("Titolo del nuovo capitolo").fill("Determinanti");
    await outline.getByRole("button", { name: "Aggiungi", exact: true }).click();
    await expect(outline.getByText("Determinanti")).toBeVisible();
    await expect(page.getByTestId("doc-preview").getByRole("heading", { name: "Determinanti" })).toBeVisible();

    await outline.getByLabel("Azioni sul capitolo Determinanti").click();
    await page.getByRole("button", { name: "Rinomina" }).click();
    await outline.getByLabel("Titolo del capitolo").fill("Determinanti e rango");
    await page.keyboard.press("Enter");
    await expect(outline.getByText("Determinanti e rango")).toBeVisible();

    await outline.getByLabel("Azioni sul capitolo Determinanti e rango").click();
    await page.getByRole("button", { name: "Elimina", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Elimina", exact: true }).click();
    await expect(outline.getByText("Determinanti e rango")).toHaveCount(0);
    con.assertClean(EXPECTED);
  });

  test("the downloads are there: LaTeX source and PDF on request", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await openCourse(page, /Algebra Lineare/);
    await page.getByRole("button", { name: "Scarica", exact: true }).click();
    const src = page.getByTestId("download-source");
    await expect(src).toHaveAttribute("href", /\/api\/courses\/\d+\/source\.zip$/);
    const zip = await page.request.get((await src.getAttribute("href"))!);
    expect(zip.status()).toBe(200);
    expect(zip.headers()["content-type"]).toMatch(/zip/);
    // The PDF is compiled when asked for (this opens it in a new tab).
    const compiled = page.waitForResponse((r) => r.url().endsWith("/compile") && r.request().method() === "POST", { timeout: 120_000 });
    await page.getByTestId("download-pdf").click();
    const res = await compiled;
    expect((await res.json()).pdf_url).toMatch(/\/api\/courses\/\d+\/pdf/);
    con.assertClean(EXPECTED);
  });

  test("the PDF tab compiles on demand", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await openCourse(page, /Algebra Lineare/);
    await page.getByRole("tab", { name: "PDF" }).click();
    await expect(page.locator(".pdf-page canvas").first()).toBeVisible({ timeout: 120_000 });
    await page.getByRole("tab", { name: "Bozza" }).click();
    await expect(page.getByTestId("doc-preview")).toBeVisible();
    con.assertClean(EXPECTED);
  });

  test("publish (ON) and see the course on the public site, with the LaTeX source", async ({ page, browser }) => {
    const con = watchConsole(page);
    await login(page);
    await openCourse(page, /Algebra Lineare/);
    await page.locator(".pub-ctl .switch").click();
    await expect(page.getByTestId("publish-state")).toContainText("Online", { timeout: 150_000 });

    const anon = await browser.newContext();
    const pub = await anon.newPage();
    await pub.goto("/");
    await expect(pub.getByRole("link", { name: "Algebra Lineare" })).toBeVisible();
    await pub.getByRole("link", { name: "Algebra Lineare" }).click();
    await expect(pub.locator(".pub-toc").getByText("Spazi vettoriali")).toBeVisible();
    const pdf = await pub.request.get((await pub.getByRole("link", { name: "Scarica PDF" }).first().getAttribute("href")) as string);
    expect(pdf.status()).toBe(200);
    expect(pdf.headers()["content-type"]).toBe("application/pdf");
    const srcLink = pub.getByRole("link", { name: "Scarica sorgente LaTeX" }).first();
    await expect(srcLink).toBeVisible();
    const zip = await pub.request.get((await srcLink.getAttribute("href")) as string);
    expect(zip.status()).toBe(200);
    expect(zip.headers()["content-type"]).toMatch(/zip/);
    await anon.close();
    con.assertClean(EXPECTED);
  });

  test("settings and jobs pages work", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await page.goto("/admin/settings#latex");
    await expect(page.getByText("Template del preambolo globale")).toBeVisible();
    await page.goto("/admin/settings#models");
    await expect(page.getByText("Assistente AI (chat con strumenti)").first()).toBeVisible();
    await expect(page.getByText(/serve un modello che li supporti/)).toBeVisible();
    await page.goto("/admin/settings#ai");
    await expect(page.getByLabel(/Passi massimi dell'assistente/)).toBeVisible();
    await page.goto("/admin/jobs");
    await expect(page.getByText(/Pubblicazione/).first()).toBeVisible();
    con.assertClean(EXPECTED);
  });

  test("old editor and chapter URLs land in the text of the course", async ({ page }) => {
    await login(page);
    await page.goto("/admin/courses");
    const href = await page.locator("a.card-link").first().getAttribute("href");
    const id = href!.split("/").pop();
    await page.goto(`/admin/editor/${id}`);
    await page.waitForURL(new RegExp(`/admin/courses/${id}/testo$`));
    await expect(page.getByTestId("doc-preview")).toBeVisible();
    const chapter = (await (await page.request.get(`/api/courses/${id}`)).json()).chapters[0].id;
    await page.goto(`/admin/courses/${id}/chapters/${chapter}`);
    await page.waitForURL(new RegExp(`/admin/courses/${id}/testo\\?chapter=${chapter}$`));
    await expect(page.getByTestId("doc-preview")).toBeVisible();
  });
});
