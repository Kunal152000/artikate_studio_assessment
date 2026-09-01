from django.db import transaction
from django.db.models import Avg, Count, ExpressionWrapper, F, DurationField, Q
from django.http import JsonResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Asset, CheckOut, Employee
from .serializers import (
    AssetSerializer,
    CheckOutReadSerializer,
    CheckOutWriteSerializer,
    OverdueReportSerializer,
    ReturnSerializer,
    EmployeeSummarySerializer,
)


class AssetViewSet(viewsets.ModelViewSet):
    queryset = Asset.objects.all()
    serializer_class = AssetSerializer
    filterset_fields = ['status', 'category']
    search_fields = ['name', 'asset_tag']
    # Disallow DELETE — assets are never deleted, only status-changed
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']


class CheckOutViewSet(viewsets.GenericViewSet):
    queryset = CheckOut.objects.all()

    def create(self, request):
        """POST /checkouts/ — applies business rules 1–5, 7, 8."""
        serializer = CheckOutWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        try:
            with transaction.atomic():
                # Rule 7 + Rule 1: lock the asset row before reading status.
                # select_for_update() → SELECT ... FOR UPDATE in PostgreSQL.
                # Any concurrent request for the same asset blocks here until
                # this transaction commits or rolls back.
                try:
                    asset = Asset.objects.select_for_update().get(
                        asset_tag=data['asset_tag']
                    )
                except Asset.DoesNotExist:
                    # Rule 8: unknown asset_tag → 404
                    return Response(
                        {'detail': 'Asset not found.'},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                # Rule 1: asset must be AVAILABLE
                if asset.status != 'AVAILABLE':
                    return Response(
                        {'detail': 'Asset is not available for checkout.'},
                        status=status.HTTP_409_CONFLICT,
                    )

                # Rule 8: unknown employee_code → 404
                try:
                    employee = Employee.objects.get(employee_code=data['employee_code'])
                except Employee.DoesNotExist:
                    return Response(
                        {'detail': 'Employee not found.'},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                # Rule 2: employee must be active
                if not employee.is_active:
                    return Response(
                        {'detail': 'Inactive employees cannot check out assets.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                # Rule 3: max 3 open checkouts per employee
                open_count = CheckOut.objects.filter(
                    employee=employee, returned_at__isnull=True
                ).count()
                if open_count >= 3:
                    return Response(
                        {'detail': 'Employee already holds 3 assets. Return one first.'},
                        status=status.HTTP_409_CONFLICT,
                    )

                # Rule 5: create checkout and update asset status atomically
                checkout = CheckOut.objects.create(
                    asset=asset,
                    employee=employee,
                    due_at=data['due_at'],
                )
                asset.status = 'CHECKED_OUT'
                asset.save(update_fields=['status', 'updated_at'])

        except Exception:
            # Unexpected DB error — let the atomic block roll back and re-raise
            raise

        return Response(
            CheckOutReadSerializer(checkout).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'], url_path='return')
    def return_asset(self, request, pk=None):
        """POST /checkouts/{id}/return/ — applies rule 6."""
        serializer = ReturnSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        with transaction.atomic():
            try:
                # Lock the checkout row to prevent double-return races
                checkout = CheckOut.objects.select_for_update().get(pk=pk)
            except CheckOut.DoesNotExist:
                return Response(
                    {'detail': 'Checkout not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Rule 6: returning an already-returned checkout → 409
            if checkout.returned_at is not None:
                return Response(
                    {'detail': 'This checkout has already been returned.'},
                    status=status.HTTP_409_CONFLICT,
                )

            checkout.returned_at = timezone.now()
            checkout.condition_note = data['condition_note']
            checkout.save(update_fields=['returned_at', 'condition_note'])

            asset = checkout.asset
            asset.status = 'MAINTENANCE' if data['needs_maintenance'] else 'AVAILABLE'
            asset.save(update_fields=['status', 'updated_at'])

        return Response(CheckOutReadSerializer(checkout).data, status=status.HTTP_200_OK)


class EmployeeSummaryView(APIView):
    """GET /employees/{employee_code}/summary/ — single ORM aggregate query."""

    def get(self, request, employee_code):
        if not Employee.objects.filter(employee_code=employee_code).exists():
            return Response(
                {'detail': 'Employee not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = CheckOut.objects.filter(
            employee__employee_code=employee_code
        ).aggregate(
            lifetime_count=Count('id'),
            currently_held=Count('id', filter=Q(returned_at__isnull=True)),
            currently_overdue=Count(
                'id',
                filter=Q(returned_at__isnull=True, due_at__lt=timezone.now()),
            ),
            # ExpressionWrapper needed because Django can't infer DurationField
            # from DateTimeField subtraction automatically.
            mean_hold_seconds=Avg(
                ExpressionWrapper(
                    F('returned_at') - F('checked_out_at'),
                    output_field=DurationField(),
                ),
                filter=Q(returned_at__isnull=False),
            ),
        )

        # Convert timedelta to float days (None if no returned items)
        mean_td = result.pop('mean_hold_seconds')
        result['mean_hold_days'] = (
            mean_td.total_seconds() / 86400 if mean_td is not None else None
        )

        return Response(EmployeeSummarySerializer(result).data)


class OverdueReportView(APIView):
    """GET /reports/overdue/ — no N+1 queries via select_related."""

    def get(self, request):
        qs = (
            CheckOut.objects.filter(
                returned_at__isnull=True,
                due_at__lt=timezone.now(),
            )
            .select_related('asset', 'employee')  # single JOIN, no per-row queries
            .order_by('due_at')                   # earliest due_at = most overdue
        )
        serializer = OverdueReportSerializer(qs, many=True)
        return Response({'count': qs.count(), 'results': serializer.data})


class HealthView(APIView):
    """GET /health/ — unauthenticated DB connectivity check."""
    permission_classes = [AllowAny]

    def get(self, request):
        from django.db import connection
        try:
            connection.ensure_connection()
            return JsonResponse({'status': 'ok', 'db': 'connected'})
        except Exception as exc:
            return JsonResponse(
                {'status': 'error', 'db': str(exc)},
                status=503,
            )
