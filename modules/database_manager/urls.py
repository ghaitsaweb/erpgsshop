from django.urls import path

from . import views


urlpatterns = [
    path("", views.index, name="database_manager_home"),
    path("api/databases/", views.databases, name="database_manager_databases"),
    path("api/choose/", views.choose_database, name="database_manager_choose"),
    path("api/operator/status/", views.operator_status, name="database_manager_operator_status"),
    path("api/operator/unlock/", views.unlock, name="database_manager_unlock"),
    path("api/operator/lock/", views.lock, name="database_manager_lock"),
    path("api/create/", views.create, name="database_manager_create"),
    path("api/backup/", views.backup, name="database_manager_backup"),
    path("api/duplicate/", views.duplicate, name="database_manager_duplicate"),
    path("api/restore/", views.restore, name="database_manager_restore"),
    path("api/upgrade/", views.upgrade, name="database_manager_upgrade"),
    path("api/drop/", views.drop, name="database_manager_drop"),
    path("api/protection/", views.protection, name="database_manager_protection"),
    path("api/backups/", views.backups, name="database_manager_backups"),
    path("api/backups/<str:filename>/download/", views.download_backup, name="database_manager_backup_download"),
    path("api/backups/<str:filename>/delete/", views.delete_backup, name="database_manager_backup_delete"),
    path("api/operations/", views.operations, name="database_manager_operations"),
    path("api/operations/<uuid:operation_id>/", views.operation_status, name="database_manager_operation_status"),
    path("api/worker/", views.worker_status, name="database_manager_worker"),
]
