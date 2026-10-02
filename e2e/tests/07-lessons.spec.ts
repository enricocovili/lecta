import { expect, test, type Page } from "@playwright/test";
import { login, watchConsole } from "./helpers";

const EXPECTED = [/status of 404/, /status of 409/];

interface ApiLesson {
  id: number;
  number: number;
  last_page_id: number | null;
  pages: { id: number; kind: string; notes: string; ink: unknown[] }[];
  course_guidelines: string;
  generated_at: string | null;
}

/** The lesson's address is /admin/courses/<course>/lessons/<n>. */
async function stored(page: Page, url = page.url()): Promise<ApiLesson> {
  const [, course, n] = /\/admin\/courses\/(\d+)\/lessons\/(\d+)/.exec(url)!;
  const res = await page.request.get(`/api/courses/${course}/lessons/${n}`);
  return (await res.json()) as ApiLesson;
}

/** Ctrl+S: saves the lesson (the automatic save runs once a minute). */
async function save(page: Page) {
  await page.keyboard.press("Control+s");
  await expect(page.getByTestId("save-state")).toContainText("Salvato");
}

/** Drag the mouse over the first page of the lesson (a diagonal and a bend). */
async function scribble(page: Page, index: number, from: [number, number], to: [number, number]) {
  const surface = page.getByTestId("ink-surface").nth(index);
  await surface.scrollIntoViewIfNeeded();
  const box = (await surface.boundingBox())!;
  const x = (f: number) => box.x + box.width * f;
  const y = (f: number) => box.y + box.height * f;
  await page.mouse.move(x(from[0]), y(from[1]));
  await page.mouse.down();
  await page.mouse.move(x((from[0] + to[0]) / 2), y(from[1] + 0.05), { steps: 6 });
  await page.mouse.move(x(to[0]), y(to[1]), { steps: 6 });
  await page.mouse.up();
}

test.describe.serial("Lezioni", () => {
  let courseId = "";

  test("a lesson from the slides: write notes, draw over the slide, everything is saved", async ({ page }) => {
    test.setTimeout(180_000);
    const con = watchConsole(page);
    await login(page);
    await page.goto("/admin/courses?new=1");
    await page.getByLabel("Nome").fill("Elettronica");
    await page.getByRole("button", { name: "Crea materia" }).click();
    await page.waitForURL(/\/admin\/courses\/\d+$/);
    courseId = page.url().split("/").pop()!;

    // New lesson with the slides.
    await page.goto(`/admin/lessons?course=${courseId}`);
    await expect(page.getByText("Nessuna lezione")).toBeVisible();
    await page.getByTestId("new-lesson").click();
    await page.getByTestId("lesson-title").fill("Lezione 1 - Sistemi LTI");
    await page.getByTestId("lesson-pdf").setInputFiles("/e2e/.fixtures/slides.pdf");
    await page.getByTestId("lesson-title").press("Enter"); // Enter confirms the dialog
    await page.waitForURL(/\/admin\/courses\/\d+\/lessons\/\d+$/);

    // Every slide is a page with its drawing surface and its notes field.
    const pages = page.getByTestId("lesson-page");
    await expect(pages).toHaveCount(3);
    await expect(page.getByTestId("page-number")).toContainText("/ 3");
    await expect(pages.first().locator("canvas.les-slide-canvas")).toBeVisible();
    await expect(page.getByTestId("save-state")).toContainText("Salvato");

    // Markdown notes: Enter continues the list, an empty item ends it.
    const notes = pages.first().locator("textarea");
    await notes.click();
    await page.keyboard.type("- campionamento");
    await page.keyboard.press("Enter");
    await page.keyboard.type("Nyquist: $f_s > 2B$");
    await page.keyboard.press("Enter");
    await page.keyboard.press("Enter");
    await page.keyboard.type("Fine.");
    await expect(notes).toHaveValue("- campionamento\n- Nyquist: $f_s > 2B$\nFine.");

    // Draw with the pen over the first slide; undo takes it away, redo brings it back.
    await scribble(page, 0, [0.15, 0.2], [0.6, 0.45]);
    await page.getByTestId("tool-hl").click();
    await scribble(page, 0, [0.1, 0.6], [0.5, 0.6]);
    await page.getByTestId("undo").click();
    await page.getByTestId("redo").click();
    await expect(page.getByTestId("save-state")).toContainText("Da salvare");
    await save(page);
    await expect.poll(async () => (await stored(page)).pages[0].ink.length, { timeout: 15_000 }).toBe(2);
    expect((await stored(page)).pages[0].notes).toBe("- campionamento\n- Nyquist: $f_s > 2B$\nFine.");
    await expect(page.getByTestId("save-state")).toContainText("Salvato");

    // The eraser removes the stroke it touches, not the others.
    await page.getByTestId("tool-eraser").click();
    await scribble(page, 0, [0.1, 0.6], [0.5, 0.6]);
    await save(page);
    await expect.poll(async () => (await stored(page)).pages[0].ink.length, { timeout: 15_000 }).toBe(1);

    // A finger scrolls instead of writing, unless "Dito scrive" is on; the pen always writes.
    await page.getByTestId("tool-pen").click();

    // Shapes: a hand-drawn line becomes a straight one (two points); with «Forme» off it stays as drawn.
    const underline = async (y: number) => {
      const surf = page.getByTestId("ink-surface").nth(2);
      await surf.scrollIntoViewIfNeeded();
      const b = (await surf.boundingBox())!;
      await page.mouse.move(b.x + b.width * 0.1, b.y + b.height * y);
      await page.mouse.down();
      for (let i = 1; i <= 12; i++) await page.mouse.move(b.x + b.width * (0.1 + i * 0.04), b.y + b.height * (y + (i % 2 ? 0.004 : -0.004)));
      await page.mouse.up();
    };
    await expect(page.getByTestId("shapes-toggle")).toHaveAttribute("aria-pressed", "true");
    await underline(0.8);
    await page.getByTestId("shapes-toggle").click();
    await underline(0.9);
    await save(page);
    await expect.poll(async () => (await stored(page)).pages[2].ink.map((s) => (s as { p: number[] }).p.length), { timeout: 15_000 }).toHaveLength(2);
    const lengths = (await stored(page)).pages[2].ink.map((s) => (s as { p: number[] }).p.length);
    expect(lengths[0]).toBe(6);
    expect(lengths[1]).toBeGreaterThan(20);
    await page.getByTestId("shapes-toggle").click();
    const surface = page.getByTestId("ink-surface").nth(1);
    await surface.scrollIntoViewIfNeeded();
    const at = { clientX: 100, clientY: 100, isPrimary: true, pointerId: 41, button: 0, buttons: 1, bubbles: true };
    const touch = async (type: string) => surface.dispatchEvent(type, { ...at, pointerType: "touch", pressure: 0.5 });
    await touch("pointerdown");
    await touch("pointermove");
    await touch("pointerup");
    const penAt = { ...at, pointerType: "pen", pointerId: 42, pressure: 0.7 };
    const box = (await surface.boundingBox())!;
    for (const [type, dx] of [["pointerdown", 0.2], ["pointermove", 0.3], ["pointermove", 0.4], ["pointerup", 0.4]] as const) {
      await surface.dispatchEvent(type, { ...penAt, clientX: box.x + box.width * dx, clientY: box.y + box.height * (0.3 + dx / 4) });
    }
    await save(page);
    await expect.poll(async () => (await stored(page)).pages[1].ink.length, { timeout: 15_000 }).toBe(1);

    // A blank page after the slide, to write more than it has room for.
    await page.getByTestId("add-page").click();
    await expect(pages).toHaveCount(4);
    await expect(pages.nth(3).locator("textarea")).toBeVisible();

    // Nothing is lost on reload, and the lesson opens on the page that was open last.
    await page.locator(".les-rail-item").nth(2).click();
    await expect(page.getByTestId("page-number")).toContainText("3 /");
    const third = (await stored(page)).pages[2].id;
    await expect.poll(async () => (await stored(page)).last_page_id, { timeout: 10_000 }).toBe(third);
    await save(page);
    await page.reload();
    await expect(pages).toHaveCount(4);
    await expect(page.getByTestId("page-number")).toContainText("3 /", { timeout: 10_000 });
    await expect(pages.first().locator("textarea")).toHaveValue("- campionamento\n- Nyquist: $f_s > 2B$\nFine.");
    await expect(page.getByTestId("lesson-editor")).toBeVisible();
    con.assertClean(EXPECTED);
  });

  test("Integra appunti asks for the guidelines, or says the text will be automatic, then writes it into the subject", async ({ page }) => {
    test.setTimeout(300_000);
    const con = watchConsole(page);
    await login(page);
    await page.goto(`/admin/lessons?course=${courseId}`);
    await expect(page.getByTestId("lesson-row")).toHaveCount(1);
    await page.getByTestId("lesson-row").getByRole("link", { name: "Lezione 1 - Sistemi LTI" }).click();
    await page.waitForURL(/\/admin\/courses\/\d+\/lessons\/\d+$/);

    await page.getByTestId("generate-open").click();
    const dialog = page.getByRole("dialog");
    // No guidelines yet: the notice says the text will be generated automatically.
    await expect(dialog.getByTestId("guidelines-notice")).toContainText("genererò il testo in automatico");
    await expect(dialog.getByTestId("generate-go")).toContainText("Integra in automatico");
    await dialog.getByTestId("guidelines").fill("Scrivi in modo discorsivo, con un esempio per ogni definizione.");
    await expect(dialog.getByTestId("guidelines-notice")).toHaveCount(0);
    await expect(dialog.getByTestId("generate-go")).toContainText("Integra con queste linee guida");
    await dialog.getByTestId("generate-go").click();
    await page.waitForURL(/\/admin\/jobs\/\d+$/);
    await expect(page.locator(".page-head [data-status]").first()).toHaveAttribute("data-status", "succeeded", { timeout: 240_000 });
    await expect(page.getByText(/^Nuovo capitolo «.*» in «Elettronica»/)).toBeVisible({ timeout: 30_000 });

    // The guidelines stay with the subject, and the lesson remembers what it produced.
    const settings = await page.request.get(`/api/courses/${courseId}`);
    expect((await settings.json()).guidelines).toContain("discorsivo");
    await page.goto("/admin/lessons");
    await expect(page.getByTestId("lesson-row")).toContainText("testo generato il");

    // The annotated PDF has the three slides plus the page added by hand.
    const href = (await page.getByTestId("lesson-row").getByRole("link", { name: "Apri" }).getAttribute("href"))!;
    expect(href).toBe(`/admin/courses/${courseId}/lessons/1`);
    const pdf = await page.request.get(`/api/lessons/${(await stored(page, href)).id}/annotated.pdf`);
    expect(pdf.status()).toBe(200);
    expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");
    con.assertClean(EXPECTED);
  });

  test("Ctrl+Z undoes the last thing done whatever it was: drawing, text, a removed slide", async ({ page }) => {
    test.setTimeout(120_000);
    const con = watchConsole(page);
    await login(page);
    await page.goto(`/admin/lessons?course=${courseId}`);
    await page.getByTestId("lesson-row").getByRole("link", { name: "Apri" }).click();
    await page.waitForURL(/\/admin\/courses\/\d+\/lessons\/\d+$/);
    const pages = page.getByTestId("lesson-page");
    const before = await stored(page);
    const inkBefore = before.pages[1].ink.length;
    const count = before.pages.length;
    await expect(pages).toHaveCount(count);

    // Text first, then a drawing: Ctrl+Z (even with the cursor still in the note) takes the drawing away first, then the text.
    const notes = pages.nth(1).locator("textarea");
    await notes.click();
    await page.keyboard.type("appunto nuovo");
    await scribble(page, 1, [0.2, 0.7], [0.5, 0.85]);
    await notes.focus();
    await page.keyboard.press("Control+z");
    await expect(notes).toHaveValue("appunto nuovo");
    await save(page);
    await expect.poll(async () => (await stored(page)).pages[1].ink.length, { timeout: 15_000 }).toBe(inkBefore);
    await notes.focus();
    await page.keyboard.press("Control+z");
    await expect(notes).toHaveValue("");
    await page.keyboard.press("Control+Shift+z");
    await page.keyboard.press("Control+Shift+z");
    await expect(notes).toHaveValue("appunto nuovo");
    await save(page);
    await expect.poll(async () => (await stored(page)).pages[1].ink.length, { timeout: 15_000 }).toBe(inkBefore + 1);
    expect((await stored(page)).pages[1].notes).toBe("appunto nuovo");

    // A removed slide asks in a dialog of the app (Enter confirms) and comes back with Ctrl+Z, notes and strokes included.
    const third = (await stored(page)).pages[2];
    await pages.nth(2).getByTestId("remove-page").click();
    await expect(page.getByRole("dialog")).toContainText("esce dalla lezione");
    await page.keyboard.press("Enter");
    await expect(pages).toHaveCount(count - 1);
    await page.keyboard.press("Control+z");
    await expect(pages).toHaveCount(count);
    const after = await stored(page);
    expect(after.pages.map((x) => x.id)).toEqual(before.pages.map((x) => x.id));
    expect(after.pages[2].ink.length).toBe(third.ink.length);
    await page.keyboard.press("Control+Shift+z");
    await expect(pages).toHaveCount(count - 1);
    await page.keyboard.press("Control+z");
    await expect(pages).toHaveCount(count);
    con.assertClean(EXPECTED);
  });

  test("a lesson shared with a link: readers only look and follow, the write link edits, a revoked link stops", async ({ page, browser, baseURL }) => {
    test.setTimeout(180_000);
    const con = watchConsole(page);
    await login(page);
    await page.goto(`/admin/lessons?course=${courseId}`);
    await page.getByTestId("lesson-row").getByRole("link", { name: "Apri" }).click();
    await page.waitForURL(/\/admin\/courses\/\d+\/lessons\/\d+$/);
    const pages = page.getByTestId("lesson-page");
    const count = (await stored(page)).pages.length;
    await expect(pages).toHaveCount(count);

    await page.getByTestId("share-open").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByTestId("share-make-read").click();
    await dialog.getByTestId("share-make-write").click();
    const readUrl = new URL(await dialog.getByTestId("share-url-read").inputValue());
    const writeUrl = new URL(await dialog.getByTestId("share-url-write").inputValue());
    expect(readUrl.pathname).toMatch(/^\/s\/[\w-]{30,}$/);
    expect(writeUrl.pathname).not.toBe(readUrl.pathname);
    await dialog.getByRole("button", { name: "Fatto" }).click();

    // A reader (not signed in): slides and rendered notes, no tools, no text fields.
    const reader = await browser.newContext({ baseURL });
    const r = await reader.newPage();
    const rcon = watchConsole(r);
    await r.goto(readUrl.pathname);
    await expect(r.getByTestId("lesson-page")).toHaveCount(count);
    await expect(r.getByTestId("lesson-page").first()).toContainText("campionamento");
    await expect(r.getByTestId("tool-pen")).toHaveCount(0);
    await expect(r.locator("textarea")).toHaveCount(0);
    await expect(r.getByTestId("generate-open")).toHaveCount(0);
    await expect(r.getByTestId("share-open")).toHaveCount(0);

    // What the owner writes shows up on the reader's screen.
    const notes = pages.first().locator("textarea");
    await notes.click();
    await notes.press("Control+End");
    await page.keyboard.type(" aggiunto dal proprietario");
    await save(page);
    await expect(r.getByTestId("lesson-page").first()).toContainText("aggiunto dal proprietario", { timeout: 30_000 });

    // A writer: edits the notes like the owner, and the owner sees it.
    const writer = await browser.newContext({ baseURL });
    const w = await writer.newPage();
    const wcon = watchConsole(w);
    await w.goto(writeUrl.pathname);
    await expect(w.getByTestId("lesson-page")).toHaveCount(count);
    const wnotes = w.getByTestId("lesson-page").nth(2).locator("textarea");
    await wnotes.click();
    await w.keyboard.type("scritto da un amico");
    await w.keyboard.press("Control+s");
    await expect(w.getByTestId("save-state")).toContainText("Salvato");
    await expect.poll(async () => (await stored(page)).pages[2].notes, { timeout: 15_000 }).toContain("scritto da un amico");
    await expect(pages.nth(2).locator("textarea")).toHaveValue(/scritto da un amico/, { timeout: 30_000 });

    // Revoked: the open page says so, and the address no longer opens the lesson.
    await page.getByTestId("share-open").click();
    await page.getByRole("dialog").getByTestId("share-revoke-read").click();
    await expect(page.getByRole("dialog").getByTestId("share-make-read")).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Fatto" }).click();
    await expect(r.getByRole("alert")).toContainText("non è più valido", { timeout: 30_000 });
    await r.reload();
    await expect(r.getByText("non è valido o è stato revocato")).toBeVisible();
    await reader.close();
    await writer.close();
    con.assertClean(EXPECTED);
    rcon.assertClean([/status of 404/]);
    wcon.assertClean(EXPECTED);
  });

  test.describe("on a phone", () => {
    test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

    test("the editor fits the screen: tools reachable, the slide and its notes stacked", async ({ page }) => {
      const con = watchConsole(page);
      await login(page);
      await page.goto(`/admin/lessons?course=${courseId}`);
      await page.getByTestId("lesson-row").getByRole("link", { name: "Apri" }).click();
      await page.waitForURL(/\/admin\/courses\/\d+\/lessons\/\d+$/);
      await expect(page.getByTestId("lesson-page").first().locator("canvas.les-slide-canvas")).toBeVisible();
      await expect(page.getByTestId("tool-pen")).toBeVisible();
      await expect(page.getByTestId("generate-open")).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, "horizontal overflow in the lesson editor").toBeLessThanOrEqual(2);
      // Slide above, notes below (a single column).
      const slide = (await page.getByTestId("lesson-page").first().locator(".les-slide").boundingBox())!;
      const notes = (await page.getByTestId("lesson-page").first().locator("textarea").boundingBox())!;
      expect(notes.y).toBeGreaterThan(slide.y + slide.height - 2);
      con.assertClean(EXPECTED);
    });
  });
});
