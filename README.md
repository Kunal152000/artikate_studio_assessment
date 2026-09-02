# Artikate Assessment - Field Asset Check-Out Service

This repository contains the completed Backend Developer Assessment (Parts A, B, C, and D) for Artikate.

## Setup Instructions (Run Locally)

Here are the exact commands, in order, to start the API from a fresh clone.
*(Requires Docker and Git installed on your system).*

```bash
# 1. Clone the repository and enter the directory
git clone <your-repo-url>
cd artikate_studio

# 2. Start the entire stack in the background (PostgreSQL, Redis, Web, Celery Worker)
docker compose up -d

# 3. Wait ~5-10 seconds for the PostgreSQL database container to become fully healthy
sleep 10

# 4. Run the database migrations
docker compose exec web python manage.py migrate

# 5. Populate the database with the seed demo data (8 assets, 4 employees, and checkouts)
docker compose exec web python manage.py seed_demo_data

# 6. Create the administrator account (you will be prompted to set a password)
docker compose exec web python manage.py createsuperuser --username admin --email admin@example.com

# 7. Generate a DRF Authentication Token for the API
docker compose exec web python manage.py drf_create_token admin
```

*(Copy the token generated in Step 7 to authenticate your API requests).*

---

## Screen Recording

**Link:** 
https://drive.google.com/file/d/1Oc01LqsWvzKvki8dYwK3nx0OuwebpFa1/view?usp=sharing
---

## Assumptions Made

1. **Authentication Mechanism (`A3`)**: I opted to use standard DRF Token Authentication (`rest_framework.authtoken`) rather than JWT or Session authentication. It provides secure, robust stateless CLI authentication without requiring third-party libraries (matching the "lazy senior developer" rule of using built-ins).
2. **"Worker" and "Beat" Containers (`A4`, `A7`)**: In the `docker-compose.yml`, the Celery worker and scheduler run in a single combined `worker` container via the command `celery worker -B`. For a massive production system these would be isolated, but conserving resources is strictly preferable for this assessment scale.
3. **Paging/Pagination (`A3`)**: I enabled DRF `PageNumberPagination` globally in `settings.py` (configured strictly to 20 per page). This guarantees that list endpoints inherently meet the pagination specification without replicating boilerplate across individual views.
4. **Timezones**: UTC is used globally as configured strictly in `settings.py`. Time calculations run against database-native timezone checking.
5. **PostgreSQL Porting**: Port 5432 is explicitly bound. If a conflict occurs with a native Windows postgres installation, binding is adjusted to bypass OS constraints.

---

## Known Gaps

There are **zero known gaps**. 
All requirements set forth in the assessment specification have been completely addressed:
* Part A (Models, 8 Endpoints, DB-level Concurrency Locks, DRF Token Auth, Celery Background Task + strictly enforced Idempotency, Docker Compose natively, Seed Script, and Pytest coverage).
* Part B, C, and D diagnostics are answered in `ANSWERS.md`.

---

## Running Tests

To run the test suite (which crucially includes a `TransactionTestCase` for thread-based concurrency testing):
```bash
docker compose exec web pytest -v
```
