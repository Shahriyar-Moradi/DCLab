"""Compose the v7 Final product pages: src/<page>.html (content fragment) -> <page>.html (full page).

Fragments may contain {{chart:name}} placeholders (see charts.py) and these meta comments:
  <!-- title: ... -->  <!-- crumbs: ... -->
  <!-- status: ok|warn|off|plan | text -->   (one or more status tags under the title)
  <!-- planned: 1 -->                          (adds the "Preview of a planned screen" banner)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from charts import CHARTS  # noqa: E402

ROOT = Path(__file__).parent
HEAD = (ROOT / "_head.html").read_text()

SHELL = """<body data-page="{page}">
<div class="shell" id="shell">
<aside class="nav" id="nav"></aside>
<main>
  <div class="topbar">
    <div class="crumbs">{crumbs}</div>
    <div class="right">
      <label class="role-sw" for="role-switch"><span>View as</span><select id="role-switch"><option value="ds">Data scientist</option><option value="admin">Admin</option><option value="biz">Business user</option></select></label>
      <button class="tbtn asst" type="button" id="asst-toggle" aria-pressed="false">Assistant</button>
      <button class="ai-switch" type="button" id="ai-switch" role="switch" aria-checked="false"><span>AI suggestions: <b>off</b></span><i aria-hidden="true"></i></button>
      <button class="tbtn" type="button" data-guide-toggle>Guide</button>
      <button class="tbtn" type="button" data-glossary>Glossary</button>
    </div>
  </div>
  <div class="content">
{content}
  </div>
</main>
<aside class="ai-panel" id="ai-panel" aria-label="AI suggestions" hidden></aside>
<aside class="ai-panel asst" id="asst-panel" aria-label="Assistant" hidden></aside>
</div>
<script src="app.js"></script>
<script src="guide.js"></script>
</body>
</html>
"""


def meta(src: str, key: str, default: str) -> str:
    m = re.search(rf"<!--\s*{key}:\s*(.*?)\s*-->", src)
    return m.group(1) if m else default


def status_row(src: str) -> str:
    tags = re.findall(r"<!--\s*status:\s*(\w+)\s*\|\s*(.*?)\s*-->", src)
    if not tags:
        return ""
    html = "".join(
        f'<a class="stag {kind}" href="real.html" title="See what is real today">{text}</a>' for kind, text in tags
    )
    if "<!-- planned: 1 -->" in src:
        html += '<span class="preview-banner"><b>Preview of a planned screen.</b> Numbers are examples.</span>'
    return f'\n<div class="statusrow">{html}</div>'


def build() -> None:
    for frag in sorted((ROOT / "src").glob("*.html")):
        src = frag.read_text()
        title = meta(src, "title", frag.stem)
        crumbs = meta(src, "crumbs", title)
        row = status_row(src)
        body = re.sub(r"<!--\s*(title|crumbs|status|planned):.*?-->\n?", "", src)
        body = re.sub(r"\{\{chart:(\w+)\}\}", lambda m: CHARTS[m.group(1)](), body)
        if row:
            start = body.index('<div class="page-head">')
            end = body.index("\n</div>", start) + len("\n</div>")
            body = body[:end] + row + body[end:]
        html = HEAD.replace("<title>TITLE</title>", f"<title>{title}</title>") + SHELL.format(
            page=frag.name, crumbs=crumbs, content=body.rstrip()
        )
        (ROOT / frag.name).write_text(html)
        print("built", frag.name)


if __name__ == "__main__":
    build()
