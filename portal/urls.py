from django.urls import path

from . import views

urlpatterns = [
    path("<slug:slug>/", views.home, name="portal_home"),
    path("<slug:slug>/disponibles/", views.available, name="portal_available"),
    path("<slug:slug>/reserver/", views.book, name="portal_book"),
    path("<slug:slug>/suivi/<str:code>/", views.status, name="portal_status"),
    path("<slug:slug>/etat/", views.rooms_fragment, name="portal_rooms"),
]
