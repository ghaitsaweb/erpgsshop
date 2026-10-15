#!/bin/sh
set -eu

if [ "${RUN_BOOTSTRAP:-True}" = "True" ]; then
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
  python manage.py create_admin
  python manage.py sync_apps
  python manage.py sync_localizations
  python manage.py seed_demo
fi

exec "$@"
