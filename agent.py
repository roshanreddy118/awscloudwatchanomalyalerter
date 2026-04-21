"""
CloudWatch Log Alert Agent
Polls a CloudWatch log group, detects anomalies (errors, stack traces, 5xx),
and sends alerts to Slack or WhatsApp (Twilio).
"""

import os
import re
import time
import boto3
from datetime import datetime, timezone, timedelta
from strands import Agent, tool
from strands.models import BedrockModel

# ── Config (override via env vars) ──────────────────────────────────────────
LOG_GROUP      = os.environ.get("CW_LOG_GROUP", "ai-agent")
POLL_INTERVAL  = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
LOOKBACK_MINS  = int(os.environ.get("LOOKBACK_MINUTES", "60"))   # 60min to catch seeded data
AWS_REGION     = os.environ.get("AWS_REGION", "eu-west-1")

# Slack: set SLACK_WEBHOOK_URL
SLACK_WEBHOOK  = os.environ.get("SLACK_WEBHOOK_URL", "")

# WhatsApp via Twilio: set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
#   TWILIO_FROM (whatsapp:+14155238886), TWILIO_TO (whatsapp:+1XXXXXXXXXX)
TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM    = os.environ.get("TWILIO_FROM", "")
TWILIO_TO      = os.environ.get("TWILIO_TO", "")

# Patterns that indicate something went wrong
ALERT_PATTERNS = [
    r"\bERROR\b",
    r"\bException\b",
    r"\bTraceback\b",
    r"\bSTACK_TRACE\b",
    r"HTTP [45]\d{2}",          # 4xx and 5xx
    r"\b5\d{2}\b",              # bare 5xx codes
    r"NullPointerException",
    r"OutOfMemoryError",
    r"FATAL",
    r"CRITICAL",
]
COMPILED = [re.compile(p, re.IGNORECASE) for p in ALERT_PATTERNS]

cw_client = boto3.client("logs", region_name=AWS_REGION)

bedrock_model = BedrockModel(
    model_id="eu.anthropic.claude-sonnet-4-6",
    region_name=AWS_REGION,
    temperature=0.1,
)


# ── Tools ────────────────────────────────────────────────────────────────────

@tool
def fetch_recent_logs(log_group: str, lookback_minutes: int = 5) -> list[dict]:
    """Fetch recent log events from a CloudWatch log group.

    Args:
        log_group: The CloudWatch log group name (e.g. /aws/lambda/my-function)
        lookback_minutes: How many minutes back to look for logs

    Returns:
        List of dicts with keys: timestamp, message, stream
    """
    start_ms = int((datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)).timestamp() * 1000)
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)

    events = []
    kwargs = {
        "logGroupName": log_group,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 200,
        "interleaved": True,
    }

    while True:
        resp = cw_client.filter_log_events(**kwargs)
        for e in resp.get("events", []):
            events.append({
                "timestamp": datetime.fromtimestamp(e["timestamp"] / 1000, tz=timezone.utc).isoformat(),
                "message": e["message"].strip(),
                "stream": e.get("logStreamName", ""),
            })
        next_token = resp.get("nextToken")
        if not next_token:
            break
        kwargs["nextToken"] = next_token

    return events


@tool
def detect_anomalies(log_events: list[dict]) -> list[dict]:
    """Scan log events and return only those matching error/anomaly patterns.

    Args:
        log_events: List of log event dicts (from fetch_recent_logs)

    Returns:
        Filtered list of anomalous log events with an added 'matched_pattern' key
    """
    hits = []
    for event in log_events:
        msg = event.get("message", "")
        for pattern in COMPILED:
            if pattern.search(msg):
                hits.append({**event, "matched_pattern": pattern.pattern})
                break  # one match per event is enough
    return hits


@tool
def send_slack_alert(anomalies: list[dict], log_group: str) -> str:
    """Send an alert to Slack via webhook when anomalies are detected.

    Args:
        anomalies: List of anomalous log events
        log_group: The log group name for context

    Returns:
        'sent' or an error message
    """
    if not SLACK_WEBHOOK:
        return "SLACK_WEBHOOK_URL not configured — skipping Slack alert"

    import urllib.request, json

    count = len(anomalies)
    sample = anomalies[:5]  # show up to 5 examples
    lines = "\n".join(f"• `{e['timestamp']}` — {e['message'][:200]}" for e in sample)
    more  = f"\n_...and {count - 5} more_" if count > 5 else ""

    payload = {
        "text": f":rotating_light: *CloudWatch Alert* — `{log_group}`",
        "attachments": [{
            "color": "danger",
            "text": f"*{count} anomalous log event(s) detected*\n{lines}{more}",
            "footer": f"Checked at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        }]
    }

    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(SLACK_WEBHOOK, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        return f"sent — {count} anomalies reported to Slack"
    except Exception as exc:
        return f"Slack send failed: {exc}"


@tool
def send_whatsapp_alert(anomalies: list[dict], log_group: str) -> str:
    """Send an alert via WhatsApp using Twilio when anomalies are detected.

    Args:
        anomalies: List of anomalous log events
        log_group: The log group name for context

    Returns:
        'sent' or an error message
    """
    if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO]):
        return "Twilio env vars not fully configured — skipping WhatsApp alert"

    try:
        from twilio.rest import Client
    except ImportError:
        return "twilio package not installed — run: pip install twilio"

    count  = len(anomalies)
    sample = anomalies[:3]
    lines  = "\n".join(f"[{e['timestamp']}] {e['message'][:150]}" for e in sample)
    more   = f"\n...and {count - 3} more" if count > 3 else ""

    body = (
        f"🚨 CloudWatch Alert\n"
        f"Log group: {log_group}\n"
        f"{count} anomaly(ies) found:\n\n{lines}{more}"
    )

    try:
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        msg = client.messages.create(body=body, from_=TWILIO_FROM, to=TWILIO_TO)
        return f"sent — WhatsApp SID {msg.sid}"
    except Exception as exc:
        return f"WhatsApp send failed: {exc}"


# ── Agent ────────────────────────────────────────────────────────────────────

agent = Agent(
    model=bedrock_model,
    tools=[fetch_recent_logs, detect_anomalies, send_slack_alert, send_whatsapp_alert],
    system_prompt=(
        "You are a CloudWatch log monitoring agent. "
        "Your job is to: "
        "1. Fetch recent logs from the given log group. "
        "2. Detect anomalies (errors, exceptions, stack traces, 5xx HTTP codes). "
        "3. If anomalies are found, send alerts via Slack and/or WhatsApp. "
        "4. Report a concise summary of what you found and what you did. "
        "Be efficient — use tools in sequence and don't repeat steps."
    ),
)


def run_once():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking {LOG_GROUP} ...")
    # stream=False prevents the SDK from printing intermediate streamed chunks
    response = agent(
        f"Check the CloudWatch log group '{LOG_GROUP}' for the last {LOOKBACK_MINS} minutes. "
        f"Detect any anomalies and send alerts if found.",
        stream=False,
    )
    print(response)
    print("-" * 60)


def run_loop():
    print(f"Starting CloudWatch alert agent — polling every {POLL_INTERVAL}s")
    print(f"Log group : {LOG_GROUP}")
    print(f"Lookback  : {LOOKBACK_MINS} minutes")
    print(f"Slack     : {'✓' if SLACK_WEBHOOK else '✗ (set SLACK_WEBHOOK_URL)'}")
    print(f"WhatsApp  : {'✓' if all([TWILIO_SID, TWILIO_TOKEN]) else '✗ (set TWILIO_* vars)'}")
    print("=" * 60)

    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as exc:
            print(f"Error during check: {exc}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        run_once()
    else:
        run_loop()
