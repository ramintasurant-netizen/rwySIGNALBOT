"""Model tabel. Harga ``Numeric(18,4)`` ↔ ``Decimal``; waktu UTC tz-aware; payload bukti JSON.

``subscribers`` merepresentasikan TUJUAN grup/channel yang diotorisasi, bukan daftar anggota.
Snapshot ``job_runs.snapshot_json`` bersifat immutable setelah job selesai.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

PRICE = Numeric(18, 4)
MONEY = Numeric(24, 2)


class UTCDateTime(TypeDecorator[datetime]):
    """Simpan UTC; kembalikan tz-aware UTC juga pada SQLite (yang membuang tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime harus tz-aware")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (
        UniqueConstraint("job_type", "trading_date", "origin", name="uq_job_runs_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(16))  # morning | afternoon
    trading_date: Mapped[date] = mapped_column(Date)
    data_session_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    origin: Mapped[str] = mapped_column(String(16))  # live | dry_run | fixture | backtest
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")  # schedule | manual
    status: Mapped[str] = mapped_column(String(16), default="claimed")
    # claimed | running | completed | blocked | failed
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    claimed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    engine_version: Mapped[str] = mapped_column(String(32))
    config_hash: Mapped[str] = mapped_column(String(32))
    snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    blockers: Mapped[list[Any]] = mapped_column(JSON, default=list)

    deliveries: Mapped[list[MessageDelivery]] = relationship(back_populates="job_run")


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_origin_status", "origin", "status"),
        Index("ix_signals_symbol_status", "symbol", "status"),
        UniqueConstraint("job_run_id", "symbol", name="uq_signals_job_symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_run_id: Mapped[int] = mapped_column(ForeignKey("job_runs.id"))
    origin: Mapped[str] = mapped_column(String(16))
    app_env: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(12))
    strategy: Mapped[str] = mapped_column(String(32))
    strategy_version: Mapped[str] = mapped_column(String(16))
    engine_version: Mapped[str] = mapped_column(String(32))
    config_hash: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[int] = mapped_column(Integer)
    entry_low: Mapped[Decimal] = mapped_column(PRICE)
    entry_high: Mapped[Decimal] = mapped_column(PRICE)
    stop_loss: Mapped[Decimal] = mapped_column(PRICE)
    tp1: Mapped[Decimal] = mapped_column(PRICE)
    tp2: Mapped[Decimal] = mapped_column(PRICE)
    tp3: Mapped[Decimal] = mapped_column(PRICE)
    rr_tp1_gross: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    rr_tp1_net: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    ara: Mapped[Decimal] = mapped_column(PRICE)
    arb: Mapped[Decimal] = mapped_column(PRICE)
    atr: Mapped[Decimal] = mapped_column(PRICE)
    sizing_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reasons_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    score_breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(String(32))
    bar_time: Mapped[datetime] = mapped_column(UTCDateTime())
    session_date: Mapped[date] = mapped_column(Date)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime())
    quality: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending_entry")
    published_session: Mapped[date] = mapped_column(Date)
    filled_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    filled_session: Mapped[date | None] = mapped_column(Date, nullable=True)
    closed_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    closed_session: Mapped[date | None] = mapped_column(Date, nullable=True)
    pnl_r: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    sessions_since_publish: Mapped[int] = mapped_column(Integer, default=0)
    sessions_held: Mapped[int] = mapped_column(Integer, default=0)
    last_evaluated_session: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())

    updates: Mapped[list[SignalUpdate]] = relationship(
        back_populates="signal", order_by="SignalUpdate.id"
    )


class SignalUpdate(Base):
    __tablename__ = "signal_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    previous_status: Mapped[str] = mapped_column(String(16))
    new_status: Mapped[str] = mapped_column(String(16))
    trigger_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    trigger_session: Mapped[date] = mapped_column(Date)
    data_time: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    pnl_r: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())

    signal: Mapped[Signal] = relationship(back_populates="updates")


class Subscriber(Base):
    """Tujuan grup/channel yang diotorisasi (BUKAN anggota)."""

    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    thread_id: Mapped[int] = mapped_column(Integer, default=0)  # 0 = tanpa topik
    kind: Mapped[str] = mapped_column(String(8))  # signal | admin
    chat_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    verification_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class WatchlistEntry(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(12), unique=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime())
    removed_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class MessageDelivery(Base):
    __tablename__ = "message_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "job_run_id", "chat_id", "thread_id", "part_index", name="uq_delivery_part"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_run_id: Mapped[int] = mapped_column(ForeignKey("job_runs.id"), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int] = mapped_column(Integer, default=0)
    part_index: Mapped[int] = mapped_column(Integer)
    part_count: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(16), default="telegram")
    status: Mapped[str] = mapped_column(String(8), default="pending")
    # pending | sending | sent | failed | unknown
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    job_run: Mapped[JobRun] = relationship(back_populates="deliveries")


class ProviderHealthRecord(Base):
    __tablename__ = "provider_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    healthy: Mapped[bool] = mapped_column(Boolean)
    breaker_state: Mapped[str] = mapped_column(String(16))
    consecutive_failures: Mapped[int] = mapped_column(Integer)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime())


class AppState(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
