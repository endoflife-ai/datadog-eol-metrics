# endoflife.ai integration for Datadog

Brings endoflife.ai's EOL Risk Scores and end-of-life dates for **the software
you run** into Datadog as metrics, so end-of-life exposure can be charted,
alerted on and correlated with the hosts Datadog already monitors.

The integration tile is in the Datadog Integrations catalog (in Datadog:
**Integrations**, search `endoflife`). This repository holds the exporter the
tile's setup steps point to, plus the dashboard and monitor templates.
Questions: partners@endoflife.ai.

Data: [endoflife.ai](https://endoflife.ai) - end-of-life dates for 700+
products, with the [EOL Risk Score](https://endoflife.ai/risk-score) (0-100).
Every date names its source: for the products endoflife.ai verifies directly
it is the vendor's own lifecycle page, for the rest it is the open
endoflife.date dataset, and the API says which.

## What it sends to Datadog

| Metric | Type | Meaning |
| --- | --- | --- |
| `endoflife.risk_score` | gauge, 0-100 | EOL Risk Score; higher means more dangerous to keep running |
| `endoflife.days_until_eol` | gauge, days | Days until the product's next scheduled end of life (`nextEolDate`); when no line has one scheduled, days since the most recent one (negative). For a version you list, the date of that release line |
| `endoflife.kev_factor_score` | gauge, 0-20 | The CISA Known Exploited Vulnerabilities component of the score, in isolation, sent only for products with an active KEV entry |
| `endoflife.stack_risk_score` | gauge, 0-100 | The score per host for software detected on that host (`dd_stack_scan.py`) |

Tags on the three product metrics: `product` (endoflife.ai slug), `grade`
(A-F), `eol_phase` (`approaching`, `past` or `none`), `kev_active`
(`true`/`false`), `detected` (`true` for products detected on your hosts or
listed in `EOL_PRODUCTS`, `false` only for catalog products sent with
`EOL_FULL_CATALOG=1`), `version` (only on products you list with a version),
`source:endoflife.ai`. The stack scan tags `host_name`, `product`, `grade` and
`kev_active`.

Version 1.3.0 dropped the `eol_date` and `eol_label` tags: a date tag started
a new series whenever a date moved and nothing used it (the days are the value
of `endoflife.days_until_eol`), and `eol_label` repeated `grade`.

Source: `https://endoflife.ai/scores.json`, rebuilt daily from the lifecycle
data endoflife.ai publishes (and `https://endoflife.ai/scanner-db.json` for
listed versions). The exporter consumes the same public files anyone can
download; no endoflife.ai account or key is needed.

## Setup

Python 3.8 or later; standard library only, nothing to install. Keep
`dd_push.py` and `dd_stack_scan.py` in the same folder (`dd_push.py` uses the
stack scan's host matching).

1. In Datadog: **Organization Settings -> API Keys**, create or copy an API key.
2. Set the environment variables (Windows shown; use `export` elsewhere):

   ```
   setx DD_API_KEY "your-datadog-api-key"
   setx DD_SITE    "datadoghq.com"
   ```

   `DD_SITE` is optional and defaults to `datadoghq.com`. Set it to your
   account's site if different: `datadoghq.eu`, `us3.datadoghq.com`,
   `us5.datadoghq.com`, `ap1.datadoghq.com`, `ddog-gov.com`.

3. Tell the exporter which software you run, in one or both of these ways:

   - **Detect it** (recommended): **Organization Settings -> Application Keys**,
     create an application key, then `setx DD_APP_KEY "your-datadog-application-key"`.
     The exporter reads your host list from the Datadog Hosts API and matches
     host tags and the apps the Datadog Agent detects against endoflife.ai's
     products, on your machine.
   - **List it**: `setx EOL_PRODUCTS "postgresql:14,nodejs:20,nginx"` - endoflife.ai
     slugs (the last part of the product page URL, for example
     `endoflife.ai/postgresql`), each optionally with a version. A version
     scopes the end-of-life date to that release line (`14.5` matches the
     `14` line); a version that matches no line is skipped with a warning.
     A listed version replaces the product-level series for that product.

4. Check the scope, then push:

   ```
   python dd_push.py --dry-run      # products in scope and the series count; sends nothing
   python dd_push.py                # product metrics for the software in scope
   python dd_stack_scan.py          # per-host scores (needs DD_APP_KEY)
   ```

5. Confirm in **Metrics -> Summary** (search `endoflife.risk_score`). Schedule
   both scripts daily (cron, Task Scheduler, a CI job) to track the daily rebuild.

With no application key, no list and no opt-in, `dd_push.py` sends nothing
and prints how to set a scope.

### Opt-in: the whole catalog

`EOL_FULL_CATALOG=1` also sends every product endoflife.ai tracks, as a
reference table, tagged `detected:false` unless detected or listed. That is
about 1,550 series per run today (see below). The monitors ignore those
products.

### Trying it without real hosts

```
python dd_stack_scan.py --sample          # the matching logic on synthetic hosts; sends nothing
python dd_stack_scan.py --sample --push   # push the synthetic sample hosts (demo use)
python dd_push.py --sample --dry-run      # product scope detected from the sample hosts
```

## Metric count and cost

A Datadog series is one metric name with one combination of tag values.

| Setup | Series per run |
| --- | --- |
| Default (detected or listed products) | per product: 1 risk score, 1 days-until-EOL if it has a dated end of life, 1 KEV factor only if it has an active CISA KEV entry (1 to 3); a product listed with two versions counts twice |
| Stack scan | 1 per host and detected product |
| `EOL_FULL_CATALOG=1` | about 1,550 today (about 700 risk scores, 660 days-until-EOL, 180 KEV factors), growing with the catalog |

endoflife.ai's own Datadog sandbox runs three sample hosts with 9 products
between them. Before: series counted there over 24 hours on 2026-10-19. After:
what 1.3.1 sends for the same hosts (`python dd_push.py --sample --dry-run`
plus the stack scan).

| | Before (1.2.x) | After (1.3.1) |
| --- | --- | --- |
| `endoflife.risk_score` | 698 | 9 |
| `endoflife.days_until_eol` | 656 | 9 |
| `endoflife.kev_factor_score` | 698 | 4 |
| `endoflife.stack_risk_score` | 10 | 10 |
| Total | about 2,062 | 32 |

Before 1.3.0 every installation sent the whole catalog whether or not it ran
those products. `product` is the only tag that adds series; the others have
one value per product, but a change in any of them starts a new series, and
both can count during the hour of the change.

Whether these count as custom metrics: Datadog's documentation lists, among
custom metrics, *"Metrics submitted by Marketplace integrations"* and
*"Metrics submitted from an integration that is not one of the more than
1,000 Datadog integrations"*
([custom metrics](https://docs.datadoghq.com/metrics/custom_metrics/)), and
says *"A custom metric is uniquely identified by a combination of a metric
name and tag values (including the host tag)."*
([custom metrics billing](https://docs.datadoghq.com/account_management/billing/custom_metrics/)).
It also says *"Metrics reported from accepted integrations are not counted as
custom metrics"*, while integrations that emit potentially unlimited metrics
may still be considered custom
([docs.datadoghq.com/extend](https://docs.datadoghq.com/extend/)).
endoflife.ai is a free partner integration in the Datadog Integrations
catalog, not a Marketplace listing, and the documentation does not say which
of these applies to a listed partner integration outside the Marketplace. We
have not had Datadog's answer yet; we are asking them with our next listing
update and will publish it here. Until then, plan as if the metrics may
count, and check **Plan & Usage -> Usage** after the first run.

## Dashboard and monitors

- `assets/dashboards/eol_risk_overview.json` - the EOL Risk Overview
  dashboard. Its `$detected` template variable defaults to `true` (your
  software).
- `assets/monitors/` - four monitor templates: critical EOL risk (76 or
  above, endoflife.ai's Critical band), approaching end of life within 30
  days, KEV-exposed product at 60 or above (these three alert only on
  `detected:true` products) and host running EOL software (Stack Risk Score of
  70 or above). The files are integration-asset wrappers: to create one in
  Datadog, open **Monitors -> New Monitor -> Import from JSON** and paste the
  object under the file's `definition` key.

## Data collection

The exporter downloads endoflife.ai's public scores file (and, when
`EOL_PRODUCTS` names a version, the public per-version file) and identifies
itself by user agent (`endoflife-datadog-exporter/<version>`), so endoflife.ai
can count active installations in aggregate. Nothing else is sent to
endoflife.ai: your Datadog API and application keys go only to Datadog, your
product list stays on your machine, and host detection reads your host
inventory from Datadog, matches it locally and pushes the results only to
Datadog. There is no install ID and no telemetry endpoint.

Host detection reads three fields of each host from `GET /api/v1/hosts`
(paged): `host_name` (or `name`), `tags_by_source` (only `os:`, `platform:`,
`image:`, `runtime:` and `service:` values are used) and `apps`. Every other
field is discarded.

## Licence

MIT. endoflife.ai, EOL Risk Score and the scores data are provided by
EndofLife AI Inc.; Datadog is a trademark of Datadog, Inc. This project is not
affiliated with or endorsed by Datadog.
