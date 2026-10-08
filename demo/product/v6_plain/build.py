"""Compose the v6 Plain pages: src/<page>.html (content fragment) -> <page>.html (full page).

Fragments may contain {{chart:name}} placeholders (see charts.py).
"""
from __future__ import annotations

import re
from pathlib import Path

from charts import CHARTS

ROOT = Path(__file__).parent
HEAD = (ROOT / "_head.html").read_text()

SHELL = """<body data-page="{page}">
<div class="shell" id="shell">
<aside class="nav" id="nav"></aside>
<main>
  <div class="topbar">
    <div class="crumbs">{crumbs}</div>
    <div class="right">
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
</div>
<script src="app.js"></script>
<script src="guide.js"></script>
</body>
</html>
"""


def meta(src: str, key: str, default: str) -> str:
    m = re.search(rf"<!--\s*{key}:\s*(.*?)\s*-->", src)
    return m.group(1) if m else default


def build() -> None:
    for frag in sorted((ROOT / "src").glob("*.html")):
        src = frag.read_text()
        title = meta(src, "title", frag.stem)
        crumbs = meta(src, "crumbs", title)
        body = re.sub(r"<!--\s*(title|crumbs):.*?-->\n?", "", src)
        body = re.sub(r"\{\{chart:(\w+)\}\}", lambda m: CHARTS[m.group(1)](), body)
        html = HEAD.replace("<title>TITLE</title>", f"<title>{title}</title>") + SHELL.format(
            page=frag.name, crumbs=crumbs, content=body.rstrip()
        )
        (ROOT / frag.name).write_text(html)
        print("built", frag.name)


if __name__ == "__main__":
    build()
