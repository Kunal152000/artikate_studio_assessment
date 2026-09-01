from django.utils import timezone
from rest_framework import serializers

from .models import Asset, CheckOut, Employee, OverdueNotice


class AssetSerializer(serializers.ModelSerializer):
    # Computed field: null when AVAILABLE, else {code, name} of the holding employee
    current_holder = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            'id', 'asset_tag', 'name', 'category', 'status',
            'purchase_date', 'created_at', 'updated_at', 'current_holder',
        ]
        read_only_fields = ['id', 'status', 'created_at', 'updated_at', 'current_holder']

    def get_current_holder(self, obj):
        # Single query: latest open checkout for this asset (if any)
        checkout = obj.checkouts.filter(returned_at__isnull=True).select_related('employee').first()
        if checkout is None:
            return None
        return {
            'employee_code': checkout.employee.employee_code,
            'full_name': checkout.employee.full_name,
        }


class CheckOutReadSerializer(serializers.ModelSerializer):
    asset_tag     = serializers.CharField(source='asset.asset_tag', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True)

    class Meta:
        model = CheckOut
        fields = [
            'id', 'asset_tag', 'employee_code',
            'checked_out_at', 'due_at', 'returned_at', 'condition_note',
        ]


class CheckOutWriteSerializer(serializers.Serializer):
    """Input-only serializer for POST /checkouts/ — validates the three request fields."""
    asset_tag     = serializers.CharField(max_length=32)
    employee_code = serializers.CharField(max_length=16)
    due_at        = serializers.DateTimeField()

    def validate_due_at(self, value):
        now = timezone.now()
        if value <= now:
            raise serializers.ValidationError("due_at must be in the future.")
        if value > now + timezone.timedelta(days=30):
            raise serializers.ValidationError("due_at must be at most 30 days from now.")
        return value


class ReturnSerializer(serializers.Serializer):
    """Input-only serializer for POST /checkouts/{id}/return/."""
    condition_note    = serializers.CharField(required=False, allow_blank=True, default='')
    needs_maintenance = serializers.BooleanField(required=False, default=False)


class EmployeeSummarySerializer(serializers.Serializer):
    """Read-only serializer for GET /employees/{code}/summary/ — wraps the aggregate dict."""
    lifetime_count    = serializers.IntegerField()
    currently_held    = serializers.IntegerField()
    currently_overdue = serializers.IntegerField()
    mean_hold_days    = serializers.FloatField(allow_null=True)


class OverdueReportSerializer(serializers.ModelSerializer):
    asset_name    = serializers.CharField(source='asset.name', read_only=True)
    asset_tag     = serializers.CharField(source='asset.asset_tag', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True)
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    days_overdue  = serializers.SerializerMethodField()

    class Meta:
        model = CheckOut
        fields = [
            'id', 'asset_name', 'asset_tag',
            'employee_code', 'employee_name', 'days_overdue',
        ]

    def get_days_overdue(self, obj):
        return (timezone.now() - obj.due_at).days
