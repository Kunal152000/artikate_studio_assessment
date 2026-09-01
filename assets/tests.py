import threading
import datetime
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token
from django.urls import reverse

from .models import Asset, Employee, CheckOut, OverdueNotice
from .tasks import flag_overdue_checkouts


class BaseAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.employee = Employee.objects.create(
            employee_code="TEST-001", full_name="Test User", email="test@example.com"
        )
        # Using a dummy user for token auth (DRF Token requires a User model by default)
        from django.contrib.auth.models import User
        self.user = User.objects.create_user(username="testuser", password="pwd")
        self.token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)

        self.asset = Asset.objects.create(
            asset_tag="CAM-TEST", name="Test Cam", category="CAMERA", purchase_date="2025-01-01"
        )

    def td(self, days):
        return datetime.timedelta(days=days)


class CheckOutLimitTest(BaseAPITestCase):
    def test_three_checkout_limit(self):
        """Rule 3: an employee may hold at most 3 open checkouts."""
        for i in range(3):
            a = Asset.objects.create(
                asset_tag=f"CAM-TEST-{i}", name=f"Cam {i}", category="CAMERA", purchase_date="2025-01-01"
            )
            CheckOut.objects.create(asset=a, employee=self.employee, due_at=timezone.now() + self.td(5))

        # 4th checkout should fail with 409
        url = "/api/v1/checkouts/"
        data = {
            "asset_tag": self.asset.asset_tag,
            "employee_code": self.employee.employee_code,
            "due_at": (timezone.now() + self.td(5)).isoformat()
        }
        res = self.client.post(url, data)
        self.assertEqual(res.status_code, 409)
        self.assertIn("already holds 3", res.data["detail"])


class OverdueReportTest(BaseAPITestCase):
    def test_overdue_calculation_including_exact_now(self):
        """Overdue calculation includes items due exactly now (due_at < now)."""
        now = timezone.now()
        
        # 1. Due in the future (not overdue)
        a1 = Asset.objects.create(asset_tag="A1", name="A1", category="CAMERA", purchase_date="2025-01-01")
        CheckOut.objects.create(asset=a1, employee=self.employee, due_at=now + self.td(2))
        
        # 2. Due in the past (overdue)
        a2 = Asset.objects.create(asset_tag="A2", name="A2", category="CAMERA", purchase_date="2025-01-01")
        c2 = CheckOut.objects.create(asset=a2, employee=self.employee, due_at=now - self.td(5))
        
        # 3. Due exactly now (or slightly in past by the time query runs) - considered overdue
        # We simulate this by setting due_at to slightly in the past (1 microsecond) to ensure it's < now when queried
        a3 = Asset.objects.create(asset_tag="A3", name="A3", category="CAMERA", purchase_date="2025-01-01")
        c3 = CheckOut.objects.create(asset=a3, employee=self.employee, due_at=now - datetime.timedelta(microseconds=1))

        res = self.client.get("/api/v1/reports/overdue/")
        self.assertEqual(res.status_code, 200)
        
        # Should return exactly 2 items (A2 and A3)
        self.assertEqual(res.data['count'], 2)
        tags = [r['asset_tag'] for r in res.data['results']]
        self.assertIn("A2", tags)
        self.assertIn("A3", tags)
        self.assertNotIn("A1", tags)


class EmployeeSummaryTest(BaseAPITestCase):
    def test_employee_summary_aggregation(self):
        """The 4 numbers should match exactly."""
        now = timezone.now()
        
        # 1. Returned on time (held for 2 days)
        a1 = Asset.objects.create(asset_tag="A1", name="A1", category="CAMERA", purchase_date="2025-01-01")
        c1 = CheckOut.objects.create(asset=a1, employee=self.employee, due_at=now + self.td(5), returned_at=now - self.td(8))
        c1.checked_out_at = now - self.td(10)
        c1.save(update_fields=['checked_out_at'])

        # 2. Returned late (held for 4 days)
        a2 = Asset.objects.create(asset_tag="A2", name="A2", category="CAMERA", purchase_date="2025-01-01")
        c2 = CheckOut.objects.create(asset=a2, employee=self.employee, due_at=now - self.td(2), returned_at=now - self.td(1))
        c2.checked_out_at = now - self.td(5)
        c2.save(update_fields=['checked_out_at'])

        # 3. Currently held, not overdue
        a3 = Asset.objects.create(asset_tag="A3", name="A3", category="CAMERA", purchase_date="2025-01-01")
        CheckOut.objects.create(asset=a3, employee=self.employee, due_at=now + self.td(5))

        # 4. Currently held, overdue
        a4 = Asset.objects.create(asset_tag="A4", name="A4", category="CAMERA", purchase_date="2025-01-01")
        CheckOut.objects.create(asset=a4, employee=self.employee, due_at=now - self.td(5))

        # Expected:
        # lifetime_count = 4
        # currently_held = 2 (A3, A4)
        # currently_overdue = 1 (A4)
        # mean_hold_days = (2 days + 4 days) / 2 = 3.0 days

        res = self.client.get(f"/api/v1/employees/{self.employee.employee_code}/summary/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['lifetime_count'], 4)
        self.assertEqual(res.data['currently_held'], 2)
        self.assertEqual(res.data['currently_overdue'], 1)
        self.assertAlmostEqual(res.data['mean_hold_days'], 3.0, places=2)


class CeleryTaskIdempotencyTest(BaseAPITestCase):
    def test_task_is_idempotent(self):
        """Run task twice, assert exactly one notice is created."""
        now = timezone.now()
        CheckOut.objects.create(asset=self.asset, employee=self.employee, due_at=now - self.td(5))

        self.assertEqual(OverdueNotice.objects.count(), 0)

        # Run 1
        flag_overdue_checkouts()
        self.assertEqual(OverdueNotice.objects.count(), 1)

        # Run 2
        flag_overdue_checkouts()
        self.assertEqual(OverdueNotice.objects.count(), 1) # Still 1!


# Note: TransactionTestCase does not wrap tests in a DB transaction.
# We need this so thread 1's select_for_update doesn't block forever waiting for the test suite's outer transaction to finish.
class ConcurrencyTest(TransactionTestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.client = APIClient()
        self.employee1 = Employee.objects.create(employee_code="EMP-1", full_name="User 1", email="u1@ex.com")
        self.employee2 = Employee.objects.create(employee_code="EMP-2", full_name="User 2", email="u2@ex.com")
        self.asset = Asset.objects.create(asset_tag="CAM-CONC", name="Cam", category="CAMERA", purchase_date="2025-01-01", status="AVAILABLE")
        self.user = User.objects.create_user(username="testuser2", password="pwd")
        self.token = Token.objects.create(user=self.user)

    def test_simultaneous_checkouts(self):
        """Rule 7: two simultaneous checkouts of one asset, exactly one succeeds."""
        # Using threads to simulate simultaneous API calls
        results = []
        
        def do_checkout(emp_code):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
            data = {
                "asset_tag": self.asset.asset_tag,
                "employee_code": emp_code,
                "due_at": (timezone.now() + datetime.timedelta(days=5)).isoformat()
            }
            res = client.post("/api/v1/checkouts/", data)
            results.append(res.status_code)

        t1 = threading.Thread(target=do_checkout, args=("EMP-1",))
        t2 = threading.Thread(target=do_checkout, args=("EMP-2",))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # One should succeed (201), one should fail (409)
        self.assertEqual(len(results), 2)
        self.assertIn(201, results)
        self.assertIn(409, results)
        
        # Verify DB state
        self.assertEqual(CheckOut.objects.count(), 1)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'CHECKED_OUT')
