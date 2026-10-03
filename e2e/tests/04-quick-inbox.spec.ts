import { expect, test } from "@playwright/test";
import { login, watchConsole } from "./helpers";

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

test("on a phone the navigation is the bottom bar and the account menu", async ({ page }) => {
  const con = watchConsole(page);
  await login(page);
  await expect(page.locator(".bottom-nav").getByText("Materie")).toBeVisible();
  await expect(page.locator(".bottom-nav").getByText("Carica")).toHaveCount(0);
  await page.locator("#user-menu summary").click();
  await expect(page.locator("#user-menu").getByText("Da smistare")).toBeVisible();
  con.assertClean([/status of 404/]);
});

test("inbox and embeddings settings render", async ({ page }) => {
  const con = watchConsole(page);
  await login(page);
  await page.goto("/admin/inbox");
  await expect(page.getByRole("heading", { name: "Da smistare" })).toBeVisible();
  await page.goto("/admin/settings#embeddings");
  await expect(page.getByText("Embeddings locali (ricerca semantica)")).toBeVisible();
  await expect(page.getByText(/Modalità di ricerca:/)).toBeVisible();
  con.assertClean([/status of 404/]);
});
