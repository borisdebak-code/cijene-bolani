"""Shema baze.

Povijest cijena čuva se kao *intervali*: jedan red u `price` vrijedi od
`valid_from` do `last_seen`. Ako je današnja cijena ista kao jučerašnja,
samo pomaknemo `last_seen`; ako je drukčija, dodajemo novi red. Stari se
zapisi nikad ne prepisuju, a baza je 20-50x manja nego s jednim redom
po danu × poslovnici × proizvodu (Konzum sam ima ~10M takvih dnevno).
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


Money = Numeric(10, 2)


class Chain(Base):
    __tablename__ = "chain"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    #: zadnji dan za koji imamo cjenik – "trenutne" cijene su one viđene tog dana
    last_day: Mapped[date | None] = mapped_column(Date)


class Store(Base):
    __tablename__ = "store"
    __table_args__ = (UniqueConstraint("chain_id", "code"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chain.id"))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(200))
    postcode: Mapped[str | None] = mapped_column(String(10))
    city: Mapped[str | None] = mapped_column(String(100), index=True)


class Product(Base):
    """Kanonski proizvod – jedan zapis za isti artikl kod svih trgovaca."""
    __tablename__ = "product"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ean: Mapped[str | None] = mapped_column(String(14), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    brand: Mapped[str | None] = mapped_column(String(150))
    quantity: Mapped[float | None] = mapped_column(Numeric(12, 3))
    unit: Mapped[str | None] = mapped_column(String(8))
    category: Mapped[str | None] = mapped_column(String(200))
    search_text: Mapped[str] = mapped_column(Text)
    match_name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_product_search_trgm", "search_text", postgresql_using="gin",
              postgresql_ops={"search_text": "gin_trgm_ops"}),
        Index("ix_product_match_trgm", "match_name", postgresql_using="gist",
              postgresql_ops={"match_name": "gist_trgm_ops"}),
        Index("ix_product_qty", "unit", "quantity"),
    )


class ChainProduct(Base):
    """Proizvod kako ga vodi pojedini trgovac (njegova šifra artikla)."""
    __tablename__ = "chain_product"
    __table_args__ = (UniqueConstraint("chain_id", "code"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chain.id"))
    code: Mapped[str] = mapped_column(String(64))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    brand: Mapped[str | None] = mapped_column(String(150))
    quantity_raw: Mapped[str | None] = mapped_column(String(50))
    unit_raw: Mapped[str | None] = mapped_column(String(20))
    quantity: Mapped[float | None] = mapped_column(Numeric(12, 3))
    unit: Mapped[str | None] = mapped_column(String(8))
    ean: Mapped[str | None] = mapped_column(String(14), index=True)
    barcode_raw: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(200))
    match_method: Mapped[str | None] = mapped_column(String(16))  # ean | fuzzy | new


class Price(Base):
    __tablename__ = "price"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("store.id"))
    chain_product_id: Mapped[int] = mapped_column(ForeignKey("chain_product.id"))
    valid_from: Mapped[date] = mapped_column(Date)
    last_seen: Mapped[date] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    regular_price: Mapped[float] = mapped_column(Money)
    unit_price: Mapped[float | None] = mapped_column(Money)
    special_price: Mapped[float | None] = mapped_column(Money)
    lowest_30d: Mapped[float | None] = mapped_column(Money)
    anchor_price: Mapped[float | None] = mapped_column(Money)
    sale_type: Mapped[str | None] = mapped_column(String(60))
    valid_until: Mapped[date | None] = mapped_column(Date)
    card_price: Mapped[float | None] = mapped_column(Money)
    card_name: Mapped[str | None] = mapped_column(String(40))

    __table_args__ = (
        Index("ux_price_current", "store_id", "chain_product_id", unique=True,
              postgresql_where=text("is_current")),
        Index("ix_price_cp_dates", "chain_product_id", "valid_from", "last_seen"),
    )


class ChainDaily(Base):
    """Dnevni sažetak cijene proizvoda po trgovcu (puni se nakon svakog preuzimanja)."""
    __tablename__ = "chain_daily"
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chain.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    price_median: Mapped[float] = mapped_column(Money)
    price_min: Mapped[float] = mapped_column(Money)
    price_max: Mapped[float] = mapped_column(Money)
    regular_median: Mapped[float] = mapped_column(Money)
    stores: Mapped[int] = mapped_column(Integer)
    deal_stores: Mapped[int] = mapped_column(Integer)
    deal_label: Mapped[str | None] = mapped_column(String(60))
    valid_until: Mapped[date | None] = mapped_column(Date)
    card_price: Mapped[float | None] = mapped_column(Money)
    card_name: Mapped[str | None] = mapped_column(String(40))

    __table_args__ = (Index("ix_chain_daily_day", "day", "chain_id"),)


class IngestRun(Base):
    """Jedan pokušaj preuzimanja za jednog trgovca i jedan dan."""
    __tablename__ = "ingest_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_code: Mapped[str] = mapped_column(String(32), index=True)
    adapter: Mapped[str] = mapped_column(String(32))
    day: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default="running")  # ok|partial|failed|running
    files_total: Mapped[int] = mapped_column(Integer, server_default="0")
    files_failed: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_total: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_skipped: Mapped[int] = mapped_column(Integer, server_default="0")
    stores: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)


class IngestError(Base):
    __tablename__ = "ingest_error"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("ingest_run.id", ondelete="CASCADE"), index=True)
    file: Mapped[str | None] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(32))   # format_changed | download | row | other
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
