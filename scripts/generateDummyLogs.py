import random
from datetime import datetime, timedelta
from pathlib import Path

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
]

WARNING_MESSAGES = [
    "Response time exceeded threshold",
    "Cache miss",
    "Disk usage above 85%",
    "Connection pool nearing capacity",
    "Inventory running low",
    "Multiple login failures detected",
    "Slow database query detected",
]

ERROR_MESSAGES = [
    "Database connection timeout",
    "Payment gateway timeout",
    "SMTP authentication failed",
    "Redis connection refused",
    "Unable to acquire database connection",
    "Internal server error",
    "NullPointerException encountered",
    "Connection pool exhausted",
    "ElasticSearch request failed",
]

STACK_TRACES = [
    """Traceback (most recent call last):
  File "/app/payment.py", line 81, in process_payment
    gateway.charge(card)
TimeoutError: Gateway timeout after 30 seconds
""",
    """Traceback (most recent call last):
  File "/app/db.py", line 45, in execute
    cursor.execute(query)
ConnectionError: Database unavailable
""",
    """Traceback (most recent call last):
  File "/app/email.py", line 22, in send_email
    smtp.login(user, password)
SMTPAuthenticationError: Invalid credentials
""",
]

NGINX = [
    'GET /api/orders HTTP/1.1" 200',
    'GET /api/products HTTP/1.1" 200',
    'POST /api/payment HTTP/1.1" 500',
    'GET /health HTTP/1.1" 200',
]

KUBE = [
    "Started container payment-service",
    "Restarting container payment-service",
    "Liveness probe failed",
    "Container restarted successfully",
]


def random_timestamp(start):
    return start.strftime("%Y-%m-%d %H:%M:%S")


def generate_log(path: str, lines: int = 500_000):
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    current = datetime(2026, 6, 26, 9, 0, 0)

    with open(path, "w", encoding="utf-8") as f:
        for i in range(lines):
            current += timedelta(milliseconds=random.randint(50, 5000))

            r = random.random()

            service = random.choice(SERVICES)

            ts = random_timestamp(current)

            if r < 0.70:
                msg = random.choice(INFO_MESSAGES)
                f.write(
                    f"{ts} INFO {service} {msg} request_id={i} "
                    f"user_id={random.randint(1000,9999)}\n"
                )

            elif r < 0.85:
                msg = random.choice(WARNING_MESSAGES)
                f.write(f"{ts} WARNING {service} {msg} request_id={i}\n")

            elif r < 0.95:
                msg = random.choice(ERROR_MESSAGES)
                f.write(f"{ts} ERROR {service} {msg} request_id={i}\n")

                if random.random() < 0.45:
                    f.write(random.choice(STACK_TRACES))
                    f.write("\n")

            elif r < 0.98:
                f.write(
                    f"{random.randint(10,255)}.{random.randint(0,255)}."
                    f"{random.randint(0,255)}.{random.randint(0,255)} "
                    f'- - [{current.strftime("%d/%b/%Y:%H:%M:%S +0000")}] '
                    f'"{random.choice(NGINX)} {random.randint(20,2000)} '
                    f'"-" "Mozilla/5.0"\n'
                )

            else:
                f.write(
                    f"{current.strftime('%Y-%m-%dT%H:%M:%SZ')} "
                    f"INFO kubelet {random.choice(KUBE)}\n"
                )


if __name__ == "__main__":
    generate_log("sample_logs/production.log", 500_000)
