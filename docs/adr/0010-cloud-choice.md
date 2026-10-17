# ADR 0010 — Cloud for the hosted beta: both GCP and AWS

**Status:** Proposed — founder direction recorded 2026-10-16 ("use both"); the shape of "both" (below) still
needs the founder's answer before P8.1-A is built.  
**Date:** 2026-10-16  
**Prompt:** P8.1-A (one-cloud deployment), P8.2-A, P8.3-A  
**Depends on:** [ROADMAP.md](../mvp/ROADMAP.md) Phase 8 and Phase 9 ("second cloud"),
[ARCHITECTURE.md](../mvp/ARCHITECTURE.md)

## Context

The roadmap planned ONE cloud for the hosted beta (P8.1-A: "choose GCP or AWS … based on the first
design partner") and listed a second cloud as a Phase 9 expansion. The founder's direction is to use
both. That is a larger commitment than one cloud: every deployment, security, backup and cost
control exists twice, and the beta exit gate (3 or more design partners finish a real project
without database intervention) gets harder to reach with two stacks to harden.

## What "both" can mean (the founder picks one)

| Option | Meaning | Cost / risk |
| --- | --- | --- |
| **A. Portable, one live** | Build everything cloud-neutral (containers, OpenTofu modules per provider behind one interface, Postgres, S3-compatible object storage). Run the beta on ONE cloud; keep the other buildable and tested in CI, switch or add later. | Lowest. Keeps the beta exit gate reachable. Recommended start. |
| **B. Per-customer cloud** | The same product deployed on whichever cloud a customer or region needs (separate stacks, separate data, no cross-cloud traffic). | Two live stacks to operate, patch and monitor. Needs a deployment per customer cloud, but no cross-cloud database. |
| **C. Active-active** | One logical deployment spanning both clouds for resilience. | Highest. Cross-cloud Postgres, replication, latency and consistency problems; not justified for a beta. |

**Recommendation:** start with **A**, move to **B** when a design partner requires a specific cloud.
Reject **C** until there is a customer or availability requirement that needs it.

## Decision (so far)

1. The product must stay **cloud-neutral**: no provider SDK in application code outside a port
   (object storage, secrets, email, compute placement), Postgres as the only state store, containers
   as the unit of deployment.
2. P8.1-A builds OpenTofu **per-provider modules behind one module interface** and proves one of
   them end to end first; the second is built next and verified by `tofu validate`/`plan` in CI
   until a live deployment is needed.
3. P8.3-A's compute placement (`ComputePort`) is provider-neutral already (home pool plus one
   external target); keep it that way.
4. Data residency (ADR 0005, P8.7-A) is decided per deployment, never across clouds.

## Open questions for the founder

* Which of A / B / C? (Recommended: A, then B on demand.)
* Which cloud is the first live one? (Recommended: the one the first design partner already uses.)
* Account owners, domain and secret-management ownership for each cloud.

## Consequences

* P8.1-A grows from "L" to roughly "L + M" if the second provider module is built in the same plan;
  split it (P8.1-A first cloud, P8.1-B second cloud).
* Costs, backups, restore drills and runbooks are written per cloud (P8.2-A, P8.5-A).
* Nothing in this ADR blocks the code-only Phase 8 plans (P8.7, P8.8, the operator console and the
  usage page).
