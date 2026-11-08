---
name: hmx-erp-parity
description: Port or review ERP GS Shop features against a user-designated HMX worktree, preserving module ownership, workflow, state transitions, permissions, side effects, and UI behavior. Use whenever the user asks for HMX parity, 1:1 behavior, copying from HMX, or continued ERP GS Shop development based on HMX.
---

# HMX to ERP GS Shop parity

Treat the designated HMX worktree as the behavioral reference. Port the
behavior into ERP GS Shop's framework without flattening HMX module boundaries
or inventing a different workflow. This skill does not authorize writes to the
HMX worktree, production data changes, deployment, commits, or pushes.

## Resolve the reference worktree

Use the worktree path explicitly supplied by the user for the current task. On
another server, accept a new path from the user or the
`HMX_REFERENCE_WORKTREE` environment variable. Never assume that a path from a
previous host exists on the current host.

Resolve and record the canonical path and Git commit with:

```bash
python3 .agents/skills/hmx-erp-parity/scripts/resolve_hmx_reference.py \
  --hmx-worktree /path/from/the/user
```

Omit `--hmx-worktree` only when `HMX_REFERENCE_WORKTREE` is configured. If the
resolver cannot prove a readable HMX source tree, ask for the exact worktree or
access and stop making 1:1 claims. Keep the reference worktree read-only unless
the user separately requests an HMX change.

## Establish the parity contract

Before changing ERP GS Shop, read
[references/parity-contract.md](references/parity-contract.md). Inspect the HMX
owning module and the directly participating extensions, views, security, and
tests. Map each relevant HMX source symbol to its ERP GS Shop target.

Parity includes:

- module ownership and dependency direction;
- model fields, defaults, constraints, relations, and computed values;
- buttons, workflow states, transitions, locks, redirects, and retry behavior;
- transaction boundaries, journal/inventory/audit side effects, and rollback;
- permissions plus company and branch isolation;
- form/list behavior, validation messages, and visible linked records;
- bounded query behavior and batch semantics.

Do not call a result 1:1 after matching only names, fields, or appearance. When
the frameworks differ, preserve the observable HMX contract and document the
adaptation. Do not silently add automation that HMX requires as an explicit
step, or combine HMX actions that are separate.

## Implement and verify

Place behavior in the ERP GS Shop module nearest to the HMX owner. Prefer
extension registries or service hooks for cross-module behavior instead of
growing unrelated core handlers. Preserve idempotency and exact-record company
scope.

For ORM, accounting, reports, background work, or data-loading changes, also
use `$large-scale-feature-performance` and `$precommit-scale-guard`. Add tests
for the HMX state sequence, failure rollback, authorization, and material side
effects. Add a small-versus-larger query-count test when work can grow with
lines or related records.

Report the HMX worktree and commit inspected, the HMX-to-ERP mapping, matched
behavior, deliberate framework adaptations, deferred gaps, and actual test and
Scale Guard results. Missing evidence is `NOT VERIFIED`, never 1:1.
