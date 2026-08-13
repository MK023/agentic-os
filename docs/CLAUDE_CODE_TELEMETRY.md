# Enabling Claude Code telemetry toward the hub

Set these in your shell profile, or in Claude Code's `settings.json` `env` block,
on any machine you want observed:

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=https://otel.marcobellingeri.dev
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer <the OTLP_INGEST_TOKEN from docker/.env>"
export OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
```

Three of these are easy to get subtly wrong:

- **`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE`**, not
  `OTEL_METRICS_EXPORTER_OTLP_TEMPORALITY_PREFERENCE`. A misspelled variable is
  silently ignored, leaving the default (`delta`) — and Prometheus wants
  `cumulative`.
- **`OTEL_LOGS_EXPORTER` is deliberately left unset.** The hub's Collector runs a
  metrics pipeline only (the Prometheus exporter supports the metrics signal
  only), so enabling the logs exporter would just produce rejected exports.
- **`OTEL_EXPORTER_OTLP_HEADERS`** is what the Collector's `bearertokenauth`
  extension checks. Without it every export is rejected with 401 rather than
  silently dropped, so a typo is visible: `docker compose logs otel-collector`
  in the Railway logs for that service.

## Check the header, because an empty one is silent

This cost an hour on 2026-07-29. If `OTEL_EXPORTER_OTLP_HEADERS` is empty or
malformed, Claude Code does not warn, does not retry visibly, and does not log
anything: the Collector answers 401 and the export disappears. Nothing anywhere says
so — not the shell, not Grafana, not the Collector's own logs at info level.

So verify it, every time you change it and the first time after a reboot:

```bash
printf %s "$OTEL_EXPORTER_OTLP_HEADERS" | wc -c   # 85: "Authorization=Bearer " (21) + 64 hex
echo "$CLAUDE_CODE_ENABLE_TELEMETRY"              # 1
```

Read the token from the Keychain rather than writing it into the profile — the file
then holds a reference, not a value:

```bash
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer $(security find-generic-password -s agentic-os-otlp-ingest -w | tr -d '\n')"
```

`tr -d '\n'` is not paranoia: a trailing newline inside a header is exactly the kind
of invisible defect that produces this failure mode. Doppler would work here too, but
it makes a network call on every new terminal and fails offline — Doppler is the
source of truth, the Keychain is the operational copy.

Variables are read **when the process starts**: a Claude Code session opened before
you edited the profile will never export, and will not say so either.

## Metric names

Claude Code exports `claude_code.session.count`, `claude_code.token.usage`
(attribute `type`: `input`/`output`/`cacheRead`/`cacheCreation`),
`claude_code.cost.usage` and `claude_code.lines_of_code.count`.

The Collector is configured with `translation_strategy:
UnderscoreEscapingWithoutSuffixes`, so Prometheus sees them as
`claude_code_session_count`, `claude_code_token_usage`, `claude_code_cost_usage`
— no type/unit suffixes. Those exact names appear in
`docker/grafana/dashboards/claude-code.json` and in `QUERIES` in
`services/public-status-api/main.py`; changing the strategy means changing both.

Claude Code's telemetry is beta and its metric names are versioned — re-check
them against your installed version's docs if a panel goes empty.

## Privacy: what actually reaches Prometheus

Measured against the real client (v2.1.220) on 2026-07-28, not assumed. Claude Code
sends identity as **data point** attributes, not resource attributes, so
`resource_to_telemetry_conversion: false` does **not** keep them out — they arrive as
plain Prometheus labels. What showed up: `user_email` (a real address),
`user_id`, `user_account_id`, `user_account_uuid`, `organization_id`.

They never reach storage: the Collector keeps an **allow-list** of labels
(`transform/label-allowlist` in `docker/otel-collector-config.yaml`) and drops
everything else. Surviving labels are `model`, `type`, `query_source`, `start_type`,
`terminal_type`, `session_id`; a grep for the address over the whole `/metrics`
output returns nothing.

An allow-list rather than a delete-list on purpose. Deleting the five known identity
attributes works until a release adds a sixth — this telemetry is beta and its
attribute set is not a contract. Verified by sending attributes no version emits
today (`user.name`, `user.phone`, `workspace.path`): all three were dropped without
any rule naming them.

**`session_id` is kept on purpose.** Deleting it looked right (one value per session
is a cardinality generator) and lost data: Claude Code's counters are cumulative per
process, so with the id gone two concurrent sessions write the same series and the
last export wins — measured, two parallel sessions produced one series reading `1`
instead of `2`. It is a random per-run UUID, it never leaves the hub, and the public
endpoint only ever returns sums.

If you add a producer, check its labels the same way — `curl` the Collector's
`/metrics` and read them — rather than trusting what its documentation implies.

### Why not delta temporality, which would remove `session_id` entirely

The OTel data model says delta temporality "unburdens the client from keeping
high-cardinality state" and suits short-lived processes — which is exactly what a
Claude Code session is, and exactly why `session_id` has to be a label here. Sending
delta and converting in the Collector (`deltatocumulative`) would sum the sessions
into one stream and make the label unnecessary. It is the more correct architecture
on paper.

Not adopted, for three reasons that are all about this deployment rather than about
the idea: the processor is **alpha**, and it keeps its accumulation **in memory**, so
every redeploy resets the counters. Trading a verified setup for an alpha component
that forgets on restart is not a good trade at this size.

Its `max_stale` default — a stream dropped after 5 minutes of inactivity — used to be
the third reason, by analogy with the exporter's `metric_expiration`, which had already
cost us a day's worth of zeroes. That argument died on 2026-08-14: the window now comes
from the query (`max_over_time(...[25h])`) and the exporter itself sits at 5 minutes on
purpose, so a short stale window is no longer disqualifying on its own. The two reasons
above are enough without it, and an argument that no longer distinguishes the options
should not be left standing as if it did.

Revisit if it reaches beta, or if Phase 2 brings enough producers that the session
label actually costs something.
