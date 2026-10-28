# GSSHOP add-ons

Each add-on lives in its own directory and exposes a `manifest.json`. Put
platform add-ons under `addons/base/` and AI add-ons under `addons/ai/`.
Like HMX's sibling `base` and `ai` modules, they remain separate applications.
Existing one-level add-on paths remain supported. Add-on directories are
mounted into the `web` and `worker` containers, so a new module does not
require editing the built-in application catalog.

The module code convention is Django-based:

```text
addons/<group>/<module>/
├── manifest.json
├── apps.py
├── models.py
├── views.py
├── urls.py
├── migrations/
└── templates/<module>/
```

GSSHOP views use Django template syntax (`{% ... %}` and `{{ ... }}`) and load
the branded template-tag namespace with `{% load gsshop_ui %}`. They do not use
an HMX tag or copy HMX view definitions.

Workflow:

1. Copy `_template` to a new lowercase module directory inside its group.
2. Rename `manifest.json.example` to `manifest.json` and update its values.
3. Add an optional Django app and URL configuration declared by the manifest.
4. Run `./gsshop reload-apps`.
5. Open `/apps/` and install the discovered module.

For metadata-only changes, an administrator can also use **Refresh Apps** on
the Apps page. Views supplied by an add-on should use
`modules.apphub.access.enabled_app_required("your-slug")` so the install state
is enforced consistently.

`reload-apps` recreates only the application containers. PostgreSQL and Redis
volumes are retained. Installing or uninstalling an application controls access
and preserves its business data.

Multiple add-on roots can be configured with the platform path separator in
`GSSHOP_ADDONS_PATHS`. Inside Docker, the default is `/app/addons`.
