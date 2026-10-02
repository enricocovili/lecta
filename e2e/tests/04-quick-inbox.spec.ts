import { expect, test } from "@playwright/test";
import { login, watchConsole } from "./helpers";

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

test("phone quick upload of a handwritten page runs by itself", async ({ page }) => {
  test.setTimeout(420_000);
  const con = watchConsole(page);
  await login(page);
  await page.goto("/admin/quick");
  await expect(page.getByRole("button", { name: /Scatta foto/ })).toBeVisible();
  // Camera capture input (accept=image/*, capture=environment).
  await page.locator('input[capture="environment"]').setInputFiles("/e2e/.fixtures/appunti.jpg");
  await expect(page.getByText("appunti.jpg")).toBeVisible();
  await page.getByRole("button", { name: "Carica ed elabora" }).click();
  await page.waitForURL(/\/admin\/jobs\/\d+$/);

  await expect(page.locator(".page-head [data-status]").first()).toHaveAttribute("data-status", "succeeded", { timeout: 300_000 });
  await expect(page.getByText("Risultati")).toBeVisible();
  // Mobile navigation: bottom tab bar, and the rest of the nav in the account menu.
  await expect(page.locator(".bottom-nav").getByText("Materie")).toBeVisible();
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
