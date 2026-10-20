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
  await page.goto("/admin");
  const courseHref = await page.locator("a.card-link").first().getAttribute("href");
  const pages = ["/?public", "/search", "/admin", "/admin/jobs", "/admin/search?q=sistema", "/admin/settings", "/admin/settings#ai"];
  for (const p of pages) {
    await page.goto(p);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `horizontal overflow on ${p}`).toBeLessThanOrEqual(2);
  }

  // The course page fits too.
  await page.goto(courseHref!);
  await expect(page.getByTestId("course-overview")).toBeVisible();
  let overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "horizontal overflow on the course page").toBeLessThanOrEqual(2);
  // Its «Scarica ▾» menu opens on screen.
  await page.getByTestId("course-overview").getByRole("button", { name: "Scarica", exact: true }).click();
  const dl = await page.getByTestId("overview-pdf").locator("..").boundingBox();
  expect(dl!.x, "download menu off the left edge").toBeGreaterThanOrEqual(0);
  expect(dl!.x + dl!.width, "download menu off the right edge").toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.keyboard.press("Escape");
  // The text on a phone: the document, and the assistant one tap away (tab bar).
  await page.goto(`${courseHref}/testo`);
  await expect(page.getByTestId("doc-preview")).toBeVisible();
  overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
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

  // The old editor URL lands in the text of the course.
  const courseId = courseHref!.split("/").pop();
  await page.goto(`/admin/editor/${courseId}`);
  await expect(page.getByTestId("doc-preview")).toBeVisible();
  // A course link that asks for the assistant opens the text with the assistant.
  await page.goto(`/admin/courses/${courseId}?chat=1`);
  await page.waitForURL(/\/admin\/courses\/\d+\/testo\?chat=1$/);
  await expect(page.getByTestId("ai-panel")).toBeVisible();
  expect(foreign, foreign.join("\n")).toEqual([]);
  con.assertClean([/status of 404/]);
});
