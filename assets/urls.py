from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    AssetViewSet,
    CheckOutViewSet,
    EmployeeSummaryView,
    OverdueReportView,
    HealthView,
)

router = DefaultRouter()
router.register(r'assets', AssetViewSet, basename='asset')
router.register(r'checkouts', CheckOutViewSet, basename='checkout')

urlpatterns = [
    path('', include(router.urls)),
    path('employees/<str:employee_code>/summary/', EmployeeSummaryView.as_view()),
    path('reports/overdue/', OverdueReportView.as_view()),
    path('health/', HealthView.as_view()),
]
