from django.urls import path

from . import views

urlpatterns = [
    path("", views.board, name="board"),
    path("live/", views.live, name="live"),
    path("planning/", views.planning, name="planning"),
    path("quart/", views.shift_home, name="shift"),
    path("quart/ouvrir/", views.shift_open, name="shift_open"),
    path("quart/cloturer/", views.shift_close, name="shift_close"),
    path("versements/", views.remittances, name="remittances"),
    path("depenses/", views.expenses, name="expenses"),
    path("depenses/<int:pk>/<str:action>/", views.expense_action, name="expense_action"),
    path("menage/", views.cleaning, name="cleaning"),
    path("menage/<int:pk>/<str:action>/", views.cleaning_action, name="cleaning_action"),
    path("audit/", views.audit_list, name="audit"),
    path("hotel/<int:pk>/", views.switch_hotel, name="switch_hotel"),
]
