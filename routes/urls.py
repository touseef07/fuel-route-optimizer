from django.urls import path

from . import views

urlpatterns = [
    path("api/route/", views.RouteView.as_view(), name="route"),
    path("map/", views.map_view, name="map"),
]
