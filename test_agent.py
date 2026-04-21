"""
Mock CloudWatch log stream test using moto.
No real AWS credentials needed.
"""

import time
import boto3
from moto import mock_aws
from agent import fetch_recent_logs, detect_anomalies

# ── Sample log data — mix of normal + anomalous ──────────────────────────────
MOCK_LOGS = [
    "INFO  Request received: GET /api/users",
    "INFO  DB query completed in 12ms",
    "ERROR NullPointerException at UserService.java:42",
    "INFO  Response sent: 200 OK",
    "ERROR Traceback (most recent call last):\n  File 'app.py', line 88\nValueError: invalid input",
    "WARN  Slow query detected: 1200ms",
    "INFO  Request received: POST /api/orders",
    "ERROR HTTP 500 Internal Server Error — unhandled exception in OrderController",
    "INFO  Cache hit for key: user:123",
    "FATAL OutOfMemoryError: Java heap space",
    "INFO  Health check: OK",
    "ERROR HTTP 503 Service Unavailable",
]

LOG_GROUP  = "/aws/lambda/test-function"
LOG_STREAM = "2024/01/01/[$LATEST]test-stream"


@mock_aws
def setup_mock_logs():
    """Create a fake CloudWatch log group + stream and push mock events."""
    client = boto3.client("logs", region_name="us-east-1")

    client.create_log_group(logGroupName=LOG_GROUP)
    client.create_log_stream(logGroupName=LOG_GROUP, logStreamName=LOG_STREAM)

    # Timestamps must be ascending
    base_ts = int(time.time() * 1000) - (len(MOCK_LOGS) * 1000)
    events  = [
        {"timestamp": base_ts + i * 1000, "message": msg}
        for i, msg in enumerate(MOCK_LOGS)
    ]

    client.put_log_events(
        logGroupName=LOG_GROUP,
        logStreamName=LOG_STREAM,
        logEvents=events,
    )
    return client


@mock_aws
def test_fetch_and_detect():
    setup_mock_logs()

    print("=== Fetching logs ===")
    logs = fetch_recent_logs(LOG_GROUP, lookback_minutes=60)
    print(f"Fetched {len(logs)} events\n")
    for e in logs:
        print(f"  [{e['timestamp']}] {e['message'][:80]}")

    print("\n=== Detecting anomalies ===")
    anomalies = detect_anomalies(logs)
    print(f"Found {len(anomalies)} anomalous events:\n")
    for a in anomalies:
        print(f"  PATTERN: {a['matched_pattern']}")
        print(f"  MESSAGE: {a['message'][:100]}")
        print()

    assert len(logs) == len(MOCK_LOGS), "Should fetch all mock events"
    assert len(anomalies) > 0, "Should detect at least one anomaly"
    print("✓ All assertions passed")


if __name__ == "__main__":
    test_fetch_and_detect()
