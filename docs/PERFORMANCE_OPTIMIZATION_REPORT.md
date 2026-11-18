# StudyHub Performance Audit and Optimization Report

## Executive summary

The production homepage is still slow, but this workspace has not been deployed.
The live service is serving an older release: `GET /healthz` returned 404 and
`/service-worker.js` still reported `studyhub-static-v1`. Consequently, no
production after-measurement is available.

The confirmed bottleneck is server response latency. Live browser measurements
showed a 6.3–6.4 second TTFB on both desktop and mobile, while FCP/LCP followed
roughly 0.25–0.33 seconds later. The page loaded only seven browser resources
and transferred about 47 KB in the latest sample. Frontend asset loading is
therefore not the main source of the observed homepage delay.

The exact slow database query is not yet confirmed. The deployed release does
not include the request/database/template timing instrumentation in this
workspace, and production database plans or Render CPU, memory, and connection
metrics were not available. The homepage source performs several sequential
database reads; the new telemetry will distinguish those operations after a
manual deployment. No cache or index was added based on speculation.

## Pages and routes audited

The route/template review covered the homepage, subjects and subject details,
Notes, Assignments, Books, Practicals, Question Papers, Important Questions,
Syllabus, search and suggestions APIs, material details/view/file/download
routes, login and registration, profile and favorites, notifications, admin
dashboard and management pages, plus health, robots, sitemap, manifest, and
service-worker routes.

Production page samples showed:

| Route/page | Production before | Notes |
|---|---:|---|
| `GET /` | 6.18–8.98 s | Single samples from access logs and browser runs; repeated requests remained slow |
| `HEAD /` | 6.18–6.96 s historically; 6.449 s latest | 200, no response body; live release does not have the workspace fast path |
| `/search` | 4.61 s | One production sample |
| Common public listing pages | 1.93–2.36 s | Range from representative single samples; not route-specific percentiles |
| `/material/4` | 2.475–9.56 s | Two samples varied substantially; database/storage phase was not available |
| `/login` / `/register` | 0.63 s / 0.34 s | One sample each |
| Authenticated user/admin pages | Redirected without credentials | No live credentials were used |
| `/subject/1` | 404 | The live catalog had other subject IDs; ID 1 was not a valid production sample |

These are not p50/p95/p99 values. There were not enough consistent production
samples to calculate meaningful percentiles.

## Current-workspace local measurements

The following are Flask test-client process times from three sequential
requests per route against an isolated, small SQLite test fixture. They are **not**
production TTFB measurements and must not be compared as production before/after
results. The medians below use all three samples shown; first-request startup
and template-warmup effects are visible in several raw sample lists.

| Route | Local median | Raw samples (ms) | Status |
|---|---:|---|---:|
| `/` | 13.88 ms | 126.42, 13.88, 8.72 | 200 |
| `/subjects` | 7.41 ms | 12.19, 7.41, 5.74 | 200 |
| `/subject/1` | 13.15 ms | 27.62, 8.31, 13.15 | 200 |
| `/notes` | 11.98 ms | 17.08, 7.35, 11.98 | 200 |
| `/assignments` | 7.88 ms | 12.40, 4.44, 7.88 | 200 |
| `/books` | 10.69 ms | 23.52, 10.69, 10.34 | 200 |
| `/practicals` | 7.05 ms | 22.20, 7.05, 6.36 | 200 |
| `/question-papers` | 11.37 ms | 21.44, 11.21, 11.37 | 200 |
| `/search?q=algebra` | 12.38 ms | 47.17, 11.92, 12.38 | 200 |
| `/login` | 4.02 ms | 32.52, 4.02, 3.74 | 200 |
| `/register` | 1.96 ms | 19.37, 1.96, 1.22 | 200 |
| `/profile` (test user) | 8.78 ms | 20.81, 8.78, 5.22 | 200 |
| `/favorites` (test user) | 8.65 ms | 29.52, 8.37, 8.65 | 200 |
| `/admin/login` | 2.71 ms | 19.35, 1.67, 2.71 | 200 |
| `/material/1` | 31.78 ms | 45.22, 31.78, 18.88 | 200 |

Three samples are insufficient for meaningful local p95/p99 values. These
results validate route behavior and local execution only; they do not establish
an improvement on Render.

## Browser and Core Web Vitals observations

The measurements below are lab observations from the live deployment, not
PageSpeed Insights field data or Lighthouse scores:

| Viewport | TTFB | FCP | LCP | CLS | Load |
|---|---:|---:|---:|---:|---:|
| Desktop, 1366×768 | 6.283 s | 6.528 s | 6.528 s | 0 | 6.539 s |
| Mobile, 390×844 | 6.426 s | 6.756 s | 6.756 s | 0 | 6.714 s |

INP was not measured because no user interaction was recorded. Lighthouse and
PageSpeed Insights were not run. The small asset count and short interval
between TTFB and paint point to the server response, rather than asset volume
or layout shift, as the primary homepage issue.

## Changes implemented

- **Per-request profiling:** `slow_request` records now include database
  acquisition, SQL execution and fetch time/counts, per-template render time,
  B2 `head_object` time/count, response byte length, endpoint, status, and a
  validated request ID. Slow SQL operations are logged by operation and table
  label without logging SQL parameters or secrets. Fast requests are not logged
  individually.
- **B2 client reuse:** `get_storage()` now retains one B2 storage/client
  instance per process and initializes it under a lock. This lets the existing
  boto client reuse its HTTP connection pool across requests; it does not cache
  object-existence results, change permissions, or alter signed URLs.
- **Material-page query reductions:** material detail and viewer routes reuse
  the guest view count already fetched for the limit check instead of querying
  that count again.
- **Profile query reduction:** the profile uses its request-cached user lookup
  and a count-only favorites query instead of loading and discarding a joined
  material card.
- **Admin query reduction:** the users page obtains the deletion-request badge
  through `COUNT(*)` instead of loading every pending request row.

No schema, database settings, indexes, cache policy, worker count, B2
permissions, PDF files, or deployment configuration were changed in this
optimization pass. Existing SEO, PWA, service worker, skeleton UI, and other
features were left intact.

Files changed in this optimization pass:

- [`app.py`](../app.py)
- [`utils/database.py`](../utils/database.py)
- [`utils/storage.py`](../utils/storage.py)
- [`models/material.py`](../models/material.py)
- [`models/user.py`](../models/user.py)
- [`routes/main.py`](../routes/main.py)
- [`routes/user.py`](../routes/user.py)
- [`routes/admin.py`](../routes/admin.py)
- [`tests/test_cloud_storage.py`](../tests/test_cloud_storage.py)
- [`tests/test_studyhub_features.py`](../tests/test_studyhub_features.py)
- [`docs/PERFORMANCE_OPTIMIZATION_REPORT.md`](./PERFORMANCE_OPTIMIZATION_REPORT.md)

## Database, Render, and storage findings

- The homepage source reads announcements, subjects, latest materials, featured
  materials, and aggregate counts sequentially. This is a plausible source of
  server-side delay, but the live release does not emit phase timings, so the
  slowest operation cannot yet be named conclusively.
- No production database access or query plan was available. No index was added
  without an explain plan.
- The PostgreSQL pool is thread-local and has no blocking global checkout
  queue, so pool wait/exhaustion cannot be inferred from queue time alone. With
  the current Procfile setting of one `gthread` worker and 100 threads, the
  existing two-idle-connections-per-thread limit allows up to 200 cached
  connections in theory. Actual concurrency and Render/Neon connection counts
  were unavailable; worker or pool sizing was not changed.
- The workspace Procfile binds to `0.0.0.0` and uses `${PORT:-10000}`. The live
  service responded to HTTP requests, but its `/healthz` returned 404, showing
  that the updated route is not deployed. The earlier “No open HTTP ports
  detected” warning cannot be tied to a specific startup cause without Render
  startup logs or dashboard metrics.
- B2 behavior was tested with fake S3 clients. No live PDF was downloaded or
  exposed during this audit. Actual B2 response timing will be available from
  the new request telemetry after deployment.

## Validation

- Full automated suite: **242 passed**.
- Python compilation, JavaScript syntax check, and `git diff --check`: passed.
- Focused tests cover B2 client reuse/timing, profile favorites count, admin
  deletion count, guest view-count reuse, and slow-request telemetry fields.
- The isolated SQLite benchmark exercised the listed public pages and
  authenticated profile/favorites routes. Admin protected pages and live
  authenticated workflows were not exercised with production credentials.
- No deployment, production write, migration, PDF download, or B2 permission
  change was performed.

## Remaining risks and deployment verification

The exact production bottleneck remains unverified until the workspace version
is deployed and the new timings are collected. The live app still has slow
homepage GET/HEAD responses, a missing `/healthz`, and service-worker v1.
Production CPU/memory, Render queueing, PostgreSQL connection count, live
EXPLAIN plans, and the B2 phase for `/material/4` remain unknown.

After a manual deployment:

1. Confirm `/healthz` returns 200 and the service worker reports the current
   cache version.
2. Capture request IDs and `slow_request`/`slow_database_operation` entries for
   repeated homepage, search, listing, and material requests. Compare database
   acquisition, SQL execution/fetch, template, and B2 timings before choosing
   further optimizations.
3. Collect at least 20–30 requests per public route under normal traffic before
   treating p50/p95 as representative; use a larger sample for p99.
4. Verify authenticated/admin routes and private PDF preview/download behavior
   through the normal authorized workflow.
5. If a rollback is needed, use Render’s prior successful deployment or revert
   the reviewed source changes; no production data rollback is required for
   this pass.
