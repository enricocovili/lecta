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
    // The draft is there to read: a click offers only a note on the block (no assistant), a double click opens nothing.
    const block = reader.getByTestId("doc-preview").locator(".doc-block:not(.doc-heading):has(img)").first();
    await expect(block).toBeVisible({ timeout: 60_000 });
    await block.click();
    await expect(reader.getByTestId("selection-toolbar").getByRole("button")).toHaveText(["Commenta", "Suggerisci modifica"]);
    await reader.keyboard.press("Escape");
    await expect(reader.getByTestId("selection-toolbar")).toHaveCount(0);
    await block.dblclick();
    await expect(reader.getByTestId("block-editor")).toHaveCount(0);
    await reader.keyboard.press("Escape");
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

  test("a reader comments on and suggests a change to a block of the admin's published course, and finds their notes again", async ({ page, browser, baseURL }) => {
    test.setTimeout(180_000);
    await login(page);
    const { context: rc, p: reader } = await invited(page, browser, baseURL!, "viewer", "commentatore");
    const con = watchConsole(reader);
    await openCourse(reader, /Algebra Lineare/);
    const doc = reader.getByTestId("doc-preview");
    const block = doc.locator(".doc-block:not(.doc-heading):has(img)").first();
    await expect(block).toBeVisible({ timeout: 60_000 });
    const at = doc.locator(`.doc-block[data-line="${await block.getAttribute("data-line")}"]`);
    const toolbar = reader.getByTestId("selection-toolbar");
    const composer = reader.getByTestId("note-editor");

    // «Commenta»: a box for the words, Ctrl+Enter sends them; the block gets a marker.
    await block.click();
    await toolbar.getByRole("button", { name: "Commenta" }).click();
    const comment = composer.getByLabel("Commento");
    await expect(comment).toBeFocused();
    await comment.fill("Non è chiaro perché lo zero sta nello spazio.");
    await comment.press("Control+Enter");
    await expect(composer).toHaveCount(0);
    await expect(at.getByTestId("note-mark")).toBeVisible();

    // «Suggerisci modifica»: the block's LaTeX in the box of the writing in place, and why. Esc leaves it unsent.
    await block.click();
    await toolbar.getByRole("button", { name: "Suggerisci modifica" }).click();
    const latex = composer.getByLabel("Testo proposto");
    await expect(latex).toBeFocused();
    const src = await latex.inputValue();
    expect(src.trim().length).toBeGreaterThan(0);
    await latex.press("Escape");
    await expect(composer).toHaveCount(0);
    await block.click();
    await toolbar.getByRole("button", { name: "Suggerisci modifica" }).click();
    await expect(latex).toHaveValue(src);
    await latex.fill(`${src}\n\nPer esempio, i vettori del piano.`);
    await composer.getByLabel("Motivo").fill("Manca un esempio.");
    await latex.press("Control+Enter");
    await expect(composer).toHaveCount(0);
    await expect(at.getByTestId("note-mark")).toHaveText("2");

    // After a reload the marker is still there, and shows the notes with their state.
    await reader.reload();
    await expect(at.getByTestId("note-mark")).toHaveText("2", { timeout: 60_000 });
    await at.getByTestId("note-mark").click();
    const notes = reader.getByTestId("block-notes").getByTestId("draft-note");
    await expect(notes).toHaveCount(2);
    await expect(notes.nth(0)).toContainText("Non è chiaro perché lo zero sta nello spazio.");
    await expect(notes.nth(1)).toContainText("Manca un esempio.");
    await expect(notes.nth(1)).toContainText("Per esempio, i vettori del piano.");
    await expect(reader.getByTestId("block-notes").getByTestId("note-status")).toHaveText(["Aperta", "Aperta"]);

    // The comment is deleted: one note left on the block.
    await notes.nth(0).getByRole("button", { name: "Elimina" }).click();
    await reader.getByRole("dialog").getByRole("button", { name: "Elimina", exact: true }).click();
    await expect(notes).toHaveCount(1);
    await expect(notes.nth(0)).toContainText("Manca un esempio.");
    await expect(at.getByTestId("note-mark")).toHaveText("");
    con.assertClean();

    // On a course that isn't published (as if the admin had taken it back), the notes stay but take no new ones.
    await reader.route(/\/api\/courses\/\d+$/, async (route) => {
      const res = await route.fetch({ url: route.request().url().replace("//localhost:", "//lecta-frontend:") }); // the browser's localhost
      await route.fulfill({ response: res, json: { ...(await res.json()), published: false } });
    });
    await reader.reload();
    await expect(at.getByTestId("note-mark")).toBeVisible({ timeout: 60_000 });
    await block.click();
    await expect(toolbar).toHaveCount(0);
    await rc.close();
  });

  test("a reader opens a lesson of the admin's course read-only: no tools, nothing sent", async ({ page, browser, baseURL }) => {
    test.setTimeout(120_000);
    await login(page);
    // The admin's lesson in Algebra Lineare, with notes on its first page.
    const lesson = await page.evaluate(async () => {
      const csrf = document.cookie.match(/lecta_csrf=([^;]+)/)?.[1] ?? "";
      const headers = { "content-type": "application/json", "x-csrf-token": csrf };
      const course = ((await (await fetch("/api/courses")).json()) as { id: number; name: string }[]).find((c) => c.name.includes("Algebra Lineare"))!;
      const l = await (await fetch("/api/lessons", { method: "POST", headers, body: JSON.stringify({ course_id: course.id, title: "Lezione da leggere" }) })).json();
      const full = await (await fetch(`/api/lessons/${l.id}`)).json();
      await fetch(`/api/lessons/${l.id}/pages/${full.pages[0].id}`, { method: "PUT", headers, body: JSON.stringify({ notes: "Una **base** genera lo spazio." }) });
      return { course_id: course.id as number, number: l.number as number };
    });

    const { context: rc, p: reader } = await invited(page, browser, baseURL!, "viewer", "uditore");
    const con = watchConsole(reader);
    // In the lessons list: no switch for the state and no delete.
    await reader.goto(`/admin/lessons?course=${lesson.course_id}`);
    const row = reader.getByTestId("lesson-row").filter({ hasText: "Lezione da leggere" });
    await expect(row.getByTestId("lesson-status")).toHaveText("In corso");
    await expect(row.getByRole("switch")).toHaveCount(0);
    await expect(row.getByRole("button", { name: /Elimina/ })).toHaveCount(0);

    // Only reads go out from the lesson editor (no sync, no last page), and nothing is refused.
    const sent: string[] = [];
    reader.on("request", (r) => r.url().includes("/api/") && r.method() !== "GET" && sent.push(`${r.method()} ${r.url()}`));
    reader.on("response", (r) => r.url().includes("/api/") && r.status() >= 400 && sent.push(`${r.status()} ${r.url()}`));
    await row.locator("a.pg-row-title").click();
    await reader.waitForURL(/\/admin\/courses\/\d+\/lessons\/\d+$/);
    const editor = reader.getByTestId("lesson-editor");
    await expect(editor.getByTestId("lesson-page").first()).toContainText("Una base genera lo spazio.");
    await expect(editor.getByTestId("read-only")).toHaveText("Sola lettura");
    await expect(editor.locator("textarea")).toHaveCount(0);
    await expect(editor.getByLabel("Titolo della lezione")).toHaveAttribute("readonly", "");
    for (const id of ["tool-pen", "tool-text", "undo", "save-state", "add-page", "add-blank-page", "remove-page", "mark-unclear", "ask-page", "generate-open", "share-open"]) {
      await expect(editor.getByTestId(id)).toHaveCount(0);
    }
    await expect(editor.getByTestId("lesson-status").getByRole("switch")).toHaveCount(0);
    await reader.waitForTimeout(2500); // past the moment the last open page would be saved
    expect(sent).toEqual([]);
    con.assertClean();

    // The admin's editor keeps everything.
    await page.goto(`/admin/courses/${lesson.course_id}/lessons/${lesson.number}`);
    await expect(page.getByTestId("tool-pen")).toBeVisible();
    await expect(page.getByTestId("share-open")).toBeVisible();
    await expect(page.getByTestId("read-only")).toHaveCount(0);
    await rc.close();
  });

  test("a reader opens the lab of a lesson of the admin's course read-only: files, comments and notes, nothing sent", async ({ page, browser, baseURL }) => {
    test.setTimeout(120_000);
    await login(page);
    // A lesson of the admin's Algebra Lineare with a lab (a file, a comment on its lines, the notes), and one without.
    const lab = await page.evaluate(async () => {
      const csrf = document.cookie.match(/lecta_csrf=([^;]+)/)?.[1] ?? "";
      const headers = { "content-type": "application/json", "x-csrf-token": csrf };
      const course = ((await (await fetch("/api/courses")).json()) as { id: number; name: string }[]).find((c) => c.name.includes("Algebra Lineare"))!;
      const make = async (title: string) => (await fetch("/api/lessons", { method: "POST", headers, body: JSON.stringify({ course_id: course.id, title }) })).json();
      const l = await make("Laboratorio da leggere");
      const bare = await make("Lezione senza laboratorio");
      await fetch(`/api/lessons/${l.id}/lab`, { method: "POST", headers });
      const code = "import numpy as np\nA = np.eye(3)\nprint(np.linalg.det(A))\n";
      const f = await (
        await fetch(`/api/lessons/${l.id}/lab/files?path=${encodeURIComponent("src/det.py")}`, { method: "POST", headers: { "content-type": "application/octet-stream", "x-csrf-token": csrf }, body: code })
      ).json();
      const body = { file_id: f.id, anchor: { from: 2, to: 3, text: "A = np.eye(3)\nprint(np.linalg.det(A))" }, body: "Il determinante dell’**identità** è 1." };
      await fetch(`/api/lessons/${l.id}/lab/comments/${crypto.randomUUID()}`, { method: "PUT", headers, body: JSON.stringify(body) });
      await fetch(`/api/lessons/${l.id}/lab/notes`, { method: "PUT", headers, body: JSON.stringify({ notes: "Consegna entro **venerdì**." }) });
      return { course_id: course.id as number, number: l.number as number, bare: bare.number as number };
    });

    const { context: rc, p: reader } = await invited(page, browser, baseURL!, "viewer", "ospite");
    const con = watchConsole(reader);
    const sent: string[] = [];
    reader.on("request", (r) => r.url().includes("/api/") && r.method() !== "GET" && sent.push(`${r.method()} ${r.url()}`));
    reader.on("response", (r) => r.url().includes("/api/") && r.status() >= 400 && sent.push(`${r.status()} ${r.url()}`));
    // From the lesson to its lab.
    await reader.goto(`/admin/courses/${lab.course_id}/lessons/${lab.number}`);
    await reader.getByTestId("lesson-lab").click();
    await reader.waitForURL(/\/lab(\?.*)?$/);
    const editor = reader.getByTestId("lab-editor");
    await expect(editor.getByTestId("read-only")).toHaveText("Sola lettura");
    await expect(editor.getByTestId("lab-open-path")).toHaveText("src/det.py");
    await expect(editor.getByTestId("lab-code").locator(".cm-line").nth(1)).toContainText("np.eye(3)");
    const comment = editor.getByTestId("lab-comment");
    await expect(comment).toHaveCount(1);
    await expect(comment).toContainText("Righe 2–3");
    await expect(comment).toContainText("Il determinante dell’identità è 1.");
    // Nothing to change: no upload, rename, delete, edit, comments, assistant; the code is not editable.
    for (const id of ["lab-upload", "lab-file-input", "lab-save-state", "lab-ai-toggle", "lab-publish", "lab-edit", "lab-comment-file", "lab-comment-selection"]) {
      await expect(editor.getByTestId(id)).toHaveCount(0);
    }
    await expect(editor.getByRole("button", { name: /Rinomina|Elimina/ })).toHaveCount(0);
    await comment.click();
    await expect(comment.locator("textarea")).toHaveCount(0);
    const lines = editor.getByTestId("lab-code").locator(".cm-line");
    await lines.nth(0).click({ position: { x: 2, y: 5 } });
    await lines.nth(2).click({ modifiers: ["Shift"] });
    await expect(editor.getByTestId("lab-comment-selection")).toHaveCount(0);
    // The code can be selected (to copy it) but not typed into.
    await reader.keyboard.type("zzz");
    await expect(lines.nth(0)).toHaveText("import numpy as np");
    // The notes are shown, not edited.
    await editor.getByTestId("lab-notes-open").click();
    await expect(editor.getByTestId("lab-notes")).toContainText("Consegna entro venerdì.");
    await expect(editor.getByTestId("lab-notes").locator("textarea")).toHaveCount(0);
    await reader.waitForTimeout(1500);
    expect(sent).toEqual([]);
    con.assertClean();

    // A lesson without a lab offers no «Laboratorio», and its lab's address doesn't offer to make one.
    await reader.goto(`/admin/courses/${lab.course_id}/lessons/${lab.bare}`);
    await expect(reader.getByTestId("lesson-editor")).toBeVisible();
    await expect(reader.getByTestId("lesson-lab")).toHaveCount(0);
    await reader.goto(`/admin/courses/${lab.course_id}/lessons/${lab.bare}/lab`);
    await expect(reader.getByText("Questa lezione non ha un laboratorio.")).toBeVisible();
    await expect(reader.getByTestId("lab-create")).toHaveCount(0);

    // The admin's lab keeps everything.
    await page.goto(`/admin/courses/${lab.course_id}/lessons/${lab.number}/lab`);
    await expect(page.getByTestId("lab-upload")).toBeVisible();
    await expect(page.getByTestId("lab-ai-toggle")).toBeVisible();
    await expect(page.getByTestId("read-only")).toHaveCount(0);
    await rc.close();
  });
});
