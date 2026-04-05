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
| Grafana | http://localhost:3000 | Dashboards & Explore |
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

### Mimir — metrics

[Grafana Mimir](https://grafana.com/docs/mimir/latest/) is a horizontally scalable, multi-tenant Prometheus-compatible metrics backend. Here it runs in **single-binary mode** — one process handles ingestion, querying, and compaction.

Metrics are received via **OTLP HTTP** (`POST /otlp/v1/metrics`) on port 9009 — the same port as the Prometheus API.

| Port | Protocol | Purpose |
|------|----------|---------|
| 9009 | HTTP | Prometheus API (`/prometheus`), `remote_write`, OTLP metrics |
| 9095 | gRPC | Internal gRPC |

**Config:** `config/mimir/mimir.yaml`

> Note: `blocks_storage.filesystem.dir` (object store chunks) and `blocks_storage.tsdb.dir` (WAL/head) must be different paths. This is a common misconfiguration to watch out for.

### Loki — logs

[Grafana Loki](https://grafana.com/docs/loki/latest/) stores logs indexed by labels, not by content. Here it runs in **single-binary mode** with a local filesystem backend.

Logs are received via the **OTLP HTTP** endpoint (`POST /otlp/v1/logs`), supported since Loki v2.9. Every log record includes `trace_id` and `span_id` injected by the OTel SDK, enabling log→trace correlation in Grafana.

| Port | Protocol | Purpose |
|------|----------|---------|
| 3100 | HTTP | OTLP logs, push API, query API |
| 9096 | gRPC | Internal gRPC |

**Config:** `config/loki/loki.yaml`

### Tempo — traces

[Grafana Tempo](https://grafana.com/docs/tempo/latest/) is a distributed tracing backend compatible with Jaeger, Zipkin, and OTLP. It runs in **single-binary mode** and includes a **metrics generator** that derives RED (rate/error/duration) metrics from incoming spans and pushes them to Mimir via `remote_write`. This powers the Service Graph and Span Metrics panels in Grafana without any extra instrumentation.

| Port | Protocol | Purpose |
|------|----------|---------|
| 3200 | HTTP | Tempo query API |
| 4317 | gRPC | OTLP trace ingestion |
| 4318 | HTTP | OTLP trace ingestion (alternative) |

**Config:** `config/tempo/tempo.yaml`

### Grafana — visualization

Pre-provisioned with all datasources and a ready-to-use dashboard. Login is required. Default credentials: `admin` / `admin` (Grafana will prompt you to change the password on first login).

**Datasources provisioned:** `config/grafana/provisioning/datasources/datasources.yaml`

**Correlations wired:**

| From | To | How |
|------|----|-----|
| Trace span | Loki logs | Filters by `trace_id` matching the span |
| Log line | Tempo trace | Matches `"trace_id"` field in the log |
| Trace span | Mimir metrics | Links to RED metrics for the same service |
| Metric data point | Tempo trace | Exemplar links back to the generating trace |

### Demo App — signal generator

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

## Exploring in Grafana

### Dashboard

Open **http://localhost:3000/d/lgtm-tutorial-overview** for the pre-built overview with:
- Request rate, error rate, and p50/p99 latency (from Tempo span metrics in Mimir)
- Per-endpoint HTTP metrics (from demo-app custom counters)
- Live log stream (from Loki)

### Logs (Loki)

Go to **Explore → Loki**:

```logql
# All demo-app logs
{service_name="demo-app"} | json

# Errors only
{service_name="demo-app"} | json | severity_text="ERROR"

# Slow inventory requests
{service_name="demo-app"} | json | code_function="check_inventory"
```

### Traces (Tempo)

Go to **Explore → Tempo** and use TraceQL:

```
# All error traces
{ status = error }

# Slow doohickey requests (> 500 ms)
{ .inventory.item = "doohickey" } | duration > 500ms
```

### Metrics (Mimir)

Go to **Explore → Mimir**:

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

### Cross-signal correlation

1. **Log → Trace:** In Explore → Loki, find any log line with a `trace_id` field → click **View Trace** → opens in Tempo
2. **Trace → Log:** In Explore → Tempo, open a trace, click a span → **Logs for this span** → opens in Loki
3. **Metric → Trace:** In Explore → Mimir, hover a data point with an exemplar diamond → click to open the trace in Tempo

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
│       │       └── dashboards.yaml     # Dashboard provider (watches /var/lib/grafana/dashboards)
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
