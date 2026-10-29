from django.urls import path

from . import views

urlpatterns = [
    path("", views.queue, name="kitchen_queue"),
    path("carte/", views.menu, name="kitchen_menu"),
    path("carte/imprimer/", views.menu_print, name="kitchen_menu_print"),
    path("carte/<int:pk>/<str:action>/", views.menu_action, name="kitchen_menu_action"),
    path("carte/<int:pk>/recette/", views.dish_recipe, name="dish_recipe"),
    path("commande/nouvelle/", views.order_new, name="order_new"),
    path("commande/<int:pk>/<str:action>/", views.order_action, name="order_action"),
    path("tables/", views.tables, name="kitchen_tables"),
    path("tables/<int:pk>/<str:action>/", views.table_action, name="kitchen_table_action"),
]
