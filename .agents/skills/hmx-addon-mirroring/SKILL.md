---
name: hmx-addon-mirroring
description: Mirror HMX add-on ownership into physical ERP GS Shop add-ons when porting, auditing, or extending HMX parity. Use only inside ERP GS Shop; apply whenever behavior comes from an HMX add-on rather than its base module.
---

# HMX Add-on Mirroring

Preserve HMX module boundaries in ERP GS Shop. Behavioral adaptation is
allowed; collapsing multiple HMX owners into one GS module is not.

## Identity and location

- Create one physical GS add-on for each HMX add-on owner involved in the
  requested scope.
- Convert the HMX Python package name to a GS package name as follows:
  - replace a leading `core_` with `gs_`;
  - otherwise add the `gs_` prefix.
- Examples:
  - `core_purchase_ph` -> `gs_purchase_ph`
  - `core_localization_purchase` -> `gs_localization_purchase`
  - `core_purchase_sg` -> `gs_purchase_sg`
  - `intercomp_sale_purchase` -> `gs_intercomp_sale_purchase`
- Put the package under the matching repository add-on group, normally
  `addons/<domain>/<gs_package>/`. Its manifest slug uses hyphens, for example
  `gs-purchase-ph`; its Django package and app label remain valid Python
  identifiers.
- An existing `modules/<domain>` package may represent the base application
  owner only. A sibling HMX extension must not be implemented there merely
  because it extends that domain.

## Ownership contract

The GS add-on owns its manifest, dependency declarations, models, migrations,
forms, services, permissions, menus, routes, templates, static assets, hooks,
tests, and scheduled work that correspond to the HMX add-on.

Shared core or a base domain module may expose a generic extension point. It
must not import a country, integration, or optional add-on directly, and it
must not grow a per-add-on branch. Registration must be deterministic,
idempotent, and tagged with the owning add-on so disabled add-ons contribute no
menu, action, route behavior, job, or side effect.

Installing or uninstalling a GS add-on controls access and behavior without
deleting business data. A disabled add-on must be absent from normal user
navigation and direct access must fail closed through the generic add-on access
gate. Do not use data deletion as an uninstall mechanism.

## Dependency mapping

- Read the source HMX add-on's `__hmx__.py` completely before implementing it.
- Translate every relevant HMX dependency to the corresponding GS base app or
  `gs_` add-on; do not silently absorb a dependency's behavior.
- Keep optional country and integration dependencies explicit. For example,
  `gs_purchase_ph` depends on the GS equivalents of Purchase, Philippine
  Accounting, and Localization Purchase rather than copying their code.
- If a required GS counterpart does not yet exist, scaffold or report that
  missing owner. Do not hide the gap inside another module.

## Porting workflow

1. Resolve the designated HMX worktree and record the exact source add-on,
   version, dependencies, files, views, security, data, jobs, tests, and hooks.
2. Identify any behavior already placed in a GS base module under the wrong
   owner. Treat it as migration input, not precedent.
3. Create or update the matching `gs_` add-on manifest and package before
   adding its behavior.
4. Keep only generic contracts in shared code. Move add-on-specific
   registration and implementation to the owning package, with no duplicate
   registrations or compatibility path that remains authoritative.
5. Add focused add-on tests plus install/disable boundary tests. Verify that
   enabling the add-on exposes its UI and behavior, while disabling it hides
   menus and blocks direct execution without removing stored records.
6. Apply `$erp-modular-architecture`, `$hmx-erp-parity`, and the repository
   Scale Guard requirements for the affected data paths.

## Completion standard

Do not call an HMX extension matched when its fields or output exist but its
physical add-on owner, dependency graph, installation boundary, permissions,
workflow, rollback, or UI visibility differs. Report each source-to-target
package mapping and any behavior still living under the wrong owner.

This skill is repository-local policy for ERP GS Shop. Do not copy or install
it as a global Codex skill and do not apply it to unrelated repositories.
