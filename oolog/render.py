import json

from oolog.schemas import LogRecord


def format_line(record: LogRecord, as_json: bool, show_stream: bool) -> str:
    if as_json:
        data = {
            "_timestamp": record.timestamp_us,
            "timestamp": record.timestamp,
            "level": record.level,
            "message": record.message,
            **record.fields,
        }
        if show_stream:
            data["_stream"] = record.stream
        return json.dumps(data, default=str)
    level = (record.level or "").upper()
    message = record.message or ""
    extra = " ".join(f"{k}={v}" for k, v in sorted(record.fields.items()))
    tail = f" {extra}" if extra else ""
    prefix = f"[{record.stream}] " if show_stream else ""
    return f"{prefix}{record.timestamp} {level:7} {message}{tail}"
