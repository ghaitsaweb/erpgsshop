# GSSHOP add-on layout

The project discovers manifests in `addons/<module>/` and
`addons/<group>/<module>/`. New applications use the grouped form:

```text
addons/
  base/       shared platform add-ons
  ai/         AI applications, one Django app per module
  _template/  scaffolding only
```

`base` and `ai` are sibling groups, following the module separation in the
`hmx-human-resources-port8000-latest` worktree. Grouping directories do not
combine Django apps, migrations, database tables, or permissions. Each module
keeps a unique manifest and a stable `AppConfig.label`.

The first grouped module is `addons/ai/erpshop_moduleai_assistant`. Its Django
app label remains `erpshop_moduleai_assistant`, so the existing database tables
and migration history keep their identities. The bundled provider supports
company-scoped, auditable chat through a configured HTTPS gateway. A Hashy OS
account reference is metadata; it is not an authorization handshake or proof
that a gateway is reachable. Provider calls run in Celery and cannot change ERP
records.

The existing `core/` and `modules/` packages remain built-in Django apps.
Moving them in bulk would alter migration import paths and application startup.
Any future move must be handled one app at a time with migration and deployment
verification before the next module begins.
