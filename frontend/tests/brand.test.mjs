// The logo is drawn once (src/lib/brand.ts): the home-screen pictures must be those of the current drawing. Run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { appIconSvg, BRAND_VERSION, faviconSvg, ICON_SIZES, MARK } from "../src/lib/brand.ts";

const icons = new URL("../public/icons/", import.meta.url);

test("the app's pictures were drawn from the current logo (otherwise run scripts/icons.sh)", () => {
  assert.equal(readFileSync(new URL("version.txt", icons), "utf8").trim(), BRAND_VERSION);
  for (const n of ICON_SIZES) {
    const png = readFileSync(new URL(`icon-${n}.png`, icons));
    assert.equal(png.toString("latin1", 12, 16), "IHDR");
    assert.deepEqual([png.readUInt32BE(16), png.readUInt32BE(20)], [n, n]);
  }
});

test("the favicon and the app's icon are the mark", () => {
  assert.ok(faviconSvg().includes(MARK));
  assert.ok(appIconSvg().includes(MARK));
  assert.match(faviconSvg(), /prefers-color-scheme:dark/);
});
