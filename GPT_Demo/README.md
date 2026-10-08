# DCLab product UI preview

This is a static, multi-page product prototype for exploring the planned DCLab
experience as a developer, a business user, or an internal platform operator.
Start at `index.html`; it is a role chooser and product workspace entry point,
not a marketing page.

## Open it

From the repository root, run:

```bash
python3 -m http.server 8080 --directory GPT_Demo
```

Then visit `http://localhost:8080/`. No dependencies, API credentials,
database, or external services are needed. Serving it locally gives the browser
one origin for navigation and local demo state.

## Workspaces and screens

- **Developer Studio:** home, Lab/chat, run pipeline, datasets, experiments,
  improve loop, graph/lineage, models, monitoring, inbox, decisions,
  governance, agents & tools, and workspace settings.
- **Business Outcomes:** a simplified, non-technical outcome dashboard with
  prediction examples, trust limits, a question flow, and sample report/export
  controls. It does not initiate business actions or expose training internals.
- **Operator Console:** internal health, worker/job queues, upload quarantine,
  platform safety caps, and evaluation gates. It shows metadata only.

Every page includes review notes. The product map distinguishes foundation
phases from planned phases and explicitly lists demand-gated items that are not
in the MVP, such as persistent Jupyter, online serving, external GPU marketplaces,
dual-cloud operation, and open-ended code execution.

## Prototype behavior and boundaries

All names, tables, events, usage amounts, metrics, and tokens are invented.
Navigation, tabs, the command search (`⌘K` / `Ctrl+K`), sample table search,
switches, and dialogs work in the browser. Actions can be previewed and marked
as simulated; exports contain only synthetic sample values. The browser does
not call DCLab, call an LLM/Jev/NOOA agent, upload a selected file, create a
token, queue a compute job, send a notification, or change a real workspace.
“Simulated” activity can be cleared from the start page.

The screen coverage describes a target product experience, not implementation
status. Follow `docs/mvp/README.md` and `docs/mvp/ROADMAP.md` for delivery status;
use `map.html` and each page's review notes to identify product questions and
features that may be unnecessary.

Editable screen fragments are in `src/`; `build.py` composes them with the
shared shell. `app.js` and `app.css` provide browser-only interactions and
presentation.
