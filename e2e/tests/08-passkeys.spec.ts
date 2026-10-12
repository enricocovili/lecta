import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { login, openCourse, USER, watchConsole } from "./helpers";

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

/** A new account with this role, from an invite the admin (signed in on `admin`) makes, signed in in its own browser. */
async function invited(admin: Page, browser: Browser, baseURL: string, role: string, username: string) {
  const path = await admin.evaluate(async (role) => {
    const csrf = document.cookie.match(/lecta_csrf=([^;]+)/)?.[1] ?? "";
    const res = await fetch("/api/invites", { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf }, body: JSON.stringify({ role }) });
    return (await res.json()).path as string;
  }, role);
  const context = await browser.newContext({ baseURL });
  const p = await context.newPage();
  await virtualAuthenticator(context, p);
  await p.goto(path);
  await p.getByLabel("Nome utente").fill(username);
  await p.getByTestId("invite-accept").click();
  await p.waitForURL("**/admin", { timeout: 20_000 });
  return { context, p };
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

    // The colleague signs in again with the passkey, then loses the device: the admin makes a recovery link.
    await guest.goto("/login");
    await guest.getByTestId("passkey-login").click();
    await guest.waitForURL("**/admin", { timeout: 20_000 });
    await row.getByTestId("user-recovery").click();
    await expect(row.getByTestId("recovery-link")).toContainText("non lo mostreremo più");
    const recovery = new URL(await row.getByTestId("recovery-url").inputValue()).pathname;
    await expect(page.getByTestId("invite-row")).toContainText("Recupero di collega");

    // A new browser with a new authenticator: no username asked, the new passkey signs in as the colleague.
    const fresh = await browser.newContext({ baseURL });
    const device = await fresh.newPage();
    const con2 = watchConsole(device);
    await virtualAuthenticator(fresh, device);
    await device.goto(recovery);
    await expect(device.getByTestId("invite-form")).toContainText("Recupera l'accesso come collega");
    await expect(device.getByLabel("Nome utente")).toHaveCount(0);
    await device.getByRole("button", { name: "Crea una nuova passkey" }).click();
    await device.waitForURL("**/admin", { timeout: 20_000 });
    expect(await device.evaluate(async () => (await (await fetch("/api/auth/me")).json()).username)).toBe("collega");
    // The lost device is signed out, the link is used up and gone from the pending list.
    expect(await guest.evaluate(async () => (await (await fetch("/api/auth/me")).json()).authenticated)).toBe(false);
    expect((await device.goto(recovery))?.status()).toBe(404);
    con2.assertClean([/status of 404/]);
    await page.reload();
    await expect(page.getByTestId("user-row").filter({ hasText: "collega" })).toContainText("2 passkey");
    await expect(page.getByTestId("invite-row")).toHaveCount(0);
    await fresh.close();
    await other.close();
  });

  test("a reader sees no admin pages and nothing to create, an author can create", async ({ page, browser, baseURL }) => {
    test.setTimeout(120_000);
    await login(page);
    const join = (role: string, username: string) => invited(page, browser, baseURL!, role, username);

    const { context: rc, p: reader } = await join("viewer", "lettore");
    const con = watchConsole(reader);
    // No create actions: not on the home page, in the sidebar, among the courses or the lessons.
    await reader.reload();
    await expect(reader.getByRole("heading", { name: "Home" })).toBeVisible();
    await expect(reader.getByRole("link", { name: "Prendi appunti a lezione" })).toHaveCount(0);
    await expect(reader.getByRole("link", { name: "Nuova materia" })).toHaveCount(0);
    await reader.goto("/admin/courses");
    await expect(reader.getByRole("heading", { name: "Materie" })).toBeVisible();
    await expect(reader.getByRole("button", { name: "Nuova materia" })).toHaveCount(0);
    await reader.goto("/admin/lessons?new=1");
    await expect(reader.getByRole("heading", { name: "Lezioni" })).toBeVisible();
    await expect(reader.getByTestId("new-lesson")).toHaveCount(0);
    await expect(reader.getByRole("dialog")).toHaveCount(0);
    // No admin entries: the menu has no costs, the settings only the account (passkeys, no password or 2FA) and the export.
    await reader.locator("#user-menu summary").click();
    await expect(reader.locator("#user-menu")).toContainText("Impostazioni");
    await expect(reader.locator("#user-menu")).not.toContainText("AI e costi");
    await reader.goto("/admin/settings#providers");
    await expect(reader.getByRole("tab")).toHaveText(["Account", "Backup"]);
    await expect(reader.getByRole("tabpanel")).toContainText("Lettore · accesso con passkey");
    await expect(reader.getByTestId("passkeys")).toBeVisible();
    await expect(reader.getByRole("button", { name: "Cambia password" })).toHaveCount(0);
    await expect(reader.getByRole("button", { name: "Configura la 2FA" })).toHaveCount(0);
    // An admin page is an address like any unknown one.
    const audit = await reader.goto("/admin/audit");
    expect(audit?.status()).toBe(404);
    await expect(reader.getByRole("heading", { name: "Pagina non trovata" })).toBeVisible();
    con.assertClean([/status of 404/]);

    // An author creates courses and lessons, but has no admin entries either.
    const { context: ac, p: author } = await join("editor", "autore");
    await author.goto("/admin/courses");
    await expect(author.getByRole("button", { name: "Nuova materia" })).toBeVisible();
    await author.goto("/admin/settings");
    await expect(author.getByRole("tab")).toHaveText(["Account", "Backup"]);
    expect((await author.goto("/admin/audit"))?.status()).toBe(404);
    // The admin keeps every section and the costs page.
    await page.goto("/admin/settings");
    await expect(page.getByRole("tab", { name: "Provider" })).toBeVisible();
    await expect(page.getByRole("tabpanel")).toContainText("Amministratore · accesso con password");
    expect((await page.goto("/admin/audit"))?.status()).toBe(200);
    await expect(page).toHaveURL(/\/admin\/settings#ai/);
    await rc.close();
    await ac.close();
  });

  test("a reader reads a course and its draft without changing anything; an author reads an admin's published course", async ({ page, browser, baseURL }) => {
    test.setTimeout(180_000);
    await login(page);

    // The reader («Lettore») opens the admin's course: no settings, publishing, new lessons or assistant anywhere.
    const { context: rc, p: reader } = await invited(page, browser, baseURL!, "viewer", "studente");
    const con = watchConsole(reader);
    await reader.goto("/admin/courses");
    await expect(reader.getByTestId("course-owner").first()).toContainText(`di ${USER}`);
    await expect(reader.locator(".tree-course").filter({ hasText: "Algebra Lineare" })).toContainText(`di ${USER}`);
    await reader.getByRole("link", { name: /Algebra Lineare/ }).first().click();
    await reader.waitForURL(/\/admin\/courses\/\d+$/);
    const overview = reader.getByTestId("course-overview");
    await expect(overview).toContainText(`di ${USER}`);
    await expect(overview.getByText("Pubblicata")).toBeVisible();
    await expect(overview.locator(".pub-ctl")).toHaveCount(0);
    await expect(overview.getByRole("button", { name: "Impostazioni della materia" })).toHaveCount(0);
    await expect(overview.getByRole("link", { name: "Nuova lezione" })).toHaveCount(0);
    await overview.getByRole("link", { name: "Apri il testo" }).first().click();
    await reader.waitForURL(/\/admin\/courses\/\d+\/testo$/);
    await expect(reader.getByTestId("read-only")).toHaveText(`Sola lettura · di ${USER}`);
    await expect(reader.getByRole("button", { name: "Assistente AI" })).toHaveCount(0);
    await expect(reader.locator(".pub-ctl")).toHaveCount(0);
    const outline = reader.getByRole("navigation", { name: "Struttura del documento" });
    await expect(outline.getByText("Spazi vettoriali")).toBeVisible();
    await expect(outline.getByRole("button", { name: "Aggiungi capitolo" })).toHaveCount(0);
    await expect(outline.getByRole("button", { name: /Azioni sul capitolo/ })).toHaveCount(0);
    await expect(outline.getByRole("link", { name: "Nuova lezione" })).toHaveCount(0);
    // The draft is there to read: a click picks nothing, a double click opens nothing.
    const block = reader.getByTestId("doc-preview").locator(".doc-block:not(.doc-heading):has(img)").first();
    await expect(block).toBeVisible({ timeout: 60_000 });
    await block.click();
    await block.dblclick();
    await expect(reader.getByTestId("selection-toolbar")).toHaveCount(0);
    await expect(reader.getByTestId("block-editor")).toHaveCount(0);
    await reader.getByRole("button", { name: "Altre azioni" }).click();
    await expect(reader.getByRole("button", { name: "Impostazioni della materia" })).toHaveCount(0);
    await expect(reader.getByRole("link", { name: "Nuova lezione" })).toHaveCount(0);
    await expect(reader.getByRole("link", { name: "Lezioni di questa materia" })).toBeVisible();
    // Only the PDF already compiled: nothing is compiled for a reader.
    await reader.keyboard.press("Escape");
    await reader.getByRole("tab", { name: "PDF" }).click();
    await expect(reader.locator(".pdfpane-err")).toHaveCount(0);
    con.assertClean();

    // An author («Autore») reads the admin's published course the same way, and doesn't see its lessons.
    const { context: ac, p: author } = await invited(page, browser, baseURL!, "editor", "collega2");
    const con2 = watchConsole(author);
    await author.reload();
    await expect(author.locator(".tree-course").filter({ hasText: "Algebra Lineare" })).toContainText(`di ${USER}`);
    await openCourse(author, /Algebra Lineare/);
    await expect(author.getByTestId("read-only")).toHaveText(`Sola lettura · di ${USER}`);
    await expect(author.getByRole("button", { name: "Assistente AI" })).toHaveCount(0);
    await expect(author.getByRole("navigation", { name: "Struttura del documento" }).getByText("Fonti")).toHaveCount(0);
    const para = author.getByTestId("doc-preview").locator(".doc-block:not(.doc-heading):has(img)").first();
    await expect(para).toBeVisible({ timeout: 60_000 });
    await para.dblclick();
    await expect(author.getByTestId("block-editor")).toHaveCount(0);
    await author.goBack();
    await expect(author.getByTestId("course-overview")).toBeVisible();
    await expect(author.getByTestId("course-overview").getByText("Lezioni", { exact: true })).toHaveCount(0);
    con2.assertClean();
    // The admin's own course says nothing of an owner, and keeps every control.
    await openCourse(page, /Algebra Lineare/);
    await expect(page.getByTestId("read-only")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Assistente AI" })).toBeVisible();
    await rc.close();
    await ac.close();
  });
});
