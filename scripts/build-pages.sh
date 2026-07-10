#!/usr/bin/env bash
# Builds the static/ folder into dist/ for Cloudflare Pages.
#
# static/home.html (and terms.html / privacy.html) reference their CSS/JS as
# "static/home.css" because FastAPI serves them at "/" with /static mounted
# alongside. Cloudflare Pages just serves this directory flat with no server,
# so those paths need rewriting relative to a flat root, and home.html needs
# to become index.html so "/" resolves to it.
set -euo pipefail

cd "$(dirname "$0")/.."

SRC="static"
DIST="dist"

rm -rf "$DIST"
mkdir -p "$DIST"

cp "$SRC/home.html" "$DIST/index.html"
cp "$SRC/terms.html" "$DIST/terms.html"
cp "$SRC/privacy.html" "$DIST/privacy.html"
cp "$SRC/home.css" "$DIST/home.css"
cp "$SRC/home.js" "$DIST/home.js"
cp "$SRC/favicon.ico" "$DIST/favicon.ico"

sed -i.bak 's|static/home.css|home.css|g; s|static/home.js|home.js|g' \
  "$DIST/index.html" "$DIST/terms.html" "$DIST/privacy.html"
rm -f "$DIST"/*.bak

echo "Built static site into $DIST/"
