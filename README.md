# oolog

[![CI](https://github.com/alansammarone/oolog/actions/workflows/ci.yml/badge.svg)](https://github.com/alansammarone/oolog/actions/workflows/ci.yml)

A typed [OpenObserve](https://openobserve.ai) logs CLI, and a Claude Code plugin that teaches
Claude to use it.

`oo` gives you `show` / `stream` / `streams` over your log data, in the style of the macOS `log`
command. The plugin bundles a skill so an agent investigating a failure reaches for your logs
instead of guessing from the code.

## Install the CLI

```sh
uv tool install git+https://github.com/alansammarone/oolog.git
```

Connection settings come from the environment. All four are required — there are no defaults, so
a misconfigured shell fails loudly rather than silently querying the wrong instance:

| Variable | Example | |
|---|---|---|
| `OPENOBSERVE_URL` | `http://localhost:5080` | base URL, no path |
| `OPENOBSERVE_ORG` | `default` | organization identifier |
| `OPENOBSERVE_USER` | `admin@example.com` | login email |
| `OPENOBSERVE_PASSWORD` | *(secret)* | |

`--url` and `--org` override per invocation. Anything unset is named in the error:

```
$ oo streams
oo: unset environment variables: OPENOBSERVE_USER, OPENOBSERVE_PASSWORD — see the README
```

## Install the Claude Code plugin

```
/plugin marketplace add alansammarone/oolog
/plugin install oolog@oolog
```

The skill loads automatically when you ask Claude about errors, exceptions, slow requests, or
log traffic. It ships the operational knowledge that isn't in `--help`: that the default
one-hour window produces false negatives on quiet instances, that raw SQL orders by `_timestamp`
rather than `timestamp`, and that fields set by your application are absent on records bridged
from library logging.

To try it without installing:

```sh
claude --plugin-dir ./plugins/oolog
```

## Usage

```sh
# What streams exist?
oo streams

# Recent errors, with full tracebacks as JSON
oo show api -f level=error --since 7d
oo show api -f level=error --since 7d --json

# Full-text search
oo show api -m "connection refused" --since 30d --limit 20

# Two processes, merged and time-ordered
oo show api worker --since 2h

# Live tail (blocks; Ctrl-C to stop)
oo stream api -f level=error --interval 5

# Aggregation via raw SQL
oo show api --json --since 7d --limit 20 \
  --sql "SELECT message, count(*) c FROM api
         WHERE level != 'info' GROUP BY message ORDER BY c DESC"
```

### Filter operators

| Syntax | Meaning |
|---|---|
| `key=value` | exact match |
| `key!=value` | not equal |
| `key~value` | contains (SQL `LIKE`) |

Multiple `-f` flags are AND-joined. `-m TEXT` adds a `match_all()` full-text term. `--sql`
accepts raw SQL and replaces all `-f`/`-m` filters.

Multiple streams are merged and time-ordered into a single view. `--limit` is a combined total
across them, not per-stream.

## Development

```sh
uv sync
uv run pytest
```

## License

MIT
