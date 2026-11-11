---
name: erp-modular-architecture
description: Keep ERP GS Shop modules installable and self-contained when adding or changing models, entities, navigation, smart actions, workflows, menus, permissions, or module-owned UI. Use for module development and architectural refactors; combine with the HMX parity skill when HMX is the behavioral reference.
---

# ERP Modular Architecture

Build features through stable extension points so installing a module supplies its
standard behavior without adding module-specific branches to shared core code.

## Architectural contract

- Keep domain models, forms, services, permissions, entity metadata, menus, and
  exceptional actions in their owning module.
- Shared core code may define generic discovery and execution contracts. It must
  not import each new module or grow a per-module `if`, route, or registry entry
  for behavior that can be derived from installed apps, entity metadata, or
  Django model relations.
- Derive standard behavior automatically. In particular, registered entities
  connected by `ForeignKey` or `OneToOneField` receive safe, bidirectional smart
  actions without manual entries in a global list.
- Use explicit registration only for a real exception: a filtered or indirect
  join, a non-relational action, custom authorization, different create/edit
  semantics, or a deliberately overridden label/icon. Explicit definitions
  override generated defaults by stable key or relation identity; do not copy a
  standard relation into custom code.
- Discovery must be deterministic and idempotent. Detect ambiguous entity/model
  mappings and key collisions instead of silently selecting an arbitrary target.
- Preserve tenant, company, branch, and record permissions in every generated
  path. Counts and list scopes must stay in querysets and remain bounded; never
  materialize an unbounded relation merely to build navigation.
- Installing an unrelated module must not change existing domain behavior.
  Removing a module must leave no hard import or stale registry dependency in
  shared core code.

## Workflow

1. Identify the owning module and the generic extension point before editing.
2. Search shared files for module-specific imports, dictionary entries, and
   relation declarations the change would otherwise require.
3. Implement or reuse automatic discovery for the standard case. Keep only
   exceptional behavior in module-owned declarations.
4. Add invariant tests at the extension point and focused tests in the module.
   Verify standard relation navigation in both directions, explicit override
   precedence, deterministic output, permission scoping, and no duplicate keys.
5. Run affected module tests and the repository's scale checks. Do not claim
   modular parity when a new standard entity still requires a central edit.

When HMX is the reference, match its observable behavior while adapting the
mechanism to this contract; do not reproduce avoidable monolithic coupling.
