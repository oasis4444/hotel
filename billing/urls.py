from django.urls import path

from . import views

urlpatterns = [
    path("", views.invoice_list, name="invoices"),
    path("<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("<int:pk>/action/<str:action>/", views.invoice_action, name="invoice_action"),
    path("<int:pk>/imprimer/", views.invoice_print, name="invoice_print"),
    path("rapports/", views.reports, name="reports"),
]
