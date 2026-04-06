# LGTM Tutorial

Local observability stack — **L**oki · **G**rafana · **T**empo · **M**imir — running in Docker, orchestrated by Tilt.

```
demo-app ──OTLP gRPC──▶ Tempo  (traces)
         ──OTLP HTTP──▶ Mimir  (metrics)
         ──OTLP HTTP──▶ Loki   (logs)

Tempo ──span metrics──▶ Mimir  (RED metrics derived from traces)

Grafana ◀── queries ── Mimir, Loki, Tempo
```

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Docker Desktop | ≥ 4.x | https://docs.docker.com/get-docker/ |
| Tilt | ≥ 0.37 | `curl -fsSL https://raw.githubusercontent.com/tilt-dev/tilt/master/scripts/install.sh \| bash` |

## Quick start

```bash
git clone <this-repo>
cd LGTM-tutorial
tilt up
```

Tilt opens a browser at `http://localhost:10350` showing all services. Once everything is green (~1–2 min on first run due to image pulls):

| Service | URL | Purpose |
|---------|-----|---------|
| Grafana | http://localhost:3000 | Dashboards & Explore — **start here** |
| Tilt UI | http://localhost:10350 | Service status & logs |
| Demo App | http://localhost:8080/docs | Swagger UI |
| Mimir | http://localhost:9009/prometheus | Prometheus-compat query API |
| Loki | http://localhost:3100 | Log query API |
| Tempo | http://localhost:3200 | Trace query API |

Open the **LGTM Tutorial — Overview** dashboard in Grafana. It starts populating within ~30 seconds because the demo app has a built-in traffic generator — no manual steps needed.

To stop:

```bash
tilt down          # stop containers, keep volumes
tilt down -v       # full reset (deletes all stored data)
```

## Stack components

### Grafana — visualization

**URL:** http://localhost:3000
**Login:** `admin` / `admin` (Grafana will prompt you to change the password on first login)

[Grafana](https://grafana.com/docs/grafana/latest/) is the front-end for the entire stack. It does not store any observability data itself — it queries Mimir, Loki, and Tempo and visualises the results in dashboards and the Explore view. Think of it as the single pane of glass over all three backends.

Pre-provisioned with all datasources and a ready-to-use dashboard.

**Correlations wired:**

| From | To | How |
|------|----|-----|
| Trace span | Loki logs | Filters by `trace_id` matching the span |
| Log line | Tempo trace | Matches `"trace_id"` field in the log |
| Trace span | Mimir metrics | Links to RED metrics for the same service |
| Metric data point | Tempo trace | Exemplar links back to the generating trace |

**Config:** `config/grafana/provisioning/`

---

### Mimir — metrics

**URL:** http://localhost:9009/prometheus
**Grafana datasource:** Explore → select **Mimir**

[Grafana Mimir](https://grafana.com/docs/mimir/latest/) is a long-term metrics store that is fully compatible with Prometheus. You query it using PromQL, the same query language as Prometheus. The key difference from plain Prometheus is that Mimir is designed to scale horizontally and retain metrics for months or years, whereas Prometheus is typically short-lived.

In this stack Mimir runs in **single-binary mode** — one process handles ingestion, querying, and compaction. Metrics arrive from two sources:
- The **demo-app** pushes custom app metrics (request counts, latency histograms) via OTLP HTTP
- **Tempo** pushes span metrics (RED metrics derived from traces) via `remote_write`

| Port | Protocol | Purpose |
|------|----------|---------|
| 9009 | HTTP | Prometheus API (`/prometheus`), `remote_write`, OTLP metrics |
| 9095 | gRPC | Internal gRPC |

**Config:** `config/mimir/mimir.yaml`

> Note: `blocks_storage.filesystem.dir` (object store chunks) and `blocks_storage.tsdb.dir` (WAL/head) must be different paths — a common misconfiguration.

---

### Loki — logs

**URL:** http://localhost:3100
**Grafana datasource:** Explore → select **Loki**

[Grafana Loki](https://grafana.com/docs/loki/latest/) is a log aggregation system. Unlike traditional log tools (e.g. Elasticsearch) it does **not** full-text index log content — it only indexes the labels attached to a log stream (e.g. `service_name`, `severity_text`). This makes it cheap to run and fast to ingest. You query it using LogQL.

In this stack Loki receives logs from the demo-app via the **OTLP HTTP** endpoint (`POST /otlp/v1/logs`). Every log record automatically includes the `trace_id` and `span_id` from the active OTel span, which is what enables the log→trace jump in Grafana.

| Port | Protocol | Purpose |
|------|----------|---------|
| 3100 | HTTP | OTLP logs, push API, query API |
| 9096 | gRPC | Internal gRPC |

**Config:** `config/loki/loki.yaml`

---

### Tempo — traces

**URL:** http://localhost:3200
**Grafana datasource:** Explore → select **Tempo**

[Grafana Tempo](https://grafana.com/docs/tempo/latest/) is a distributed tracing backend. A **trace** represents a single request flowing through your system, broken into **spans** — one span per operation (HTTP handler, DB query, external call). Tempo stores these traces and lets you search them by service, duration, status, or any span attribute using TraceQL.

Tempo also runs a built-in **metrics generator** that reads the incoming spans and derives RED (Rate / Error / Duration) metrics from them, then pushes those metrics to Mimir via `remote_write`. This gives you service-level metrics without adding any extra instrumentation to your code.

| Port | Protocol | Purpose |
|------|----------|---------|
| 3200 | HTTP | Tempo query API |
| 4317 | gRPC | OTLP trace ingestion |
| 4318 | HTTP | OTLP trace ingestion (alternative) |

**Config:** `config/tempo/tempo.yaml`

---

### Demo App — signal generator

**URL:** http://localhost:8080/docs

A Python [FastAPI](https://fastapi.tiangolo.com/) application (`demo-app/main.py`) instrumented with the OpenTelemetry SDK. It sends all three signal types and runs a background traffic generator so dashboards are always live.

**Endpoints:**

```
GET  /api/health
GET  /api/orders
POST /api/orders            body: {"item": "widget", "quantity": 3}
GET  /api/inventory/{item}  items: widget, gadget, doohickey
```

Simulated behaviours:
- ~10% of `POST /api/orders` fail with 402 (payment declined) — visible in error rate panels
- `GET /api/inventory/doohickey` adds 0.3–1.2 s random delay — visible in latency panels

## How OpenTelemetry signals flow

```
demo-app/main.py
  │
  ├─ TracerProvider
  │    └─ BatchSpanProcessor
  │         └─ OTLPSpanExporter (gRPC) ──────────────────▶ Tempo:4317
  │
  ├─ MeterProvider
  │    └─ PeriodicExportingMetricReader (every 15s)
  │         └─ OTLPMetricExporter (HTTP) ─────────────────▶ Mimir:9009/otlp/v1/metrics
  │
  └─ LoggerProvider
       └─ BatchLogRecordProcessor
            └─ OTLPLogExporter (HTTP) ──────────────────▶ Loki:3100/otlp/v1/logs

Auto-instrumentation:
  FastAPIInstrumentor → server span per HTTP request
  LoggingInstrumentor → injects trace_id + span_id into every log record
  LoggingHandler      → bridges Python logging to the OTel LoggerProvider
  metrics_middleware  → records demo_http_requests_total + duration histogram
```

The `trace_id` injected into every log record is what allows Grafana to jump between a log line and the trace that produced it.

## Investigating an alert — step by step

This is the typical flow when something goes wrong.

### Step 1 — See the alert on the dashboard

Open the overview dashboard:
**http://localhost:3000/d/lgtm-tutorial-overview**

Log in with `admin` / `admin` if prompted.

The top row shows request rate, error rate, and latency. An error rate spike or latency jump is usually the first signal something is wrong. Note the **time window** when it started.

---

### Step 2 — Identify the problem in metrics (Mimir)

Go to: **http://localhost:3000/explore** → select datasource **Mimir**

Narrow down which endpoint or service is affected:

```promql
# Error rate by endpoint
sum by (endpoint) (rate(demo_http_requests_total{http_status=~"4..|5.."}[1m]))

# p99 latency by service
histogram_quantile(0.99,
  sum by (service_name, le) (
    rate(traces_spanmetrics_duration_seconds_bucket{span_kind="SPAN_KIND_SERVER"}[1m])
  )
)
```

Set the time range to cover the incident window. This tells you **what** is broken and **when** it started.

---

### Step 3 — Find the failing requests in logs (Loki)

Go to: **http://localhost:3000/explore** → select datasource **Loki**

Filter to the same time window and look for errors:

```logql
{service_name="demo-app"} | json | severity_text="ERROR"
```

Each log line shows the full context (endpoint, item, error message) and a `trace_id` field. Click **View Trace** on any error log line to jump directly to the trace that produced it.

---

### Step 4 — Inspect the trace (Tempo)

Go to: **http://localhost:3000/explore** → select datasource **Tempo**

You can arrive here from a log line (Step 3) or search directly:

```
{ status = error && .service.name = "demo-app" }
```

Open a trace to see the full span tree. Each span shows:
- How long it took
- Whether it errored and why
- Any attributes set by the code (e.g. `order.item`, `inventory.item`)

Click a span → **Logs for this span** to jump back to Loki filtered to that exact trace.

---

### Step 5 — Check the service graph

Go to: **http://localhost:3000/explore** → select datasource **Tempo** → switch to **Service Graph** tab

This shows request rate and error rate between services as a node graph, powered by the span metrics Tempo generates. Useful for spotting cascading failures across multiple services.

---

### Step 6 — Confirm recovery

After a fix is deployed, watch the metrics recover in real time on the dashboard or in Explore → Mimir:

```promql
sum by (endpoint) (rate(demo_http_requests_total[1m]))
```

---

### The thread connecting everything

```
Alert (Mimir metric spike)
  └─▶ Explore Loki — filter by time + severity="ERROR"
        └─▶ find trace_id in a log line → click "View Trace"
              └─▶ Explore Tempo — inspect the failing span
                    └─▶ click "Logs for this span" → back to Loki with full context
```

The `trace_id` is present in every log record and every trace span, so you never have to copy-paste IDs manually — Grafana follows the links automatically.

## Exploring in Grafana

### Dashboard

**http://localhost:3000/d/lgtm-tutorial-overview**

Pre-built overview with:
- Request rate, error rate, and p50/p99 latency (from Tempo span metrics in Mimir)
- Per-endpoint HTTP metrics (from demo-app custom counters)
- Live log stream (from Loki)

### Logs (Loki)

**http://localhost:3000/explore** → select **Loki**

```logql
# All demo-app logs
{service_name="demo-app"} | json

# Errors only
{service_name="demo-app"} | json | severity_text="ERROR"

# Slow inventory requests
{service_name="demo-app"} | json | code_function="check_inventory"
```

### Traces (Tempo)

**http://localhost:3000/explore** → select **Tempo**

```
# All error traces
{ status = error }

# Slow doohickey requests (> 500 ms)
{ .inventory.item = "doohickey" } | duration > 500ms
```

### Metrics (Mimir)

**http://localhost:3000/explore** → select **Mimir**

```promql
# Request rate by endpoint (custom app metric)
sum by (endpoint) (rate(demo_http_requests_total[1m]))

# Error rate by endpoint
sum by (endpoint) (rate(demo_http_requests_total{http_status=~"4.."}[1m]))

# p99 latency derived from Tempo span metrics
histogram_quantile(0.99,
  sum by (service_name, le) (
    rate(traces_spanmetrics_duration_seconds_bucket{span_kind="SPAN_KIND_SERVER"}[1m])
  )
)
```

## Project structure

```
LGTM-tutorial/
├── Tiltfile                            # Tilt orchestration (docker_compose + links + deps)
├── docker-compose.yml                  # All 5 service definitions
├── config/
│   ├── mimir/
│   │   └── mimir.yaml                  # Single-binary, filesystem backend
│   ├── loki/
│   │   └── loki.yaml                   # Single-binary, OTLP ingestion enabled
│   ├── tempo/
│   │   └── tempo.yaml                  # Single-binary + span metrics → Mimir
│   └── grafana/
│       ├── provisioning/
│       │   ├── datasources/
│       │   │   └── datasources.yaml    # Mimir + Loki + Tempo with full correlations
│       │   └── dashboards/
│       │       └── dashboards.yaml     # Dashboard provider
│       └── dashboards/
│           └── lgtm-overview.json      # Pre-built overview dashboard
└── demo-app/
    ├── main.py                         # FastAPI + full OTel instrumentation
    ├── requirements.txt
    └── Dockerfile
```

## Resetting state

```bash
tilt down -v   # stop containers and delete all Docker volumes
tilt up        # fresh start
```
