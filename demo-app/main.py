"""
LGTM Tutorial — Demo Application
=================================
Demonstrates the three observability signal types flowing to the LGTM stack:

  Traces  → Tempo     (via OTLP gRPC)
  Metrics → Mimir     (via OTLP gRPC)
  Logs    → Loki      (via OTLP HTTP — Loki's /otlp/v1/logs endpoint)

A background task continuously hits the API endpoints so there is always
data flowing into Grafana without any manual effort.

Endpoints
---------
  GET  /api/health            — health check
  GET  /api/orders            — list orders (simulated)
  POST /api/orders            — create order (simulated, ~10% fail rate)
  GET  /api/inventory/{item}  — check inventory (simulated, sometimes slow)
"""

import asyncio
import logging
import os
import random
import time

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

# ── OpenTelemetry setup ───────────────────────────────────────────────────────

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs._internal import LoggingHandler
from opentelemetry.semconv.resource import ResourceAttributes

# ── Service identity ──────────────────────────────────────────────────────────

SERVICE_NAME = "demo-app"
SERVICE_VERSION = "1.0.0"

TEMPO_ENDPOINT  = os.getenv("TEMPO_ENDPOINT",  "http://tempo:4317")   # OTLP gRPC
MIMIR_ENDPOINT  = os.getenv("MIMIR_ENDPOINT", "http://mimir:9009")   # OTLP HTTP (/otlp/v1/metrics)
LOKI_ENDPOINT   = os.getenv("LOKI_ENDPOINT",  "http://loki:3100")    # OTLP HTTP (/otlp/v1/logs)

resource = Resource.create({
    ResourceAttributes.SERVICE_NAME: SERVICE_NAME,
    ResourceAttributes.SERVICE_VERSION: SERVICE_VERSION,
    ResourceAttributes.DEPLOYMENT_ENVIRONMENT: "local-dev",
})

# ── Traces → Tempo ────────────────────────────────────────────────────────────

trace_exporter = OTLPSpanExporter(endpoint=TEMPO_ENDPOINT)
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer(SERVICE_NAME)

# ── Metrics → Mimir ───────────────────────────────────────────────────────────
# Mimir accepts OTLP metrics via HTTP POST /otlp/v1/metrics on port 9009.

metric_exporter = OTLPMetricExporter(endpoint=f"{MIMIR_ENDPOINT}/otlp/v1/metrics")
metric_reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=15_000)
meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter(SERVICE_NAME)

# Custom instruments
http_requests_counter = meter.create_counter(
    "demo_http_requests_total",
    description="Total HTTP requests handled by demo-app",
    unit="1",
)
http_request_duration = meter.create_histogram(
    "demo_http_request_duration_seconds",
    description="HTTP request latency in seconds",
    unit="s",
)
active_orders_gauge = meter.create_up_down_counter(
    "demo_active_orders",
    description="Number of active orders currently in the system",
    unit="1",
)

# ── Logs → Loki ───────────────────────────────────────────────────────────────
# Loki 2.9+ accepts OTLP logs at POST /otlp/v1/logs (HTTP).

log_exporter = OTLPLogExporter(endpoint=f"{LOKI_ENDPOINT}/otlp/v1/logs")
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
set_logger_provider(logger_provider)

# Attach an OTel LoggingHandler to the root logger so that every
# logging.info/warning/error call is exported to Loki via the logger provider.
otel_log_handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
logging.getLogger().addHandler(otel_log_handler)

# Also inject trace_id / span_id into the log format for correlation in Loki.
LoggingInstrumentor().instrument(set_logging_format=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(SERVICE_NAME)

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="LGTM Demo App", version=SERVICE_VERSION)

# Auto-instrument FastAPI: creates a server span for every request
FastAPIInstrumentor.instrument_app(app)

# ── Middleware — record custom metrics for every request ──────────────────────

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    endpoint = request.url.path
    status   = str(response.status_code)

    http_requests_counter.add(1, {"endpoint": endpoint, "http_status": status})
    http_request_duration.record(duration, {"endpoint": endpoint})

    return response

# ── Simulated data store ──────────────────────────────────────────────────────

ORDERS: dict[str, dict] = {}
INVENTORY = {"widget": 100, "gadget": 50, "doohickey": 5}

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    logger.info("Health check called")
    return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}


@app.get("/api/orders")
async def list_orders():
    with tracer.start_as_current_span("list-orders") as span:
        span.set_attribute("orders.count", len(ORDERS))
        logger.info("Listing orders", extra={"orders_count": len(ORDERS)})
        return {"orders": list(ORDERS.values()), "total": len(ORDERS)}


@app.post("/api/orders")
async def create_order(request: Request):
    with tracer.start_as_current_span("create-order") as span:
        body = await request.json()
        item = body.get("item", "widget")
        qty  = body.get("quantity", 1)

        span.set_attribute("order.item", item)
        span.set_attribute("order.quantity", qty)

        # Simulate ~10% failure rate so error panels have something to show
        if random.random() < 0.10:
            span.set_status(trace.StatusCode.ERROR, "Simulated payment failure")
            logger.error(
                "Order creation failed — payment declined",
                extra={"item": item, "quantity": qty},
            )
            raise HTTPException(status_code=402, detail="Payment declined (simulated)")

        order_id = f"ORD-{int(time.time() * 1000) % 100_000}"
        ORDERS[order_id] = {"id": order_id, "item": item, "quantity": qty, "status": "confirmed"}
        active_orders_gauge.add(1)

        logger.info(
            "Order created",
            extra={"order_id": order_id, "item": item, "quantity": qty},
        )
        return ORDERS[order_id]


@app.get("/api/inventory/{item}")
async def check_inventory(item: str):
    with tracer.start_as_current_span("check-inventory") as span:
        span.set_attribute("inventory.item", item)

        if item not in INVENTORY:
            span.set_status(trace.StatusCode.ERROR, "Item not found")
            logger.warning("Inventory check — item not found", extra={"item": item})
            raise HTTPException(status_code=404, detail=f"Item '{item}' not found")

        # Simulate occasional slowness for "doohickey" — visible in latency panels
        if item == "doohickey":
            delay = random.uniform(0.3, 1.2)
            span.set_attribute("simulated_delay_ms", int(delay * 1000))
            await asyncio.sleep(delay)

        stock = INVENTORY[item]
        logger.info("Inventory check", extra={"item": item, "stock": stock})
        return {"item": item, "stock": stock}


# ── Background traffic generator ──────────────────────────────────────────────
# Generates a continuous stream of realistic-looking requests so that
# Grafana dashboards always have data to display.

async def _traffic_generator():
    """Calls our own API in a loop to produce continuous telemetry."""
    await asyncio.sleep(5)  # Wait for the server to be ready

    base_url = "http://localhost:8080"
    async with httpx.AsyncClient(base_url=base_url, timeout=5) as client:
        items = list(INVENTORY.keys()) + ["unknown-item"]
        while True:
            try:
                action = random.choices(
                    ["health", "list", "create", "inventory"],
                    weights=[10, 20, 40, 30],
                )[0]

                if action == "health":
                    await client.get("/api/health")

                elif action == "list":
                    await client.get("/api/orders")

                elif action == "create":
                    item = random.choice(list(INVENTORY.keys()))
                    await client.post(
                        "/api/orders",
                        json={"item": item, "quantity": random.randint(1, 10)},
                    )

                elif action == "inventory":
                    item = random.choice(items)
                    await client.get(f"/api/inventory/{item}")

            except Exception as exc:  # noqa: BLE001
                logger.warning("Traffic generator error", extra={"error": str(exc)})

            await asyncio.sleep(random.uniform(0.5, 2.0))


@app.on_event("startup")
async def startup():
    logger.info("demo-app starting", extra={"tempo": TEMPO_ENDPOINT, "mimir": MIMIR_ENDPOINT, "loki": LOKI_ENDPOINT})
    asyncio.create_task(_traffic_generator())


@app.on_event("shutdown")
async def shutdown():
    tracer_provider.shutdown()
    meter_provider.shutdown()
    logger_provider.shutdown()
