# endoflife.ai integration for Datadog

Brings endoflife.ai's EOL Risk Scores and end-of-life dates for **the software
you run** into Datadog as metrics, so end-of-life exposure can be charted,
alerted on and correlated with the hosts Datadog already monitors.

The integration tile is in the Datadog Integrations catalog (in Datadog:
**Integrations**, search `endoflife`). This repository holds the exporter the
tile's setup steps point to, plus the dashboard and monitor templates.
Questions: partners@endoflife.ai.

Data: [endoflife.ai](https://endoflife.ai) - end-of-life dates for 600+
products, with the [EOL Risk Score](https://endoflife.ai/risk-score) (0-100).
Every date names its source: for the products endoflife.ai verifies directly
it is the vendor's own lifecycle page, for the rest it is the open
endoflife.date dataset, and the API says which.

## What it sends to Datadog

| Metric | Type | Meaning |
| --- | --- | --- |
| `endoflife.risk_score` | gauge, 0-100 | EOL Risk Score; higher means more dangerous to keep running |
| `endoflife.days_until_eol` | gauge, days | Days until the product's next scheduled end of life (`nextEolDate`); when no line has one scheduled, days since the most recent one (negative). For a version you list, the date of that release line |
| `endoflife.kev_factor_score` | gauge, 0-20 | The CISA Known Exploited Vulnerabilities component of the score, in isolation |
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
about 2,050 series per run today (see below). The monitors ignore those
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
| Default (detected or listed products) | 3 per product, 2 if it has no dated end of life; a product listed with two versions counts twice |
| Stack scan | 1 per host and detected product |
| `EOL_FULL_CATALOG=1` | about 2,050 today (about 700 products x 2, plus about 660 dated products), growing with the catalog |

Example: the three sample hosts match 9 products, so 27 product series plus
10 host series. Before 1.3.0 every installation sent the whole catalog, about
2,050 series per run, whether or not it ran those products.

Whether these count as custom metrics: Datadog's documentation says
*"Metrics reported from accepted integrations are not counted as custom
metrics, and therefore don't impact your custom metric allocation.
(Integrations that emit potentially unlimited metrics may still be considered
custom.)"* ([docs.datadoghq.com/extend](https://docs.datadoghq.com/extend/))
and *"If a metric is not submitted from one of the more than 1,000 Datadog
integrations it's considered a custom metric."*
([custom metrics billing](https://docs.datadoghq.com/account_management/billing/custom_metrics/)).
endoflife.ai is a partner-built integration in the Datadog Integrations
catalog; we have not confirmed how Datadog classifies its metrics in a
customer account. Plan as if they may count, and check **Plan & Usage ->
Usage** after the first run.

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
