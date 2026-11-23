"""Database schema. Every market datum carries `source` and `fetched_at`."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- users
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    base_currency: Mapped[str] = mapped_column(String(3), default="EUR")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ------------------------------------------------------------------- market data
class PriceBar(Base):
    """Daily OHLCV time series (one row per ticker per session)."""

    __tablename__ = "price_bars"
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    adj_close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(40))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SeriesMeta(Base):
    """Freshness bookkeeping for each cached price series."""

    __tablename__ = "series_meta"
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    source: Mapped[str] = mapped_column(String(120))
    adjusted: Mapped[str] = mapped_column(String(160), default="")
    first_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    error: Mapped[str] = mapped_column(Text, default="")


class CacheEntry(Base):
    """Generic JSON cache (fundamentals, calendars, news, quotes, model artefacts)."""

    __tablename__ = "cache_entries"
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(80), default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


# ---------------------------------------------------------------- watchlists
class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    items: Mapped[list["WatchlistItem"]] = relationship(
        cascade="all, delete-orphan", order_by="WatchlistItem.id"
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(20))
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("watchlist_id", "ticker"),)


# -------------------------------------------------------------------- alerts
class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    repeat: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40))
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


# ----------------------------------------------------------------- portfolio
class Portfolio(Base):
    __tablename__ = "portfolios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    base_currency: Mapped[str] = mapped_column(String(3), default="EUR")
    benchmark: Mapped[str] = mapped_column(String(20), default="SPY")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    transactions: Mapped[list["Transaction"]] = relationship(
        cascade="all, delete-orphan", order_by="Transaction.trade_date"
    )


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(4))  # BUY / SELL
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)  # in instrument currency
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    fee: Mapped[float] = mapped_column(Float, default=0.0)  # in fee_currency
    fee_currency: Mapped[str] = mapped_column(String(3), default="EUR")
    # Units of portfolio base currency per 1 unit of instrument currency, at trade time.
    fx_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    fx_source: Mapped[str] = mapped_column(String(60), default="")
    trade_date: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ------------------------------------------------------------- paper trading
class PaperAccount(Base):
    __tablename__ = "paper_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    initial_cash: Mapped[float] = mapped_column(Float, default=100_000.0)
    cash: Mapped[float] = mapped_column(Float, default=100_000.0)
    commission_per_share: Mapped[float] = mapped_column(Float, default=0.005)
    min_commission: Mapped[float] = mapped_column(Float, default=1.0)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    strategy: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PaperPosition(Base):
    __tablename__ = "paper_positions"
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    quantity: Mapped[float] = mapped_column(Float)
    avg_price: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)


class PaperOrder(Base):
    __tablename__ = "paper_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(12))  # market/limit/stop/stop_limit
    quantity: Mapped[float] = mapped_column(Float)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_in_force: Mapped[str] = mapped_column(String(4), default="GTC")  # DAY/GTC
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    role: Mapped[str] = mapped_column(String(12), default="entry")  # entry / stop_loss / take_profit
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("paper_orders.id"), nullable=True)
    oco_group: Mapped[str | None] = mapped_column(String(40), nullable=True)
    attach_stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    attach_take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("paper_orders.id"), nullable=True)
    ticker: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    reference_price: Mapped[float] = mapped_column(Float)
    commission: Mapped[float] = mapped_column(Float)
    spread_bps: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_bps: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    quote_source: Mapped[str] = mapped_column(String(60), default="")
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PaperEquity(Base):
    """Daily mark-to-market snapshots for performance analytics."""

    __tablename__ = "paper_equity"
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)


# ------------------------------------------------------- predictions & models
class PredictionRecord(Base):
    """Immutable forecast log. Rows are written once and only *evaluated* later."""

    __tablename__ = "prediction_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)  # last bar used
    horizon: Mapped[str] = mapped_column(String(6))
    horizon_days: Mapped[int] = mapped_column(Integer)
    target_date: Mapped[date] = mapped_column(Date, index=True)
    model_version: Mapped[str] = mapped_column(String(40))
    price_at_prediction: Mapped[float] = mapped_column(Float)
    price_source: Mapped[str] = mapped_column(String(40))
    expected_return: Mapped[float] = mapped_column(Float)
    median_return: Mapped[float] = mapped_column(Float)
    p_up: Mapped[float] = mapped_column(Float)
    q05: Mapped[float] = mapped_column(Float)
    q10: Mapped[float] = mapped_column(Float)
    q50: Mapped[float] = mapped_column(Float)
    q90: Mapped[float] = mapped_column(Float)
    q95: Mapped[float] = mapped_column(Float)
    sigma: Mapped[float] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    direction_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    in_80: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    in_90: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    brier: Mapped[float | None] = mapped_column(Float, nullable=True)
    abs_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (
        UniqueConstraint("ticker", "as_of_date", "horizon", "model_version", name="uq_prediction"),
        Index("ix_pred_status_target", "status", "target_date"),
    )


class BacktestRun(Base):
    """Every backtest run is stored and listed - none can be deleted (anti cherry-picking)."""

    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    kind: Mapped[str] = mapped_column(String(30))
    config: Mapped[dict] = mapped_column(JSON)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(12), default="done")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="running")
    message: Mapped[str] = mapped_column(Text, default="")
