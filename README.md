# aws-cloudwatch-anomaly-alerter

A Strands-based agent that polls CloudWatch logs and fires alerts to Slack/WhatsApp when it detects errors, stack traces, or 5xx responses.
<img width="670" height="426" alt="Screenshot 2026-04-21 at 7 27 18 PM" src="https://github.com/user-attachments/assets/db3a6503-550a-4319-b1ec-0364290c4a06" />


## Setup

```bash
cd cloudwatch-alert-agent
pip install -r requirements.txt

cp .env.example .env
# edit .env with your values
source .env
```

## Run

```bash
# Poll continuously (every 60s by default)
python agent.py

# Run once and exit
python agent.py --once
```

## What it detects

- `ERROR`, `FATAL`, `CRITICAL`
- `Exception`, `Traceback`, `NullPointerException`, `OutOfMemoryError`
- HTTP 4xx / 5xx status codes

## Alert channels

| Channel   | Required env vars |
|-----------|-------------------|
| Slack     | `SLACK_WEBHOOK_URL` |
| WhatsApp  | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, `TWILIO_TO` |

Both are optional — configure one or both.


## Test Run Results

Real run against AWS CloudWatch log group `ai-agent` (eu-west-1) with mock data seeded into stream `mock-stream/test-run-1`.

### Console Output

```
[19:25:01] Checking ai-agent ...

Step 1 — Fetch recent logs
Tool #1: fetch_recent_logs

Step 2 — Detect anomalies
Tool #2: detect_anomalies

Step 3 — Send Slack & WhatsApp alerts (in parallel)
Tool #3: send_slack_alert
Tool #4: send_whatsapp_alert
```

### 📋 CloudWatch Monitoring Report — `ai-agent`

**Window:** Last 60 minutes | **Stream:** `mock-stream/test-run-1`

#### Log Overview

| Level | Count |
|-------|-------|
| ✅ INFO | 8 |
| ⚠️ WARN | 2 |
| ❌ ERROR | 5 |
| 💀 FATAL | 1 |
| **Total** | **16** |

#### 🚨 Anomalies Detected (6)

| # | Timestamp (UTC) | Severity | Description |
|---|-----------------|----------|-------------|
| 1 | 13:43:55 | ERROR | `NullPointerException` at `ModelRunner.java:88` |
| 2 | 13:44:05 | ERROR | HTTP **500** Internal Server Error — unhandled exception in `/api/v1/infer` |
| 3 | 13:44:15 | ERROR | **Stack trace** — `ValueError`: Input tensor shape mismatch `(1,768)` vs `(1,512)` |
| 4 | 13:44:25 | ERROR | HTTP **503** Service Unavailable — downstream timeout after 30s |
| 5 | 13:44:30 | FATAL | **`OutOfMemoryError`** — unable to allocate 2.4GB tensor buffer |
| 6 | 13:44:35 | ERROR | CRITICAL — **agent process crashed**, restarted after 5s |

#### 📣 Alert Status

| Channel | Status |
|---------|--------|
| Slack | ⚠️ Skipped — `SLACK_WEBHOOK_URL` not configured |
| WhatsApp | ⚠️ Skipped — Twilio env vars not configured |

#### 🔍 Key Takeaways & Recommendations

1. **Memory Pressure → Crash:** The `OutOfMemoryError` (2.4GB tensor allocation) directly caused the agent crash. Consider increasing memory limits or optimizing tensor buffer management.
2. **Tensor Shape Mismatch:** The `ValueError` at `runner.py:112` suggests a model input pipeline bug — input shapes need to be validated before inference.
3. **NullPointerException:** `ModelRunner.java:88` needs a null-check guard to prevent cascading failures.
4. **5xx Errors:** Both HTTP 500 and 503 indicate the service was degraded during this window. The agent has since recovered (`Process restarted successfully`), but root causes should be addressed.
5. **Slow DB Query (1450ms):** While not flagged as an anomaly, the slow query warning may be contributing to downstream timeouts.
