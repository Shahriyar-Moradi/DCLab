"""Versioning for the product prototype.

Every version lives in its own folder (v1/, v2/, …) and is never edited once a newer
one exists. versions.json is the list. This script keeps the generated files in step.

  python3 demo/product/versions.py build
      Rebuild every version's pages, versions.js (the switcher shown on every page)
      and the landing page index.html.

  python3 demo/product/versions.py new "Name" "One-line summary" [--from vN] [--id vX]
      Freeze the latest version and start the next one as a copy of it
      (or of version vN, to build on an older one). --id names the folder
      (default: next number); ids are "v" + letters, digits, _ or -.
      Edit the new folder; the old ones stay as they were.
"""
from __future__ import annotations

import datetime
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
LIST = ROOT / "versions.json"
INCLUDE = '<script src="../versions.js" data-version="{id}"></script>'


def load() -> dict:
    return json.loads(LIST.read_text())


def pages(version_id: str) -> list[str]:
    return sorted(p.name for p in (ROOT / version_id).glob("*.html") if not p.name.startswith("_"))


def ensure_include(version_id: str) -> None:
    """Every page of a version loads ../versions.js with its own version id."""
    tag = INCLUDE.format(id=version_id)
    pattern = re.compile(r'<script src="\.\./versions\.js"[^>]*></script>')
    for path, anchor in ((ROOT / version_id / "build.py", '<script src="app.js"></script>'),
                         (ROOT / version_id / "index.html", "</body>")):
        text = path.read_text()
        if pattern.search(text):
            text = pattern.sub(tag, text)
        elif anchor == "</body>":
            text = text.replace(anchor, tag + "\n" + anchor, 1)
        else:
            text = text.replace(anchor, anchor + "\n" + tag, 1)
        path.write_text(text)


SWITCHER = r"""
(function () {
  var data = window.DCLAB_VERSIONS;
  var script = document.currentScript;
  var match = location.pathname.match(/\/(v[\w-]+)\/([^\/]*)$/);
  var current = (match && match[1]) || (script && script.getAttribute('data-version'));
  var file = (match && match[2]) || 'index.html';
  var here = data.versions.filter(function (v) { return v.id === current; })[0];
  if (!here) return;

  var style = document.createElement('style');
  style.textContent =
    '.dcv{position:fixed;right:18px;bottom:18px;z-index:90;font:600 14px/1.3 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}' +
    '.dcv-btn{font:inherit;display:inline-flex;align-items:center;gap:8px;padding:9px 14px;border-radius:999px;border:1px solid #CAD7D5;background:#fff;color:#15211E;cursor:pointer;box-shadow:0 6px 20px rgba(21,33,30,.12)}' +
    '.dcv-btn:hover,.dcv-btn:focus-visible{border-color:#1B88AE;outline:none}' +
    '.dcv-btn i{font-style:normal;color:#8B9A96}' +
    '.dcv-old .dcv-btn{background:#FBF1DD;border-color:#E3C98F}' +
    '.dcv-menu{position:absolute;right:0;bottom:calc(100% + 10px);width:min(340px,calc(100vw - 36px));background:#fff;border:1px solid #E3EAE9;border-radius:16px;padding:8px;box-shadow:0 20px 60px rgba(21,33,30,.2);display:grid;gap:2px}' +
    '.dcv-menu[hidden]{display:none}' +
    '.dcv-menu a{display:grid;gap:2px;padding:11px 12px;border-radius:10px;text-decoration:none;color:#15211E}' +
    '.dcv-menu a:hover,.dcv-menu a:focus-visible{background:#F4F8F8;outline:none}' +
    '.dcv-menu a[aria-current]{background:#EAF4EC}' +
    '.dcv-menu b{font-weight:650;display:flex;gap:8px;align-items:center}' +
    '.dcv-menu small{font-weight:400;font-size:13px;color:#586864;line-height:1.4}' +
    '.dcv-tag{font-size:12px;font-weight:650;color:#1F8A42;background:#E7F4EB;border-radius:999px;padding:1px 8px}' +
    '.dcv-all{border-top:1px solid #E3EAE9;margin-top:4px;border-radius:0 0 10px 10px!important;color:#1B88AE!important;font-weight:600}';
  document.head.appendChild(style);

  var box = document.createElement('div');
  box.className = 'dcv' + (current === data.latest ? '' : ' dcv-old');
  var number = current.replace(/^v/, '').replace(/_/g, ' ');
  var items = data.versions.map(function (v) {
    var target = '../' + v.id + '/' + (v.pages.indexOf(file) > -1 ? file : 'index.html');
    return '<a href="' + target + '"' + (v.id === current ? ' aria-current="true"' : '') + '><b>Version ' + v.id.replace('v', '') + ' · ' + v.name +
      (v.id === data.latest ? ' <span class="dcv-tag">Latest</span>' : '') + '</b><small>' + v.date + ' · ' + v.summary + '</small></a>';
  }).join('');
  box.innerHTML = '<div class="dcv-menu" hidden>' + items + '<a class="dcv-all" href="../index.html">All versions</a></div>' +
    '<button class="dcv-btn" type="button" aria-haspopup="true" aria-expanded="false">Version ' + number + ' · ' + here.name +
    (current === data.latest ? '' : ' (older)') + ' <i aria-hidden="true">▴</i></button>';
  document.body.appendChild(box);

  var button = box.querySelector('.dcv-btn');
  var menu = box.querySelector('.dcv-menu');
  function toggle(open) { menu.hidden = !open; button.setAttribute('aria-expanded', open ? 'true' : 'false'); }
  button.addEventListener('click', function () { toggle(menu.hidden); });
  document.addEventListener('click', function (event) { if (!box.contains(event.target)) toggle(false); });
  document.addEventListener('keydown', function (event) { if (event.key === 'Escape') toggle(false); });
})();
"""

LANDING = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>DCLab product preview</title>
<style>
  :root {{ color-scheme: light; --bg:#F4F8F8; --surface:#fff; --ink:#15211E; --muted:#586864; --faint:#8B9A96; --line:#E3EAE9; --accent:#1B88AE; --ok:#1F8A42; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif; -webkit-font-smoothing:antialiased; }}
  main {{ max-width: 880px; margin: 0 auto; padding: 48px 20px 72px; display: grid; gap: 36px; }}
  .brand {{ display:flex; align-items:center; gap:12px; font-weight:700; font-size:1.15rem; }}
  .logo {{ width:34px; height:34px; border-radius:10px; background:linear-gradient(145deg,#2D9C67,#1F8A42); color:#fff; display:grid; place-items:center; font-weight:800; font-size:.85rem; }}
  h1 {{ margin:0; font-size:clamp(2rem,5vw,2.8rem); letter-spacing:-.03em; line-height:1.1; text-wrap:balance; }}
  .sub {{ margin:12px 0 0; color:var(--muted); font-size:1.1rem; max-width:56ch; }}
  .card {{ background:var(--surface); border:1px solid var(--line); border-radius:18px; padding:28px 30px; display:grid; gap:14px; }}
  .latest {{ border-color: color-mix(in srgb, var(--ok) 35%, var(--line)); }}
  h2 {{ margin:0; font-size:1.4rem; letter-spacing:-.02em; display:flex; flex-wrap:wrap; align-items:center; gap:12px; }}
  .tag {{ font-size:.85rem; font-weight:650; color:var(--ok); background:color-mix(in srgb,var(--ok) 12%,#fff); border-radius:999px; padding:2px 11px; }}
  .meta {{ color:var(--faint); font-size:.95rem; }}
  p {{ margin:0; color:var(--muted); }}
  .btn {{ justify-self:start; font-weight:600; padding:11px 20px; border-radius:10px; background:var(--accent); color:#fff; text-decoration:none; }}
  .btn:hover, .btn:focus-visible {{ background:#16779A; outline:none; }}
  h3 {{ margin:0 0 4px; font-size:1rem; color:var(--faint); font-weight:600; }}
  .row {{ display:grid; grid-template-columns:minmax(0,1fr) auto; gap:6px 24px; align-items:center; padding:20px 0; border-top:1px solid var(--line); }}
  .row:first-of-type {{ border-top:none; padding-top:6px; }}
  .row:last-child {{ padding-bottom:0; }}
  .row b {{ font-weight:650; font-size:1.08rem; }}
  .row p {{ grid-column:1; }}
  .row a {{ grid-column:2; grid-row:1 / span 2; font-weight:600; color:var(--accent); text-decoration:none; white-space:nowrap; }}
  .row a:hover {{ text-decoration:underline; }}
  footer {{ color:var(--faint); font-size:.92rem; }}
  code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.9em; background:#EBF2F2; padding:1px 6px; border-radius:6px; }}
  @media (max-width:560px) {{ .row {{ grid-template-columns:1fr; }} .row a {{ grid-column:1; grid-row:auto; }} }}
</style>
</head>
<body>
<main>
  <div class="brand"><span class="logo">DC</span>DCLab</div>
  <div>
    <h1>Product preview</h1>
    <p class="sub">Every version of the prototype is kept. Open the latest, or go back to an earlier one to compare.</p>
  </div>
  <section class="card latest">
    <h2>Version {latest_number} · {latest_name} <span class="tag">Latest</span></h2>
    <span class="meta">{latest_date}</span>
    <p>{latest_summary}</p>
    <a class="btn" href="{latest_id}/index.html">Open version {latest_number}</a>
  </section>
  <section class="card">
    <h3>Earlier versions</h3>
{rows}
  </section>
  <footer>Sample data only. You can also switch versions from the button in the bottom-right corner of any screen.</footer>
</main>
</body>
</html>
"""


def nice_date(iso: str) -> str:
    d = datetime.date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


def build() -> None:
    data = load()
    for v in data["versions"]:
        ensure_include(v["id"])
        subprocess.run([sys.executable, str(ROOT / v["id"] / "build.py")], check=True, stdout=subprocess.DEVNULL)
    public = {
        "latest": data["latest"],
        "versions": [{**v, "date": nice_date(v["date"]), "pages": pages(v["id"])} for v in data["versions"]],
    }
    (ROOT / "versions.js").write_text(
        "// Generated by versions.py from versions.json. Do not edit.\n"
        "window.DCLAB_VERSIONS = " + json.dumps(public, indent=1, ensure_ascii=False) + ";\n" + SWITCHER
    )
    latest = next(v for v in data["versions"] if v["id"] == data["latest"])
    rows = "\n".join(
        f'    <div class="row"><b>Version {label(v["id"])} · {html.escape(v["name"])}</b>'
        f'<p>{nice_date(v["date"])} · {html.escape(v["summary"])}</p>'
        f'<a href="{v["id"]}/index.html">Open →</a></div>'
        for v in data["versions"] if v["id"] != data["latest"]
    )
    (ROOT / "index.html").write_text(LANDING.format(
        latest_id=latest["id"], latest_number=label(latest["id"]), latest_name=html.escape(latest["name"]),
        latest_date=nice_date(latest["date"]), latest_summary=html.escape(latest["summary"]), rows=rows,
    ))
    print("built", ", ".join(v["id"] for v in data["versions"]), "· latest", data["latest"])


def label(version_id: str) -> str:
    return version_id[1:].replace("_", " ")


def new(name: str, summary: str, source: str | None = None, new_id: str | None = None) -> None:
    data = load()
    latest = data["latest"]
    source = source or latest
    if not (ROOT / source).is_dir():
        sys.exit(f"no version folder {source}")
    if new_id is None:
        numbers = [int(m.group(1)) for v in data["versions"] if (m := re.match(r"v(\d+)$", v["id"]))]
        new_id = f"v{max(numbers) + 1}"
    if not re.match(r"^v[\w-]+$", new_id) or (ROOT / new_id).exists():
        sys.exit(f"bad or existing version id {new_id}")
    shutil.copytree(ROOT / source, ROOT / new_id)
    data["versions"].insert(0, {"id": new_id, "name": name, "date": datetime.date.today().isoformat(), "summary": summary})
    data["latest"] = new_id
    LIST.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    build()
    print(f"{new_id} starts as a copy of {source}; {latest} is frozen. Work in demo/product/{new_id}/ and run 'versions.py build' after changes.")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "build"
    if command == "build":
        build()
    elif command == "new" and len(sys.argv) >= 3:
        args = sys.argv[2:]
        source = None
        custom_id = None
        if "--from" in args:
            i = args.index("--from")
            source = args[i + 1]
            del args[i:i + 2]
        if "--id" in args:
            i = args.index("--id")
            custom_id = args[i + 1]
            del args[i:i + 2]
        new(args[0], args[1] if len(args) > 1 else "", source, custom_id)
    else:
        sys.exit(__doc__)
