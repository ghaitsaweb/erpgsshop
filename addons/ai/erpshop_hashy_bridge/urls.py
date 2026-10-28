from django.urls import path

from . import views


app_name = "erpshop_hashy_bridge"

urlpatterns = [
    path("hashy/v1/ping", views.ping, name="ping"),
    path("hashy/v1/health", views.health, name="health"),
    path("hashy/v1/instance_info", views.instance_info, name="instance_info"),
    path("hashy/v1/schema", views.schema, name="schema"),
    path("hashy/v1/search_read", views.search_read, name="search_read"),
    path("hashy/v1/read_group", views.read_group, name="read_group"),
    path("hashy/v1/batch", views.batch, name="batch"),
]
