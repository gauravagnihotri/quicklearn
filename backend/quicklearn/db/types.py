"""Custom SQLAlchemy column types.

SQLite has no native timezone-aware datetime. Left alone, SQLAlchemy hands back naive
``datetime`` objects, and the first time one is compared against an aware ``datetime.now(UTC)``
Python raises ``TypeError``. FSRS due-date arithmetic does exactly that comparison on every
review, so the coercion below is load-bearing rather than cosmetic.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import DateTime, Dialect
from sqlalchemy.types import TypeDecorator


def utcnow() -> dt.datetime:
    """Timezone-aware current time. The only clock the application should read."""
    return dt.datetime.now(dt.UTC)


class UTCDateTime(TypeDecorator[dt.datetime]):
    """Stores datetimes as UTC and always returns them timezone-aware.

    Naive values going in are assumed to already be UTC rather than silently reinterpreted
    as local time, which would shift stored due dates by the host's offset.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: Dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)

    def process_result_value(
        self, value: dt.datetime | None, dialect: Dialect
    ) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)


JSONDict = dict[str, Any]
JSONList = list[Any]
