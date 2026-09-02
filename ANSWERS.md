# Part B — Diagnose three broken snippets

## Snippet 1 — Overdue Report View

**1. What is wrong?**
* **RAM Exhaustion & DB Overhead:** The query `.filter(returned_at__isnull=True)` pulls *every single open checkout* from the database into Python memory. The condition `c.due_at < timezone.now()` is then evaluated in a Python for-loop, rather than using the database engine to filter the rows before they leave PostgreSQL.
* **The N+1 Query Problem:** The line `c.asset.name` and `c.employee.full_name` triggers two brand new, separate SQL `SELECT` queries for *each iteration* of the loop, violently locking and overloading the database if there are many checkouts.
* **Inefficient Sorting:** Sorting (`rows.sort(...)`) is done heavily in Python RAM instead of at the database level.

**2. Why does it look correct in local testing?**
In a local developer environment, the database likely only contains 3 to 10 open checkouts. Fetching 10 rows into RAM and executing 20 extra N+1 SQL queries happens in milliseconds, completely masking the catastrophic exponential slowdown that occurs when processing 50,000 real production rows limit.

**3. How would you fix it?**
Push all processing (filtering, sorting, and joining tables) directly into the PostgreSQL database.
```python
from django.http import JsonResponse
from django.utils import timezone

def overdue_report(request):
    # Offload filtering, sorting, and joining to the database
    checkouts = CheckOut.objects.filter(
        returned_at__isnull=True, 
        due_at__lt=timezone.now()
    ).select_related('asset', 'employee').order_by('due_at')

    rows = [{
        "asset": c.asset.name,
        "asset_tag": c.asset.asset_tag,
        "employee": c.employee.full_name,
        "days_overdue": (timezone.now() - c.due_at).days,
    } for c in checkouts]

    return JsonResponse({"count": len(rows), "rows": rows})
```

**4. What test or tooling would have caught this?**
* Installing `django-debug-toolbar` or `nplusone` during local development would instantly flag the hundreds of duplicate SQL queries being fired per page load.
* Writing a performance/load test that runs against a seeded database of 10,000 rows would have caused local testing to visibly lock up.

---

## Snippet 2 — Check-out Endpoint

**1. What is wrong?**
* **Severe Race Condition:** There is no database lock. If Alice and Bob both request the item simultaneously, both threads hit `asset.status != "AVAILABLE"` (which is True for both), and both proceed to `.create()` a checkout for the same asset.
* **No Transaction Atomicity:** The checkout is created *before* the asset status is updated. If `asset.save()` crashes (e.g. database disconnect, validation error), the database is left in a corrupted state where a `CheckOut` row exists, but the asset is still `AVAILABLE` for someone else to take.
* **Unhandled 500 Server Errors:** Using `.get()` throws a fatal `ObjectDoesNotExist` exception if the `asset_tag` or `employee_code` is mistyped, resulting in a nasty 500 crash instead of a graceful 404.

**2. Why does it look correct in local testing?**
A single developer clicking buttons in a UI one-by-one is single-threaded. Because the developer is not firing two concurrent API requests at the identical millisecond, the race condition is never triggered. The developer also usually clicks on valid assets, never typing an invalid `asset_tag` to discover the fatal 500 error.

**3. How would you fix it?**
```python
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

@api_view(["POST"])
def check_out_asset(request):
    # Wrap in transaction to guarantee atomicity
    with transaction.atomic():
        # lock the row safely, 404 if invalid item
        asset = get_object_or_404(
            Asset.objects.select_for_update(), 
            asset_tag=request.data["asset_tag"]
        )
        
        if asset.status != "AVAILABLE":
            return Response({"detail": "not available"}, status=409)

        employee = get_object_or_404(Employee, employee_code=request.data["employee_code"])
        
        open_count = CheckOut.objects.filter(
            employee=employee, returned_at__isnull=True
        ).count()
        
        if open_count >= 3:
            return Response({"detail": "limit reached"}, status=409)

        checkout = CheckOut.objects.create(
            asset=asset,
            employee=employee,
            due_at=request.data["due_at"],
        )
        
        asset.status = "CHECKED_OUT"
        asset.save(update_fields=['status'])  # optimization trick
        
        return Response({"id": checkout.id}, status=201)
```

**4. What test or tooling would have caught this?**
Writing a concurrent Python test using `threading.Thread` or `multiprocessing` to programmatically blast the endpoint with two identical checkout payloads simultaneously would guarantee that the state corrupts and fails the test.

---

## Snippet 3 — Nightly Notice Task

**1. What is wrong?**
* **Non-Idempotent (Spamming risk):** The task is blindly creating rows. If `deliver_email` hits a rate-limit API error on loop iteration 5,000, Celery will automatically crash and retry the task later. During the retry, rows 1 to 4,999 will receive a *second* duplicate overdue notice and a duplicate email.
* **RAM Exhaustion:** The query does not use `.iterator()`. By loading tens of thousands of rows entirely into memory at once, the Celery worker will likely hit OOM (Out Of Memory) limits and be killed by the OS.
* **Celery Anti-Pattern:** Passing a complex Python class instance (`c.employee, c`) via `deliver_email.delay()` forces Celery to use brittle serialization (Pickle/JSON). State could mutate before the email actually fires.

**2. Why does it look correct in local testing?**
Locally, the queue empties instantly because there are no API rate-limits to simulate network crashes mid-loop, and there are only a handful of test rows so the RAM usage sits safely low.

**3. How would you fix it?**
```python
from celery import shared_task
from django.utils import timezone

@shared_task
def send_overdue_notices():
    # Use iterator() to stream rows heavily saving memory
    overdue = CheckOut.objects.filter(
        returned_at__isnull=True,
        due_at__lt=timezone.now(),
    ).iterator(chunk_size=500)
    
    count = 0
    today = timezone.now().date()
    
    for c in overdue:
        # Use get_or_create to make the task perfectly idempotent
        notice, created = OverdueNotice.objects.get_or_create(
            checkout=c, 
            notice_date=today
        )
        
        # Only email if this is a brand new notice
        if created:
            # Pass only primitive IDs over the wire, never full objects
            deliver_email.delay(c.employee.id, c.id)
            count += 1
            
    return "sent %d notices" % count
```

**4. What test or tooling would have caught this?**
An "idempotency test" where you explicitly call the `send_overdue_notices()` function twice in a row within the same test case. The test should `assert` that exactly 1 notice exists per overdue checkout, rather than randomly doubling.


---

## Part C — Optimise a slow PostgreSQL query

**1. Rewrite the query**
``sql
SELECT c.*
FROM checkouts c
INNER JOIN employees e ON c.employee_id = e.id
WHERE c.checked_out_at >= '2026-01-01 00:00:00+00' 
  AND c.checked_out_at < '2026-07-01 00:00:00+00'
  AND c.returned_at IS NULL
  AND e.is_active = true
ORDER BY c.due_at ASC;
``
*What was changed:*
* **Sargable Date Filtering:** Using the DATE() function wrapped around c.checked_out_at dynamically casted every row at runtime, rendering any B-Tree indexes on that column absolutely useless. I rewrote it as bounding variables (>= and <).
* **Explicit JOIN:** Changed the nested IN (SELECT ...) subquery to a rigorous INNER JOIN. While modern PostgreSQL planners can often flatten IN clauses, an explicit join offers the query planner the highest deterministic chance to utilize nested loop paths or hash joins efficiently.

**2. Index Strategy**
``sql
CREATE INDEX idx_checkouts_open_performance 
ON checkouts (employee_id, checked_out_at, due_at) 
WHERE returned_at IS NULL;
``
*Why this index earns its place (The Ponytail approach):*
* **Partial Indexing:** We specifically append WHERE returned_at IS NULL. Assuming this is a standard physical asset tracker, 99% of 4.2 million historically checked-out items have been returned. A standard composite index would index all 4.2 million rows, wasting memory. By adding this partial clause, the index theoretically shrinks to only track the tiny subset of currently open checkouts (perhaps 10,000 rows). It is extremely cheap to load into RAM.
* **Composite Strategy:** We index employee_id to rapidly complete the INNER JOIN, include checked_out_at to resolve the date filter boundary, and include due_at so the database can fulfill the ORDER BY clause directly from the index tree without needing an in-memory Sort node.

**3. EXPLAIN (ANALYZE, BUFFERS)**
* **Before:** EXPLAIN would show a brutal Seq Scan on checkouts (cost=...). The BUFFERS would show shared hit or 
ead numbering in the millions (thrashing disk I/O) as it loads 4 million rows into RAM. Next, you would see a massive Sort Method: quicksort (Memory) node consuming megabytes of work memory to arrange them by due_at.
* **After:** The specific line that proves the fix worked will be an **Index Scan using idx_checkouts_open_performance on checkouts c**. The buffers hit metric will drop from millions to roughly a few dozen block hits, and the Sort node will completely vanish (or become trivial) because the values emerge pre-sorted via due_at.

**4. Scalability at 8,000 rows/day**
* **What breaks first:** At ~3 million rows a year, the core problem becomes bloated cold data. Standard B-Tree insertion performance degrades logarithmically, and Autovacuum processes start lagging, causing fragmentation. Even if the partial index stays small, the raw disk footprint of the 4GB+ monolithic table slows down page caching and inserts.
* **What to do:** **Table Partitioning (Range)**. Before the table hits 10 million rows, partition checkouts natively by checked_out_at (e.g., partitioned by Month). PostgreSQL can then automatically drop entire historical partitions off the query plan (Partition Pruning), guaranteeing queries never scan decade-old tables.

**5. One thing to measure on the real DB**
* **Measure:** The exact cardinality of open checkouts: SELECT COUNT(*) FROM checkouts WHERE returned_at IS NULL;
* **Why:** The entire elegance of the Partial Index relies on the assumption that open checkouts are a minority. If some system bug previously left 4 million items marked open forever, the partial index becomes just as aggressively bloated as a standard index, and its performance benefit vanishes. I cannot be certain it is the right call until I know how big that subset actually is.

---

## Part D — Production reasoning

### D1. Zero-downtime migration
Adding a strict `NOT NULL` column to a 4.2-million row table simultaneously locks the table and crashes old in-flight requests that try to insert rows without it. The zero-downtime sequence requires spreading this across multiple deploy phases:

1. **Deploy 1 (Schema prep):** Run a migration to `ALTER TABLE checkouts ADD COLUMN location_id bigint NULL`. The column is strictly nullable. In-flight old code runs perfectly fine, ignoring it.
2. **Deploy 2 (Dual Write):** Update Django application code to actively write `location_id` for all *new* checkouts.
3. **Deploy 3 (Backfill):** Run a background script that batches through historical rows 10,000 at a time, calculating and updating their `location_id`.
4. **Deploy 4 (Constraint Lock):** Run `ALTER TABLE checkouts ADD CONSTRAINT location_id_not_null CHECK (location_id IS NOT NULL) NOT VALID;`. This adds the rule instantly without checking old rows. Then run `ALTER TABLE checkouts VALIDATE CONSTRAINT location_id_not_null;` which scans the table without taking an exclusive lock.
5. **Deploy 5 (App enforce):** Update Django `models.py` setting `null=False`.

**The Fatal Lock:** Running a naive `ALTER TABLE checkouts ADD COLUMN location_id bigint NOT NULL DEFAULT X;` takes an aggressive `ACCESS EXCLUSIVE` lock on the entire table to rewrite 4.2 million rows to apply the default, triggering immediate site-wide downtime and 502 gateway timeouts across the load balancer.

### D2. Latency triage
1. **Check APM/Tracing (Datadog/NewRelic):** Look at the slow trace. Is the 25 seconds spent executing SQL in the database, or burning CPU in Python? (Rules out a Django-level bug vs a PostgreSQL issue).
2. **Check Database Server Metrics:** Are CPU and Disk I/O pegged? If yes, look at `pg_stat_activity` to find the exact blocking query locking out the report view.
3. **Check `pg_stat_statements`:** Specifically look at the execution plan of the overdue query today versus yesterday.

**The two most likely causes (assuming no code/deploy changes):**
* **Cause 1: Data Bloat Planner Shift.** The table finally grew past a critical memory threshold, causing the PostgreSQL Query Planner to randomly flip from a fast Index Scan to a devastating Sequential Scan. 
  * *Confirmation:* Run `EXPLAIN ANALYZE` on the query in production. If it shows a `Seq Scan` with thousands of milliseconds of execution, the planner flipped.
* **Cause 2: Dead Tuple Bloat (Autovacuum failure).** Due to heavy UPDATE traffic on 8,000 queries a day, millions of "deleted" historical row fragments are silently crowding the disk because the Autovacuum worker crashed or couldn't keep up.
  * *Confirmation:* Query `pg_stat_user_tables`. If `n_dead_tup` is massive and `last_autovacuum` hasn't run recently, PostgreSQL is scanning through garbage data disguised as real rows.

### D3. CI/CD and safety
**The Pipeline Setup:**
* **On Pull Request:** Run the test suite (`pytest`), linting (`flake8`/`black`), and critically, run `python manage.py makemigrations --check` to automatically fail the PR if a developer tweaked `models.py` but forgot to generate the migration file!
* **On Merge to Main:** Build the Docker image, tag it with the Git SHA, and push to the container registry. Automatically deploy this image to the Staging environment and fire a suite of HTTP integration tests against it.
* **Production Gate:** Require a manual approval click in GitHub Actions UI.

**Migration Sequencing:**
Migrations must run in a "Release Phase" container that spins up, runs `python manage.py migrate`, and dies — *before* any of the four Web application load-balancer instances are told to reboot to the new `main` code.

**The Rollback Story:**
Zero-downtime rollbacks dictate that you **never** rollback the database schema. If the new deploy crashes, we instantly roll the Docker container image back to the previous stable SHA. Because we strictly mandate that all new Django code is written to be backwards-and-forwards compatible with the database schema (e.g., adding nullable columns, removing code readers *before* dropping columns natively), rolling the app code back over a migrated database causes no collision.
