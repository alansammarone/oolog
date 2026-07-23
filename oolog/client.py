from __future__ import annotations

import time
from collections.abc import Iterator

import httpx
import structlog

from oolog.query import build_sql
from oolog.schemas import LogQuery, LogRecord, StreamInfo, TailRequest

log = structlog.get_logger()

_RETRY_BASE_S = 1.0
_RETRY_CAP_S = 30.0


def _parse_hit(hit: dict, stream: str) -> LogRecord:
    return LogRecord(
        timestamp_us=hit.get("_timestamp", 0),
        timestamp=hit.get("timestamp"),
        level=hit.get("level"),
        message=hit.get("message"),
        fields={
            k: v
            for k, v in hit.items()
            if k not in ("_timestamp", "timestamp", "level", "message")
        },
        stream=stream,
    )


def _dedup_key(record: LogRecord) -> tuple:
    return (record.timestamp_us, record.message)


class OpenObserveClient:
    def __init__(self, http: httpx.Client, org: str) -> None:
        self._http = http
        self._org = org

    @classmethod
    def connect(cls, url: str, org: str, user: str, password: str) -> OpenObserveClient:
        http = httpx.Client(base_url=url, auth=(user, password), timeout=30.0)
        return cls(http=http, org=org)

    def search(self, query: LogQuery) -> list[LogRecord]:
        records: list[LogRecord] = []
        for stream in query.selector.streams:
            records.extend(self._search_one(query, stream))
        if query.selector.sql is not None:
            return records[: query.limit]
        records.sort(key=lambda r: r.timestamp_us, reverse=True)
        return records[: query.limit]

    def _search_one(self, query: LogQuery, stream: str) -> list[LogRecord]:
        sql = build_sql(query.selector, stream, order="DESC")
        body = {
            "query": {
                "sql": sql,
                "start_time": query.start_time_us,
                "end_time": query.end_time_us,
                "from": 0,
                "size": query.limit,
            }
        }
        response = self._http.post(f"/api/{self._org}/_search?type=logs", json=body)
        response.raise_for_status()
        return [_parse_hit(h, stream) for h in response.json()["hits"]]

    def _poll(
        self, stream: str, sql: str, start_us: int, end_us: int
    ) -> list[LogRecord]:
        body = {
            "query": {
                "sql": sql,
                "start_time": start_us,
                "end_time": end_us,
                "from": 0,
                "size": 1000,
            }
        }
        response = self._http.post(f"/api/{self._org}/_search?type=logs", json=body)
        response.raise_for_status()
        return [_parse_hit(h, stream) for h in response.json()["hits"]]

    def _poll_with_retry(
        self, stream: str, sql: str, start_us: int, end_us: int
    ) -> list[LogRecord]:
        delay = _RETRY_BASE_S
        disconnected = False
        while True:
            try:
                records = self._poll(stream, sql, start_us, end_us)
                if disconnected:
                    log.info("reconnected", stream=stream)
                return records
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise
                if not disconnected:
                    log.warning(
                        "connection lost, retrying", stream=stream, error=str(exc)
                    )
                    disconnected = True
            except httpx.TransportError as exc:
                if not disconnected:
                    log.warning(
                        "connection lost, retrying", stream=stream, error=str(exc)
                    )
                    disconnected = True
            time.sleep(delay)
            delay = min(delay * 2, _RETRY_CAP_S)

    def tail(self, req: TailRequest) -> Iterator[LogRecord]:
        streams = req.selector.streams
        sqls = {s: build_sql(req.selector, s, order="ASC") for s in streams}
        start = int(time.time() * 1_000_000)
        cursors: dict[str, int] = {s: start for s in streams}
        seen: dict[str, set[tuple]] = {s: set() for s in streams}
        while True:
            now = int(time.time() * 1_000_000)
            batch: list[LogRecord] = []
            for stream in streams:
                records = self._poll_with_retry(
                    stream, sqls[stream], cursors[stream], now
                )
                fresh = [r for r in records if _dedup_key(r) not in seen[stream]]
                if fresh:
                    max_ts = max(r.timestamp_us for r in fresh)
                    cursors[stream] = max_ts
                    seen[stream] = {
                        _dedup_key(r) for r in records if r.timestamp_us == max_ts
                    }
                batch.extend(fresh)
            batch.sort(key=lambda r: r.timestamp_us)
            for record in batch:
                yield record
            time.sleep(req.interval_s)

    def list_streams(self) -> list[StreamInfo]:
        response = self._http.get(f"/api/{self._org}/streams?type=logs")
        response.raise_for_status()
        return [
            StreamInfo(
                name=s["name"],
                doc_num=s.get("stats", {}).get("doc_num"),
            )
            for s in response.json().get("list", [])
        ]
