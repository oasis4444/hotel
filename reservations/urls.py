from django.urls import path

from . import views

urlpatterns = [
    path("", views.reservation_list, name="reservations"),
    path("nouvelle/", views.reservation_new, name="reservation_new"),
    path("disponibles/", views.available_api, name="available_api"),
    path("<int:pk>/", views.reservation_detail, name="reservation_detail"),
    path("<int:pk>/modifier/", views.reservation_edit, name="reservation_edit"),
    path("<int:pk>/arrivee/", views.checkin, name="checkin"),
    path("<int:pk>/action/<str:action>/", views.reservation_action, name="reservation_action"),
    path("clients/", views.client_list, name="clients"),
    path("clients/<int:pk>/", views.client_edit, name="client_edit"),
]
