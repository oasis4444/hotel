from django.urls import path

from . import views

urlpatterns = [
    path("hotels/", views.hotel_list, name="hotel_list"),
    path("hotels/nouveau/", views.hotel_new, name="hotel_new"),
]
