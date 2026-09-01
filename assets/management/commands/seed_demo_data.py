import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from assets.models import Asset, Employee, CheckOut

class Command(BaseCommand):
    help = 'Populates the database with demo data idempotently.'

    def handle(self, *args, **options):
        # 1. Employees (at least 4, 1 inactive)
        emps_data = [
            {'code': 'EMP-001', 'name': 'Alice Smith', 'email': 'alice@example.com', 'active': True},
            {'code': 'EMP-002', 'name': 'Bob Jones', 'email': 'bob@example.com', 'active': True},
            {'code': 'EMP-003', 'name': 'Charlie Brown', 'email': 'charlie@example.com', 'active': True},
            {'code': 'EMP-004', 'name': 'Diana Prince', 'email': 'diana@example.com', 'active': False},
        ]
        emps = {}
        for ed in emps_data:
            emp, created = Employee.objects.get_or_create(
                employee_code=ed['code'],
                defaults={
                    'full_name': ed['name'],
                    'email': ed['email'],
                    'is_active': ed['active']
                }
            )
            emps[ed['code']] = emp
            if created:
                self.stdout.write(f"Created employee: {emp.employee_code}")

        # 2. Assets (at least 8 across 4 categories)
        from datetime import date
        d = date(2025, 1, 15)
        assets_data = [
            {'tag': 'CAM-001', 'name': 'Canon R5', 'cat': 'CAMERA'},
            {'tag': 'CAM-002', 'name': 'Sony A7S III', 'cat': 'CAMERA'},
            {'tag': 'LAP-001', 'name': 'MacBook Pro M3', 'cat': 'LAPTOP'},
            {'tag': 'LAP-002', 'name': 'ThinkPad X1', 'cat': 'LAPTOP'},
            {'tag': 'SEN-001', 'name': 'LiDAR Scanner', 'cat': 'SENSOR'},
            {'tag': 'SEN-002', 'name': 'Temp Probe X', 'cat': 'SENSOR'},
            {'tag': 'VEH-001', 'name': 'Toyota Hilux 1', 'cat': 'VEHICLE'},
            {'tag': 'VEH-002', 'name': 'Ford Ranger', 'cat': 'VEHICLE'},
        ]
        assets = {}
        for ad in assets_data:
            # We don't override status here initially, except for newly created ones
            asset, created = Asset.objects.get_or_create(
                asset_tag=ad['tag'],
                defaults={
                    'name': ad['name'],
                    'category': ad['cat'],
                    'purchase_date': d,
                }
            )
            assets[ad['tag']] = asset
            if created:
                self.stdout.write(f"Created asset: {asset.asset_tag}")

        # 3. Checkouts
        now = timezone.now()
        
        # Scenarios we need:
        # 2 currently overdue (open, due in past)
        # 2 returned on time
        # 1 returned late

        checkout_scenarios = [
            # 1: Overdue
            {
                'emp': 'EMP-001', 'asset': 'CAM-001',
                'created_offset': -10, 'due_offset': -2, 'ret_offset': None
            },
            # 2: Overdue
            {
                'emp': 'EMP-002', 'asset': 'LAP-001',
                'created_offset': -20, 'due_offset': -5, 'ret_offset': None
            },
            # 3: Returned on time
            {
                'emp': 'EMP-001', 'asset': 'SEN-001',
                'created_offset': -15, 'due_offset': -5, 'ret_offset': -10
            },
            # 4: Returned on time
            {
                'emp': 'EMP-003', 'asset': 'VEH-001',
                'created_offset': -30, 'due_offset': -20, 'ret_offset': -25
            },
            # 5: Returned late
            {
                'emp': 'EMP-002', 'asset': 'CAM-002',
                'created_offset': -40, 'due_offset': -30, 'ret_offset': -28
            },
        ]

        def td(days):
            return datetime.timedelta(days=days)

        for i, sc in enumerate(checkout_scenarios):
            emp = emps[sc['emp']]
            asset = assets[sc['asset']]
            
            # Idempotency check: see if a checkout already exists for this exact pair
            # Since checkouts don't have natural keys besides the IDs/timestamps, 
            # we'll look for an existing one with these exact offsets roughly.
            due_t = now + td(sc['due_offset'])
            
            existing = CheckOut.objects.filter(employee=emp, asset=asset, due_at__date=due_t.date()).first()
            if not existing:
                checkout = CheckOut.objects.create(
                    asset=asset,
                    employee=emp,
                    due_at=due_t,
                    returned_at=(now + td(sc['ret_offset'])) if sc['ret_offset'] is not None else None
                )
                
                # Update checkout_out_at explicitly since auto_now_add locked it
                checkout.checked_out_at = now + td(sc['created_offset'])
                checkout.save(update_fields=['checked_out_at'])
                
                # Update asset status
                if sc['ret_offset'] is None:
                    asset.status = 'CHECKED_OUT'
                    asset.save(update_fields=['status'])
                
                self.stdout.write(f"Created checkout: {asset.asset_tag} to {emp.employee_code}")

        self.stdout.write(self.style.SUCCESS("Demo data seeded successfully!"))

