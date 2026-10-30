"""Prometheus metrics for observability."""
import re
import threading

from prometheus_client import Counter, Histogram, Gauge, Info, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response

# ── Request Metrics ──

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"]
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

# ── Business Metrics ──

RSS_FETCH_COUNT = Counter(
    "rss_fetch_total",
    "Total RSS fetches",
    ["source"]
)

RSS_FETCH_ERRORS = Counter(
    "rss_fetch_errors_total",
    "RSS fetch errors",
    ["source", "error_type"]
)

RSS_ITEMS_FETCHED = Histogram(
    "rss_items_fetched",
    "Items fetched per source",
    ["source"],
    buckets=[1, 5, 10, 25, 50, 100, 250, 500, 1000]
)

DISPATCH_SENT = Counter(
    "dispatch_sent_total",
    "Total Discord notifications sent",
    ["source", "status"]
)

DISPATCH_LATENCY = Histogram(
    "dispatch_duration_seconds",
    "Discord notification latency",
    ["source"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0]
)

DISPATCH_ERRORS = Counter(
    "dispatch_errors_total",
    "Discord notification errors",
    ["error_type"]
)

COVER_BACKFILL_ERRORS = Counter(
    "cover_backfill_errors_total",
    "Cover backfill failures from series_meta",
    ["source"]
)

PARTIAL_INSERT_FAILURES = Counter(
    "partial_insert_failures_total",
    "Partial batch insert failures (some chunks failed)",
    ["action", "source"]
)

REDIS_QUEUE_DEPTH = Gauge(
    "redis_queue_depth",
    "Redis cron queue depth",
    ["queue"]  # main, processing, dlq
)

# ── System Metrics ──

ACTIVE_JOBS = Gauge(
    "active_jobs",
    "Currently running cron jobs",
    ["job_type"]
)

CIRCUIT_BREAKER_STATE = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=half-open, 2=open)",
    ["service"]
)

DB_POOL_SIZE = Gauge(
    "db_pool_connections",
    "DB connection pool size",
    ["state"]  # active, idle
)

APP_INFO = Info(
    "app_info",
    "Application information"
)


# ── Label cardinality guards ──
#
# A raw request path or error string used directly as a label value creates a
# new time series per distinct value — a UUID or free-text error in the path
# grows the series set without bound until the scrape is unusable. Endpoints
# are normalised to a route pattern and every label value set is capped; the
# overflow lands in a single "other" series instead of a new one.

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_NUM_SEG_RE = re.compile(r"^\d+$")
# A path segment longer than this is treated as an opaque id/slug, not a
# literal route part — real route segments are short words.
_LONG_SEG = 40

_endpoint_lock = threading.Lock()
_endpoint_seen: set[str] = set()
_ENDPOINT_CAP = 200

_label_lock = threading.Lock()
_label_seen: dict[str, set[str]] = {}
_LABEL_CAP = 50


def normalize_endpoint(path: str) -> str:
    """Collapse a request path to a bounded route pattern.

    UUIDs, numeric segments and long opaque segments become placeholders, so
    /api/v1/series/<uuid> and /api/v1/series/<other-uuid> share one series.
    """
    if not path:
        return "/"
    p = _UUID_RE.sub("{id}", path)
    segs = p.split("/")
    out = []
    for s in segs:
        if _NUM_SEG_RE.match(s) or len(s) > _LONG_SEG:
            out.append("{id}")
        else:
            out.append(s)
    return "/".join(out)


def _bounded_label(metric: str, value: str) -> str:
    """Cap the distinct values of a free-text label; overflow -> 'other'."""
    if value is None:
        value = ""
    value = str(value)
    with _label_lock:
        seen = _label_seen.setdefault(metric, set())
        if value in seen or len(seen) < _LABEL_CAP:
            seen.add(value)
            return value
        return "other"


def _bounded_endpoint(path: str) -> str:
    norm = normalize_endpoint(path)
    with _endpoint_lock:
        if norm in _endpoint_seen or len(_endpoint_seen) < _ENDPOINT_CAP:
            _endpoint_seen.add(norm)
            return norm
        return "other"


def track_request(method: str, endpoint: str, status: int, duration: float):
    """Record request metrics."""
    ep = _bounded_endpoint(endpoint)
    REQUEST_COUNT.labels(method=method, endpoint=ep, status=status).inc()
    REQUEST_LATENCY.labels(method=method, endpoint=ep).observe(duration)


def track_rss_fetch(source: str, item_count: int, error: str = None):
    """Track RSS fetch."""
    if error:
        RSS_FETCH_ERRORS.labels(source=source, error_type=_bounded_label("rss_error", error)).inc()
    else:
        RSS_FETCH_COUNT.labels(source=source).inc()
        RSS_ITEMS_FETCHED.labels(source=source).observe(item_count)


def track_dispatch(source: str, status: str, duration: float):
    """Track dispatch."""
    DISPATCH_SENT.labels(source=source, status=status).inc()
    DISPATCH_LATENCY.labels(source=source).observe(duration)


def track_dispatch_error(error_type: str):
    """Track dispatch error."""
    DISPATCH_ERRORS.labels(error_type=_bounded_label("dispatch_error", error_type)).inc()


def get_metrics():
    """Generate Prometheus metrics output."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )
