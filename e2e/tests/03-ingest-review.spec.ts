import { expect, test } from "@playwright/test";
import { login, watchConsole } from "./helpers";

test("a lesson's slides and notes are written into the course by themselves", async ({ page }) => {
  test.setTimeout(420_000);
  const con = watchConsole(page);
  await login(page);
  // A fresh course to receive the notes (the assistant tests work on it next).
  await page.goto("/admin/courses?new=1");
  await page.getByLabel("Nome").fill("Teoria dei Segnali");
  await page.getByRole("button", { name: "Crea materia" }).click();
  await page.waitForURL(/\/admin\/courses\/\d+$/);
  const courseId = page.url().split("/").pop();

  // The material comes from a lesson: the slides, and notes next to the first one.
  await page.goto(`/admin/lessons?new=1&course=${courseId}`);
  await page.getByTestId("lesson-title").fill("Lezione 3 - Sistemi LTI");
  await page.getByTestId("lesson-pdf").setInputFiles("/e2e/.fixtures/slides.pdf");
  await page.getByTestId("lesson-title").press("Enter");
  await page.waitForURL(/\/admin\/courses\/\d+\/lessons\/\d+$/);
  await page.getByTestId("lesson-page").first().locator("textarea").fill("- sistema lineare e tempo-invariante\n- risposta all'impulso $h(t)$\n- convoluzione");
  await page.keyboard.press("Control+s");
  await expect(page.getByTestId("save-state")).toContainText("Salvato");

  // Without guidelines the text is generated automatically.
  await page.getByTestId("generate-open").click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByTestId("guidelines-notice")).toContainText("genererò il testo in automatico");
  await dialog.getByTestId("generate-go").click();
  await page.waitForURL(/\/admin\/jobs\/\d+$/);

  // No questions: the job runs to the end and says where it wrote.
  await expect(page.locator(".page-head [data-status]").first()).toHaveAttribute("data-status", "succeeded", { timeout: 300_000 });
  await expect(page.getByText(/^Nuovo capitolo «.*» in «Teoria dei Segnali»/)).toBeVisible({ timeout: 30_000 });

  // "Apri" leads to the workspace, scrolled to the new chapter: the draft shows the text.
  await page.locator(".pg-manifest").getByRole("link", { name: "Apri" }).first().click();
  await page.waitForURL(/\/admin\/courses\/\d+(\?chapter=\d+)?$/);
  const doc = page.getByTestId("doc-preview");
  await expect(doc).toBeVisible();
  await expect(doc.locator(".doc-block img").first()).toBeVisible({ timeout: 60_000 });
  // The outline lists the chapter and the assistant is ready to work on it.
  await expect(page.getByRole("navigation", { name: "Struttura del documento" }).locator(".rail-ch").first()).toBeVisible();
  await expect(page.getByTestId("ai-composer")).toBeVisible();
  con.assertClean([/status of 404/]);
});
