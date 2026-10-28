"""Discover clean-room GSSHOP add-ons from versioned JSON manifests."""

import json
import os
import re
from pathlib import Path

from .catalog import APP_CATALOG


SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DOTTED_PATH_PATTERN = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")
VALID_STATUSES = {"stable", "beta", "planned"}
REQUIRED_FIELDS = {"name", "category", "summary"}


class AddonManifestError(ValueError):
    pass


def configured_addon_paths():
    default_path = Path(__file__).resolve().parents[2] / "addons"
    configured = os.getenv("GSSHOP_ADDONS_PATHS", str(default_path))
    return [Path(item).expanduser().resolve() for item in configured.split(os.pathsep) if item.strip()]


def _validate_manifest(payload, manifest_path):
    if not isinstance(payload, dict):
        raise AddonManifestError(f"{manifest_path}: manifest root must be an object.")

    definition = dict(payload)
    definition.setdefault("slug", manifest_path.parent.name)
    missing = sorted(REQUIRED_FIELDS - definition.keys())
    if missing:
        raise AddonManifestError(f"{manifest_path}: missing required fields: {', '.join(missing)}.")
    if not SLUG_PATTERN.fullmatch(str(definition["slug"])):
        raise AddonManifestError(f"{manifest_path}: slug must use lowercase letters, numbers, and hyphens.")

    status = definition.get("status", "beta")
    if status not in VALID_STATUSES:
        raise AddonManifestError(f"{manifest_path}: status must be stable, beta, or planned.")
    for key in ("django_app", "urlconf"):
        value = definition.get(key)
        if value and not DOTTED_PATH_PATTERN.fullmatch(str(value)):
            raise AddonManifestError(f"{manifest_path}: {key} must be a dotted Python path.")

    dependencies = definition.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(SLUG_PATTERN.fullmatch(str(item)) for item in dependencies):
        raise AddonManifestError(f"{manifest_path}: dependencies must be a list of application slugs.")
    route = str(definition.get("route", ""))
    if route and (not route.startswith("/") or route.startswith("//")):
        raise AddonManifestError(f"{manifest_path}: route must be a local path beginning with one slash.")

    definition.update(
        {
            "status": status,
            "icon": str(definition.get("icon", "◆"))[:8],
            "version": str(definition.get("version", "0.1.0"))[:20],
            "sequence": int(definition.get("sequence", 100)),
            "dependencies": dependencies,
            "enabled": bool(definition.get("enabled", False)),
            "is_core": False,
            "route": route[:120],
            "manifest_path": str(manifest_path),
        }
    )
    return definition


def discover_addon_manifests(paths=None):
    manifests = []
    seen = set()
    for addon_root in paths or configured_addon_paths():
        if not addon_root.is_dir():
            continue
        manifest_paths = set(addon_root.glob("*/manifest.json"))
        manifest_paths.update(addon_root.glob("*/*/manifest.json"))
        for manifest_path in sorted(manifest_paths):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AddonManifestError(f"Cannot read {manifest_path}: {exc}") from exc
            definition = _validate_manifest(payload, manifest_path)
            if definition["slug"] in seen:
                raise AddonManifestError(f"Duplicate add-on slug: {definition['slug']}.")
            seen.add(definition["slug"])
            manifests.append(definition)
    return manifests


def discover_app_catalog(paths=None):
    catalog = [dict(definition) for definition in APP_CATALOG]
    built_in_slugs = {definition["slug"] for definition in catalog}
    for definition in discover_addon_manifests(paths):
        if definition["slug"] in built_in_slugs:
            raise AddonManifestError(f"Add-on slug conflicts with a built-in application: {definition['slug']}.")
        catalog.append(definition)
    return catalog


def discover_django_apps(paths=None):
    return [definition["django_app"] for definition in discover_addon_manifests(paths) if definition.get("django_app")]


def discover_urlconfs(paths=None):
    return [definition["urlconf"] for definition in discover_addon_manifests(paths) if definition.get("urlconf")]
