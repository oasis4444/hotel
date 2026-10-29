from django.urls import path

from . import views

urlpatterns = [
    path("", views.items, name="stock_items"),
    path("articles/<int:pk>/basculer/", views.item_toggle, name="stock_item_toggle"),
    path("mouvement/", views.movement_create, name="stock_movement_create"),
    path("fournisseurs/", views.suppliers, name="stock_suppliers"),
    path("fournisseurs/<int:pk>/basculer/", views.supplier_toggle, name="stock_supplier_toggle"),
    path("achats/", views.purchases, name="stock_purchases"),
    path("achats/<int:pk>/", views.purchase_detail, name="stock_purchase_detail"),
]
