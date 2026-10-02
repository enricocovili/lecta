// The notes part of the undo history: run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { applyPatch, caretAfter, diffPatch, mergeable } from "../src/components/lessons/history.ts";

test("a patch turns the text into the new one and back", () => {
  const cases = [
    ["", "ciao"],
    ["ciao", ""],
    ["ciao mondo", "ciao bel mondo"],
    ["aaa", "aaaa"],
    ["- uno\n- due", "- uno\n- due\n- tre"],
    ["x😀y", "x😀😀y"],
    ["abc", "abc"],
  ];
  for (const [a, b] of cases) {
    const p = diffPatch(a, b);
    assert.equal(applyPatch(a, p), b, `${a} → ${b}`);
    assert.equal(applyPatch(b, p, true), a, `${b} → ${a}`);
  }
});

test("a patch keeps only what changed", () => {
  assert.deepEqual(diffPatch("ciao mondo", "ciao bel mondo"), { at: 5, del: "", ins: "bel " });
  assert.equal(caretAfter(diffPatch("ciao mondo", "ciao bel mondo")), 9);
  assert.equal(caretAfter(diffPatch("ciao mondo", "ciao bel mondo"), true), 5);
});

test("only small edits right after each other are merged", () => {
  const small = diffPatch("a", "ab");
  assert.ok(mergeable(small, 1000, 1500));
  assert.ok(!mergeable(small, 1000, 2500), "a pause starts a new step");
  assert.ok(!mergeable(diffPatch("a", "a" + "x".repeat(100)), 1000, 1100), "a paste is a step of its own");
});
