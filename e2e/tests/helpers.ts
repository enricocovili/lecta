import { expect, type Page } from "@playwright/test";

export const USER = "e2e";
export const PASSWORD = "e2e-password-1234";

/** Fail the test on any console error (CSP violations show up here too). */
export function watchConsole(page: Page) {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  return {
    errors,
    assertClean(ignore: RegExp[] = []) {
      const real = errors.filter((e) => !ignore.some((r) => r.test(e)));
      expect(real, real.join("\n")).toEqual([]);
    },
  };
}

export async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Nome utente").fill(USER);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Accedi", exact: true }).click();
  await page.waitForURL("**/admin");
}

/** Open a course from the courses list, then its text: the workspace with the draft of the document. */
export async function openCourse(page: Page, name: string | RegExp) {
  await page.goto("/admin/courses");
  await page.getByRole("link", { name }).first().click();
  await page.waitForURL(/\/admin\/courses\/\d+$/);
  await page.getByTestId("course-overview").getByRole("link", { name: "Apri il testo" }).first().click();
  await page.waitForURL(/\/admin\/courses\/\d+\/testo$/);
  await expect(page.getByTestId("doc-preview")).toBeVisible();
}

/** Pick a block of the draft (a paragraph or an environment typeset by LaTeX): the floating toolbar appears next to it. */
export async function pickBlock(page: Page, index = 0) {
  const block = page.getByTestId("doc-preview").locator(".doc-block:not(.doc-heading):has(img)").nth(index);
  await block.scrollIntoViewIfNeeded();
  await block.click();
  await expect(page.getByTestId("selection-toolbar")).toBeVisible();
  return block;
}
