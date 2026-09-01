from django.urls import path, include
from rest_framework.routers import DefaultRouter

# Router and URL patterns will be filled in as views are added
router = DefaultRouter()

urlpatterns = [
    path('', include(router.urls)),
]
