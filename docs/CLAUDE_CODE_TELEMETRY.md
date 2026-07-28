# Enabling Claude Code telemetry toward the hub

Set these in your shell profile, or in Claude Code's `settings.json` `env` block,
on any machine you want observed:

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=https://otel.yourdomain.com
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
  on the VPS.

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

## Privacy note

Claude Code attaches `user.email`, `user.id`, `organization.id` and `session.id`
as resource attributes. The Collector leaves `resource_to_telemetry_conversion`
off, so none of them become Prometheus labels: no PII is stored, and `session.id`
never becomes an unbounded-cardinality label. Don't turn it on to "get more
detail" without revisiting that trade-off.
