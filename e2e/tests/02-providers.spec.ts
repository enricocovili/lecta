import { expect, test } from "@playwright/test";
import { login, watchConsole } from "./helpers";

test("configure the fake provider and assign it to every role", async ({ page }) => {
  const con = watchConsole(page);
  await login(page);
  await page.goto("/admin/settings#providers");
  await page.getByRole("button", { name: "Aggiungi provider" }).click();
  await page.getByLabel("Nome").fill("Fake cloud");
  await page.getByLabel("Tipo").selectOption("fake");
  await page.getByRole("button", { name: "Salva", exact: true }).click();
  await expect(page.getByText("Fake cloud")).toBeVisible();
  await page.getByRole("button", { name: "Verifica connessione" }).first().click();
  await expect(page.getByText(/Connessione OK/)).toBeVisible();

  await page.getByRole("tab", { name: "Modelli" }).click();
  await page.getByRole("button", { name: "Usa il provider Fake ovunque" }).click();
  await page.getByRole("button", { name: "Salva assegnazioni" }).click();
  await expect(page.getByText("Assegnazioni salvate")).toBeVisible();

  await page.getByRole("tab", { name: "Prompt" }).click();
  await expect(page.getByText("Lettura delle pagine (testo, formule e immagini → LaTeX)")).toBeVisible();
  await page.getByRole("tab", { name: "AI e costi" }).click();
  await expect(page.getByText(/vengono inviati ai provider AI che configuri/)).toBeVisible();
  await expect(page.getByText("Costi e chiamate")).toBeVisible();
  // Old links land somewhere sensible.
  await page.goto("/admin/audit");
  await expect(page).toHaveURL(/\/admin\/settings#ai$/);
  con.assertClean([/status of 404/]);
});
