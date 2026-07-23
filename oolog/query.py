from oolog.schemas import Selector


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_where(selector: Selector) -> str:
    terms: list[str] = []
    for f in selector.filters:
        if f.op == "eq":
            terms.append(f"{f.field} = {_sql_quote(f.value)}")
        elif f.op == "ne":
            terms.append(f"{f.field} != {_sql_quote(f.value)}")
        elif f.op == "contains":
            terms.append(f"{f.field} LIKE {_sql_quote('%' + f.value + '%')}")
    for m in selector.match:
        terms.append(f"match_all({_sql_quote(m)})")
    return " AND ".join(terms)


def build_sql(selector: Selector, stream: str, order: str) -> str:
    if selector.sql is not None:
        return selector.sql
    where = build_where(selector)
    clause = f" WHERE {where}" if where else ""
    return f"SELECT * FROM {stream}{clause} ORDER BY _timestamp {order}"
