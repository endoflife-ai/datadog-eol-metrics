#!/usr/bin/env python3
"""
endoflife.ai -> Datadog Stack Scan (v2 engine)
================================================
The base dd_push.py pushes the FULL endoflife.ai product catalog into Datadog
as generic reference data. That's useful as a lookup table, but it doesn't
know anything about what a given Datadog account actually runs.

This script closes that gap: it reads the REAL host inventory already sitting
in a Datadog account (host tags + detected "apps"/integrations, which Datadog
populates automatically via the Agent) and correlates it against the
endoflife.ai risk database. The result is a metric scoped to what's actually
running, tagged by real host name, instead of a generic 458-product firehose.

Requires TWO Datadog credentials (the Hosts API needs both):
    setx DD_API_KEY "your-datadog-api-key"
    setx DD_APP_KEY "your-datadog-application-key"
(Organization Settings -> Application Keys, if one doesn't exist yet - create it)
Optional: DD_SITE (default datadoghq.com; e.g. datadoghq.eu, us3.datadoghq.com).

Data collection: the only request to endoflife.ai is a GET of the public
scores file, identified by the user agent below. Your host inventory is read
from Datadog, matched locally, and the results are pushed only to Datadog;
no hostnames, tags, apps or keys are sent to endoflife.ai.

Usage:
    python dd_stack_scan.py            # scans your real Datadog infrastructure
    python dd_stack_scan.py --sample   # runs against synthetic sample hosts,
                                        # so you can see the matching logic work
                                        # even in an account with no real infra
                                        # (e.g. a fresh partner sandbox)
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse

DD_API_KEY = os.environ.get("DD_API_KEY")
DD_APP_KEY = os.environ.get("DD_APP_KEY")
# Datadog site: datadoghq.com (default, US1), us3.datadoghq.com, us5.datadoghq.com,
# datadoghq.eu, ap1.datadoghq.com, ddog-gov.com. Set DD_SITE to match your account.
DD_SITE = (os.environ.get("DD_SITE") or "datadoghq.com").strip().lower()
HOSTS_URL = f"https://api.{DD_SITE}/api/v1/hosts"
METRICS_URL = f"https://api.{DD_SITE}/api/v2/series"
SCORES_URL = "https://endoflife.ai/scores.json"

# Honest user agent for the one request this script makes to endoflife.ai (the
# public scores file), so endoflife.ai can count active installations in
# aggregate. Host names, tags and apps read from your Datadog account are
# matched locally and pushed only to Datadog; none of it is sent to endoflife.ai.
EXPORTER_VERSION = "1.2.0"
USER_AGENT = f"endoflife-datadog-exporter-stackscan/{EXPORTER_VERSION} (+https://endoflife.ai/integrations)"
# endoflife.ai's own sandbox job sets EOL_EXPORTER_INTERNAL=1 so its daily run
# is counted apart from customer installations.
if os.environ.get("EOL_EXPORTER_INTERNAL") == "1":
    USER_AGENT = USER_AGENT[:-1] + "; internal)"

# Common alias/normalization map: how real-world tag/app names typically show
# up on a Datadog host vs. how endoflife.ai names the product. Extend this as
# real-world matches get discovered - this is deliberately a starting point,
# not an exhaustive dictionary.
ALIASES = {
    "postgres": "postgresql",
    "pg": "postgresql",
    "mongo": "mongodb",
    "docker": "docker-engine",
    "k8s": "kubernetes",
    "win": "windows",
    "windows-server": "windows-server",
    "amzn": "amazon-linux",
    "amazonlinux": "amazon-linux",
    "rhel": "rhel",
    "centos": "centos",
    "alpine": "alpine-linux",
    "node": "nodejs",
}

# Synthetic hosts used for --sample mode, modeled on what a typical Datadog
# Agent-monitored host actually looks like (tags + detected apps).
SAMPLE_HOSTS = [
    {
        "name": "web-01.prod",
        "tags": ["os:ubuntu", "env:prod", "team:platform"],
        "apps": ["nginx", "docker", "postgres"],
    },
    {
        "name": "db-01.prod",
        "tags": ["os:centos", "env:prod", "team:data"],
        "apps": ["mysql", "redis"],
    },
    {
        "name": "build-agent-03",
        "tags": ["os:debian", "env:ci"],
        "apps": ["docker", "node"],
    },
]


def fetch_scores():
    print("Fetching risk scores from endoflife.ai...")
    req = urllib.request.Request(
        SCORES_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read())


def fetch_real_hosts():
    if not DD_API_KEY or not DD_APP_KEY:
        print("ERROR: DD_API_KEY and DD_APP_KEY must both be set to scan real infrastructure.")
        print('    setx DD_API_KEY "your-datadog-api-key"')
        print('    setx DD_APP_KEY "your-datadog-application-key"')
        print("Or run with --sample to see the matching logic against synthetic hosts instead.")
        sys.exit(1)

    print("Fetching host inventory from your Datadog account...")
    req = urllib.request.Request(
        HOSTS_URL,
        headers={"DD-API-KEY": DD_API_KEY, "DD-APPLICATION-KEY": DD_APP_KEY},
    )
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read())

    hosts = []
    for h in data.get("host_list", []):
        tags = []
        for source_tags in (h.get("tags_by_source") or {}).values():
            tags.extend(source_tags)
        hosts.append({
            "name": h.get("host_name") or h.get("name"),
            "tags": tags,
            "apps": h.get("apps", []) or [],
        })
    return hosts


def normalize(name):
    name = name.lower().strip()
    return ALIASES.get(name, name)


def candidate_identifiers(host):
    """Pull every plausible product-name signal off a host: its detected
    apps, plus the values of any tag that looks like it's naming software
    (os:, platform:, image:, runtime:, service:)."""
    candidates = set()
    for app in host.get("apps", []):
        candidates.add(normalize(app))

    signal_prefixes = ("os:", "platform:", "image:", "runtime:", "service:")
    for tag in host.get("tags", []):
        if ":" in tag:
            key, _, value = tag.partition(":")
            if f"{key}:" in signal_prefixes:
                candidates.add(normalize(value))

    return candidates


def correlate(hosts, scores):
    """Match each host's candidate identifiers against the real endoflife.ai
    product catalog. Returns a list of (host_name, product, score_data)."""
    matches = []
    known_products = set(scores.keys())

    for host in hosts:
        host_matches = set()
        for candidate in candidate_identifiers(host):
            if candidate in known_products:
                host_matches.add(candidate)
        for product in host_matches:
            matches.append((host["name"], product, scores[product]))

    return matches


def build_series(matches):
    now = int(time.time())
    series = []
    for host_name, product, data in matches:
        score = data.get("score")
        if score is None:
            continue
        factors = data.get("factors", {}) or {}
        kev_active = "true" if (factors.get("kev", 0) or 0) > 0 else "false"
        tags = [
            f"host_name:{host_name}",
            f"product:{product}",
            f"grade:{data.get('grade', '')}",
            f"kev_active:{kev_active}",
            f"eol_date:{data.get('eolDate') or 'none'}",
            "source:endoflife.ai-stack-scan",
        ]
        series.append({
            "metric": "endoflife.stack_risk_score",
            "type": 0,
            "points": [{"timestamp": now, "value": float(score)}],
            "tags": tags,
            "resources": [{"type": "host", "name": host_name}],
        })
    return series


def push_to_datadog(series):
    if not series:
        return 0
    payload = json.dumps({"series": series}).encode("utf-8")
    req = urllib.request.Request(
        METRICS_URL,
        data=payload,
        headers={"Content-Type": "application/json", "DD-API-KEY": DD_API_KEY},
        method="POST",
    )
    with urllib.request.urlopen(req) as response:
        result = json.loads(response.read())
        errors = result.get("errors", [])
        print(f"  Pushed {len(series)} points ... {'OK' if not errors else errors}")
    return len(series)


def main():
    sample_mode = "--sample" in sys.argv

    scores = fetch_scores()
    print(f"Loaded {len(scores)} products from endoflife.ai.")

    if sample_mode:
        print("\n[--sample mode] Using synthetic hosts, not your real Datadog infrastructure.\n")
        hosts = SAMPLE_HOSTS
    else:
        hosts = fetch_real_hosts()

    print(f"Found {len(hosts)} host(s) to scan.")
    if not hosts:
        print("No hosts found in this Datadog account. This engine has nothing to correlate")
        print("against until real infrastructure (Agent, containers, etc.) is reporting in.")
        print("Try `python dd_stack_scan.py --sample` to see the matching logic in action.")
        return

    matches = correlate(hosts, scores)
    print(f"Matched {len(matches)} host/product pair(s):")
    for host_name, product, data in matches:
        print(f"  {host_name:20s} -> {product:20s} (score {data.get('score')}, grade {data.get('grade')})")

    if not matches:
        print("\nNo matches. This usually means the ALIASES map needs a few more entries for")
        print("your naming conventions - open dd_stack_scan.py and add them.")
        return

    series = build_series(matches)
    if sample_mode and "--push" not in sys.argv:
        print("\n[--sample mode] Not pushing to Datadog - this was a dry run of the matching logic.")
        print("Add --push to publish the synthetic sample-host metrics (demo/sandbox use).")
        return
    if sample_mode:
        print("\n[--sample --push] Publishing SYNTHETIC sample-host metrics (demo/sandbox use).")

    print(f"\nPushing {len(series)} stack-scoped metric points to Datadog...")
    push_to_datadog(series)
    print(f"Done. Check: Metrics Explorer on your Datadog site ({DD_SITE}), query endoflife.stack_risk_score")


if __name__ == "__main__":
    main()
