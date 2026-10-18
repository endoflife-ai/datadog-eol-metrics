# endoflife.ai integration for Datadog

Brings endoflife.ai's per-product EOL Risk Scores into Datadog as metrics, so
end-of-life exposure can be charted, alerted on and correlated with the hosts
Datadog already monitors.

The integration tile is in the Datadog Integrations catalog (in Datadog:
**Integrations**, search `endoflife`). This repository holds the exporter the
tile's setup steps point to, plus the dashboard and monitor templates.
Questions: partners@endoflife.ai.

Data: [endoflife.ai](https://endoflife.ai) - end-of-life dates for 600+
products, with the [EOL Risk Score](https://endoflife.ai/risk-score) (0-100)
on every version. Every date names its source: for the products endoflife.ai
verifies directly it is the vendor's own lifecycle page, for the rest it is the
open endoflife.date dataset, and the API says which.

## What it sends to Datadog

| Metric | Type | Meaning |
| --- | --- | --- |
| `endoflife.risk_score` | gauge, 0-100 | EOL Risk Score; higher means more dangerous to keep running |
| `endoflife.days_until_eol` | gauge, days | Days until the product's next scheduled end of life (`nextEolDate`); when no line has one scheduled, days since the most recent one (negative) |
| `endoflife.kev_factor_score` | gauge, 0-20 | The CISA Known Exploited Vulnerabilities component of the score, in isolation |
| `endoflife.stack_risk_score` | gauge, 0-100 | Optional: the score scoped to software detected on hosts in your own Datadog account (`dd_stack_scan.py`) |

Tags on every point: `product` (endoflife.ai slug), `grade` (A-F), `eol_label`,
`eol_date`, `eol_phase` (`approaching`, `past` or `none`), `kev_active`
(`true`/`false`), `source:endoflife.ai`. The stack scan adds `host_name`.

Source: `https://endoflife.ai/scores.json`, rebuilt daily from the lifecycle
data endoflife.ai publishes. The exporter consumes the same public file anyone
can download; no endoflife.ai account or key is needed.

## Setup

Python 3.8 or later; standard library only, nothing to install.

1. In Datadog: **Organization Settings -> API Keys**, create or copy an API key.
2. Set the environment variables (Windows shown; use `export` elsewhere):

   ```
   setx DD_API_KEY "your-datadog-api-key"
   setx DD_SITE    "datadoghq.com"
   ```

   `DD_SITE` is optional and defaults to `datadoghq.com`. Set it to your
   account's site if different: `datadoghq.eu`, `us3.datadoghq.com`,
   `us5.datadoghq.com`, `ap1.datadoghq.com`, `ddog-gov.com`.

3. Push the catalog scores:

   ```
   python dd_push.py
   ```

4. Confirm in **Metrics -> Summary** (search `endoflife.risk_score`). Schedule
   the push daily (cron, Task Scheduler, a CI job) to track the daily rebuild.

### Optional: scores for the software on your own hosts

`dd_stack_scan.py` reads your host inventory from Datadog (host names, host
tags and detected apps), matches it against endoflife.ai's products on your
machine, and pushes `endoflife.stack_risk_score` per host and product back to
Datadog. It needs an application key as well:

```
setx DD_APP_KEY "your-datadog-application-key"
python dd_stack_scan.py --sample          # try the matching logic on synthetic hosts, sends nothing
python dd_stack_scan.py                   # scan your real hosts and push the matches to Datadog
python dd_stack_scan.py --sample --push   # push the synthetic sample hosts (demo use)
```

## Dashboard and monitors

- `assets/dashboards/eol_risk_overview.json` - the EOL Risk Overview dashboard
  (import in Datadog: **Dashboards -> New Dashboard -> Import dashboard JSON**).
- `assets/monitors/` - four monitor templates: critical EOL risk, approaching
  end of life within 30 days, host running EOL software, KEV-exposed product
  (create in Datadog: **Monitors -> New Monitor -> Import from JSON**).

The integration tile in the Datadog Integrations catalog includes the same
dashboard.

## Data collection

The exporter downloads endoflife.ai's public scores file and identifies itself
by user agent (`endoflife-datadog-exporter/<version>`), so endoflife.ai can
count active installations in aggregate. Nothing else is sent to endoflife.ai:
your Datadog API and application keys go only to Datadog, and the stack scan
reads your host inventory from Datadog, matches it locally and pushes the
results only to Datadog. There is no install ID and no telemetry endpoint.

## Licence

MIT. endoflife.ai, EOL Risk Score and the scores data are provided by
EndofLife AI Inc.; Datadog is a trademark of Datadog, Inc. This project is not
affiliated with or endorsed by Datadog.
