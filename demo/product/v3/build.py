"""Compose the product prototype pages: src/<page>.html (content fragment) -> <page>.html (full page).

Fragment header comments:
  <!-- title: … -->   browser title
  <!-- crumbs: … -->  breadcrumb HTML
  <!-- role: … -->    developer (default) | client | operator — picks the sidebar
  <!-- nav: … -->     sidebar item to highlight when the page is not itself in the sidebar
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent
HEAD = (ROOT / "_head.html").read_text()

SHELL = """<body data-page="{page}" data-role="{role}"{nav}>
<div class="shell">
<aside class="nav" id="nav"></aside>
<main>
  <div class="topbar">
    <div class="crumbs">{crumbs}</div>
    <div class="cmd">Search or jump to…<kbd>⌘K</kbd></div>
    <div class="right"><button class="pill demo-status" type="button" data-demo-status>Demo data</button></div>
  </div>
  <div class="content">
{content}
  </div>
</main>
</div>
<script src="app.js"></script>
<script src="../versions.js" data-version="v3"></script>
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
        nav = meta(src, "nav", "")
        body = re.sub(r"<!--\s*(title|crumbs|role|nav):.*?-->\n?", "", src)
        html = HEAD.replace("<title>TITLE</title>", f"<title>{title}</title>") + SHELL.format(
            page=frag.name,
            role=meta(src, "role", "developer"),
            nav=f' data-nav="{nav}"' if nav else "",
            crumbs=meta(src, "crumbs", title),
            content=body.rstrip(),
        )
        (ROOT / frag.name).write_text(html)
        print("built", frag.name)


if __name__ == "__main__":
    build()
