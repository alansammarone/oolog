---
name: investigating-logs
description: Use when investigating application behavior, errors, exceptions, tracebacks, slow requests, failed jobs, or HTTP traffic in logs stored in OpenObserve, or whenever the user asks to look at, search, grep, or tail logs. Requires the `oo` CLI.
allowed-tools: Bash(oo *)
---

# Investigating logs with `oo`

`oo` is a typed CLI over an OpenObserve instance. Use it to answer "what is the app doing / why
did it break" from logs rather than guessing from code.

Streams on the configured instance:

!`oo streams 2>&1 || echo "(oo unavailable — see Setup below: install the CLI, then set the OPENOBSERVE_* env vars)"`

If that shows an error instead of stream names, stop and report it. Do not guess stream names or
credentials.

## Setup

```sh
uv tool install git+https://github.com/alansammarone/oolog.git
```

Connection comes from the environment (pydantic settings, prefix `OPENOBSERVE_`):
`OPENOBSERVE_URL`, `OPENOBSERVE_ORG`, `OPENOBSERVE_USER`, `OPENOBSERVE_PASSWORD`.
`--url` / `--org` override per invocation. Never echo the password.

## Commands

```sh
oo streams                          # list streams + doc counts
oo show   <stream...> [filters]     # historical query (default --since 1h, --limit 100)
oo stream <stream...> [filters]     # live tail by polling (default --interval 2s)
```

`show` prints oldest→newest. `stream` blocks forever — only for live tailing, never for
history.

Both accept multiple streams, merged and time-ordered, so you read them as one log. With 2+
streams each line gets a `[stream_name]` prefix (a `_stream` field under `--json`).

## Widen the window before concluding anything

**`--since` defaults to 1h.** On a quiet or freshly-restarted instance an hour holds almost
nothing, so a filtered query returns empty and it looks like there are no errors. That is a
false negative, not an answer.

Before reporting "no errors found", re-run at `--since 7d` or `30d`. Confirm the window actually
contains data:

```sh
oo show <stream> --json --since 30d --limit 1 --sql "SELECT count(*) c FROM <stream>"
```

If a narrow window is empty and a wide one isn't, say when the activity actually stopped — that
is usually the real finding.

## Filters

| Form | Meaning |
|---|---|
| `-f field=value` | exact match |
| `-f field!=value` | not equal |
| `-f field~value` | substring (SQL `LIKE %value%`) |
| `-m TEXT` | full-text `match_all(TEXT)` across the record |
| `--sql "..."` | raw SQL, **replaces** all `-f`/`-m` |

Multiple `-f` are AND-joined. `--since` takes `30m` / `2h` / `3d` or an ISO timestamp. `--json`
emits one JSON object per line — use it to parse fields or read a full multi-line `exception`.

## Record shape

Top-level on every record: `timestamp` (ISO8601 UTC), `level`, `message`, then arbitrary
structlog kwargs. For records bridged from stdlib logging, `message` is the raw log message.

Structlog kwargs are present; fields the application only sets on its own events (a `service` or
component name, say) are **absent on bridged records** from libraries like uvicorn, SQLAlchemy,
or httpx. Scope to a process by **stream name**, which is always reliable — don't filter on an
application field until you've confirmed it's populated for the events you want.

HTTP access logs typically carry `method`, `path`, `status_code`, `duration_ms`. Exceptions
carry a multi-line `exception` field.

## Workflow

1. Read the stream list above; confirm the connection works.
2. Scope with `--since` and a `level` or `message` filter, then widen.
3. Skim in text mode; switch to `--json` to extract fields or read tracebacks.
4. Reach for `--sql` only for aggregation or numeric comparison —
   see `${CLAUDE_SKILL_DIR}/references/sql.md`.

```sh
# Recent errors
oo show api -f level=error --since 7d

# Full tracebacks
oo show api -f level=error --since 7d --json

# Find a failure by full-text term
oo show api -m "connection refused" -f level=error --since 30d --limit 20

# One route
oo show api -f path=/api/users --since 6h

# Correlate two processes, merged and time-ordered
oo show api worker --since 2h

# Live-tail errors (blocks; Ctrl-C to stop)
oo stream api -f level=error --interval 5
```

## Gotchas

- **`oo stream` never returns.** Use `oo show` for history. If you do tail, background it or cap it.
- **Levels below `info` are usually filtered at the source** — don't expect `debug` to be there.
- **`-f field~value` is a SQL `LIKE`**; the value is wrapped in `%...%` for you.
- **With multiple streams, `--limit` is a combined total**, not per-stream — the results are
  merged and sorted, then truncated. One bad stream name fails the whole command.
- **`--sql` is single-stream only** (it carries its own `FROM`) and replaces `-f`/`-m`.
- **In raw SQL, order by `_timestamp`, not `timestamp`.** `_timestamp` is the microsecond integer
  OpenObserve sorts on; `timestamp` is the display string.
- **Aggregate rows come back wearing the log-record envelope.** Under `--json`, a `GROUP BY` row
  carries `_timestamp: 0`; any column you selected that is named `timestamp`, `level`, or
  `message` is hoisted into the envelope, and everything else trails after it. So
  `SELECT level, count(*) c` yields
  `{"_timestamp": 0, "timestamp": null, "level": "error", "message": null, "c": 81}` — envelope
  fields you didn't select stay null. Read the row as a whole, not just the tail.
