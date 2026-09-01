# Artikate Assessment - Part A

This repository contains the completed Part A of the Artikate Backend Developer Assessment.

## Quick Start (Docker)

To run the whole stack via Docker (Web, Worker, Redis, Postgres):

```bash
# 1. Start all containers in the background
docker-compose up -d

# 2. Run migrations
docker-compose exec web python manage.py migrate

# 3. Seed demo data (idempotent, 8 assets, 4 employees, 5 checkouts)
docker-compose exec web python manage.py seed_demo_data
```

## Running Tests

To run the tests (which include thread-based concurrency testing), do so against the Docker Postgres instance:
```bash
docker-compose exec web pytest
```

## Creating a Token / Testing the API

```bash
# Create a superuser and get a DRF Auth token
docker-compose exec web python manage.py createsuperuser --username admin --email admin@example.com
docker-compose exec web python manage.py drf_create_token admin

# Now hit the endpoints (replace TOKEN below with the token generated above)
export T="Token <your_token_here>"

# List assets
curl -H "Authorization: $T" http://localhost:8000/api/v1/assets/

# Overdue report
curl -H "Authorization: $T" http://localhost:8000/api/v1/reports/overdue/

# Health check (unauthenticated)
curl http://localhost:8000/api/v1/health/
```

## Assumptions Made

1. **Auth mechanism**: I chose DRF Token Authentication (`rest_framework.authtoken`) over JWT or Session Auth because it is standard for CLI/service-to-service REST APIs, requires no third-party packages, and matches the "lazy/boring" philosophy.
2. **"Worker" and "Beat"**: In `docker-compose.yml`, the Celery worker and scheduler run in a single `worker` container via `celery worker -B`. For a large production system these would be separated, but for a 4-container limit and resource conservation, this is the cleanest approach.
3. **Paging/Pagination**: I enabled DRF `PageNumberPagination` globally (configured to 20 per page) so list views are paginated exactly per spec without boilerplate in every view.
4. **Timezones**: UTC is used globally as configured in `settings.py`. Time calculations run against `timezone.now()`.

## Known Gaps

No known gaps. All requirements (Models, 8 Endpoints/Actions, Concurrency Locks, DRF Token Auth, Celery Background Task + Idempotency, Docker Compose, and Seed Script) are implemented natively.
