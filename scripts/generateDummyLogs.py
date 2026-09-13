"""
generate_logs.py
=================
Generates a large, realistic, and intentionally messy synthetic production
log file - good for stress-testing log readers, search tools, and analyzers.

What makes this "messy" on purpose:
  - Mixed line formats in one file: plain app logs, Apache/nginx access
    logs, Kubernetes-style events, and JSON-structured logs.
  - Multi-line Python tracebacks, including chained "Caused by" exceptions
    (the direct-cause chaining Python 3 itself produces).
  - Correlated INCIDENTS: a service starts throwing WARNINGs, escalates
    into a tight burst of ERRORs (with tiny timestamp gaps, like a real
    outage), and then recovers with a "circuit breaker closed" INFO line -
    rather than every line being independently random noise.
  - request_id / trace_id / user_id fields threaded through related lines
    so log-correlation tooling has something real to chew on.

Usage:
    python scripts/generateDummyLogs.py
    python scripts/generateDummyLogs.py --output sampleLogs/production.log --lines 500000 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import string
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


class LogGenerator:
    """
    Stateful generator that writes one synthetic log file per call to
    `generate()`. All randomness is instance-local (via `self._rng`) so
    multiple generators can run with different seeds without interfering
    with each other or with global `random` state elsewhere in a program.
    """

    # ------------------------------------------------------------------ #
    # Static content pools
    # ------------------------------------------------------------------ #

    SERVICES = [
        "AuthService",
        "PaymentService",
        "InventoryService",
        "OrderService",
        "GatewayService",
        "NotificationService",
        "Scheduler",
        "EmailService",
        "DatabaseService",
        "CacheService",
        "SearchService",
        "RecommendationService",
        "ShippingService",
        "FraudDetectionService",
        "SessionService",
        "BillingService",
    ]

    INFO_MESSAGES = [
        "Request received",
        "Request completed successfully",
        "Payment processed",
        "Inventory updated",
        "Cache hit",
        "Cache populated",
        "Email sent",
        "Job started",
        "Job completed",
        "Connection established",
        "Connection released",
        "Health check passed",
        "Retry succeeded",
        "Session created",
        "Session refreshed",
        "Token issued",
        "Webhook delivered",
        "Batch processed",
        "Configuration reloaded",
        "Leader election completed",
        "Circuit breaker closed, service recovered",
    ]

    WARNING_MESSAGES = [
        "Response time exceeded threshold",
        "Cache miss",
        "Disk usage above 85%",
        "Connection pool nearing capacity",
        "Inventory running low",
        "Multiple login failures detected",
        "Slow database query detected",
        "Rate limit approaching for client",
        "Retrying after transient failure",
        "Deprecated API endpoint called",
        "Memory usage above 80%",
        "Queue depth increasing",
        "Upstream latency degraded",
        "Clock skew detected between nodes",
    ]

    # Each scenario pairs a human-readable ERROR message with the exception
    # class/traceback that would plausibly produce it, so error text and
    # stack traces stay consistent with each other.
    ERROR_SCENARIOS = [
        {
            "message": "Database connection timeout",
            "exception_class": "TimeoutError",
            "exception_message": "Connection to database timed out after 30s (request_id={request_id})",
            "frames": [
                (
                    "/app/db/pool.py",
                    112,
                    "acquire",
                    "conn = self._wait_for_slot(timeout=30)",
                ),
                (
                    "/app/db/pool.py",
                    88,
                    "_wait_for_slot",
                    "raise TimeoutError(self._timeout_msg())",
                ),
            ],
        },
        {
            "message": "Payment gateway timeout",
            "exception_class": "requests.exceptions.Timeout",
            "exception_message": "HTTPSConnectionPool(host='payments.internal', port=443): Read timed out.",
            "frames": [
                (
                    "/app/payment.py",
                    81,
                    "process_payment",
                    "gateway.charge(card, amount)",
                ),
                (
                    "/app/payment/gateway.py",
                    47,
                    "charge",
                    "resp = self.session.post(url, json=payload, timeout=10)",
                ),
            ],
            "caused_by": {
                "exception_class": "socket.timeout",
                "exception_message": "timed out",
                "frames": [
                    (
                        "/usr/lib/python3.11/socket.py",
                        705,
                        "readinto",
                        "return self._sock.recv_into(b)",
                    ),
                ],
            },
        },
        {
            "message": "SMTP authentication failed",
            "exception_class": "smtplib.SMTPAuthenticationError",
            "exception_message": "(535, b'5.7.8 Username and Password not accepted')",
            "frames": [
                (
                    "/app/email.py",
                    22,
                    "send_email",
                    "smtp.login(user, password)",
                ),
                (
                    "/usr/lib/python3.11/smtplib.py",
                    734,
                    "login",
                    "raise SMTPAuthenticationError(code, resp)",
                ),
            ],
        },
        {
            "message": "Redis connection refused",
            "exception_class": "ConnectionRefusedError",
            "exception_message": "[Errno 111] Connection refused",
            "frames": [
                (
                    "/app/cache/client.py",
                    33,
                    "get",
                    "return self._client.get(key)",
                ),
                (
                    "/usr/lib/python3.11/site-packages/redis/connection.py",
                    561,
                    "connect",
                    "raise ConnectionError(self._error_message(e))",
                ),
            ],
        },
        {
            "message": "Unable to acquire database connection",
            "exception_class": "sqlalchemy.exc.TimeoutError",
            "exception_message": "QueuePool limit of size 20 overflow 10 reached, connection timed out",
            "frames": [
                (
                    "/app/db/session.py",
                    19,
                    "get_session",
                    "return self._session_factory()",
                ),
                (
                    "/usr/lib/python3.11/site-packages/sqlalchemy/pool/impl.py",
                    145,
                    "_do_get",
                    "raise TimeoutError(msg)",
                ),
            ],
        },
        {
            "message": "Internal server error",
            "exception_class": "RuntimeError",
            "exception_message": "Unhandled state transition for order request_id={request_id}",
            "frames": [
                (
                    "/app/orders/state_machine.py",
                    203,
                    "transition",
                    "raise RuntimeError(self._unhandled_state_msg())",
                ),
            ],
        },
        {
            "message": "NullPointerException encountered",
            "exception_class": "java.lang.NullPointerException",
            "exception_message": 'Cannot invoke "Order.getId()" because "order" is null',
            "frames": [
                (
                    "com.company.orders.OrderProcessor",
                    87,
                    "process",
                    "String id = order.getId();",
                ),
                (
                    "com.company.orders.OrderController",
                    42,
                    "handleRequest",
                    "processor.process(order);",
                ),
            ],
            "java_style": True,
        },
        {
            "message": "Connection pool exhausted",
            "exception_class": "ConnectionPoolExhaustedError",
            "exception_message": "All 50 connections in use, request queued for 15000ms then abandoned",
            "frames": [
                (
                    "/app/db/pool.py",
                    130,
                    "acquire",
                    "raise ConnectionPoolExhaustedError(self._pool_size)",
                ),
            ],
        },
        {
            "message": "ElasticSearch request failed",
            "exception_class": "elasticsearch.exceptions.ConnectionTimeout",
            "exception_message": "TIMEOUT after 5000ms",
            "frames": [
                (
                    "/app/search/client.py",
                    61,
                    "query",
                    "return self._es.search(index=index, body=body)",
                ),
                (
                    "/usr/lib/python3.11/site-packages/elasticsearch/connection/http_urllib3.py",
                    251,
                    "perform_request",
                    "raise ConnectionTimeout('TIMEOUT', str(e), e)",
                ),
            ],
        },
        {
            "message": "Out of memory while processing batch job",
            "exception_class": "MemoryError",
            "exception_message": "Unable to allocate 2.1 GiB for an array",
            "frames": [
                (
                    "/app/jobs/batch_processor.py",
                    154,
                    "run",
                    "results = np.array(rows, dtype=np.float64)",
                ),
            ],
        },
        {
            "message": "Deadlock detected during order update",
            "exception_class": "psycopg2.errors.DeadlockDetected",
            "exception_message": "deadlock detected\nDETAIL: Process 4821 waits for ShareLock on transaction 998231.",
            "frames": [
                (
                    "/app/orders/repository.py",
                    76,
                    "update_status",
                    "cursor.execute(UPDATE_STATUS_SQL, params)",
                ),
            ],
        },
        {
            "message": "Rate limit exceeded for downstream service",
            "exception_class": "RateLimitExceededError",
            "exception_message": "429 Too Many Requests from fraud-detection.internal, retry-after=30s",
            "frames": [
                (
                    "/app/fraud/client.py",
                    40,
                    "check",
                    "raise RateLimitExceededError(retry_after=30)",
                ),
            ],
        },
    ]

    NGINX_PATHS = [
        "/api/orders",
        "/api/products",
        "/api/payment",
        "/api/cart",
        "/api/users",
        "/api/search",
        "/health",
        "/metrics",
    ]
    NGINX_METHODS = ["GET", "GET", "GET", "POST", "POST", "PUT", "DELETE"]
    NGINX_STATUS_WEIGHTS = [
        (200, 0.55),
        (201, 0.08),
        (301, 0.03),
        (304, 0.05),
        (400, 0.08),
        (404, 0.08),
        (500, 0.08),
        (502, 0.03),
        (503, 0.02),
    ]

    KUBE_EVENTS = [
        "Started container {service}",
        "Restarting container {service}",
        "Liveness probe failed for {service}",
        "Readiness probe failed for {service}",
        "Container {service} restarted successfully",
        "Pulling image for {service}",
        "Scaled deployment {service} to {n} replicas",
        "OOMKilled: container {service} exceeded memory limit",
        "Node pressure detected, evicting pod {service}",
    ]

    def __init__(
        self,
        output_path: str | Path,
        total_lines: int = 500_000,
        start_time: Optional[datetime] = None,
        seed: Optional[int] = None,
        incident_probability: float = 0.0015,
        incident_length_range: tuple[int, int] = (20, 90),
    ):
        """
        Args:
            output_path: Where to write the generated log file.
            total_lines: Number of top-level log *entries* to emit (multi-line
                stack traces count as one entry but add several physical lines,
                so the file will end up longer than `total_lines` lines).
            start_time: Timestamp of the first log entry (default 2026-06-26 09:00:00).
            seed: Optional RNG seed for reproducible output.
            incident_probability: Chance, checked before each entry, of a new
                incident starting (ignored while already inside one).
            incident_length_range: Min/max number of entries an incident lasts.
        """
        self.output_path = Path(output_path)
        self.total_lines = total_lines
        self.current_time = start_time or datetime(2026, 6, 26, 9, 0, 0)
        self.incident_probability = incident_probability
        self.incident_length_range = incident_length_range

        self._rng = random.Random(seed)
        self._request_counter = 0
        self._in_incident = False
        self._incident_remaining = 0
        self._incident_service: Optional[str] = None
        self._incident_scenario: Optional[dict] = None

    # ------------------------------------------------------------------ #
    # Small helpers
    # ------------------------------------------------------------------ #

    def _advance_time(self, min_ms: int = 50, max_ms: int = 5000) -> None:
        """Normal inter-log gap. Incidents use a tighter gap (see _advance_time_incident)."""
        self.current_time += timedelta(milliseconds=self._rng.randint(min_ms, max_ms))

    def _advance_time_incident(self) -> None:
        """During an incident, errors fire in a tight burst rather than being spread out."""
        self.current_time += timedelta(milliseconds=self._rng.randint(5, 400))

    def _ts_standard(self) -> str:
        return self.current_time.strftime("%Y-%m-%d %H:%M:%S")

    def _ts_iso(self) -> str:
        return self.current_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _ts_apache(self) -> str:
        return self.current_time.strftime("%d/%b/%Y:%H:%M:%S +0000")

    def _next_request_id(self) -> int:
        self._request_counter += 1
        return self._request_counter

    def _random_ip(self) -> str:
        return ".".join(str(self._rng.randint(1, 255)) for _ in range(4))

    def _random_trace_id(self) -> str:
        return "".join(self._rng.choices(string.hexdigits.lower(), k=32))

    def _weighted_status(self) -> int:
        statuses, weights = zip(*self.NGINX_STATUS_WEIGHTS)
        return self._rng.choices(statuses, weights=weights, k=1)[0]

    # ------------------------------------------------------------------ #
    # Entry renderers - each returns a complete, newline-terminated string
    # ------------------------------------------------------------------ #

    def _render_traceback(self, scenario: dict, request_id: int) -> str:
        lines = ["Traceback (most recent call last):"]
        for frame in scenario["frames"]:
            file_, lineno, func, code = frame
            lines.append(f'  File "{file_}", line {lineno}, in {func}')
            lines.append(f"    {code}")
        exc_msg = scenario["exception_message"].format(request_id=request_id)
        lines.append(f"{scenario['exception_class']}: {exc_msg}")

        cause = scenario.get("caused_by")
        if cause:
            lines.append("")
            lines.append(
                "The above exception was the direct cause of the following exception:"
            )
            lines.append("")
            lines.append("Traceback (most recent call last):")
            for frame in cause["frames"]:
                file_, lineno, func, code = frame
                lines.append(f'  File "{file_}", line {lineno}, in {func}')
                lines.append(f"    {code}")
            cause_msg = cause["exception_message"].format(request_id=request_id)
            lines.append(f"{cause['exception_class']}: {cause_msg}")

        return "\n".join(lines) + "\n"

    def _render_java_stacktrace(self, scenario: dict, request_id: int) -> str:
        exc_msg = scenario["exception_message"].format(request_id=request_id)
        lines = [f"{scenario['exception_class']}: {exc_msg}"]
        for frame in scenario["frames"]:
            cls, lineno, method, _code = frame
            lines.append(f"\tat {cls}.{method}({cls.split('.')[-1]}.java:{lineno})")
        return "\n".join(lines) + "\n"

    def _gen_info(self, service: str) -> str:
        msg = self._rng.choice(self.INFO_MESSAGES)
        return (
            f"{self._ts_standard()} INFO {service} {msg} "
            f"request_id={self._next_request_id()} "
            f"user_id={self._rng.randint(1000, 9999)} "
            f"latency_ms={self._rng.randint(5, 250)}\n"
        )

    def _gen_warning(self, service: str) -> str:
        msg = self._rng.choice(self.WARNING_MESSAGES)
        return (
            f"{self._ts_standard()} WARNING {service} {msg} "
            f"request_id={self._next_request_id()}\n"
        )

    def _gen_error(self, service: str, force_scenario: Optional[dict] = None) -> str:
        scenario = force_scenario or self._rng.choice(self.ERROR_SCENARIOS)
        request_id = self._next_request_id()
        trace_id = self._random_trace_id()

        out = (
            f"{self._ts_standard()} ERROR {service} {scenario['message']} "
            f"request_id={request_id} trace_id={trace_id}\n"
        )

        # ~50% of errors include the full stack trace, like a real app would
        # only log the traceback for uncaught exceptions.
        if self._rng.random() < 0.5:
            if scenario.get("java_style"):
                out += self._render_java_stacktrace(scenario, request_id)
            else:
                out += self._render_traceback(scenario, request_id)
            out += "\n"

        return out

    def _gen_access_log(self) -> str:
        ip = self._random_ip()
        method = self._rng.choice(self.NGINX_METHODS)
        path = self._rng.choice(self.NGINX_PATHS)
        status = self._weighted_status()
        size = self._rng.randint(20, 4000)
        return (
            f"{ip} - - [{self._ts_apache()}] "
            f'"{method} {path} HTTP/1.1" {status} {size} '
            f'"-" "Mozilla/5.0"\n'
        )

    def _gen_kube_event(self, service: Optional[str] = None) -> str:
        service = service or self._rng.choice(self.SERVICES)
        template = self._rng.choice(self.KUBE_EVENTS)
        msg = template.format(service=service, n=self._rng.randint(2, 8))
        return f"{self._ts_iso()} INFO kubelet {msg}\n"

    def _gen_json_log(self, service: str) -> str:
        """Occasional structured JSON log line, as some services emit."""
        level, msg = self._rng.choice(
            [
                ("INFO", self._rng.choice(self.INFO_MESSAGES)),
                ("WARNING", self._rng.choice(self.WARNING_MESSAGES)),
            ]
        )
        payload = {
            "timestamp": self._ts_iso(),
            "level": level,
            "service": service,
            "message": msg,
            "request_id": self._next_request_id(),
            "trace_id": self._random_trace_id(),
            "latency_ms": self._rng.randint(5, 500),
        }
        return json.dumps(payload) + "\n"

    # ------------------------------------------------------------------ #
    # Incident simulation
    # ------------------------------------------------------------------ #

    def _maybe_start_incident(self) -> None:
        if self._in_incident:
            return
        if self._rng.random() < self.incident_probability:
            self._in_incident = True
            self._incident_remaining = self._rng.randint(*self.incident_length_range)
            self._incident_service = self._rng.choice(self.SERVICES)
            # Lock this incident to one failure scenario so repeated errors
            # look like the same recurring root cause, not unrelated noise.
            self._incident_scenario = self._rng.choice(self.ERROR_SCENARIOS)

    def _gen_incident_line(self) -> str:
        """
        Simulate a realistic outage on `self._incident_service`: a couple of
        precursor WARNINGs, a cluster of tightly-spaced ERRORs (same
        scenario repeated, like a real recurring failure), then a recovery
        INFO line once the incident ends.
        """
        self._advance_time_incident()
        service = self._incident_service
        self._incident_remaining -= 1

        if self._incident_remaining <= 0:
            self._in_incident = False
            return (
                f"{self._ts_standard()} INFO {service} "
                f"Circuit breaker closed, service recovered "
                f"request_id={self._next_request_id()}\n"
            )

        roll = self._rng.random()
        if roll < 0.25:
            return (
                f"{self._ts_standard()} WARNING {service} "
                f"{self._rng.choice(self.WARNING_MESSAGES)} "
                f"request_id={self._next_request_id()}\n"
            )
        return self._gen_error(service, force_scenario=self._incident_scenario)

    def _gen_normal_line(self) -> str:
        self._advance_time()
        service = self._rng.choice(self.SERVICES)
        r = self._rng.random()

        if r < 0.65:
            return self._gen_info(service)
        elif r < 0.80:
            return self._gen_warning(service)
        elif r < 0.90:
            return self._gen_error(service)
        elif r < 0.94:
            return self._gen_json_log(service)
        elif r < 0.98:
            return self._gen_access_log()
        else:
            return self._gen_kube_event(service)

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def generate(self) -> Path:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        with self.output_path.open("w", encoding="utf-8") as f:
            for _ in range(self.total_lines):
                self._maybe_start_incident()
                line = (
                    self._gen_incident_line()
                    if self._in_incident
                    else self._gen_normal_line()
                )
                f.write(line)

        return self.output_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a synthetic production log file."
    )
    parser.add_argument(
        "--output",
        default="sample_logs/production.log",
        help="Output file path.",
    )
    parser.add_argument(
        "--lines",
        type=int,
        default=500_000,
        help="Number of log entries to generate.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed for reproducible output.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    generator = LogGenerator(
        output_path=args.output,
        total_lines=args.lines,
        seed=args.seed,
    )
    output_path = generator.generate()

    print(f"Generated {args.lines:,} log entries -> {output_path.resolve()}")
