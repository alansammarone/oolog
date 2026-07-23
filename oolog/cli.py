from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from typing import NoReturn

import httpx
import structlog
from pydantic import ValidationError

from oolog.client import OpenObserveClient
from oolog.config import OOSettings
from oolog.render import format_line
from oolog.schemas import Filter, LogQuery, LogRecord, Selector, TailRequest

_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNIT_S: dict[str, int] = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_SIGINT_EXIT = 130


def _die(message: str) -> NoReturn:
    sys.exit(f"oo: {message}")


def _now_us() -> int:
    return int(time.time() * 1_000_000)


def _parse_since(spec: str) -> int:
    match = _DURATION.match(spec)
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        return _now_us() - amount * _UNIT_S[unit] * 1_000_000
    try:
        parsed = datetime.fromisoformat(spec.replace("Z", "+00:00"))
    except ValueError:
        _die(
            f"cannot parse --since {spec!r}: use 30s / 5m / 2h / 7d or an ISO timestamp"
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000)


def _parse_filter(spec: str) -> Filter:
    for op_str, op_name in (("!=", "ne"), ("~", "contains"), ("=", "eq")):
        if op_str in spec:
            field, value = spec.split(op_str, 1)
            return Filter(field=field.strip(), op=op_name, value=value)
    _die(f"bad -f value (need key=value, key!=value, or key~value): {spec!r}")


def _build_selector(args: argparse.Namespace) -> Selector:
    filters = [_parse_filter(f) for f in (args.filter or [])]
    if args.sql is not None and len(args.stream) > 1:
        _die("--sql cannot be combined with multiple streams")
    return Selector(
        streams=args.stream,
        filters=filters,
        match=args.match or [],
        sql=args.sql,
    )


def _make_client(args: argparse.Namespace) -> OpenObserveClient:
    settings = OOSettings()  # type: ignore[call-arg]  # ty: ignore[missing-argument]
    url = args.url if args.url is not None else settings.url
    org = args.org if args.org is not None else settings.org
    return OpenObserveClient.connect(
        url=url, org=org, user=settings.user, password=settings.password
    )


def _cmd_streams(args: argparse.Namespace) -> None:
    client = _make_client(args)
    for stream in client.list_streams():
        print(f"{stream.name:24} docs={stream.doc_num}")


def _display_order(records: list[LogRecord], selector: Selector) -> list[LogRecord]:
    # Raw SQL carries its own ORDER BY; reversing it would contradict the caller.
    if selector.sql is not None:
        return records
    return list(reversed(records))


def _cmd_show(args: argparse.Namespace) -> None:
    selector = _build_selector(args)
    show_stream = len(args.stream) > 1
    client = _make_client(args)
    query = LogQuery(
        selector=selector,
        start_time_us=_parse_since(args.since),
        end_time_us=_now_us(),
        limit=args.limit,
    )
    records = client.search(query)
    for record in _display_order(records, selector):
        print(format_line(record, args.json, show_stream))


def _cmd_stream(args: argparse.Namespace) -> None:
    selector = _build_selector(args)
    show_stream = len(args.stream) > 1
    client = _make_client(args)
    req = TailRequest(selector=selector, interval_s=args.interval)
    for record in client.tail(req):
        print(format_line(record, args.json, show_stream), flush=True)


def _unset_settings_vars(exc: ValidationError) -> list[str]:
    return [
        f"OPENOBSERVE_{str(error['loc'][0]).upper()}"
        for error in exc.errors()
        if error["type"] == "missing" and error["loc"]
    ]


def _server_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        detail = body.get("error") or body.get("message")
    else:
        detail = response.text
    return " ".join(str(detail).split()) or f"HTTP {response.status_code}"


def _origin(url: httpx.URL) -> str:
    return f"{url.scheme}://{url.netloc.decode()}"


def _status_error_message(exc: httpx.HTTPStatusError) -> str:
    if exc.response.status_code in (401, 403):
        return (
            "OpenObserve rejected the credentials — "
            "check OPENOBSERVE_USER and OPENOBSERVE_PASSWORD"
        )
    return f"OpenObserve rejected the request: {_server_detail(exc.response)}"


def _dispatch(args: argparse.Namespace) -> None:
    try:
        args.func(args)
    except ValidationError as exc:
        unset = _unset_settings_vars(exc)
        if not unset:
            raise
        _die(f"unset environment variables: {', '.join(unset)} — see the README")
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        _die(
            f"cannot reach OpenObserve at {_origin(exc.request.url)} "
            "— check OPENOBSERVE_URL"
        )
    except httpx.TimeoutException as exc:
        _die(f"OpenObserve timed out: {_origin(exc.request.url)}")
    except httpx.HTTPStatusError as exc:
        _die(_status_error_message(exc))
    except KeyboardInterrupt:
        sys.exit(_SIGINT_EXIT)


def _add_query_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("stream", nargs="+", help="one or more stream names")
    parser.add_argument(
        "-f", "--filter", action="append", help="key=value / key!=value / key~value"
    )
    parser.add_argument(
        "-m", "--match", action="append", help="full-text match_all term"
    )
    parser.add_argument("--sql", help="raw SQL, replaces -f/-m")
    parser.add_argument("--json", action="store_true", help="emit JSONL")


def main() -> None:
    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        processors=[
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
    )
    parser = argparse.ArgumentParser(prog="oo", description="OpenObserve log CLI")
    parser.add_argument("--url", help="override OPENOBSERVE_URL")
    parser.add_argument("--org", help="override OPENOBSERVE_ORG")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("streams", help="list log streams").set_defaults(func=_cmd_streams)

    show = sub.add_parser("show", help="query historical logs")
    _add_query_flags(show)
    show.add_argument(
        "--since", default="1h", help="duration (1h/30m/2d) or ISO timestamp"
    )
    show.add_argument("--limit", type=int, default=100)
    show.set_defaults(func=_cmd_show)

    stream = sub.add_parser("stream", help="live tail (polling)")
    _add_query_flags(stream)
    stream.add_argument(
        "--interval", type=float, default=2.0, help="poll interval in seconds"
    )
    stream.set_defaults(func=_cmd_stream)

    args = parser.parse_args()
    _dispatch(args)
