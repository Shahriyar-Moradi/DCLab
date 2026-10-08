# DCLab product UI preview

A static, click-through prototype of the planned DCLab product. **Every version
is kept**: each lives in its own folder and is never edited once a newer one
exists.

| Version | Name | What it is |
| --- | --- | --- |
| `v3/` | Clean (latest) | The product look: 10 screens, one job each, details behind tabs, plain words |
| `v2/` | Detailed, tabbed | Every detail kept; 16 screens, dense cards split into section tabs |
| `v1/` | Detailed | The first full walkthrough; 16 screens with every field and review note |

`versions.json` is the list. `index.html` (generated) is the landing page, and
`versions.js` (generated) puts a version button in the bottom-right corner of
every screen; switching keeps you on the same screen when the other version has it.

## Open it

```bash
python3 -m http.server 8787 --directory demo/product
```

Then visit `http://localhost:8787/`. No dependencies or services are needed.

## Make a new version

```bash
python3 demo/product/versions.py new "Name" "One-line summary"
```

This freezes the latest version and creates the next folder as a copy of it.
Edit page content in `vN/src/*.html` (and `vN/app.css`, `vN/app.js`,
`vN/index.html`), then rebuild everything:

```bash
python3 demo/product/versions.py build
```

Do not edit older folders; that is what makes them versions.

## Design rules for the latest version

1. One job per screen: a title, one sentence, at most two buttons.
2. Tabs across the top. The first tab is the answer; details are in the others.
3. Plain words: "Observe", "Ask first", "Automatic"; no record ids, digests or
   prompt versions on main views.
4. Evidence is one click away, not on the surface.
5. A few roomy blocks per tab.

## Boundaries

All names, numbers, events and spend are invented. Buttons, tabs, search and
switches respond in the browser; nothing calls DCLab, a model or any service.
The screens show a target experience, not implementation status: see
`docs/mvp/` for that.
