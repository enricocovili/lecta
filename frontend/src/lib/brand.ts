// The Lecta mark, drawn once: the header's logo, the favicon, the manifest's icon and the home-screen pictures
// (scripts/icons.sh) all come from here, so they can't drift apart. The URLs carry `BRAND_VERSION`, so a browser that
// remembers an older icon fetches the new one as soon as the drawing changes.

/** The area of the drawing (x, y, width, height). */
export const MARK_VIEWBOX = [257, 290, 760, 760] as const;

/** One half of the brain, mirrored for the other half. */
const BRAIN_HALF =
  '<path d="M803 692a50 50 0 0 0-91 36a52 52 0 0 0-40 94a55 55 0 0 0 18 116a58 58 0 0 0 113 44" stroke-width="30"/>' +
  '<path d="M742 812l38 42h23M733 906l35 37v37" stroke-width="16"/>' +
  '<circle cx="726" cy="793" r="19" stroke-width="16"/><circle cx="717" cy="890" r="19" stroke-width="16"/>';

/** A notebook with three lines, a sparkle and a circuit brain, in `currentColor`. */
export const MARK =
  '<g fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M594 897H402a62 62 0 0 1-62-62V405a62 62 0 0 1 62-62h336a62 62 0 0 1 62 62v232" stroke-width="36" stroke-linecap="butt"/>' +
  '<path d="M290 503h88M290 645h88M460 495h228M460 578h228M460 660h174" stroke-width="36"/>' +
  '<path d="M933 508q10 67 72 77q-62 10-72 80q-10-70-73-80q63-10 73-77z" fill="currentColor" stroke="none"/>' +
  `<g>${BRAIN_HALF}</g><g transform="matrix(-1 0 0 1 1606 0)">${BRAIN_HALF}</g>` +
  '<path d="M803 700v282" stroke-width="16"/>' +
  "</g>";

/** The colours of the mark on a light and on a dark background (the app's --fg). */
const INK = "#1c1b17";
const INK_DARK = "#f3f2ed";
const PAPER = "#fcfbf7";

/** A short hash of the drawing, for the icons' URLs. */
function hash(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193);
  return (h >>> 0).toString(36);
}

/** The favicon: the mark alone, dark, light on a dark browser theme (the tab bar's, not the app's). */
export function faviconSvg(): string {
  const style = `<style>g{color:${INK}}@media (prefers-color-scheme:dark){g{color:${INK_DARK}}}</style>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${MARK_VIEWBOX.join(" ")}">${style}${MARK}</svg>`;
}

/** The app's icon (home screen, installed app): the mark on the page colour, with room around it so that a launcher
 *  cutting it to a circle or a rounded square keeps all of the drawing. */
export function appIconSvg(): string {
  const [x, y, w] = MARK_VIEWBOX;
  const side = w / 0.68;
  const o = (side - w) / 2;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${x - o} ${y - o} ${side} ${side}">` +
    `<rect x="${x - o}" y="${y - o}" width="${side}" height="${side}" fill="${PAPER}"/>` +
    `<g color="${INK}">${MARK}</g></svg>`
  );
}

export const BRAND_VERSION = hash(MARK + PAPER + INK + INK_DARK);

/** The pictures of the app's icon made by scripts/icons.sh, at these sides (180 is the iPhone's and iPad's home screen). */
export const ICON_SIZES = [180, 192, 512] as const;
