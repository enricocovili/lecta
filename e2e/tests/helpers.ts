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

/** Open a course from the courses list: the workspace with the draft of the document. */
export async function openCourse(page: Page, name: string | RegExp) {
  await page.goto("/admin/courses");
  await page.getByRole("link", { name }).first().click();
  await page.waitForURL(/\/admin\/courses\/\d+/);
  await expect(page.getByTestId("doc-preview")).toBeVisible();
}

/** Select a whole paragraph of the draft (triple click): the floating toolbar appears next to it. */
export async function selectParagraph(page: Page, index = 0) {
  const para = page.getByTestId("doc-preview").locator("p[data-line]").nth(index);
  await para.scrollIntoViewIfNeeded();
  await para.click({ clickCount: 3 });
  await expect(page.getByTestId("selection-toolbar")).toBeVisible();
  return para;
}
