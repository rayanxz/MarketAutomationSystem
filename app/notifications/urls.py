from django.urls import path
from . import views

urlpatterns = [
    path("list/", views.list_notifications, name="notifications_list"),
    path("<int:notif_id>/read/", views.mark_read, name="notifications_mark_read"),
    path("", views.notifications_page, name="notifications_page"),
]
