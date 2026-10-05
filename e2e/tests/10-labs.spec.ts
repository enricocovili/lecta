import { expect, test, type Page } from "@playwright/test";
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
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

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
});
