from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("app/", views.app_page, name="app"),
    path("app/sw.js", views.service_worker, name="sw"),
    path("api/school", views.api_school, name="api_school"),
    path("api/registers", views.api_submit, name="api_submit"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/school/<int:school_id>/<str:day>/", views.school_day, name="school_day"),
    path("entries/<int:entry_id>/correct", views.correct, name="correct"),
]
