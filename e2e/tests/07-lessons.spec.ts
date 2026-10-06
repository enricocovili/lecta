import { expect, test, type Page } from "@playwright/test";
import { login, watchConsole } from "./helpers";

const EXPECTED = [/status of 404/, /status of 409/];

interface ApiLesson {
  id: number;
  number: number;
  status: string;
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

/** A finger dragged upwards by `dy` px over a page's drawing surface, all at once; returns how far the lesson scrolled.
 *  The pen (its events on the page, outside the slides) meanwhile: `hover` it is just above the screen, `writing` it touches
 *  the screen, `lifted` it has just left it, `after` it lands while the finger is already scrolling. */
async function fingerDrag(page: Page, index: number, dy: number, pen: "hover" | "writing" | "lifted" | "after"): Promise<number> {
  return page
    .getByTestId("ink-surface")
    .nth(index)
    .evaluate(
      async (el, [dy, pen]) => {
        const scroller = el.closest(".les-scroll")!;
        const before = scroller.scrollTop;
        const r = el.getBoundingClientRect();
        const x = r.left + r.width / 2;
        const y = r.top + r.height * 0.7;
        const fire = (on: Element, type: string, pointerType: string, cy: number) =>
          on.dispatchEvent(
            new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: pointerType === "pen" ? 52 : 51, pointerType, isPrimary: true, clientX: x, clientY: cy, button: 0, buttons: type === "pointerup" ? 0 : 1, pressure: 0.5 }),
          );
        const body = document.body;
        if (pen === "hover") fire(body, "pointermove", "pen", 5);
        if (pen === "writing" || pen === "lifted") fire(body, "pointerdown", "pen", 5);
        if (pen === "lifted") fire(body, "pointerup", "pen", 5);
        fire(el, "pointerdown", "touch", y);
        for (let i = 1; i <= 10; i++) fire(el, "pointermove", "touch", y - (dy * i) / 10);
        if (pen === "after") fire(body, "pointerdown", "pen", 5);
        fire(el, "pointerup", "touch", y - dy);
        if (pen === "writing" || pen === "after") fire(body, "pointerup", "pen", 5);
        // Measured before the glide that follows a flick starts (at the next frame).
        const moved = scroller.scrollTop - before;
        await new Promise((ok) => setTimeout(ok, 50));
        return moved;
      },
      [dy, pen] as const,
    );
}

/** Two fingers on a page's slide, `from` px apart around its middle, moved to `to` px apart; the first one draws a little
 *  before the second lands. More pinches follow at once, 30 ms apart (zooming fast). Returns how far (px) the point of the
 *  slide that was first between them ended up from there (`off`), and how far the point under the last pinch moved when the
 *  fingers were lifted, from where the zooming pages showed it (`jump`): at most, right away and once the rows settle. */
async function pinch(page: Page, index: number, ...moves: [number, number][]): Promise<{ off: number; jump: number }> {
  return page
    .getByTestId("ink-surface")
    .nth(index)
    .evaluate(
      async (el, moves) => {
        const r0 = el.getBoundingClientRect();
        const cx = r0.left + r0.width / 2;
        const cy = r0.top + r0.height / 2;
        const fire = (type: string, id: number, x: number) =>
          el.dispatchEvent(
            new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: id, pointerType: "touch", isPrimary: id === 61, clientX: x, clientY: cy, button: 0, buttons: type === "pointerup" ? 0 : 1, pressure: 0.5 }),
          );
        // A point of the slide (in its widths from its corner) and where it is on screen now, zoomed pages included.
        const at = (p: { u: number; v: number }) => {
          const r = el.getBoundingClientRect();
          return { x: r.left + p.u * r.width, y: r.top + p.v * r.width };
        };
        const under = () => {
          const r = el.getBoundingClientRect();
          return { u: (cx - r.left) / r.width, v: (cy - r.top) / r.width };
        };
        const first = under();
        let last = first;
        let shown = { x: cx, y: cy };
        for (const [from, to] of moves) {
          last = under();
          fire("pointerdown", 61, cx - from / 2);
          fire("pointermove", 61, cx - from / 2 - 6);
          fire("pointermove", 61, cx - from / 2);
          fire("pointerdown", 62, cx + from / 2);
          for (let i = 1; i <= 5; i++) {
            const d = from + ((to - from) * i) / 5;
            fire("pointermove", 61, cx - d / 2);
            fire("pointermove", 62, cx + d / 2);
          }
          shown = at(last);
          fire("pointerup", 61, cx - to / 2);
          fire("pointerup", 62, cx + to / 2);
          await new Promise((ok) => setTimeout(ok, 30));
        }
        const measure = () => {
          const a = at(first);
          const b = at(last);
          return { off: Math.hypot(a.x - cx, a.y - cy), jump: Math.hypot(b.x - shown.x, b.y - shown.y) };
        };
        await new Promise((ok) => requestAnimationFrame(() => requestAnimationFrame(ok)));
        const now = measure();
        await new Promise((ok) => setTimeout(ok, 600));
        const later = measure();
        return { off: Math.max(now.off, later.off), jump: Math.max(now.jump, later.jump) };
      },
      moves,
    );
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
    await page.waitForURL(/\/admin\/courses\/\d+(\/testo)?$/);
    courseId = /\/admin\/courses\/(\d+)/.exec(page.url())![1];

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
    // Every slide gets a small picture too, shown while the sharp one is drawn (zooming, scrolling to it).
    await expect(page.locator("img.les-slide-thumb")).toHaveCount(3);
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

    // The finger scrolls at once, also with the pen hovering just above the screen; a touch while the pen writes, or right
    // after a stroke, is the palm; the pen landing while a finger scrolls takes that scroll back (it was the palm too).
    await page.getByTestId("ink-surface").nth(1).scrollIntoViewIfNeeded();
    await page.waitForTimeout(400); // the last pen stroke is long gone
    expect(await fingerDrag(page, 1, 60, "hover")).toBeGreaterThan(40);
    expect(await fingerDrag(page, 1, 60, "writing")).toBe(0);
    expect(await fingerDrag(page, 1, 60, "lifted")).toBe(0);
    expect(await fingerDrag(page, 1, 60, "after")).toBe(0);
    await page.waitForTimeout(400);
    expect(await fingerDrag(page, 1, -60, "hover")).toBeLessThan(-40);
    expect((await stored(page)).pages[1].ink.length).toBe(1);
    // The synthetic drag is instantaneous, so its glide is long: once it is over, the second page goes back in the middle.
    await page.waitForTimeout(2500);
    await page.getByTestId("lesson-page").nth(1).evaluate((el) => el.scrollIntoView({ block: "center" }));
    await expect(page.getByTestId("page-number")).toContainText("2 /");

    // Two fingers zoom around the point between them, which stays where it was; with «Dito scrive» on, the stroke the first
    // finger began is dropped. Ctrl + wheel (a touchpad's pinch) zooms too, and the percentage brings it back to 100%.
    const zoomLevel = page.getByTestId("zoom-level");
    await expect(zoomLevel).toHaveText("100%");
    await page.getByRole("button", { name: "Dito scrive" }).click();
    const first = await pinch(page, 1, [100, 150]);
    expect(first.off).toBeLessThan(3);
    expect(first.jump).toBeLessThan(3);
    await expect(zoomLevel).toHaveText("150%");
    await page.getByRole("button", { name: "Dito scrive" }).click();
    await save(page);
    expect((await stored(page)).pages[1].ink.length).toBe(1);
    const zoomed = (await page.getByTestId("ink-surface").nth(1).boundingBox())!;
    await page.mouse.move(zoomed.x + zoomed.width / 2, zoomed.y + zoomed.height / 2);
    await page.keyboard.down("Control");
    await page.mouse.wheel(0, 100);
    await page.keyboard.up("Control");
    await expect(zoomLevel).toHaveText("117%");
    // The buttons zoom around the middle of the editor, which stays at the same height of the slide (sideways the pages may
    // have to move: at 100 % they cannot scroll).
    const middle = () =>
      page.getByTestId("ink-surface").nth(1).evaluate((el) => {
        const r = el.getBoundingClientRect();
        const s = document.querySelector("[data-testid=lesson-scroll]")!.getBoundingClientRect();
        return { u: (s.left + s.width / 2 - r.left) / r.width, v: (s.top + s.height / 2 - r.top) / r.width };
      });
    for (const button of ["Ingrandisci", "Riduci", "Riduci"]) {
      const before = await middle();
      await page.getByRole("button", { name: button, exact: true }).click();
      await page.waitForTimeout(600);
      const after = await middle();
      const width = (await page.getByTestId("ink-surface").nth(1).boundingBox())!.width;
      expect(Math.abs(after.v - before.v) * width).toBeLessThan(3);
    }
    await expect(zoomLevel).toHaveText("85%");
    await zoomLevel.click();
    await expect(zoomLevel).toHaveText("100%");
    // Pinching fast, in and out again before the last zoom has settled, stays on the same point too.
    await page.getByTestId("lesson-page").nth(1).evaluate((el) => el.scrollIntoView({ block: "center" }));
    const fast = await pinch(page, 1, [120, 200], [200, 120], [150, 200]);
    expect(fast.off).toBeLessThan(3);
    expect(fast.jump).toBeLessThan(3);
    await expect(zoomLevel).toHaveText("133%");
    // Zoomed out below 100 % the pages are centred, not under the fingers: they are shown centred while zooming, so lifting
    // the fingers moves nothing.
    await zoomLevel.click();
    await page.getByTestId("lesson-page").nth(1).evaluate((el) => el.scrollIntoView({ block: "center" }));
    expect((await pinch(page, 1, [200, 120])).jump).toBeLessThan(3);
    await expect(zoomLevel).toHaveText("60%");
    // At the top of the lesson the first page cannot come further down: zooming out it stays at the top while zooming too.
    await zoomLevel.click();
    await page.getByTestId("lesson-scroll").evaluate((el) => el.scrollTo(0, 0));
    expect((await pinch(page, 0, [200, 100])).jump).toBeLessThan(3);
    await expect(zoomLevel).toHaveText("50%");
    await zoomLevel.click();
    await page.getByTestId("lesson-page").nth(1).evaluate((el) => el.scrollIntoView({ block: "center" }));

    // A blank page after the slide, to write more than it has room for.
    await page.getByTestId("add-page").click();
    await expect(pages).toHaveCount(4);
    await expect(pages.nth(3).locator("textarea")).toBeVisible();

    // Nothing is lost on reload, and the lesson opens on the page that was open last.
    await expect(page.getByTestId("page-number")).toContainText("3 /");
    await page.getByRole("button", { name: "Pagina precedente" }).click();
    await expect(page.getByTestId("page-number")).toContainText("2 /");
    await page.getByRole("button", { name: "Pagina successiva" }).click();
    await expect(page.getByTestId("page-number")).toContainText("3 /");
    // The page being looked at sits in the middle of the editor (once the scroll has ended).
    await expect
      .poll(async () => {
        const row = (await page.locator("#lesson-page-" + (await stored(page)).pages[2].id).boundingBox())!;
        const area = (await page.getByTestId("lesson-scroll").boundingBox())!;
        return Math.abs(row.y + row.height / 2 - (area.y + area.height / 2));
      }, { timeout: 5_000 })
      .toBeLessThan(30);
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

  test("the «Completata» switch marks a lesson completed by hand, in the list and in the editor", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await page.goto(`/admin/lessons?course=${courseId}`);
    const row = page.getByTestId("lesson-row");
    const href = (await row.locator("a.pg-row-title").getAttribute("href"))!;
    const toggle = row.getByRole("switch", { name: "Completata" });
    await expect(toggle).toBeVisible();
    await expect(toggle).not.toBeChecked();
    await row.getByText("Completata").click();
    await expect(toggle).toBeChecked();
    await expect.poll(async () => (await stored(page, href)).status).toBe("completed");
    await page.reload();
    await expect(page.getByTestId("lesson-row").getByRole("switch", { name: "Completata" })).toBeChecked();

    // In the editor's top bar the same switch puts it back in progress.
    await page.goto(href);
    const inEditor = page.getByTestId("lesson-editor").getByRole("switch", { name: "Completata" });
    await expect(inEditor).toBeChecked();
    await page.getByTestId("lesson-status").getByText("Completata").click();
    await expect(inEditor).not.toBeChecked();
    await expect.poll(async () => (await stored(page)).status).toBe("working");
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
    await expect(dialog.getByTestId("generate-go")).toContainText("Integra appunti");
    await expect(dialog.getByTestId("generate-complete")).toBeChecked(); // «Segna la lezione come completata», on by default
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
    await page.goto(`/admin/courses/${courseId}`);
    await expect(page.getByTestId("overview-lesson")).toContainText("nel testo dal");

    // The annotated PDF has the three slides plus the page added by hand.
    const href = (await page.getByTestId("overview-lesson").locator("a.pg-row-title").getAttribute("href"))!;
    expect(href).toBe(`/admin/courses/${courseId}/lessons/1`);
    const pdf = await page.request.get(`/api/lessons/${(await stored(page, href)).id}/annotated.pdf`);
    expect(pdf.status()).toBe(200);
    expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");
    con.assertClean(EXPECTED);
  });

  test("the course page lists the lessons with their state and the chapters, each one click away", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await page.goto(`/admin/courses/${courseId}`);
    const lesson = page.getByTestId("overview-lesson");
    await expect(lesson).toHaveCount(1);
    await expect(lesson).toContainText("Lezione 1 - Sistemi LTI");
    await expect(lesson.getByRole("switch")).toBeChecked(); // its text is in the course: completed
    // No lesson has a lab yet: the section says where labs come from.
    const lab = page.getByTestId("overview-lab");
    await expect(lab).toContainText("Ancora nessun laboratorio");
    await expect(lab.locator("a, button")).toHaveCount(0);
    const chapter = page.getByTestId("overview-chapter").first();
    const href = (await chapter.getAttribute("href"))!;
    expect(href).toMatch(new RegExp(`^/admin/courses/${courseId}/testo\\?chapter=\\d+$`));
    await chapter.click();
    await page.waitForURL(/\/admin\/courses\/\d+\/testo\?chapter=\d+$/);
    await expect(page.getByTestId("doc-preview")).toBeVisible();
    // The text leads back to the course page, and the course page to a lesson.
    await page.getByRole("link", { name: "Torna alla materia" }).click();
    await page.waitForURL(new RegExp(`/admin/courses/${courseId}$`));
    await page.getByTestId("overview-lesson").getByRole("link", { name: "Lezione 1 - Sistemi LTI" }).click();
    await page.waitForURL(/\/admin\/courses\/\d+\/lessons\/1$/);
    con.assertClean(EXPECTED);
  });

  test("Ctrl+Z undoes the last thing done whatever it was: drawing, text, a removed slide", async ({ page }) => {
    test.setTimeout(120_000);
    const con = watchConsole(page);
    await login(page);
    await page.goto(`/admin/lessons?course=${courseId}`);
    await page.getByTestId("lesson-row").locator("a.pg-row-title").click();
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

  test("the select tool picks strokes with a click or a rectangle, drags them elsewhere, deletes them, all undoable", async ({ page }) => {
    test.setTimeout(120_000);
    const con = watchConsole(page);
    await login(page);
    await page.goto(`/admin/lessons?course=${courseId}`);
    await page.getByTestId("lesson-row").locator("a.pg-row-title").click();
    await page.waitForURL(/\/admin\/courses\/\d+\/lessons\/\d+$/);
    const pages = page.getByTestId("lesson-page");
    const count = (await stored(page)).pages.length;
    await expect(pages).toHaveCount(count);

    // A blank page at the end with two strokes on it.
    await page.getByRole("button", { name: "Pagina bianca in fondo" }).click();
    await expect(pages).toHaveCount(count + 1);
    const last = count;
    const surface = page.getByTestId("ink-surface").nth(last);
    const ink = async () => (await stored(page)).pages[last].ink as { p: number[] }[];
    await page.getByTestId("tool-pen").click();
    await scribble(page, last, [0.1, 0.15], [0.3, 0.25]);
    await scribble(page, last, [0.6, 0.6], [0.8, 0.7]);
    await save(page);
    const drawn = await ink();
    expect(drawn).toHaveLength(2);
    const at = async (fx: number, fy: number) => {
      const b = (await surface.boundingBox())!;
      return [b.x + b.width * fx, b.y + b.height * fy] as const;
    };

    // A click picks the stroke under it; dragging it moves it (by a tenth of the page's width here).
    await page.keyboard.press("s");
    await expect(page.getByTestId("tool-select")).toHaveAttribute("aria-pressed", "true");
    const count1 = page.getByTestId("selection-count");
    await expect(count1).toContainText("Clic su un tratto");
    const [ax, ay] = await at(0.1, 0.15);
    await page.mouse.click(ax, ay);
    await expect(count1).toHaveText("1 tratto selezionato");
    const width = (await surface.boundingBox())!.width;
    await page.mouse.move(ax, ay);
    await page.mouse.down();
    await page.mouse.move(ax + width * 0.05, ay, { steps: 4 });
    await page.mouse.move(ax + width * 0.1, ay, { steps: 4 });
    await page.mouse.up();
    await save(page);
    const moved = await ink();
    expect(moved[0].p[0]).toBeCloseTo(drawn[0].p[0] + 0.1, 2);
    expect(moved[0].p[1]).toBeCloseTo(drawn[0].p[1], 2);
    expect(moved[1]).toEqual(drawn[1]);
    await expect(count1).toHaveText("1 tratto selezionato");

    // Undo puts it back where it was, redo moves it again.
    await page.keyboard.press("Control+z");
    await save(page);
    expect((await ink())[0]).toEqual(drawn[0]);
    await page.keyboard.press("Control+Shift+z");
    await save(page);
    expect((await ink())[0].p[0]).toBeCloseTo(drawn[0].p[0] + 0.1, 2);

    // A click on an empty spot lets go; a rectangle around both picks both; Canc deletes them, undo brings them back.
    const [ex, ey] = await at(0.5, 0.9);
    await page.mouse.click(ex, ey);
    await expect(count1).toContainText("Clic su un tratto");
    const [rx0, ry0] = await at(0.02, 0.03);
    const [rx1, ry1] = await at(0.97, 0.95);
    await page.mouse.move(rx0, ry0);
    await page.mouse.down();
    await page.mouse.move(rx1, ry1, { steps: 8 });
    await page.mouse.up();
    await expect(count1).toHaveText("2 tratti selezionati");
    await page.keyboard.press("Delete");
    await expect(count1).toContainText("Clic su un tratto");
    await save(page);
    expect(await ink()).toHaveLength(0);
    await page.keyboard.press("Control+z");
    await save(page);
    expect(await ink()).toHaveLength(2);

    // The toolbar's button deletes too.
    await page.mouse.move(rx0, ry0);
    await page.mouse.down();
    await page.mouse.move(rx1, ry1, { steps: 8 });
    await page.mouse.up();
    await page.getByTestId("delete-selection").click();
    await save(page);
    expect(await ink()).toHaveLength(0);

    // The page goes away again, so the lesson is as it was.
    await pages.nth(last).getByTestId("remove-page").click();
    await page.keyboard.press("Enter");
    await expect(pages).toHaveCount(count);
    await page.getByTestId("tool-pen").click();
    con.assertClean(EXPECTED);
  });

  test("a lesson shared with a link: readers only look and follow, the write link edits, a revoked link stops", async ({ page, browser, baseURL }) => {
    test.setTimeout(180_000);
    const con = watchConsole(page);
    await login(page);
    await page.goto(`/admin/lessons?course=${courseId}`);
    await page.getByTestId("lesson-row").locator("a.pg-row-title").click();
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
      await page.getByTestId("lesson-row").locator("a.pg-row-title").click();
      await page.waitForURL(/\/admin\/courses\/\d+\/lessons\/\d+$/);
      await expect(page.getByTestId("lesson-page").first().locator("canvas.les-slide-canvas")).toBeVisible();
      await expect(page.getByTestId("tool-pen")).toBeVisible();
      await expect(page.getByTestId("generate-open")).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, "horizontal overflow in the lesson editor").toBeLessThanOrEqual(2);
      // The top bar fits too, with a readable title; what has no room in it is in the «…» menu.
      const bar = page.getByTestId("lesson-editor").locator("header.wsb");
      expect(await bar.evaluate((el) => el.scrollWidth - el.clientWidth), "the top bar scrolls sideways").toBeLessThanOrEqual(0);
      expect((await page.getByLabel("Titolo della lezione").boundingBox())!.width).toBeGreaterThan(250);
      await expect(page.getByTestId("share-open")).toBeHidden();
      await bar.getByRole("button", { name: "Altre azioni" }).click();
      const menu = bar.getByRole("menu");
      await expect(menu.getByRole("link", { name: "Scarica il PDF con le tue scritte" })).toBeVisible();
      await expect(menu.getByRole("button", { name: "Condividi" })).toBeVisible();
      await expect(menu.getByRole("button", { name: "Tema chiaro / scuro" })).toBeVisible();
      // Slide above, notes below (a single column).
      const slide = (await page.getByTestId("lesson-page").first().locator(".les-slide").boundingBox())!;
      const notes = (await page.getByTestId("lesson-page").first().locator("textarea").boundingBox())!;
      expect(notes.y).toBeGreaterThan(slide.y + slide.height - 2);
      con.assertClean(EXPECTED);
    });
  });
});
