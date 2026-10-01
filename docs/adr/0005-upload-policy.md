# ADR 0005 — Upload publication policy: `internal_training`

**Status:** Accepted (founder decision, 2026-10-01)  
**Date:** 2026-10-01  
**Prompt:** P0.2-B (closes S0-P05B)  
**Depends on:** S0-P05A dataset policy revisions, S0-P05B publication state machine (migration `0061_ingestion_publication`)

## Context

S0-P05B made uploaded datasets unreadable until an audited publication run reaches
`published`, but no path could publish: production refused every scan-derived
transition (no malware/content scanner exists) and `publishable` required a
complete dataset/column policy that uploads never received. Production therefore
refused all Labs uploads, and development used an unenforced compatibility path.
A product decision was required.

## Decision

A direct Labs upload by a user with **write authority on the target workspace**
(an owner/member with an ML-write role, or a platform admin, whose write access to
every workspace already exists) is published as **`internal_training`** once it
passes structural validation. The publication is bound to that upload: the
ingestion run's data source must be `upload` and the artifact must have been
created by the same user.

1. **Structural validation** (`services/upload_structure_scan.py`): safe filename,
   supported type, declared MIME and magic bytes agree, Parquet/XLSX signatures,
   bounded and safe XLSX archive members, parsable tabular schema. It is **not** a
   malware or content scan and is never recorded as one.
2. **Failure → rejected, not retained.** A structurally invalid file is deleted from
   object storage before any lineage row exists; the API returns 422 with a safe,
   human-readable reason that the upload UI displays. Nothing is kept, so there is no
   quarantined object to manage.
3. **Success → audited publication.** Within the upload transaction,
   `publish_upload_for_internal_training` applies conservative policy labels to the
   dataset and every column and advances the existing state machine, one append-only
   event per step, actor = the uploading user:

   | Step | Reason code |
   | --- | --- |
   | `quarantined` | `upload_received` |
   | `scanned` | `structural_validation_passed` |
   | `classified` | `internal_training_policy_applied` |
   | `publishable` | `internal_training_policy_complete` |
   | `published` | `internal_training_published` |

4. **Labels** (dataset default and every column), source `policy` because nothing
   was classified: sensitivity `restricted` (unknown content is treated as most
   sensitive), LLM exposure `deny`, retention `standard`, residency
   `home_cloud_only`; columns also `model_use_policy = allow`. A person may append a
   manual dataset policy revision later (dataset revisions are append-only; column
   labels are current-state fields; publication events stay immutable).
5. **What the grant allows:** deterministic training, evaluation, predictions and
   in-workspace preview/download by authorized workspace members.
   **What it does not allow:** any LLM/agent exposure (label `deny`; see item 7), export
   or sharing outside the workspace, signed URLs where production already denies
   them, cross-workspace use.
6. **Guard against forgery.** Only `transition_publication(..., attestation=
   "structural_internal_training")` may pass the production scan guard, and only
   with the exact reason code for the target state and a user actor. Any other
   production caller still receives 503 ("production scan attestation is
   unavailable"). Tests cover the policy reason without the attestation (503), a
   wrong attestation string, operator/system actors, a mismatched reason, and a
   different same-workspace user (403).
7. **The LLM label is not yet enforced at call sites.** Current LLM paths (decision
   agent, pipeline verifier) are gated by global flags and do not read dataset
   policy. Until the Phase 6 gateway checks `llm_exposure_policy`, production refuses
   to start with `DECISION_AGENT_ENABLED` or `PIPELINE_LLM_VERIFIER_ENABLED` on
   (`config.validate_runtime_settings`).
8. **Atomic upload.** The whole upload, from lineage rows through publication,
   commits once; any failure rolls everything back and deletes the stored object.
   (`seed_dogfood(commit=False)` removed a pre-existing mid-transaction commit.)
9. **Legacy rows.** Uploads created before this policy remain unpublished and
   redacted; their status message tells the user they are quarantined and to upload
   again. No backfill infers a publication.

## Scope

Applies to the Labs dataset upload (`POST /app/labs/uploads`), the golden path.
Still production-denied (frozen or retiring surfaces, unchanged): admin lab file
ingestion (`api/lab.py`, retired by P1.1), uploaded trial data
(`client_lab_service`), legacy opportunity CSV import (`api/opportunities.py`).

## Deferred to Phase 8 (formerly S0-P05C–E)

Content/malware scanner and classifier adapters, resumable quarantine worker,
retention and deletion jobs, residency enforcement beyond labels, and an operator
review UI for quarantined records.

## Consequences

- Production accepts own-workspace uploads again; enforcement stays on for
  materialization, download and reproduction (`publication_enforced`).
- Publication now runs on every Labs upload in every environment, so a dataset whose
  policy cannot be completed fails the upload instead of silently training.
- Rollback: set the upload route to refuse (code change) — never relabel existing
  rows or delete their audit events.
