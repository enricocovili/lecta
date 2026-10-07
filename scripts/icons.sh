#!/usr/bin/env bash
# Draw the app's icon (home screen, installed app) as PNG pictures from the mark in frontend/src/lib/brand.ts, into
# frontend/public/icons/. Run it after changing the logo: the frontend tests fail until the pictures match the drawing.
# Needs Node (24+) and the app image (lecta-app:local, for PyMuPDF).
set -euo pipefail
cd "$(dirname "$0")/.."
out=frontend/public/icons
mkdir -p "$out"
brand='import("./frontend/src/lib/brand.ts").then((m) => process.stdout.write(JSON.stringify({ svg: m.appIconSvg(), sizes: m.ICON_SIZES, version: m.BRAND_VERSION })))'
node -e "$brand" | docker run --rm -i -v "$PWD/$out:/out" --user "$(id -u):$(id -g)" --entrypoint python lecta-app:local -c '
import json, sys, pymupdf
b = json.load(sys.stdin)
page = pymupdf.open(stream=b["svg"].encode(), filetype="svg")[0]
for n in b["sizes"]:
    z = n / page.rect.width
    page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False).save(f"/out/icon-{n}.png")
with open("/out/version.txt", "w") as f:
    f.write(b["version"] + "\n")
print("icons", b["version"], *b["sizes"])
'
