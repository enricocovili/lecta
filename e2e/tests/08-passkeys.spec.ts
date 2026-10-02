import { expect, test } from "@playwright/test";
import { login, watchConsole } from "./helpers";

// WebAuthn needs a secure context: plain HTTP is one only on localhost, so the browser reaches the test site as localhost.
test.use({ baseURL: "http://localhost:4321", launchOptions: { args: ["--host-resolver-rules=MAP localhost lecta-frontend"] } });

test.describe("Passkey", () => {
  test("a passkey is added in the settings and then signs in without a password", async ({ page, context }) => {
    test.setTimeout(120_000);
    const con = watchConsole(page);
    // A software authenticator that holds discoverable credentials and verifies the user on its own (like a password manager).
    const cdp = await context.newCDPSession(page);
    await cdp.send("WebAuthn.enable");
    await cdp.send("WebAuthn.addVirtualAuthenticator", {
      options: { protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true },
    });

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
});
