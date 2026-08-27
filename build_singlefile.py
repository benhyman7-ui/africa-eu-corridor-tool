"""Inline everything into one self-contained HTML file.

Produces dist/corridor-tool.html: no server, no external requests, opens from a
file:// URL or an email attachment. Useful for sending the tool to someone who
will not clone a repo.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DIST = ROOT / "dist"


def main() -> None:
    DIST.mkdir(exist_ok=True)
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "style.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")

    payload = {name: json.loads((WEB / "data" / f"{name}.json").read_text(encoding="utf-8"))
               for name in ("corridors", "meta", "series", "sectors", "geo")}

    html = html.replace('<link rel="stylesheet" href="style.css?v=2">',
                        f"<style>\n{css}\n</style>")

    # The logo is a separate file on the hosted site; a standalone page has to
    # carry it inline or the brand mark silently 404s.
    logo = WEB / "assets" / "ajf-logo.svg"
    if logo.exists():
        b64 = base64.b64encode(logo.read_bytes()).decode("ascii")
        html = html.replace('src="assets/ajf-logo.svg"',
                            f'src="data:image/svg+xml;base64,{b64}"')
    mark = WEB / "assets" / "ajf-mark.svg"
    if mark.exists():
        m64 = base64.b64encode(mark.read_bytes()).decode("ascii")
        html = html.replace('href="assets/ajf-mark.svg"',
                            f'href="data:image/svg+xml;base64,{m64}"')
    for leftover in ("assets/ajf-logo.svg", "assets/ajf-mark.svg"):
        if leftover in html:
            print(f"    WARNING {leftover} not inlined")
    # </script> inside a JSON string would end the script element early
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace(
        '<script src="app.js?v=2"></script>',
        f"<script>window.__CORRIDOR_DATA__={data};</script>\n<script>\n{js}\n</script>")

    if 'href="style.css"' in html or 'src="app.js"' in html:
        raise RuntimeError("inlining failed - index.html markup changed")

    out = DIST / "corridor-tool.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}  ({out.stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
