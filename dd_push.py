#!/usr/bin/env python3
"""
endoflife.ai -> Datadog Integration
Fetches EOL Risk Scores for the software you run and pushes them as metrics to
your Datadog account.

Requires the DD_API_KEY environment variable to be set, e.g.:
    setx DD_API_KEY "your-datadog-api-key"
(then open a new terminal, or just let the scheduled task pick it up)
Optional: DD_SITE (default datadoghq.com; e.g. datadoghq.eu, us3.datadoghq.com).

Which products are sent (the scope):
  - DD_APP_KEY set: the products the stack scan detects on your Datadog hosts
    (the same matching as dd_stack_scan.py; your host list is read from
    Datadog and matched on this machine).
  - EOL_PRODUCTS: a comma-separated list of endoflife.ai slugs you run, each
    optionally with a version, e.g. "postgresql:14,nodejs:20,nginx".
    A version scopes the end-of-life date to that release line.
  Both may be set; the scope is the union. Every product in the scope is
  tagged detected:true, and the recommended monitors alert only on those.
  - EOL_FULL_CATALOG=1 (opt-in): also send every product endoflife.ai tracks
    (about 700 today, about 1,550 series per run), tagged detected:false
    unless detected or listed. Use it as a reference table; it may count
    toward your Datadog custom metrics (see the README).
  With none of these set the exporter sends nothing and says how to set a scope.

Options:
  --dry-run   show the scope and the series count, push nothing (no DD_API_KEY needed)
  --sample    detect products from the stack scan's synthetic sample hosts
              (demo/sandbox use) instead of your real hosts

Data collection: the only requests to endoflife.ai are GETs of the public
scores file (and, when EOL_PRODUCTS names a version, the public per-version
file scanner-db.json), identified by the user agent below. No hostnames,
Datadog keys or other customer data are sent to endoflife.ai.

Metrics pushed, one series per product in scope (per product and version for
listed versions):
  endoflife.risk_score        (0-100) composite score
  endoflife.days_until_eol    days to the next scheduled end of life (negative once past)
  endoflife.kev_factor_score  (0-20) the CISA KEV component in isolation, only for KEV-active products
Tags: product, grade, eol_phase, kev_active, detected, source (and version for
listed versions).
"""

import os
import sys
import urllib.request
import json
import time
from datetime import datetime, timezone

import dd_stack_scan

DD_API_KEY = os.environ.get("DD_API_KEY")
DD_APP_KEY = os.environ.get("DD_APP_KEY")
# Datadog site: datadoghq.com (default, US1), us3.datadoghq.com, us5.datadoghq.com,
# datadoghq.eu, ap1.datadoghq.com, ddog-gov.com. Set DD_SITE to match your account.
DD_SITE = (os.environ.get("DD_SITE") or "datadoghq.com").strip().lower()
DD_METRICS_URL = f"https://api.{DD_SITE}/api/v2/series"
SCORES_URL = "https://endoflife.ai/scores.json"
VERSIONS_URL = "https://endoflife.ai/scanner-db.json"

EOL_PRODUCTS = os.environ.get("EOL_PRODUCTS", "")
EOL_FULL_CATALOG = os.environ.get("EOL_FULL_CATALOG", "").strip().lower() in ("1", "true", "yes")

# The exporter identifies itself honestly when it fetches the public scores
# file, so endoflife.ai can count active installations in aggregate (requests
# and distinct client addresses per day, from its CDN analytics). Nothing else
# is sent to endoflife.ai: no install ID, no hostnames, no Datadog keys, no
# customer data. The Datadog API key is only ever sent to Datadog.
EXPORTER_VERSION = "1.3.1"
USER_AGENT = f"endoflife-datadog-exporter/{EXPORTER_VERSION} (+https://endoflife.ai/integrations)"
# endoflife.ai's own sandbox job sets EOL_EXPORTER_INTERNAL=1 so its daily run
# is counted apart from customer installations.
if os.environ.get("EOL_EXPORTER_INTERNAL") == "1":
    USER_AGENT = USER_AGENT[:-1] + "; internal)"


def fetch_json(url, label):
    print(f"Fetching {label} from endoflife.ai...")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json"
        }
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read())


def days_until(eol_date_str):
    if not eol_date_str or not isinstance(eol_date_str, str):
        return None
    try:
        eol_dt = datetime.strptime(eol_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (eol_dt - now).days
    except ValueError:
        return None


def parse_product_list(text):
    """'postgresql:14, nodejs:20,nginx' -> [('postgresql', '14'), ('nodejs', '20'), ('nginx', None)]"""
    entries = []
    for item in text.split(","):
        item = item.strip().lower()
        if not item:
            continue
        slug, _, version = item.partition(":")
        entries.append((slug.strip(), version.strip() or None))
    return entries


def match_cycle(cycles, version):
    """The release line a version belongs to: the exact cycle, else the longest
    cycle the version starts with ('14.5' -> '14', '9.6.24' -> '9.6')."""
    by_name = {str(c.get("c", "")).lower(): c for c in cycles}
    if version in by_name:
        return by_name[version]
    best = None
    for name, cycle in by_name.items():
        if version.startswith(name + ".") and (best is None or len(name) > len(str(best.get("c")))):
            best = cycle
    return best


def detect_products(scores, sample):
    """Products the stack scan finds on your hosts (or on the sample hosts)."""
    if sample:
        print("[--sample] Detecting products on the synthetic sample hosts, not your real hosts.")
        hosts = dd_stack_scan.SAMPLE_HOSTS
    elif DD_APP_KEY:
        hosts = dd_stack_scan.fetch_hosts()
    else:
        return None
    matches = dd_stack_scan.correlate(hosts, scores)
    print(f"Stack scan: {len(hosts)} host(s), {len(matches)} host/product match(es).")
    return {product for _, product, _ in matches}


def phase_for(dte, eol_value=None):
    # approaching = EOL date in the future; past = already EOL'd; none = no dated EOL.
    # Lets dashboards split "plan the upgrade" from "remediate overdue"
    # without value-filter gymnastics (Bits AI critique, 2026-08-03).
    if dte is not None:
        return "past" if dte < 0 else "approaching"
    return "past" if eol_value is True else "none"


def resolve_scope(scores, sample):
    """Returns (entries, configured) where entries is a list of
    (product, version_or_None, detected_bool)."""
    listed = parse_product_list(EOL_PRODUCTS)
    unknown = sorted({slug for slug, _ in listed if slug not in scores})
    if unknown:
        print(f"WARNING: not endoflife.ai product slugs, skipped: {', '.join(unknown)}")
        print("  Slugs are the last part of the product page URL, e.g. endoflife.ai/postgresql")
    listed = [(slug, ver) for slug, ver in listed if slug in scores]

    detected = detect_products(scores, sample)
    configured = bool(EOL_PRODUCTS.strip()) or detected is not None or EOL_FULL_CATALOG

    versioned = {}
    for slug, ver in listed:
        if ver:
            versioned.setdefault(slug, [])
            if ver not in versioned[slug]:
                versioned[slug].append(ver)
    in_use = set(detected or set()) | {slug for slug, _ in listed}

    entries = []
    for slug in sorted(in_use):
        if slug in versioned:
            # A listed version is more precise than detection: it replaces the
            # product-level series, so a monitor never alerts on a release
            # line you do not run.
            for ver in versioned[slug]:
                entries.append((slug, ver, True))
        else:
            entries.append((slug, None, True))
    if EOL_FULL_CATALOG:
        for slug in sorted(scores):
            if slug not in in_use:
                entries.append((slug, None, False))
    return entries, configured


def build_series(scores, entries, versions_db):
    now = int(time.time())
    series = []
    for product, version, detected in entries:
        data = scores[product]
        score = data.get("score")
        if score is None:
            continue

        factors = data.get("factors", {}) or {}
        kev_score = factors.get("kev", 0) or 0
        kev_active = "true" if kev_score > 0 else "false"

        version_tag = None
        if version:
            cycles = ((versions_db or {}).get(product) or {}).get("cycles") or []
            cycle = match_cycle(cycles, version)
            if cycle is None:
                # Never stand in another line's date for a version we cannot place.
                print(f"WARNING: {product} has no release line matching version {version}; skipped "
                      f"(see endoflife.ai/{product} for its release lines)")
                continue
            version_tag = str(cycle.get("c"))
            eol_value = cycle.get("eol")
        else:
            # nextEolDate (scores.json since 2026-10-18) is the next scheduled end of life among the
            # lines still supported. eolDate is the risk score basis: once any line has ended it is
            # that PAST date, so PostgreSQL was tagged past while 14 ends 2026-11-12 and the 30-day
            # approaching monitor could never fire. Fall back to eolDate where no line is scheduled.
            eol_value = data.get("nextEolDate") or data.get("eolDate")
        dte = days_until(eol_value)
        eol_phase = phase_for(dte, eol_value)

        # No eol_date or eol_label tag (1.3.0): a date tag started a new series every time a
        # date moved and no widget or monitor used it (the days are the value of
        # endoflife.days_until_eol); eol_label repeated grade.
        base_tags = [
            f"product:{product}",
            f"grade:{data.get('grade', '')}",
            f"eol_phase:{eol_phase}",
            f"kev_active:{kev_active}",
            f"detected:{'true' if detected else 'false'}",
            "source:endoflife.ai"
        ]
        if version_tag:
            base_tags.append(f"version:{version_tag}")
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

        # Only for products with an active CISA KEV entry (1.3.1): the value is zero everywhere
        # else, so a zero series added nothing but a billable series per product.
        if kev_score > 0:
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


def print_no_scope():
    print("Nothing to send: no scope is set, so the exporter does not push the whole catalog.")
    print("Tell it which software you run, in one or both of these ways:")
    print('    setx DD_APP_KEY "your-datadog-application-key"   (detect products on your Datadog hosts)')
    print('    setx EOL_PRODUCTS "postgresql:14,nodejs:20,nginx"  (list endoflife.ai slugs, optional :version)')
    print("To send every product endoflife.ai tracks (about 1,550 series per run), set EOL_FULL_CATALOG=1.")


def main():
    dry_run = "--dry-run" in sys.argv
    sample = "--sample" in sys.argv

    if not DD_API_KEY and not dry_run:
        print("ERROR: DD_API_KEY environment variable is not set.")
        print('Run this once, then open a new terminal:')
        print('    setx DD_API_KEY "your-datadog-api-key"')
        sys.exit(1)

    scores = fetch_json(SCORES_URL, "risk scores")
    print(f"Loaded {len(scores)} products.")

    entries, configured = resolve_scope(scores, sample)
    if not configured:
        print_no_scope()
        sys.exit(2)

    versions_db = None
    if any(version for _, version, _ in entries):
        versions_db = (fetch_json(VERSIONS_URL, "per-version end-of-life dates") or {}).get("products") or {}

    in_scope = sum(1 for _, _, d in entries if d)
    print(f"Scope: {in_scope} detected or listed product(s)"
          + (f" plus {len(entries) - in_scope} catalog product(s) (EOL_FULL_CATALOG=1)" if EOL_FULL_CATALOG else "")
          + ".")
    for product, version, detected in entries:
        if detected:
            print(f"  {product}{':' + version if version else ''}")

    if not entries:
        print("No detected or listed product matches endoflife.ai's catalog yet; nothing to send.")
        print("Add products with EOL_PRODUCTS, or alias entries in dd_stack_scan.py if a host app is missed.")
        return

    series = build_series(scores, entries, versions_db)
    if dry_run:
        print(f"\n[--dry-run] Would push {len(series)} series to Datadog ({DD_SITE}); nothing sent.")
        return
    print(f"Pushing {len(series)} metric points ({len(series)} series) to Datadog ({DD_SITE})...")
    pushed = push_to_datadog(series)
    print(f"\nDone. {pushed} metric points are live in your Datadog account.")
    print(f"Check: Metrics Explorer on your Datadog site ({DD_SITE}), query endoflife.risk_score{{detected:true}}")


if __name__ == "__main__":
    main()
