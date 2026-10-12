// A reader's notes on the draft, placed on its blocks: run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { noteProblem, placeNotes } from "../src/components/workspace/notes.ts";

const block = (start, end, src) => ({ start, end, src, pages: [], error: null });
const note = (id, start, src, extra = {}) => ({ id, chapter_id: 1, kind: "comment", status: "open", src, start, end: start, changed: false, body: "x", proposed: null, mine: true, ...extra });

const blocks = [block(1, 1, "\\chapter{Spazi}"), block(3, 4, "Un vettore è\nun elemento."), block(6, 6, "Una base.")];

test("a note sits on the block with its first line and its text", () => {
  const { onBlock, apart } = placeNotes(blocks, [note(1, 3, "Un vettore è\nun elemento."), note(2, 3, "Un vettore è\nun elemento."), note(3, 6, "Una base.")]);
  assert.deepEqual([...onBlock.keys()], [1, 2]);
  assert.deepEqual(onBlock.get(1).map((n) => n.id), [1, 2]);
  assert.deepEqual(apart, []);
});

test("a note whose text changed, or whose block isn't at its lines any more, is apart", () => {
  const { onBlock, apart } = placeNotes(blocks, [
    note(1, 3, "Un vettore è\nun elemento.", { changed: true }),
    note(2, 6, "Una base di tre vettori.", { status: "accepted" }), // closed: where it was, the text is another one
    note(3, 9, "Una base."),
  ]);
  assert.equal(onBlock.size, 0);
  assert.deepEqual(apart.map((n) => n.id), [1, 2, 3]);
});

test("the draft's text of a long block is cut short: the note's full text still matches it", () => {
  const long = "a".repeat(50);
  const { onBlock } = placeNotes([block(1, 2, long.slice(0, 20))], [note(1, 1, long)]);
  assert.deepEqual([...onBlock.keys()], [0]);
  assert.equal(placeNotes([block(1, 2, "")], [note(1, 1, long)]).apart.length, 1);
});

test("a comment needs words, a suggestion a change to the block", () => {
  assert.equal(noteProblem("comment", "  ", "", "src"), "Scrivi il commento");
  assert.equal(noteProblem("comment", "Non torna", "", "src"), null);
  assert.match(noteProblem("suggestion", "", "src", "src"), /Cambia il testo/);
  assert.equal(noteProblem("suggestion", "", "nuovo", "src"), null);
  assert.equal(noteProblem("suggestion", "", "", "src"), null); // empty: take the block out
  assert.equal(noteProblem("suggestion", "", "src"), null); // a note being changed: its text may stay
});
