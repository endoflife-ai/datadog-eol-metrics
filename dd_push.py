#!/usr/bin/env python3
"""
endoflife.ai -> Datadog Integration
Fetches EOL Risk Scores and pushes them as metrics to your Datadog account.

Requires the DD_API_KEY environment variable to be set, e.g.:
    setx DD_API_KEY "your-datadog-api-key"
(then open a new terminal, or just let the scheduled task pick it up)
Optional: DD_SITE (default datadoghq.com; e.g. datadoghq.eu, us3.datadoghq.com).

Data collection: the only request to endoflife.ai is a GET of the public
scores file, identified by the user agent below. No hostnames, Datadog keys
or other customer data are sent to endoflife.ai.

Metrics pushed:
  endoflife.risk_score        (0-100) composite score, tagged product/grade/eol_label/eol_date/kev_active
  endoflife.days_until_eol    (can be negative if already past EOL) - powers countdown/urgency widgets
  endoflife.kev_factor_score  (0-20) the CISA KEV component in isolation - powers the "active KEV" widget
"""

import os
import sys
import urllib.request
import json
import time
from datetime import datetime, timezone

DD_API_KEY = os.environ.get("DD_API_KEY")
# Datadog site: datadoghq.com (default, US1), us3.datadoghq.com, us5.datadoghq.com,
# datadoghq.eu, ap1.datadoghq.com, ddog-gov.com. Set DD_SITE to match your account.
DD_SITE = (os.environ.get("DD_SITE") or "datadoghq.com").strip().lower()
DD_METRICS_URL = f"https://api.{DD_SITE}/api/v2/series"
SCORES_URL = "https://endoflife.ai/scores.json"

# The exporter identifies itself honestly when it fetches the public scores
# file, so endoflife.ai can count active installations in aggregate (requests
# and distinct client addresses per day, from its CDN analytics). Nothing else
# is sent to endoflife.ai: no install ID, no hostnames, no Datadog keys, no
# customer data. The Datadog API key is only ever sent to Datadog.
EXPORTER_VERSION = "1.2.0"
USER_AGENT = f"endoflife-datadog-exporter/{EXPORTER_VERSION} (+https://endoflife.ai/integrations)"
# endoflife.ai's own sandbox job sets EOL_EXPORTER_INTERNAL=1 so its daily run
# is counted apart from customer installations.
if os.environ.get("EOL_EXPORTER_INTERNAL") == "1":
    USER_AGENT = USER_AGENT[:-1] + "; internal)"


def fetch_scores():
    print("Fetching scores from endoflife.ai...")
    req = urllib.request.Request(
        SCORES_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json"
        }
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read())


def days_until(eol_date_str):
    if not eol_date_str:
        return None
    try:
        eol_dt = datetime.strptime(eol_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (eol_dt - now).days
    except ValueError:
        return None


def build_series(scores):
    now = int(time.time())
    series = []
    for product, data in scores.items():
        score = data.get("score")
        if score is None:
            continue

        factors = data.get("factors", {}) or {}
        kev_score = factors.get("kev", 0) or 0
        kev_active = "true" if kev_score > 0 else "false"
        eol_date = data.get("eolDate")
        dte = days_until(eol_date)

        # approaching = EOL date in the future; past = already EOL'd.
        # Lets dashboards split "plan the upgrade" from "remediate overdue"
        # without value-filter gymnastics (Bits AI critique, 2026-08-03).
        eol_phase = "none" if dte is None else ("past" if dte < 0 else "approaching")

        base_tags = [
            f"product:{product}",
            f"grade:{data.get('grade', '')}",
            f"eol_label:{data.get('label', '').lower().replace(' ', '_')}",
            f"eol_date:{eol_date or 'none'}",
            f"eol_phase:{eol_phase}",
            f"kev_active:{kev_active}",
            "source:endoflife.ai"
        ]
        resources = [{"type": "product", "name": product}]

        series.append({
            "metric": "endoflife.risk_score",
            "type": 0,
            "points": [{"timestamp": now, "value": float(score)}],
            "tags": base_tags,
            "resources": resources
        })

        if dte is not None:
            series.append({
                "metric": "endoflife.days_until_eol",
                "type": 0,
                "points": [{"timestamp": now, "value": float(dte)}],
                "tags": base_tags,
                "resources": resources
            })

        series.append({
            "metric": "endoflife.kev_factor_score",
            "type": 0,
            "points": [{"timestamp": now, "value": float(kev_score)}],
            "tags": base_tags,
            "resources": resources
        })

    return series


def push_to_datadog(series):
    batch_size = 100
    total = len(series)
    pushed = 0
    for i in range(0, total, batch_size):
        batch = series[i:i + batch_size]
        payload = json.dumps({"series": batch}).encode("utf-8")
        req = urllib.request.Request(
            DD_METRICS_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "DD-API-KEY": DD_API_KEY
            },
            method="POST"
        )
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read())
            pushed += len(batch)
            errors = result.get("errors", [])
            status = "OK" if not errors else errors
            print(f"  Pushed {pushed}/{total} ... {status}")
    return pushed


def main():
    if not DD_API_KEY:
        print("ERROR: DD_API_KEY environment variable is not set.")
        print('Run this once, then open a new terminal:')
        print('    setx DD_API_KEY "your-datadog-api-key"')
        sys.exit(1)

    scores = fetch_scores()
    print(f"Loaded {len(scores)} products.")
    series = build_series(scores)
    print(f"Pushing {len(series)} metric points to Datadog ({DD_SITE})...")
    pushed = push_to_datadog(series)
    print(f"\nDone. {pushed} metric points are live in your Datadog account.")
    print(f"Check: Metrics Explorer on your Datadog site ({DD_SITE}), query endoflife.risk_score")


if __name__ == "__main__":
    main()
