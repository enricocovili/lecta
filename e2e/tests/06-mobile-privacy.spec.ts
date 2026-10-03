import { expect, test } from "@playwright/test";
import { login, watchConsole } from "./helpers";

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

test("pages fit a phone screen and never load anything from another origin", async ({ page, baseURL }) => {
  const con = watchConsole(page);
  const foreign: string[] = [];
  page.on("request", (req) => {
    const u = new URL(req.url());
    if (!["http:", "https:", "ws:", "wss:"].includes(u.protocol)) return; // data:, blob:
    if (u.origin !== new URL(baseURL!).origin) foreign.push(req.url());
  });
  await login(page);
  await page.goto("/admin/courses");
  const courseHref = await page.locator("a.card-link").first().getAttribute("href");
  const pages = ["/", "/search", "/admin", "/admin/courses", "/admin/jobs",
                 "/admin/inbox", "/admin/lessons", "/admin/search?q=sistema", "/admin/settings", "/admin/settings#ai"];
  for (const p of pages) {
    await page.goto(p);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `horizontal overflow on ${p}`).toBeLessThanOrEqual(2);
  }

  // The workspace on a phone: the document, and the assistant one tap away (tab bar).
  await page.goto(courseHref!);
  await expect(page.getByTestId("doc-preview")).toBeVisible();
  let overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "horizontal overflow in the workspace").toBeLessThanOrEqual(2);
  await page.getByRole("navigation", { name: "Vista" }).getByRole("button", { name: "Assistente" }).click();
  await expect(page.getByTestId("ai-panel")).toBeVisible();
  await expect(page.getByTestId("ai-composer")).toBeVisible();
  overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "horizontal overflow in the assistant").toBeLessThanOrEqual(2);
  await page.getByRole("navigation", { name: "Vista" }).getByRole("button", { name: "Documento" }).click();
  // The outline is a drawer.
  await page.getByRole("button", { name: "Indice" }).first().click();
  await expect(page.getByRole("navigation", { name: "Struttura del documento" })).toBeVisible();

  // The old editor URL lands in the workspace.
  const courseId = courseHref!.split("/").pop();
  await page.goto(`/admin/editor/${courseId}`);
  await expect(page.getByTestId("doc-preview")).toBeVisible();
  expect(foreign, foreign.join("\n")).toEqual([]);
  con.assertClean([/status of 404/]);
});
