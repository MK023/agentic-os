# Enabling Claude Code telemetry toward the hub

Set these in your shell profile on any machine you want observed. Claude Code's
`settings.json` `env` block also works for every line except the header. Keep the ingest
bearer in the machine's own secret store and let the profile reference it: the Keychain
on macOS, a `0600` file on Linux. Both are below.

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp          # dalla Fase 1.5: senza, Loki resta vuoto
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
- **`OTEL_LOGS_EXPORTER=otlp` — set it, since Phase 1.5.** This line said the
  opposite until 2026-08-20, and for a good reason that expired: the Collector used
  to run a metrics pipeline only, so the logs exporter had nowhere to land. It now
  runs a *second, separate* `logs` pipeline into Loki, and **this variable is the
  switch that feeds it**. Leave it unset and the pipeline is wired correctly and
  receives nothing — Grafana shows an empty log store, every gate stays green, and the
  diagnosis will be "Loki is broken". This file is the only place that tells an
  operator which variables to export, so a stale line here is not a documentation
  bug, it is the outage.

  **Setting it in a profile is not the same as the running client having it**, and the
  difference is invisible from the hub. `claude` inherits its environment from the
  process that launched it: a terminal window opened *before* you edited the profile
  keeps the old environment, and every session started in that window exports metrics
  and no logs — which is the exact picture of "Loki is broken". Measured on
  2026-08-21, twenty minutes after the first empty query: the profile was correct, a
  fresh login shell had the variable, and none of the seven running Claude Code
  processes did.

  So check the **process**, not the file, and do it without printing the token that
  lives next to it in `OTEL_EXPORTER_OTLP_HEADERS`:

  ```bash
  # presence only, never values — the headers variable holds the ingest bearer
  for p in $(pgrep -f claude); do
    printf '%s %s\n' "$p" "$(ps eww -o command= -p $p | grep -c OTEL_LOGS_EXPORTER=otlp)"
  done
  ```

  A `0` means that session is not exporting logs, whatever the profile says. Open a new
  terminal **window**, confirm `echo $OTEL_LOGS_EXPORTER` there, and start `claude` from
  it.

  What reaches Loki is **not** what the client sends: two allow-lists strip it down to
  22 attributes plus the event name. Identity — `user.email`, `user.id`,
  `organization.id`, `user.account_id`, `user.account_uuid` — rides on **every** log
  record and the vendor marks it *always included*, so no environment variable turns
  it off and the allow-list is the only control. See `SECURITY.md` and
  `docs/LOCAL_DRY_RUN.md` §4-bis for the proof that runs.

  **Do not set the four `OTEL_LOG_*` un-redaction variables** —
  `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_TOOL_DETAILS`,
  `OTEL_LOG_RAW_API_BODIES`. Each un-redacts a different field of session content, and
  the last one ships whole request bodies. The allow-list drops those keys by name, so
  setting one changes nothing here — but the reason to leave them alone is that a
  default is not a control, and the next thing they touch may not be on the list.
- **`OTEL_EXPORTER_OTLP_HEADERS`** is what the Collector's `bearertokenauth`
  extension checks. Without it every export is rejected with 401 rather than
  silently dropped, so a typo is visible: `docker compose logs otel-collector`
  in the Railway logs for that service.

## Check the header, because an empty one is silent

This cost an hour on 2026-07-29. If `OTEL_EXPORTER_OTLP_HEADERS` is empty or
malformed, Claude Code does not warn, does not retry visibly, and does not log
anything: the Collector answers 401 and the export disappears. Not the shell, not
Grafana, not the Collector's own logs at info level.

**There is one witness, found 2026-09-04, and this file used to say there was none.**
`claude --debug` writes a `[3P telemetry]` channel into `~/.claude/debug/<uuid>.txt`,
and it answers the questions that cost that hour — whether telemetry initialised at all,
against which endpoint, and whether the *first* export succeeded:

```bash
grep -ah -oE '\[3P telemetry\][^\r\n]{0,200}' ~/.claude/debug/*.txt | sort -u
```

```
[3P telemetry] isTelemetryEnabled=true (CLAUDE_CODE_ENABLE_TELEMETRY=1)
[3P telemetry] getOtlpReaders: types=["otlp"], interval=60000, protocol=http/protobuf, endpoint=https://otel.marcobellingeri.dev
[3P telemetry] First metrics export: SUCCESS
[3P telemetry] First logs export: SUCCESS
```

`interval=60000` is the **metrics** export period and covers nothing else. No metric
reaches the hub in the first minute of a session, so a check made sooner proves nothing:
add the Prometheus scrape (15s) and the status API's `STATUS_CACHE_TTL_S` (60s) and the
three public numbers can trail a real export by more than two minutes. Logs run on a
separate exporter with its own schedule (`OTEL_LOGS_EXPORT_INTERVAL`, whose default is
not measured here) and the debug channel reports their first export on a line of its
own. Reading an empty Loki as "covered by the metrics interval" is how this project
diagnoses "Loki is broken" a second time.

The channel is per *process*. It says whether **that** session initialised telemetry,
which is the one thing the environment cannot tell you. Measured the same day: a session
holding all seven variables had never exported after twenty minutes, while one started
later from the same profile was counted within ~110 seconds. Telemetry initialises once,
at startup, and a session that failed looks exactly like an idle one. If a session is
missing from the hub and its environment is right, restart it before debugging the hub.

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

### On Linux: a `0600` file, not the Keychain and not `settings.json`

Set up on the VM on 2026-09-04. There is no Keychain here, and `~/.claude/settings.json`
is the wrong substitute: Claude Code rewrites that file, its mode is `0644` and not ours
to choose, and it is the kind of file that gets copied or synced. Who can actually read
it today depends on the home directory above it, which on this VM is `/root` at `0700` —
so the honest statement is that the token would be protected by a directory mode nobody
chose for that purpose, rather than by anything the secret's own file says. The profile
keeps a reference and a file we do control keeps the value:

```bash
install -d -m 0700 ~/.config/agentic-os
install -m 0600 /dev/null ~/.config/agentic-os/otlp-ingest
printf %s "$OTLP_INGEST_TOKEN" > ~/.config/agentic-os/otlp-ingest   # no trailing newline
```

`install -m 0600` rather than a `umask`: on a **rotation** the file already exists, `>`
truncates it in place and a `umask` touches nothing, so a mode that was once wrong stays
wrong while this page asserts `0600`. A `umask` also outlives the command and hands
`0600` to every other file that terminal creates.

The profile block reads that file and **exports nothing at all when it is missing or
empty**, instead of exporting an empty header. An empty header is not a loud error, it
is a 401 the client never prints: the failure that cost an hour on 2026-07-29. Any
profile block written for another machine should keep that guard.

```bash
if [ -r ~/.config/agentic-os/otlp-ingest ]; then
  _otlp_token="$(tr -d '\n' < ~/.config/agentic-os/otlp-ingest)"
  if [ -n "$_otlp_token" ]; then
    export CLAUDE_CODE_ENABLE_TELEMETRY=1
    # ... the five other OTEL_* lines from the top of this file, header excluded ...
    export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer $_otlp_token"
  fi
  unset _otlp_token
fi
```

**The block lives in `~/.bashrc`, which returns early for non-interactive shells.** This
VM's root profile spells that guard `[ -z "$PS1" ] && return`, on line 6; Ubuntu's
`/etc/skel/.bashrc` spells the same idea `case $- in *i*) ;; *) return;; esac`, so grep
for the early `return` rather than for `PS1` and do not conclude the guard is absent.
Either form means cron jobs, systemd units and plain `bash -lc` never inherit these
variables, so anything launched that way exports nothing and says nothing. Use an
*interactive* login shell to exercise the real environment: `bash -lic`, not `bash -lc`.
A session started under a service manager is the first thing to suspect when a machine
that is "configured" has never appeared in the hub.

Two further traps measured the same day:

- `OTEL_EXPORTER_OTLP_HEADERS` uses the OTEL `key=value` form; `curl -H` wants
  `key: value`. Passing the variable straight to `curl` sends a malformed header and
  earns a `403` that reads like a permissions problem. Convert it:
  `h="${OTEL_EXPORTER_OTLP_HEADERS/=/: }"`.
- The header is 85 characters — 21 for `Authorization=Bearer ` plus 64 hex — and
  `printf %s "$OTEL_EXPORTER_OTLP_HEADERS" | wc -c` is the whole check.

Rotating this token now means **four** places, not three; `docs/DECISIONS.md` has the
table and what a missed one costs.

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

**Re-measured 2026-08-16 against v2.1.227**, with two real headless sessions: the label
set reaching Prometheus is still exactly the allow-list, no identity attribute survives,
and the two sessions stayed two distinct series. The allow-list is what makes this a
cheap check rather than an anxious one — it keeps what is named and drops everything
else, so a release that invents a new identity attribute fails safe by construction. The
thing this dry run is really watching for is the opposite: a *rename* of something we
keep, which drops data silently instead of leaking it.

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
