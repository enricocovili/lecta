import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { login, USER, watchConsole } from "./helpers";

// WebAuthn needs a secure context: plain HTTP is one only on localhost, so the browser reaches the test site as localhost.
test.use({ baseURL: "http://localhost:4321", launchOptions: { args: ["--host-resolver-rules=MAP localhost lecta-frontend"] } });

/** A software authenticator that holds discoverable credentials and verifies the user on its own (like a password manager). */
async function virtualAuthenticator(context: BrowserContext, page: Page) {
  const cdp = await context.newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: { protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true },
  });
}

test.describe("Passkey", () => {
  test("a passkey is added in the settings and then signs in without a password", async ({ page, context }) => {
    test.setTimeout(120_000);
    const con = watchConsole(page);
    await virtualAuthenticator(context, page);

    await login(page);
    await page.goto("/admin/settings#account");
    const card = page.getByTestId("passkeys");
    await expect(card).toBeVisible();
    await page.getByTestId("passkey-name").fill("Bitwarden di prova");
    await page.getByTestId("passkey-add").click();
    await expect(page.getByTestId("passkey-row")).toContainText("Bitwarden di prova", { timeout: 15_000 });
    await expect(page.getByTestId("passkey-row")).toContainText("mai usata");

    // Signed out, the login page offers the passkey; no username, no password, no code.
    await context.clearCookies();
    await page.goto("/login");
    await page.getByTestId("passkey-login").click();
    await page.waitForURL("**/admin", { timeout: 20_000 });
    await expect(page.locator("body")).not.toContainText("404");
    await page.goto("/admin/settings#account");
    await expect(page.getByTestId("passkey-row")).toContainText("usata il", { timeout: 15_000 });

    // Removing it (Enter confirms the dialog): the passkey no longer signs in.
    await page.getByTestId("passkey-remove").click();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("passkey-row")).toHaveCount(0);
    await context.clearCookies();
    await page.goto("/login");
    await page.getByTestId("passkey-login").click();
    await expect(page.getByRole("alert")).toBeVisible({ timeout: 15_000 });
    await expect(page).toHaveURL(/\/login/);
    con.assertClean([/status of 401/]);
  });

  test("an invite link makes an account with a passkey, once", async ({ page, browser, baseURL }) => {
    test.setTimeout(120_000);
    // The admin makes an invite in Settings → Utenti: the link is shown once.
    await login(page);
    await page.goto("/admin/settings#users");
    await page.getByTestId("new-invite-role").selectOption("editor");
    await page.getByTestId("new-invite-note").fill("Per la collega di Analisi");
    await page.getByTestId("invite-create").click();
    await expect(page.getByTestId("invite-link")).toContainText("non lo mostreremo più");
    const url = await page.getByTestId("invite-url").inputValue();
    expect(url).toMatch(/^http:\/\/localhost:4321\/invito\/.+/);
    const path = new URL(url).pathname;
    await expect(page.getByTestId("invite-row")).toContainText("Per la collega di Analisi");

    // Signed in, the page says that accepting switches account.
    await page.goto(path);
    await expect(page.getByTestId("invite-form")).toContainText(`accesso come ${USER}`);

    // Someone else, signed out, in their own browser.
    const other = await browser.newContext({ baseURL });
    const guest = await other.newPage();
    const con = watchConsole(guest);
    await virtualAuthenticator(other, guest);
    await guest.goto(path);
    await expect(guest.getByTestId("invite-role")).toHaveText("Autore");
    await expect(guest.getByTestId("invite-note")).toHaveText("Per la collega di Analisi");
    await expect(guest.getByTestId("invite-form")).not.toContainText("accesso come");

    // A taken username is said on the spot, and the link stays usable.
    await guest.getByLabel("Nome utente").fill(USER);
    await guest.getByTestId("invite-accept").click();
    await expect(guest.getByRole("alert")).toContainText("già in uso");

    await guest.getByLabel("Nome utente").fill("collega");
    await guest.getByTestId("invite-accept").click();
    await guest.waitForURL("**/admin", { timeout: 20_000 });
    await expect(guest.locator("body")).not.toContainText("404");
    expect(await guest.evaluate(async () => (await (await fetch("/api/auth/me")).json()).username)).toBe("collega");

    // Used once: the same link is now an address like any unknown one.
    const again = await guest.goto(path);
    expect(again?.status()).toBe(404);
    await expect(guest.getByRole("heading", { name: "Pagina non trovata" })).toBeVisible();
    con.assertClean([/status of 409/, /status of 404/]);

    // The admin sees the new account (passkey only) and the invite gone; disabling it signs the colleague out.
    await page.goto("/admin/settings#users");
    await expect(page.getByTestId("invite-row")).toHaveCount(0);
    const row = page.getByTestId("user-row").filter({ hasText: "collega" });
    await expect(row).toContainText("solo passkey");
    await expect(row.getByTestId("user-role")).toHaveValue("editor");
    await row.getByTestId("user-disable").click();
    await page.keyboard.press("Enter");
    await expect(row).toContainText("disattivato");
    expect(await guest.evaluate(async () => (await (await fetch("/api/auth/me")).json()).authenticated)).toBe(false);
    await row.getByTestId("user-enable").click();
    await expect(row).not.toContainText("disattivato");
    await other.close();
  });
});
