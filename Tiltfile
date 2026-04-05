# ── LGTM Tutorial Tiltfile ────────────────────────────────────────────────────
# Orchestrates the full Loki + Grafana + Tempo + Mimir stack via Docker Compose.
#
# Usage:
#   tilt up          # start everything
#   tilt down        # stop and remove containers (volumes are preserved)
#   tilt down -v     # stop and delete volumes too (full reset)
#
# Docs: https://docs.tilt.dev/api.html

# Load Docker Compose services into Tilt
docker_compose("docker-compose.yml")

# ── Labels (group resources in the Tilt UI sidebar) ───────────────────────────

dc_resource("mimir",    labels=["observability"])
dc_resource("loki",     labels=["observability"])
dc_resource("tempo",    labels=["observability"])
dc_resource("grafana",  labels=["observability"])
dc_resource("demo-app", labels=["app"])

# ── Links (clickable shortcuts in the Tilt UI) ────────────────────────────────

dc_resource("grafana", links=[
    link("http://localhost:3000",                                        "Grafana"),
    link("http://localhost:3000/d/lgtm-tutorial-overview",              "LGTM Overview Dashboard"),
    link("http://localhost:3000/explore",                                "Explore"),
])

dc_resource("mimir", links=[
    link("http://localhost:9009/prometheus/api/v1/metadata",             "Mimir — metric metadata"),
    link("http://localhost:9009/memberlist",                             "Mimir — memberlist ring"),
])

dc_resource("loki", links=[
    link("http://localhost:3100/ready",                                  "Loki — ready"),
    link("http://localhost:3100/loki/api/v1/labels",                    "Loki — labels"),
])

dc_resource("tempo", links=[
    link("http://localhost:3200/ready",                                  "Tempo — ready"),
    link("http://localhost:3200/api/echo",                               "Tempo — echo"),
])

dc_resource("demo-app", links=[
    link("http://localhost:8080/docs",                                   "Demo App — Swagger UI"),
    link("http://localhost:8080/api/health",                             "Demo App — Health"),
    link("http://localhost:8080/api/orders",                             "Demo App — Orders"),
])

# ── Dependency ordering ───────────────────────────────────────────────────────
# Tilt infers ordering from docker-compose depends_on, but we make it explicit.

dc_resource("loki",     resource_deps=["mimir"])
dc_resource("tempo",    resource_deps=["mimir"])
dc_resource("grafana",  resource_deps=["mimir", "loki", "tempo"])
dc_resource("demo-app", resource_deps=["mimir", "loki", "tempo"])

