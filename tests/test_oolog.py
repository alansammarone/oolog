from __future__ import annotations

import json
import sys
from collections.abc import Callable
from unittest.mock import patch

import httpx
import pytest

from oolog import cli
from oolog.client import OpenObserveClient
from oolog.query import build_sql
from oolog.render import format_line
from oolog.schemas import Filter, LogQuery, LogRecord, Selector, StreamInfo, TailRequest


class _MockTransport(httpx.BaseTransport):
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._handler(request)


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> OpenObserveClient:
    http = httpx.Client(transport=_MockTransport(handler), base_url="http://test")
    return OpenObserveClient(http=http, org="testorg")


def _record(stream: str | None) -> LogRecord:
    return LogRecord(
        timestamp_us=1,
        timestamp="t",
        level="info",
        message="hi",
        fields={"x": "y"},
        stream=stream,
    )


def test_format_line_text_no_prefix_single_stream() -> None:
    assert format_line(_record("frontend"), False, False) == "t INFO    hi x=y"


def test_format_line_text_prefixes_stream_when_multi() -> None:
    assert (
        format_line(_record("frontend"), False, True) == "[frontend] t INFO    hi x=y"
    )


def test_format_line_json_omits_stream_field_single() -> None:
    data = json.loads(format_line(_record("frontend"), True, False))
    assert "_stream" not in data


def test_format_line_json_adds_stream_field_when_multi() -> None:
    data = json.loads(format_line(_record("frontend"), True, True))
    assert data["_stream"] == "frontend"
    assert data["x"] == "y"


def test_show_builds_sql_and_parses_hits() -> None:
    selector = Selector(
        streams=["mystream"],
        filters=[Filter(field="level", op="eq", value="error")],
        match=[],
        sql=None,
    )
    query = LogQuery(selector=selector, start_time_us=1000, end_time_us=2000, limit=10)

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "_timestamp": 1500000000,
                        "timestamp": "2026-01-01T00:00:00Z",
                        "level": "error",
                        "message": "something failed",
                        "trace_id": "abc",
                    }
                ],
                "total": 1,
            },
        )

    client = _make_client(handler)
    records = client.search(query)

    assert len(records) == 1
    record = records[0]
    assert record.timestamp_us == 1500000000
    assert record.level == "error"
    assert record.message == "something failed"
    assert record.fields == {"trace_id": "abc"}

    body = captured["body"]
    assert (
        body["query"]["sql"]
        == "SELECT * FROM mystream WHERE level = 'error' ORDER BY _timestamp DESC"
    )
    assert body["query"]["start_time"] == 1000
    assert body["query"]["end_time"] == 2000
    assert body["query"]["size"] == 10


def test_filter_apostrophe_escaping() -> None:
    selector = Selector(
        streams=["s"],
        filters=[Filter(field="name", op="eq", value="it's here")],
        match=[],
        sql=None,
    )
    sql = build_sql(selector, "s", "DESC")
    assert sql == "SELECT * FROM s WHERE name = 'it''s here' ORDER BY _timestamp DESC"


def test_tail_deduplicates_across_polls() -> None:
    hit1 = {"_timestamp": 1000, "message": "first", "level": "info"}
    hit2 = {"_timestamp": 2000, "message": "second", "level": "info"}

    poll_responses = iter(
        [
            {"hits": [hit1]},
            {"hits": [hit1, hit2]},
            {"hits": []},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(poll_responses))

    selector = Selector(streams=["s"], filters=[], match=[], sql=None)
    req = TailRequest(selector=selector, interval_s=0.0)
    client = _make_client(handler)

    emitted = []
    with patch("time.sleep"), patch("time.time", return_value=10.0):
        gen = client.tail(req)
        emitted.append(next(gen))
        emitted.append(next(gen))

    assert len(emitted) == 2
    assert emitted[0].message == "first"
    assert emitted[1].message == "second"


def test_tail_retries_on_connection_error() -> None:
    hit1 = {"_timestamp": 3000, "message": "after retry", "level": "info"}

    call_count = {"n": 0}

    poll_responses: list = [
        httpx.ConnectError("boom"),
        httpx.ConnectError("boom"),
        {"hits": [hit1]},
        {"hits": []},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        resp = poll_responses[call_count["n"]]
        call_count["n"] += 1
        if isinstance(resp, Exception):
            raise resp
        return httpx.Response(200, json=resp)

    selector = Selector(streams=["s"], filters=[], match=[], sql=None)
    req = TailRequest(selector=selector, interval_s=0.0)
    client = _make_client(handler)

    with patch("time.sleep"), patch("time.time", return_value=10.0):
        gen = client.tail(req)
        record = next(gen)

    assert record.message == "after retry"


def test_tail_raises_on_client_error() -> None:
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(400, json={"error": "bad sql"})

    selector = Selector(streams=["s"], filters=[], match=[], sql=None)
    req = TailRequest(selector=selector, interval_s=0.0)
    client = _make_client(handler)

    with patch("time.sleep"), patch("time.time", return_value=10.0):
        with pytest.raises(httpx.HTTPStatusError):
            next(client.tail(req))

    assert call_count["n"] == 1


def test_tail_merges_streams_in_timestamp_order() -> None:
    a_polls = iter(
        [
            {"hits": [{"_timestamp": 1000, "message": "a1", "level": "info"}]},
            {"hits": []},
        ]
    )
    b_polls = iter(
        [
            {"hits": [{"_timestamp": 1500, "message": "b1", "level": "info"}]},
            {"hits": []},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        sql = json.loads(request.content)["query"]["sql"]
        if "FROM a " in sql:
            return httpx.Response(200, json=next(a_polls))
        return httpx.Response(200, json=next(b_polls))

    selector = Selector(streams=["a", "b"], filters=[], match=[], sql=None)
    req = TailRequest(selector=selector, interval_s=0.0)
    client = _make_client(handler)

    emitted = []
    with patch("time.sleep"), patch("time.time", return_value=10.0):
        gen = client.tail(req)
        emitted.append(next(gen))
        emitted.append(next(gen))

    assert [r.message for r in emitted] == ["a1", "b1"]
    assert [r.stream for r in emitted] == ["a", "b"]


def test_search_merges_streams_sorted_desc_and_tags_source() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sql = json.loads(request.content)["query"]["sql"]
        if "FROM frontend" in sql:
            return httpx.Response(
                200,
                json={"hits": [{"_timestamp": 2000, "message": "f", "level": "info"}]},
            )
        if "FROM backend" in sql:
            return httpx.Response(
                200,
                json={"hits": [{"_timestamp": 1000, "message": "b", "level": "info"}]},
            )
        return httpx.Response(200, json={"hits": []})

    selector = Selector(streams=["frontend", "backend"], filters=[], match=[], sql=None)
    query = LogQuery(selector=selector, start_time_us=0, end_time_us=9999, limit=100)
    client = _make_client(handler)

    records = client.search(query)

    assert [r.message for r in records] == ["f", "b"]
    assert [r.stream for r in records] == ["frontend", "backend"]


def test_search_truncates_merged_results_to_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sql = json.loads(request.content)["query"]["sql"]
        if "FROM a" in sql:
            return httpx.Response(
                200,
                json={"hits": [{"_timestamp": 3000, "message": "a", "level": "info"}]},
            )
        return httpx.Response(
            200,
            json={"hits": [{"_timestamp": 1000, "message": "b", "level": "info"}]},
        )

    selector = Selector(streams=["a", "b"], filters=[], match=[], sql=None)
    query = LogQuery(selector=selector, start_time_us=0, end_time_us=9999, limit=1)
    client = _make_client(handler)

    records = client.search(query)

    assert [r.message for r in records] == ["a"]


def test_search_fails_fast_on_bad_stream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sql = json.loads(request.content)["query"]["sql"]
        if "FROM good" in sql:
            return httpx.Response(
                200,
                json={"hits": [{"_timestamp": 1, "message": "ok", "level": "info"}]},
            )
        return httpx.Response(404, json={"error": "stream not found"})

    selector = Selector(streams=["good", "missing"], filters=[], match=[], sql=None)
    query = LogQuery(selector=selector, start_time_us=0, end_time_us=9999, limit=100)
    client = _make_client(handler)

    with pytest.raises(httpx.HTTPStatusError):
        client.search(query)


def test_cli_rejects_sql_with_multiple_streams(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["oo", "show", "a", "b", "--sql", "SELECT 1"])
    with pytest.raises(SystemExit):
        cli.main()


def _clear_settings_env(monkeypatch) -> None:
    for suffix in ("URL", "ORG", "USER", "PASSWORD"):
        monkeypatch.delenv(f"OPENOBSERVE_{suffix}", raising=False)


def _stub_client(
    monkeypatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    monkeypatch.setattr(cli, "_make_client", lambda args: _make_client(handler))


def test_missing_config_reports_one_line_naming_the_unset_variables(
    monkeypatch,
) -> None:
    _clear_settings_env(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["oo", "streams"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    message = str(exc.value)
    assert "\n" not in message
    assert "OPENOBSERVE_URL" in message
    assert "OPENOBSERVE_PASSWORD" in message


def test_missing_config_omits_variables_that_are_set(monkeypatch) -> None:
    _clear_settings_env(monkeypatch)
    monkeypatch.setenv("OPENOBSERVE_URL", "http://localhost:5080")
    monkeypatch.setenv("OPENOBSERVE_ORG", "default")
    monkeypatch.setattr(sys, "argv", ["oo", "streams"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    message = str(exc.value)
    assert "OPENOBSERVE_USER" in message
    assert "OPENOBSERVE_PASSWORD" in message
    assert "OPENOBSERVE_URL" not in message


def test_unreachable_host_reports_the_url(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _stub_client(monkeypatch, handler)
    monkeypatch.setattr(sys, "argv", ["oo", "streams"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    message = str(exc.value)
    assert "\n" not in message
    assert "http://test" in message


def test_rejected_credentials_reported_separately_from_bad_query(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    _stub_client(monkeypatch, handler)
    monkeypatch.setattr(sys, "argv", ["oo", "streams"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    message = str(exc.value)
    assert "OPENOBSERVE_USER" in message
    assert "401" not in message


def test_query_rejected_by_server_surfaces_the_server_response(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "sql parser error near GROUP"})

    _stub_client(monkeypatch, handler)
    monkeypatch.setattr(
        sys, "argv", ["oo", "show", "api", "--sql", "SELECT bogus FROM api"]
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert "sql parser error near GROUP" in str(exc.value)


def test_unparseable_since_reports_the_offending_value(monkeypatch) -> None:
    _stub_client(monkeypatch, lambda request: httpx.Response(200, json={"hits": []}))
    monkeypatch.setattr(sys, "argv", ["oo", "show", "api", "--since", "yesterday"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    message = str(exc.value)
    assert "\n" not in message
    assert "yesterday" in message


def test_interrupting_a_live_tail_exits_without_a_traceback(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    _stub_client(monkeypatch, handler)
    monkeypatch.setattr(sys, "argv", ["oo", "stream", "api"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 130


def test_search_does_not_resort_raw_sql_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hits": [
                    {"_timestamp": 1000, "message": "older", "level": "info"},
                    {"_timestamp": 3000, "message": "newer", "level": "info"},
                ]
            },
        )

    selector = Selector(
        streams=["api"],
        filters=[],
        match=[],
        sql="SELECT * FROM api ORDER BY _timestamp ASC",
    )
    query = LogQuery(selector=selector, start_time_us=0, end_time_us=9999, limit=100)
    client = _make_client(handler)

    records = client.search(query)

    assert [r.timestamp_us for r in records] == [1000, 3000]


def test_show_prints_raw_sql_rows_in_server_order(monkeypatch, capsys) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hits": [
                    {"level": "warning", "c": 636158},
                    {"level": "info", "c": 20373},
                    {"level": "error", "c": 81},
                ]
            },
        )

    monkeypatch.setattr(cli, "_make_client", lambda args: _make_client(handler))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "oo",
            "show",
            "api",
            "--json",
            "--sql",
            "SELECT level, count(*) c FROM api GROUP BY level ORDER BY c DESC",
        ],
    )

    cli.main()

    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["c"] for line in lines] == [636158, 20373, 81]


def test_streams_parses_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "list": [
                    {"name": "api", "stats": {"doc_num": 12345}},
                    {"name": "worker", "stats": {}},
                ]
            },
        )

    client = _make_client(handler)
    streams = client.list_streams()

    assert len(streams) == 2
    assert streams[0] == StreamInfo(name="api", doc_num=12345)
    assert streams[1] == StreamInfo(name="worker", doc_num=None)
