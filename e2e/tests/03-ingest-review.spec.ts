import { expect, test } from "@playwright/test";
import { login, watchConsole } from "./helpers";

test("upload the mixed zip: the notes are written into the course by themselves", async ({ page }) => {
  test.setTimeout(420_000);
  const con = watchConsole(page);
  await login(page);
  // A fresh course to receive the notes.
  await page.goto("/admin/courses?new=1");
  await page.getByLabel("Nome").fill("Teoria dei Segnali");
  await page.getByRole("button", { name: "Crea materia" }).click();
  await page.waitForURL(/\/admin\/courses\/\d+$/);
  const courseId = page.url().split("/").pop();

  await page.goto(`/admin/upload?course=${courseId}`);
  await page.locator('input[type="file"][multiple]:not([capture])').first().setInputFiles("/e2e/.fixtures/mixed.zip");
  await expect(page.getByText("mixed.zip")).toBeVisible();
  await page.getByRole("button", { name: "Carica ed elabora" }).click();
  // Into a chosen subject: the guidelines are asked first, and without any the text is generated automatically.
  const guide = page.getByRole("dialog");
  await expect(guide.getByTestId("guidelines-notice")).toContainText("genererò il testo in automatico");
  await guide.getByRole("button", { name: "Continua in automatico" }).click();
  await page.waitForURL(/\/admin\/jobs\/\d+$/);

  // No questions: the job runs to the end and says where it wrote.
  await expect(page.locator(".page-head [data-status]").first()).toHaveAttribute("data-status", "succeeded", { timeout: 300_000 });
  await expect(page.getByText(/^Nuovo capitolo «.*» in «Teoria dei Segnali»/)).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".pg-pic").first()).toBeVisible();
  await expect(page.locator(".thumbs figure").first()).toBeVisible();

  // "Apri" leads to the workspace, scrolled to the new chapter: the draft shows the text, the formulas and the picture.
  await page.locator(".pg-manifest").getByRole("link", { name: "Apri" }).first().click();
  await page.waitForURL(/\/admin\/courses\/\d+(\?chapter=\d+)?$/);
  const doc = page.getByTestId("doc-preview");
  await expect(doc).toBeVisible();
  await expect(doc.locator(".doc-block img").first()).toBeVisible({ timeout: 60_000 });
  await expect(doc.locator(".doc-block img.raster").first()).toBeVisible(); // a picture from the sources
  // The outline lists the chapter and the AI panel is ready to work on it.
  await expect(page.getByRole("navigation", { name: "Struttura del documento" }).locator(".rail-ch").first()).toBeVisible();
  await expect(page.getByTestId("ai-composer")).toBeVisible();
  con.assertClean([/status of 404/]);
});

test("slides without class notes: warned first, then summarised on confirmation", async ({ page }) => {
  test.setTimeout(300_000);
  const con = watchConsole(page);
  await login(page);
  await page.goto("/admin/courses?new=1");
  await page.getByLabel("Nome").fill("Solo slide");
  await page.getByRole("button", { name: "Crea materia" }).click();
  await page.waitForURL(/\/admin\/courses\/\d+$/);
  await page.goto(`/admin/upload?course=${page.url().split("/").pop()}`);
  await page.locator('input[type="file"][multiple]:not([capture])').first().setInputFiles("/e2e/.fixtures/slides.pdf");
  await expect(page.locator(".pg-notes-missing")).toBeVisible();
  await page.getByRole("button", { name: "Carica ed elabora" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Mancano gli appunti della lezione")).toBeVisible();
  // Adding the notes file makes the warning go away…
  await dialog.getByRole("button", { name: "Aggiungi gli appunti" }).click();
  await page.locator('input[type="file"][multiple]:not([capture])').first().setInputFiles("/e2e/.fixtures/note.md");
  await expect(page.locator(".pg-notes-missing")).toHaveCount(0);
  await page.getByRole("button", { name: "Togli note.md" }).click();
  // …or go on without them.
  await page.getByRole("button", { name: "Carica ed elabora" }).click();
  await dialog.getByRole("button", { name: "Continua senza appunti" }).click();
  await dialog.getByRole("button", { name: "Continua in automatico" }).click();
  await page.waitForURL(/\/admin\/jobs\/\d+$/);
  await expect(page.locator(".page-head [data-status]").first()).toHaveAttribute("data-status", "succeeded", { timeout: 240_000 });
  await expect(page.getByText("senza appunti: riassunto del materiale")).toBeVisible({ timeout: 30_000 });
  con.assertClean([/status of 404/]);
});
