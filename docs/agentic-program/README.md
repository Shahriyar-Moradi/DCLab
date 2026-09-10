# DCLab agentic program documentation

This directory is the executable program of work for evolving DCLab from the
verified deterministic platform at commit `1bce168327e1a159d4804a268043720b80630013`
into a secure, scalable, agentic decision-intelligence product.

## Start here

1. Read [`MASTER_SCOPE_0_TO_10_PLAN.md`](MASTER_SCOPE_0_TO_10_PLAN.md).
2. Open the prompt file for the active scope under [`prompts/`](prompts/README.md).
3. Execute plan IDs in order. Do not start a later plan when its dependency or
   scope gate is incomplete.
4. Use one coding-agent conversation and one pull request per prompt unless the
   prompt explicitly states otherwise.
5. Record verification with the evidence template in the master plan.

## Document roles

- `MASTER_SCOPE_0_TO_10_PLAN.md` is the canonical sequence, dependency model,
  architecture boundary, status baseline, and definition of done.
- `prompts/README.md` is the coding-agent execution protocol.
- `prompts/SCOPE_*.md` files contain implementation prompts. Each plan has at
  least two prompts: implementation and adversarial/integration verification.

## Authority and change control

The current repository and verified tests override stale counts or statuses in
older reports. Product and architecture decisions in the master plan govern new
work. Existing scientific, tenant, and evidence invariants remain mandatory.
If implementation discovers a conflict, stop that plan, add an ADR, update the
master plan, and obtain review before broadening behavior.

