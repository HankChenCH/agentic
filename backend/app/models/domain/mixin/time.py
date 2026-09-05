from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlmodel import SQLModel, Field

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

class TimeFieldMixin(SQLModel, table=False):
    # timezone=True：PostgreSQL 下生成 timestamptz；SQLite 方言不保留 tzinfo（存储层限制），
    # 但写入值恒为 UTC，读回的 naive 时间即 UTC 墙钟时间
    created_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"onupdate": _utcnow},
    )