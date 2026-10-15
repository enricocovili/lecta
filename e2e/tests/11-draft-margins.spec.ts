import { expect, test, type Page } from "@playwright/test";
import { login, watchConsole } from "./helpers";

const LONG = "una frase che non può andare a capo perché è chiusa in una scatola ".repeat(3);
const TEXT = [
  "\\chapter{Margini}",
  "",
  `Prima \\underline{${LONG}} fine.`,
  "",
  `Poi \\ul{${LONG}} e \\hl{${LONG}} fine.`,
  "",
  "\\begin{tabular}{lll}",
  `${LONG} & ${LONG} & colonna \\\\`,
  "\\end{tabular}",
  "",
  "Un paragrafo normale.",
  "",
].join("\n");

/** A course whose only chapter has blocks LaTeX can't fit in the text width; → the address of its draft. */
async function courseWithWideBlocks(page: Page): Promise<string> {
  return page.evaluate(async (text) => {
    const csrf = document.cookie.match(/lecta_csrf=([^;]+)/)?.[1] ?? "";
    const headers = { "content-type": "application/json", "x-csrf-token": csrf };
    const r = await fetch("/api/courses", { method: "POST", headers, body: JSON.stringify({ name: "Margini stretti", chapters: ["Margini"] }) });
    if (!r.ok) throw new Error(await r.text());
    const course = (await r.json()) as { id: number; chapters: { path: string }[] };
    const res = await fetch(`/api/courses/${course.id}/files/content`, {
      method: "PUT",
      headers,
      body: JSON.stringify({ path: course.chapters[0].path, content: text }),
    });
    if (!res.ok) throw new Error(await res.text());
    return `/admin/courses/${course.id}/testo`;
  }, TEXT);
}

/** Every picture of the draft lies within the page (the article), and the document does not scroll sideways. */
async function expectInsideThePage(page: Page) {
  const doc = page.getByTestId("doc-preview");
  await expect(doc.locator(".doc-block img").first()).toBeVisible({ timeout: 90_000 });
  await expect(doc.locator(".doc-block:has(img)")).toHaveCount(5, { timeout: 90_000 });
  const out = await page.evaluate(() => {
    const article = document.querySelector("article.doc")!.getBoundingClientRect();
    const scroller = document.querySelector(".doc-scroll")!;
    const wide = [...document.querySelectorAll<HTMLImageElement>(".doc-block img")]
      .map((img) => img.getBoundingClientRect())
      .filter((r) => r.left < article.left - 1 || r.right > article.right + 1)
      .map((r) => `${Math.round(r.left)}–${Math.round(r.right)} outside ${Math.round(article.left)}–${Math.round(article.right)}`);
    return { wide, scroll: scroller.scrollWidth - scroller.clientWidth, page: document.documentElement.scrollWidth - window.innerWidth };
  });
  expect(out.wide).toEqual([]);
  expect(out.scroll).toBeLessThanOrEqual(1);
  expect(out.page).toBeLessThanOrEqual(2);
}

test("a block LaTeX can't fit stays inside the page, shrunk, and its author is told", async ({ page }) => {
  const con = watchConsole(page);
  await login(page);
  const draft = await courseWithWideBlocks(page);

  await page.goto(draft);
  await expectInsideThePage(page);
  // The \underline and the table run past the margin in the PDF: the draft says so, with how far.
  const marks = page.getByTestId("block-overflow");
  await expect(marks).toHaveCount(2);
  await expect(marks.first()).toContainText(/Esce dal margine di \d+(,\d)? mm/);
  // \ul and \hl break across lines like normal text.
  const ul = page.locator(".doc-block").filter({ has: page.locator('img[alt^="Poi \\\\ul"]') });
  await expect(ul.getByTestId("block-overflow")).toHaveCount(0);

  // On a phone too.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expectInsideThePage(page);
  con.assertClean();
});
