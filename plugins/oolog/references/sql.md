# Raw SQL with `oo --sql`

Use `--sql` only when `-f`/`-m` can't express what you need: aggregation (`GROUP BY`, `count`),
numeric comparison, or selecting specific columns.

## Rules

- `--sql` **replaces** all `-f` and `-m` filters. Put every condition in the `WHERE` clause.
- `FROM` must name exactly one stream. `--sql` cannot be combined with multiple streams.
- The time window still comes from `--since` / now — it is applied outside your SQL.
- **Order by `_timestamp`, not `timestamp`.** `_timestamp` is the microsecond integer
  OpenObserve sorts on; `timestamp` is the display string.
- Your `ORDER BY` is preserved exactly as the server returned it. (Plain `oo show` queries, by
  contrast, print oldest-first regardless of the internal sort.)
- Pair with `--json`. Aggregate rows are emitted through the log-record envelope: `_timestamp` is
  `0`, any selected column named `timestamp`/`level`/`message` is hoisted into the envelope, and
  the rest trail after it. `SELECT level, count(*) c` yields
  `{"_timestamp": 0, "timestamp": null, "level": "error", "message": null, "c": 81}`.

## Recipes

Error counts grouped by message — the fastest way to see what is actually breaking:

```sh
oo show api --json --since 7d --limit 50 \
  --sql "SELECT message, level, count(*) c FROM api
         WHERE level != 'info' GROUP BY message, level ORDER BY c DESC"
```

Level distribution — a cheap sanity check that the window holds data at all:

```sh
oo show api --json --since 30d --limit 10 \
  --sql "SELECT level, count(*) c FROM api GROUP BY level ORDER BY c DESC"
```

Slowest requests:

```sh
oo show api --json --since 3d --limit 10 \
  --sql "SELECT path, duration_ms FROM api
         WHERE duration_ms > 100 ORDER BY duration_ms DESC"
```

Failing endpoints ranked by volume:

```sh
oo show api --json --since 7d --limit 20 \
  --sql "SELECT path, status_code, count(*) c FROM api
         WHERE status_code >= 400 GROUP BY path, status_code ORDER BY c DESC"
```

Most recent non-2xx responses:

```sh
oo show api --json --since 6h --limit 50 \
  --sql "SELECT timestamp, method, path, status_code FROM api
         WHERE status_code >= 400 ORDER BY _timestamp DESC"
```

Does this window contain anything? Run this before concluding "no errors":

```sh
oo show api --json --since 30d --limit 1 --sql "SELECT count(*) c FROM api"
```
