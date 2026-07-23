from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

FilterOp = Literal["eq", "ne", "contains"]


class Filter(BaseModel):
    field: str
    op: FilterOp
    value: str


class Selector(BaseModel):
    streams: list[str]
    filters: list[Filter]
    match: list[str]
    sql: str | None


class LogQuery(BaseModel):
    selector: Selector
    start_time_us: int
    end_time_us: int
    limit: int


class TailRequest(BaseModel):
    selector: Selector
    interval_s: float


class LogRecord(BaseModel):
    timestamp_us: int
    timestamp: str | None
    level: str | None
    message: str | None
    fields: dict[str, Any]
    stream: str | None = None


class StreamInfo(BaseModel):
    name: str
    doc_num: int | None
