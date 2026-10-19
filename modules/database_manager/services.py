import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql
from django.conf import settings
from django.core.management import call_command
from django.db import connections

from .models import ManagedDatabase
from .runtime import database_context, primary_database_name, register_database, validate_database_name


BACKUP_FILENAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,62}-\d{8}T\d{6}Z\.dump$")


def backup_directory():
    path = Path(settings.ERP_DATABASE_BACKUP_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _connection_kwargs(database="postgres"):
    config = settings.DATABASES["default"]
    return {
        "dbname": database,
        "user": config["USER"],
        "password": config["PASSWORD"],
        "host": config["HOST"],
        "port": config["PORT"],
        "connect_timeout": 10,
    }


def admin_connection(database="postgres"):
    return psycopg.connect(**_connection_kwargs(database), autocommit=True)


def database_exists(name):
    validate_database_name(name)
    with admin_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname = %s)", (name,))
            return cursor.fetchone()[0]


def ensure_primary_registry():
    name = primary_database_name()
    record, _ = ManagedDatabase.objects.using("default").update_or_create(
        name=name,
        defaults={"label": "ERP GSSHOP · Main", "is_primary": True, "is_protected": True},
    )
    return record


def list_database_inventory():
    ensure_primary_registry()
    records = {item.name: item for item in ManagedDatabase.objects.using("default").all()}
    names = list(records)
    rows = {}
    version = "Unknown"
    with admin_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT d.datname,
                       pg_size_pretty(pg_database_size(d.datname)),
                       pg_database_size(d.datname),
                       COUNT(a.pid) FILTER (WHERE a.pid IS NOT NULL)
                  FROM pg_database d
             LEFT JOIN pg_stat_activity a ON a.datname = d.datname
                 WHERE d.datname = ANY(%s)
              GROUP BY d.datname
              ORDER BY d.datname
                """,
                (names,),
            )
            rows = {
                row[0]: {"size": row[1], "size_bytes": row[2], "connections": row[3]}
                for row in cursor.fetchall()
            }
            cursor.execute("SHOW server_version")
            version = cursor.fetchone()[0]

    inventory = []
    for name, record in records.items():
        details = rows.get(name, {})
        inventory.append(
            {
                "name": name,
                "label": record.label,
                "is_primary": record.is_primary,
                "is_protected": record.is_protected,
                "status": "ready" if details else "offline",
                "size": details.get("size", "Unavailable"),
                "size_bytes": details.get("size_bytes", 0),
                "connections": details.get("connections", 0),
                "version": version,
                "created_at": record.created_at.isoformat(),
            }
        )
    return sorted(inventory, key=lambda item: (not item["is_primary"], item["label"].lower()))


def registered_database(name, *, must_exist=True):
    name = validate_database_name(name)
    try:
        record = ManagedDatabase.objects.using("default").get(name=name)
    except ManagedDatabase.DoesNotExist as exc:
        raise ValueError("Database is not registered in ERP GSSHOP.") from exc
    if must_exist and not database_exists(name):
        raise ValueError("Database is registered but is not available on PostgreSQL.")
    return record


def create_database(name):
    name = validate_database_name(name, allow_primary=False)
    if database_exists(name):
        raise ValueError("A database with this name already exists.")
    owner = settings.DATABASES["default"]["USER"]
    with admin_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {} WITH OWNER {} TEMPLATE template0 ENCODING 'UTF8'").format(
                    sql.Identifier(name), sql.Identifier(owner)
                )
            )


def drop_database(name):
    record = registered_database(name)
    if record.is_primary:
        raise ValueError("The primary ERP GSSHOP database cannot be dropped.")
    if record.is_protected:
        raise ValueError("Remove database protection before dropping it.")
    connections.close_all()
    with admin_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("ALTER DATABASE {} WITH ALLOW_CONNECTIONS false").format(sql.Identifier(name)))
            cursor.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,))
            cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
    ManagedDatabase.objects.using("default").filter(pk=record.pk).delete()


def _client_environment():
    environment = os.environ.copy()
    environment["PGPASSWORD"] = str(settings.DATABASES["default"]["PASSWORD"])
    return environment


def _client_base(binary):
    config = settings.DATABASES["default"]
    return [
        binary,
        "--host", str(config["HOST"]),
        "--port", str(config["PORT"]),
        "--username", str(config["USER"]),
    ]


def _run_client(arguments):
    try:
        return subprocess.run(
            arguments,
            env=_client_environment(),
            check=True,
            capture_output=True,
            text=True,
            timeout=1500,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("PostgreSQL client tools are not installed in the ERP image.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "PostgreSQL client command failed.").strip()
        raise RuntimeError(detail[-1000:]) from exc


def backup_filename(database_name):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{database_name}-{stamp}.dump"


def safe_backup_path(filename, *, must_exist=True):
    filename = os.path.basename(filename or "")
    if not BACKUP_FILENAME_PATTERN.fullmatch(filename):
        raise ValueError("Invalid backup filename.")
    root = backup_directory().resolve()
    path = (root / filename).resolve()
    if path.parent != root:
        raise ValueError("Invalid backup path.")
    if must_exist and not path.is_file():
        raise ValueError("Backup file does not exist.")
    return path


def create_backup(database_name, destination=None):
    registered_database(database_name)
    destination = Path(destination) if destination else backup_directory() / backup_filename(database_name)
    _run_client(
        _client_base("pg_dump")
        + ["--format=custom", "--compress=6", "--no-owner", "--no-acl", "--file", str(destination), database_name]
    )
    return destination


def validate_backup(path):
    _run_client(_client_base("pg_restore") + ["--list", str(path)])


def restore_backup(path, target_name):
    target_name = validate_database_name(target_name, allow_primary=False)
    validate_backup(path)
    create_database(target_name)
    try:
        _run_client(
            _client_base("pg_restore")
            + ["--no-owner", "--no-acl", "--exit-on-error", "--dbname", target_name, str(path)]
        )
    except Exception:
        _drop_unregistered_database(target_name)
        raise


def duplicate_database(source_name, target_name):
    registered_database(source_name)
    with tempfile.NamedTemporaryFile(suffix=".dump", dir=backup_directory(), delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        create_backup(source_name, temporary_path)
        restore_backup(temporary_path, target_name)
    finally:
        temporary_path.unlink(missing_ok=True)


def _drop_unregistered_database(name):
    if not database_exists(name):
        return
    connections.close_all()
    with admin_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,))
            cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def bootstrap_database(database_name, *, username="", email="", password=""):
    register_database(database_name)
    with database_context(database_name) as alias:
        call_command("migrate", database=alias, interactive=False, verbosity=0)
        call_command("sync_apps", verbosity=0)
        call_command("sync_localizations", verbosity=0)
        if username and password:
            from django.contrib.auth import get_user_model

            user_model = get_user_model()
            manager = user_model._default_manager.db_manager(alias)
            user = manager.filter(username=username).first()
            if user is None:
                manager.create_superuser(username=username, email=email, password=password)
            else:
                user.email = email
                user.is_staff = True
                user.is_superuser = True
                user.set_password(password)
                user.save(using=alias, update_fields=("email", "is_staff", "is_superuser", "password"))


def list_backups():
    backups = []
    for path in backup_directory().glob("*.dump"):
        try:
            safe_backup_path(path.name)
        except ValueError:
            continue
        stat = path.stat()
        database_name = path.name.rsplit("-", 1)[0]
        backups.append(
            {
                "filename": path.name,
                "database": database_name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
        )
    return sorted(backups, key=lambda item: item["created_at"], reverse=True)
