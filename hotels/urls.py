from django.urls import path

from . import views

urlpatterns = [
    path("tableau-de-bord/", views.dashboard, name="dashboard"),
    path("hotels/", views.hotel_list, name="hotel_list"),
    path("hotels/nouveau/", views.hotel_new, name="hotel_new"),
    path("parametres/", views.hotel_settings, name="hotel_settings"),
    path("equipe/", views.team, name="team"),
    path("equipe/<int:pk>/<str:action>/", views.team_action, name="team_action"),
]
