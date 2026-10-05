import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { login, watchConsole } from "./helpers";

const EXPECTED = [/status of 404/];

const MAIN_C = `#include <stdio.h>

/* Somma dei primi n numeri */
int somma(int n) {
  int s = 0;
  for (int i = 1; i <= n; i++) s += i;
  return s;
}

int main(void) {
  printf("%d\\n", somma(10));
  return 0;
}
`;
const PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const PNG = Buffer.from(PNG_B64, "base64");

const NOTEBOOK = JSON.stringify({
  nbformat: 4,
  metadata: { language_info: { name: "python" } },
  cells: [
    { cell_type: "markdown", source: ["# Liste in Python\n", "Una **lista** si scorre con `for`."] },
    { cell_type: "code", execution_count: 1, source: ["for x in [1, 2, 3]:\n", "    print(x * 2)"], outputs: [{ output_type: "stream", name: "stdout", text: ["2\n", "4\n", "6\n"] }] },
    { cell_type: "code", execution_count: 2, source: ["1 / 0"], outputs: [{ output_type: "error", ename: "ZeroDivisionError", evalue: "division by zero", traceback: ["\u001b[31mZeroDivisionError\u001b[0m"] }] },
    { cell_type: "code", execution_count: 3, source: ["show()"], outputs: [{ output_type: "display_data", data: { "text/html": "<b id='evil'>no</b>" } }, { output_type: "display_data", data: { "image/png": PNG_B64 } }] },
  ],
});

/** A new lesson without slides in a new course; returns the lesson's address. */
async function newLesson(page: Page, course: string, title: string): Promise<string> {
  await page.goto("/admin/courses?new=1");
  await page.getByLabel("Nome").fill(course);
  await page.getByRole("button", { name: "Crea materia" }).click();
  await page.waitForURL(/\/admin\/courses\/\d+(\/testo)?$/);
  const courseId = /\/admin\/courses\/(\d+)/.exec(page.url())![1];
  await page.goto(`/admin/lessons?course=${courseId}`);
  await page.getByTestId("new-lesson").click();
  await page.getByTestId("lesson-title").fill(title);
  await page.getByTestId("lesson-title").press("Enter");
  await page.waitForURL(/\/admin\/courses\/\d+\/lessons\/\d+$/);
  return new URL(page.url()).pathname;
}

test.describe.serial("Laboratorio", () => {
  let lessonPath = "";

  test("a lesson's lab: files uploaded together, read with their colours, renamed", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    lessonPath = await newLesson(page, "Programmazione", "Lezione 3 - Cicli");

    // From the lesson to its lab, which is made on the first visit.
    await page.getByTestId("lesson-lab").click();
    await page.waitForURL(/\/lessons\/\d+\/lab$/);
    await page.getByTestId("lab-create").click();
    await expect(page.getByTestId("lab-editor")).toBeVisible();
    await expect(page.getByTestId("lab-title")).toHaveText("Lezione 3 - Cicli");
    await expect(page.getByTestId("lab-publish")).toContainText("in sviluppo");

    // Several files at once.
    await page.getByTestId("lab-file-input").setInputFiles([
      { name: "main.c", mimeType: "text/x-c", buffer: Buffer.from(MAIN_C) },
      { name: "query.sql", mimeType: "application/sql", buffer: Buffer.from("SELECT nome FROM studenti WHERE voto >= 18;\n") },
      { name: "schema.png", mimeType: "image/png", buffer: PNG },
    ]);
    await expect(page.getByTestId("lab-file")).toHaveCount(3);

    // A source opens as code with its colours (a keyword is coloured by the C grammar).
    await page.getByTestId("lab-file").filter({ hasText: "main.c" }).click();
    await expect(page.getByTestId("lab-open-path")).toHaveText("main.c");
    const code = page.getByTestId("lab-code");
    await expect(code.locator(".cm-line").nth(3)).toContainText("int somma(int n) {");
    await expect(code.locator(".cm-lineNumbers .cm-gutterElement").filter({ hasText: /^13$/ })).toHaveCount(1);
    await expect.poll(async () => code.locator(".cm-line span").filter({ hasText: /^return$/ }).first().evaluate((el) => getComputedStyle(el).color)).not.toBe(
      await code.locator(".cm-line").first().evaluate((el) => getComputedStyle(el).color),
    );
    // The open file is in the address: a reload comes back to it.
    await expect(page).toHaveURL(/file=main\.c/);
    await page.reload();
    await expect(page.getByTestId("lab-open-path")).toHaveText("main.c");

    // A picture is shown.
    await page.getByTestId("lab-file").filter({ hasText: "schema.png" }).click();
    await expect(page.locator(".lab-image img")).toBeVisible();

    // Renaming into a folder.
    await page.getByTestId("lab-file").filter({ hasText: "query.sql" }).hover();
    await page.getByRole("button", { name: "Rinomina query.sql" }).click();
    await page.getByTestId("lab-rename-input").fill("sql/query.sql");
    await page.getByTestId("lab-rename-save").click();
    await expect(page.locator(".lab-folder").filter({ hasText: "sql" })).toBeVisible();
    await page.getByTestId("lab-file").filter({ hasText: "query.sql" }).click();
    await expect(page.getByTestId("lab-open-path")).toHaveText("sql/query.sql");

    await page.screenshot({ path: "/e2e/.results/lab-files.png" });

    // And back to the theory lesson.
    await page.getByTestId("lab-lesson").click();
    await page.waitForURL(new RegExp(`${lessonPath}$`));
    con.assertClean(EXPECTED);
  });

  test("comments written live on the lines of a file stay there", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await page.goto(`${lessonPath}/lab?file=main.c`);
    const code = page.getByTestId("lab-code");
    await expect(code.locator(".cm-line").nth(3)).toContainText("int somma");

    // Select lines 4–7 and comment them from the bubble over the selection.
    await code.locator(".cm-line").nth(3).click({ position: { x: 2, y: 5 } });
    await code.locator(".cm-line").nth(6).click({ modifiers: ["Shift"] });
    await page.getByTestId("lab-comment-selection").click();
    await page.keyboard.type("La somma con un **for**: $\\sum_{i=1}^n i$");
    await expect(page.getByTestId("lab-save-state")).toHaveClass(/pending/);
    await page.keyboard.press("Control+s");
    await expect(page.getByTestId("lab-save-state")).toHaveClass(/saved/);

    // A comment on the whole file, finished with Escape.
    await page.getByTestId("lab-comment-file").click();
    await page.keyboard.type("Primo esercizio del laboratorio");
    await page.keyboard.press("Escape");
    // An empty comment that loses the focus goes away.
    await page.getByTestId("lab-comment-file").click();
    await page.keyboard.press("Escape");
    await page.keyboard.press("Control+s");
    await expect(page.getByTestId("lab-save-state")).toHaveClass(/saved/);

    await page.reload();
    const cards = page.getByTestId("lab-comment");
    await expect(cards).toHaveCount(2);
    await expect(cards.nth(0)).toContainText("Tutto il file");
    await expect(cards.nth(1)).toContainText("Righe 4–7");
    await expect(cards.nth(1).locator(".md-math")).toBeVisible();
    await expect(code.locator(".cm-lab-mark")).toHaveCount(4);
    await expect(page.getByTestId("lab-file").filter({ hasText: "main.c" }).locator(".lab-count")).toHaveText("2");

    // The dot in the gutter picks its comment.
    await code.locator(".cm-lab-dot").first().click();
    await expect(cards.nth(1)).toHaveClass(/on/);
    await page.screenshot({ path: "/e2e/.results/lab-comments.png" });

    // Removing a comment.
    await cards.nth(0).hover();
    await cards.nth(0).getByRole("button", { name: "Elimina il commento" }).click();
    await page.keyboard.press("Control+s");
    await expect(page.getByTestId("lab-save-state")).toHaveClass(/saved/);
    await page.reload();
    await expect(page.getByTestId("lab-comment")).toHaveCount(1);
    con.assertClean(EXPECTED);
  });

  test("the lab has free notes, not tied to a file", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await page.goto(`${lessonPath}/lab`);
    await page.getByTestId("lab-notes-open").click();
    await expect(page).toHaveURL(/\?notes$/);
    await page.getByTestId("lab-notes").locator("textarea").fill("- consegna entro venerdì\n- all'esame: i puntatori");
    await page.keyboard.press("Control+s");
    await expect(page.getByTestId("lab-save-state")).toHaveClass(/saved/);
    await page.reload();
    await expect(page.getByTestId("lab-notes").locator("textarea")).toHaveValue("- consegna entro venerdì\n- all'esame: i puntatori");
    await page.getByRole("button", { name: "Anteprima delle note" }).click();
    await expect(page.getByTestId("lab-notes").locator("li")).toHaveCount(2);
    con.assertClean(EXPECTED);
  });

  test("a text file is edited by hand and its comments follow their lines", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await page.goto(`${lessonPath}/lab?file=main.c`);
    const code = page.getByTestId("lab-code");
    const card = page.getByTestId("lab-comment");
    await expect(card).toHaveCount(1);
    await expect(card).toContainText("Righe 4–7");

    // Two lines written above the comment move it down.
    await page.getByTestId("lab-edit").click();
    await code.locator(".cm-line").first().click();
    await page.keyboard.press("Control+Home");
    await page.keyboard.type("// Laboratorio 3\n// Autore: io\n");
    await expect(card).toContainText("Righe 6–9");
    await expect(code.locator(".cm-lab-mark").first()).toContainText("int somma");
    await page.keyboard.press("Control+s");
    await expect(page.getByTestId("lab-save-state")).toHaveClass(/saved/);
    await page.reload();
    await expect(code.locator(".cm-line").first()).toHaveText("// Laboratorio 3");
    await expect(card).toContainText("Righe 6–9");

    // Deleting the commented lines keeps the comment, as «Righe rimosse».
    await page.getByTestId("lab-edit").click();
    await code.locator(".cm-line").nth(5).click({ position: { x: 2, y: 5 } });
    await page.keyboard.press("Home");
    for (let i = 0; i < 4; i++) await page.keyboard.press("Shift+ArrowDown");
    await page.keyboard.press("Delete");
    await expect(card).toContainText("Righe rimosse");
    await expect(code.locator(".cm-lab-mark")).toHaveCount(0);
    await page.getByTestId("lab-edit").click(); // «Fine» saves
    await expect(page.getByTestId("lab-save-state")).toHaveClass(/saved/);
    await page.reload();
    await expect(card).toContainText("Righe rimosse");
    await expect(code).not.toContainText("int somma(int n)");
    con.assertClean(EXPECTED);
  });

  test("notebooks show their cells and PDFs their pages, each with its comments", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await page.goto(`${lessonPath}/lab`);
    await page.getByTestId("lab-file-input").setInputFiles([
      { name: "liste.ipynb", mimeType: "application/json", buffer: Buffer.from(NOTEBOOK) },
      { name: "testo.pdf", mimeType: "application/pdf", buffer: readFileSync("/e2e/.fixtures/slides.pdf") },
    ]);
    await expect(page.getByTestId("lab-file")).toHaveCount(5);

    // The notebook: Markdown rendered, code coloured, outputs as saved, no HTML.
    await page.getByTestId("lab-file").filter({ hasText: "liste.ipynb" }).click();
    const cells = page.getByTestId("lab-cell");
    await expect(cells).toHaveCount(4);
    await expect(cells.nth(0).locator(".md-h")).toHaveText("Liste in Python");
    await expect(cells.nth(1).locator(".lt-keyword").first()).toHaveText("for");
    await expect(cells.nth(1).locator(".lab-out")).toHaveText("2\n4\n6\n");
    await expect(cells.nth(2).locator(".lab-out.err")).toContainText("ZeroDivisionError: division by zero");
    await expect(cells.nth(3)).toContainText("Output HTML non mostrato");
    await expect(page.locator("#evil")).toHaveCount(0);
    await expect(cells.nth(3).locator("img.lab-out-img")).toBeVisible();
    await page.getByRole("button", { name: "Commenta la cella 2" }).click();
    await page.keyboard.type("Il ciclo raddoppia ogni elemento");
    await page.keyboard.press("Escape");

    // The PDF: its pages, one commented.
    await page.getByTestId("lab-file").filter({ hasText: "testo.pdf" }).click();
    const pdfPages = page.locator(".lab-pdf-page");
    await expect(pdfPages).toHaveCount(3);
    await expect(pdfPages.first().locator("canvas")).toBeVisible();
    await pdfPages.nth(1).getByRole("button", { name: "Commenta" }).click();
    await page.keyboard.type("La consegna dell'esercizio");
    await page.keyboard.press("Escape");
    await page.keyboard.press("Control+s");
    await expect(page.getByTestId("lab-save-state")).toHaveClass(/saved/);

    await page.reload();
    await expect(page.getByTestId("lab-comment")).toContainText("Pagina 2");
    await expect(pdfPages.nth(1).locator(".lab-count")).toHaveText("1");
    await page.getByTestId("lab-file").filter({ hasText: "liste.ipynb" }).click();
    await expect(page.getByTestId("lab-comment")).toContainText("Cella 2");
    await page.getByTestId("lab-comment").click();
    await expect(cells.nth(1)).toHaveClass(/on/);
    await page.screenshot({ path: "/e2e/.results/lab-notebook.png" });
    con.assertClean(EXPECTED);
  });

  test("the course page lists the labs, each one click away from its lab and its lesson", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    const course = /\/admin\/courses\/(\d+)/.exec(lessonPath)![1];
    await page.goto(`/admin/courses/${course}`);
    const row = page.getByTestId("overview-lab-row");
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("Laboratorio · Lezione 3 - Cicli");
    await expect(row).toContainText("5 file · 3 commenti");
    await row.getByRole("link", { name: "Laboratorio · Lezione 3 - Cicli" }).click();
    await page.waitForURL(new RegExp(`${lessonPath}/lab$`));
    await expect(page.getByTestId("lab-editor")).toBeVisible();
    await page.goto(`/admin/courses/${course}`);
    await page.getByTestId("overview-lab-row").getByRole("link", { name: "Lezione 1" }).click();
    await page.waitForURL(new RegExp(`${lessonPath}$`));
    con.assertClean(EXPECTED);
  });

  test("the lab's assistant explains the picked lines, comments when asked, and Annulla takes it back", async ({ page }) => {
    test.setTimeout(180_000);
    const con = watchConsole(page);
    await login(page);
    await page.goto(`${lessonPath}/lab?file=main.c`);
    const code = page.getByTestId("lab-code");
    await expect(code.locator(".cm-line").first()).toHaveText("// Laboratorio 3");

    // Pick two lines and ask about them: the panel opens with them as context.
    await code.locator(".cm-line").nth(0).click({ position: { x: 2, y: 5 } });
    await code.locator(".cm-line").nth(1).click({ modifiers: ["Shift"] });
    await page.getByTestId("lab-ask-selection").click();
    const panel = page.getByTestId("lab-ai");
    await expect(panel).toBeVisible();
    await expect(panel.locator(".ai-scope-pill")).toContainText("main.c · selezione di 2 righe");
    await page.getByTestId("ai-composer").fill("Cosa fanno queste righe?");
    await page.getByTestId("ai-composer").press("Enter");
    await expect(panel.getByTestId("ai-message").last()).toContainText("fanno il lavoro principale", { timeout: 60_000 });
    await expect(panel.getByTestId("ai-change-card")).toHaveCount(0);

    // Asked to comment, it comments: the comment shows up next to the code; Annulla takes it away.
    const before = await page.getByTestId("lab-comment").count();
    await page.getByTestId("ai-composer").fill("Commenta la prima riga");
    await page.getByTestId("ai-composer").press("Enter");
    await expect(panel.getByTestId("ai-change-card")).toContainText("Ho aggiunto dei commenti", { timeout: 60_000 });
    await expect(page.getByTestId("lab-comment")).toHaveCount(before + 1);
    await expect(page.getByTestId("lab-comment").first()).toContainText("Riga 1");
    await page.screenshot({ path: "/e2e/.results/lab-assistant.png" });
    await panel.getByTestId("ai-undo").click();
    await expect(panel.getByTestId("ai-change-card")).toContainText("Annullato");
    await expect(page.getByTestId("lab-comment")).toHaveCount(before);

    // The course's text has its own conversations: the lab's don't show up there.
    const course = /\/admin\/courses\/(\d+)/.exec(lessonPath)![1];
    const sessions = await (await page.request.get(`/api/chat/sessions?course_id=${course}`)).json();
    expect(sessions).toEqual([]);
    con.assertClean(EXPECTED);
  });
});
