# ADR 0011 — Open-source license for the engine, SDK and MCP server

**Status:** Proposed — the founder decides. No `LICENSE` file is added and no package
claims a license until this ADR is accepted.  
**Date:** 2026-10-16  
**Prompt:** P8.6-A (open-source readiness)  
**Depends on:** [ARCHITECTURE.md](../mvp/ARCHITECTURE.md) §2 principle 5 (what becomes
open source), [OPEN_SOURCE_PLAN.md](../mvp/OPEN_SOURCE_PLAN.md) (what is published, how)  
**Number note:** ADR 0010 is reserved for the cloud choice (P8.1-A).

## Context

Three parts could be published: the SDK + CLI (`dclab-client`), the MCP server
(`dclab-mcp`) and the pure ML engine (`dclab-engine`, from `app/engine`). The server —
state graph services, tenancy, governance, agents, Studio — stays private
([OPEN_SOURCE_PLAN.md §2](../mvp/OPEN_SOURCE_PLAN.md)). The architecture names the
reasons to publish: agents and users integrate through the SDK/MCP, and a public
benchmark should prove engine quality. DCLab's differentiators (state graph,
verify, improve loop, decision records) live in the server, not in the engine.

The two client packages only speak HTTP to `/v1`; they are useless without a DCLab
server, so their license matters for adoption, not for competition. The engine is the
part a hosted competitor could reuse.

## Options

| Option | Hosted-service competitors | Contributors | Patents | Fit |
| --- | --- | --- | --- | --- |
| **Apache-2.0** | May run, modify and sell it without sharing changes | Easiest for companies to contribute and adopt; DCO is enough | Explicit patent grant from every contributor, terminated for whoever sues | Standard for ML libraries (scikit-learn is BSD, most of the ecosystem Apache/BSD/MIT) |
| **MIT** | Same as Apache-2.0 | Easiest; very short | No explicit patent grant | Fine for small clients; weaker for an engine with novel methods |
| **AGPL-3.0** | Must publish their modified source if they offer it as a network service | Many companies forbid AGPL dependencies, so fewer users and contributors; needs a CLA if DCLab ever wants to sell other terms | Patent grant (GPLv3 terms) | Protects a server; a poor fit for libraries people embed (it scares away exactly the integrators the SDK/MCP exist for) |
| **BSL 1.1 / source-available** (e.g. BSL with a 3–4 year change date to Apache-2.0, or Elastic License 2.0) | Forbidden from offering it as a competing hosted service until the change date | Not "open source" (not OSI-approved); some users and distributions refuse it; needs a CLA | Depends on the text; BSL has none of its own | Used by companies protecting a hosted product (MariaDB, HashiCorp, Sentry) |
| **Dual** (AGPL-3.0 or commercial) | Must open-source their service or buy a license | Requires a CLA from every contributor (DCLab must own the right to relicense) | As AGPL | Works when the published part is itself the product |

What each means per component:

- **SDK / CLI / MCP:** any copyleft or source-available term only slows adoption by
  agent frameworks and customers' internal tools; there is nothing to protect (they
  need a DCLab server). Permissive is the only sensible choice.
- **Engine:** permissive helps the "proven engine" story and outside contributions;
  copyleft or BSL protects against someone hosting the engine as a competing service,
  but that competitor would still lack the state graph, governance and agents.
- **Server:** stays private under this plan. If it is ever opened, that is a separate
  ADR, and BSL or AGPL are the realistic options there.

## Recommendation (not a decision)

**Apache-2.0 for all three published packages, the server stays private, and
contributions use the DCO (`Signed-off-by`), not a CLA.**

Reasoning: the value that competitors could not rebuild is in the private server; the
engine's job in public is credibility and adoption, which copyleft and source-available
terms work against; Apache-2.0 adds the patent grant MIT lacks, which matters for an
engine with its own investigation and selection methods. A CLA is only needed to keep
the option of relicensing contributions (dual licensing); with a permissive license
DCLab can already use every contribution in its private server, so the DCO is enough
and is friendlier to contributors.

Reconsider (and prefer BSL 1.1 with an Apache-2.0 change date for the engine only) if
the founder expects a hosted competitor to win customers with the engine alone.

## Consequences (if the recommendation is accepted)

- Add `LICENSE` (Apache-2.0 text) and `NOTICE` per package; set
  `license = "Apache-2.0"` (PEP 639 expression) and `license-files` in each
  `pyproject.toml`; remove the `Private :: Do Not Upload` classifier at publish time.
- Third-party license review of runtime dependencies (all current engine deps are
  BSD/MIT/Apache-compatible; re-check optional boosting libraries at release).
- `CONTRIBUTING.md` with the DCO, `SECURITY.md`, `CODE_OF_CONDUCT.md` in the public repo.
- `scripts/check_packages_build.py` then checks for the chosen license instead of its
  absence.

## TODO before acceptance

- [ ] Founder picks an option (or accepts the recommendation) and records it here.
- [ ] Confirm no customer contract restricts publishing engine code written for them.
- [ ] Confirm trademark use of the name "DCLab" for public packages.
