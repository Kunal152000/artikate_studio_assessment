from celery import shared_task
from django.utils import timezone

from .models import CheckOut, OverdueNotice


@shared_task
def flag_overdue_checkouts():
    """
    Find every open, overdue checkout and create an OverdueNotice dated today.

    Idempotency: get_or_create + UniqueConstraint(checkout, notice_date) on
    OverdueNotice means running this task N times in one day produces exactly
    one notice per checkout — the DB constraint is the safety net.

    Memory: .iterator() streams rows via a server-side cursor instead of
    loading the entire queryset into Python memory. Safe at any scale.
    """
    today = timezone.now().date()

    overdue_qs = CheckOut.objects.filter(
        returned_at__isnull=True,
        due_at__lt=timezone.now(),
    ).only('id')  # we only need the PK for get_or_create; no extra columns

    created_count = 0
    for checkout in overdue_qs.iterator(chunk_size=500):
        _, created = OverdueNotice.objects.get_or_create(
            checkout=checkout,
            notice_date=today,
        )
        if created:
            created_count += 1

    return f'flag_overdue_checkouts: {created_count} new notices created for {today}'
