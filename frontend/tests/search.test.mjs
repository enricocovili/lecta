// Searching a lesson (Ctrl+F): run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { findAll, firstFrom, fold, foldQuery, inkTextRects, searchLesson, slideRects, slideText } from "../src/components/lessons/search.ts";

const q = foldQuery;

test("capitals, accents, ligatures and runs of spaces do not count", () => {
  assert.deepEqual(findAll("Perché no? PERCHE sì", q("perche")), [[0, 6], [11, 17]]);
  assert.deepEqual(findAll("La ﬁne", q("fine")), [[3, 6]]);
  const text = "principio  di\n   sovrapposizione";
  const [[a, b]] = findAll(text, q("di sovrapp"));
  assert.equal(text.slice(a, b), "di\n   sovrapp");
});

test("an empty or blank query finds nothing; occurrences do not overlap", () => {
  assert.deepEqual(findAll("abc", q("")), []);
  assert.deepEqual(findAll("abc", q("   ")), []);
  assert.deepEqual(findAll("aaaa", q("aa")), [[0, 2], [2, 4]]);
});

test("the folded text knows where every char comes from", () => {
  const f = fold("Ça  va");
  assert.equal(f.text, "ca va");
  assert.deepEqual(f.from, [0, 1, 2, 4, 5]);
  assert.deepEqual(f.to, [1, 2, 4, 5, 6]);
});

// A slide 100 points wide: the page is 100 points wide, so a PDF point is 0.01 page widths (y flipped, 75 points tall).
const toPage = (x, y) => [x / 100, (75 - y) / 100];
const items = [
  { str: "Linear", transform: [10, 0, 0, 10, 10, 60], width: 30, hasEOL: false },
  { str: "ita", transform: [10, 0, 0, 10, 40, 60], width: 15, hasEOL: true },
  { str: "Convoluzione", transform: [10, 0, 0, 10, 10, 40], width: 60, hasEOL: false },
];

test("the pieces of a slide make one text; a word split between two pieces is found and boxed in both", () => {
  const { text, starts } = slideText(items);
  assert.equal(text, "Linearita Convoluzione");
  assert.deepEqual(starts, [0, 6, 10]);
  const [r] = findAll(text, q("linearità"));
  const rects = slideRects(items, starts, r, toPage);
  assert.equal(rects.length, 2);
  assert.ok(Math.abs(rects[0].x0 - 0.1) < 1e-9 && Math.abs(rects[1].x1 - 0.55) < 1e-9);
  // From under the baseline (y = 60 → 0.15 down the page) to the top of the letters.
  assert.ok(rects[0].y0 < 0.15 - 0.08 && rects[0].y1 > 0.15);
});

test("a range inside a piece takes its share of the width", () => {
  const { text, starts } = slideText(items);
  const [r] = findAll(text, q("voluz"));
  const [box] = slideRects(items, starts, r, toPage);
  assert.ok(Math.abs(box.x0 - (0.1 + 0.6 * (3 / 12))) < 1e-9);
  assert.ok(Math.abs(box.x1 - (0.1 + 0.6 * (8 / 12))) < 1e-9);
});

test("typed text: one box per line the range covers, a turned text its whole box", () => {
  const s = { t: "text", c: "#000000", w: 0.02, p: [0.1, 0.2, 0], s: "uno due\ntre" };
  const rects = inkTextRects(s, [4, 11]);
  assert.equal(rects.length, 2);
  assert.ok(rects[0].x0 > 0.1 && Math.abs(rects[0].y0 - 0.2) < 1e-9);
  assert.ok(Math.abs(rects[1].x0 - 0.1) < 1e-9 && Math.abs(rects[1].y0 - (0.2 + 1.25 * 0.02)) < 1e-9);
  assert.equal(inkTextRects({ ...s, m: [0, 1, -1, 0] }, [0, 3]).length, 1);
});

test("the lesson: slide, then typed text, then notes, page after page; starting from the page being looked at", () => {
  const pages = [
    { id: 1, notes: "la convoluzione è commutativa", ink: [], slide: { items, toPage } },
    { id: 2, notes: "", ink: [{ t: "pen", c: "#000000", w: 0.003, p: [0, 0, 0.5] }, { t: "text", c: "#000000", w: 0.02, p: [0.1, 0.1, 0], s: "Convoluzione!" }], slide: null },
    { id: 3, notes: "Niente", ink: [], slide: null },
  ];
  const hits = searchLesson(pages, "CONVOLUZIONE");
  assert.deepEqual(
    hits.map((h) => [h.pid, h.where]),
    [[1, "slide"], [1, "notes"], [2, "ink"]],
  );
  assert.deepEqual(hits[1].range, [3, 15]);
  assert.equal(firstFrom(hits, [1, 2, 3], 1), 2);
  assert.equal(firstFrom(hits, [1, 2, 3], 2), 0); // nothing after: back to the first
  assert.equal(firstFrom([], [1], 0), -1);
});
