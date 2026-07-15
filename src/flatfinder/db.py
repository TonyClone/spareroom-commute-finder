from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from flatfinder.models import FailReason, JourneyResult, Listing, ScoredListing


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    listing_count: Mapped[int] = mapped_column(Integer, default=0)
    pass_count: Mapped[int] = mapped_column(Integer, default=0)


class ListingRow(Base):
    __tablename__ = "listings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    price_raw: Mapped[str] = mapped_column(String(64), default="")
    price_pcm: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_pw: Mapped[float | None] = mapped_column(Float, nullable=True)
    postcode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    geo_confidence: Mapped[str] = mapped_column(String(32), default="unknown")
    area: Mapped[str] = mapped_column(String(128), default="")
    room_type: Mapped[str] = mapped_column(String(64), default="")
    living_room: Mapped[str] = mapped_column(String(32), default="")
    bills_included: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    available_from: Mapped[str] = mapped_column(String(64), default="")
    nearest_station: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str] = mapped_column(Text, default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")


class JourneyRow(Base):
    __tablename__ = "journeys"

    cache_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    origin_postcode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    origin_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    dest_lat: Mapped[float] = mapped_column(Float)
    dest_lon: Mapped[float] = mapped_column(Float)
    arrive_time: Mapped[str] = mapped_column(String(16), default="")
    journey_date: Mapped[str] = mapped_column(String(16), default="")
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transfers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    walk_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    legs_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="OK")
    error: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ScoreRow(Base):
    __tablename__ = "listing_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    transit_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transfers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank_score: Mapped[float] = mapped_column(Float, default=0.0)
    filter_pass: Mapped[bool] = mapped_column(Boolean, default=False)
    fail_reason: Mapped[str] = mapped_column(String(32), default="OTHER")
    journey_summary: Mapped[str] = mapped_column(Text, default="")
    ai_keep: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ai_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SeenRow(Base):
    """Listings already shown/opened so daily runs stay deduped."""

    __tablename__ = "seen_listings"

    listing_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    times_seen: Mapped[int] = mapped_column(Integer, default=1)
    opened: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(64), default="daily")  # daily | manual | ai_reject


class ScannedRow(Base):
    """Every search-card id the scraper has paginated over.

    Distinct from seen_listings (opened/AI-rejected). This is the incremental
    watermark: newest-first, once a page is entirely already-scanned we've
    reached previous-run territory and can stop paginating. Records skipped
    outer/denylist cards too, so they don't keep the watermark from triggering.
    """

    __tablename__ = "scanned_listings"

    listing_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_scanned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_scanned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    times_scanned: Mapped[int] = mapped_column(Integer, default=1)


def _ensure_column(conn, table: str, column: str, ddl_type: str) -> None:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    existing = {r[1] for r in rows}
    if column not in existing:
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.engine = create_engine(f"sqlite:///{path}", future=True)
        Base.metadata.create_all(self.engine)
        self._migrate()
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def _migrate(self) -> None:
        """Additive SQLite migrations for existing personal DBs."""
        with self.engine.begin() as conn:
            # listing_scores AI columns
            try:
                _ensure_column(conn, "listing_scores", "ai_keep", "BOOLEAN")
                _ensure_column(conn, "listing_scores", "ai_score", "INTEGER")
                _ensure_column(conn, "listing_scores", "ai_summary", "TEXT DEFAULT ''")
                _ensure_column(conn, "listings", "living_room", "VARCHAR(32) DEFAULT ''")
            except Exception:
                pass  # table may not exist yet on brand-new DBs before create_all

    def session(self) -> Session:
        return self.Session()

    def start_run(self, params: dict[str, Any]) -> int:
        with self.session() as s:
            row = RunRow(params_json=json.dumps(params), started_at=datetime.utcnow())
            s.add(row)
            s.commit()
            return row.id

    def finish_run(self, run_id: int, listing_count: int, pass_count: int) -> None:
        with self.session() as s:
            row = s.get(RunRow, run_id)
            if row:
                row.finished_at = datetime.utcnow()
                row.listing_count = listing_count
                row.pass_count = pass_count
                s.commit()

    def upsert_listing(self, listing: Listing) -> None:
        with self.session() as s:
            row = s.get(ListingRow, listing.id)
            data = dict(
                url=listing.url,
                title=listing.title,
                price_raw=listing.price_raw,
                price_pcm=listing.price_pcm,
                price_pw=listing.price_pw,
                postcode=listing.postcode,
                lat=listing.lat,
                lon=listing.lon,
                geo_confidence=listing.geo_confidence,
                area=listing.area,
                room_type=listing.room_type,
                living_room=listing.living_room,
                bills_included=listing.bills_included,
                available_from=listing.available_from,
                nearest_station=listing.nearest_station,
                description=listing.description,
                image_url=listing.image_url,
                scraped_at=listing.scraped_at,
                raw_json=json.dumps(listing.raw),
            )
            if row is None:
                s.add(ListingRow(id=listing.id, **data))
            else:
                for k, v in data.items():
                    setattr(row, k, v)
            s.commit()

    def get_journey(self, cache_key: str) -> JourneyResult | None:
        with self.session() as s:
            row = s.get(JourneyRow, cache_key)
            if not row:
                return None
            return JourneyResult(
                duration_minutes=row.duration_minutes,
                transfers=row.transfers,
                walk_minutes=row.walk_minutes,
                summary=row.summary,
                legs_json=json.loads(row.legs_json or "[]"),
                status=row.status,
                error=row.error,
                cache_key=row.cache_key,
            )

    def save_journey(
        self,
        cache_key: str,
        journey: JourneyResult,
        *,
        origin_postcode: str | None,
        origin_lat: float | None,
        origin_lon: float | None,
        dest_lat: float,
        dest_lon: float,
        arrive_time: str,
        journey_date: str,
    ) -> None:
        with self.session() as s:
            row = s.get(JourneyRow, cache_key)
            data = dict(
                origin_postcode=origin_postcode,
                origin_lat=origin_lat,
                origin_lon=origin_lon,
                dest_lat=dest_lat,
                dest_lon=dest_lon,
                arrive_time=arrive_time,
                journey_date=journey_date,
                duration_minutes=journey.duration_minutes,
                transfers=journey.transfers,
                walk_minutes=journey.walk_minutes,
                summary=journey.summary,
                legs_json=json.dumps(journey.legs_json),
                status=journey.status,
                error=journey.error,
                fetched_at=datetime.utcnow(),
            )
            if row is None:
                s.add(JourneyRow(cache_key=cache_key, **data))
            else:
                for k, v in data.items():
                    setattr(row, k, v)
            s.commit()

    def save_scores(self, run_id: int, scored: Iterable[ScoredListing]) -> None:
        with self.session() as s:
            for item in scored:
                ai_keep = item.ai.keep if item.ai else None
                ai_score = item.ai.score if item.ai else None
                ai_summary = ""
                if item.ai:
                    bits = [item.ai.summary] if item.ai.summary else []
                    if item.ai.red_flags:
                        bits.append("flags: " + "; ".join(item.ai.red_flags[:5]))
                    ai_summary = " | ".join(bits)
                s.add(
                    ScoreRow(
                        listing_id=item.listing.id,
                        run_id=run_id,
                        transit_minutes=(
                            item.journey.duration_minutes if item.journey else None
                        ),
                        transfers=item.journey.transfers if item.journey else None,
                        rank_score=item.rank_score,
                        filter_pass=item.filter_pass,
                        fail_reason=item.fail_reason.value,
                        journey_summary=item.journey.summary if item.journey else "",
                        ai_keep=ai_keep,
                        ai_score=ai_score,
                        ai_summary=ai_summary,
                    )
                )
            s.commit()

    def replace_scores(self, run_id: int, scored: Iterable[ScoredListing]) -> None:
        with self.session() as s:
            old = s.scalars(select(ScoreRow).where(ScoreRow.run_id == run_id)).all()
            for row in old:
                s.delete(row)
            s.commit()
        self.save_scores(run_id, scored)

    def is_seen(self, listing_id: str) -> bool:
        with self.session() as s:
            return s.get(SeenRow, listing_id) is not None

    def filter_unseen_ids(self, listing_ids: Iterable[str]) -> set[str]:
        ids = list(listing_ids)
        if not ids:
            return set()
        with self.session() as s:
            rows = s.scalars(select(SeenRow.listing_id).where(SeenRow.listing_id.in_(ids))).all()
            seen = set(rows)
        return set(ids) - seen

    def mark_seen(
        self,
        listing_ids: Iterable[str],
        *,
        opened: bool = False,
        source: str = "daily",
    ) -> int:
        count = 0
        now = datetime.utcnow()
        with self.session() as s:
            for lid in listing_ids:
                row = s.get(SeenRow, lid)
                if row is None:
                    s.add(
                        SeenRow(
                            listing_id=lid,
                            first_seen_at=now,
                            last_seen_at=now,
                            times_seen=1,
                            opened=opened,
                            source=source,
                        )
                    )
                    count += 1
                else:
                    row.last_seen_at = now
                    row.times_seen = (row.times_seen or 0) + 1
                    if opened:
                        row.opened = True
                    count += 1
            s.commit()
        return count

    def have_scanned_ids(self, listing_ids: Iterable[str]) -> set[str]:
        """Subset of the given ids already recorded in scanned_listings."""
        ids = list(listing_ids)
        if not ids:
            return set()
        with self.session() as s:
            rows = s.scalars(
                select(ScannedRow.listing_id).where(ScannedRow.listing_id.in_(ids))
            ).all()
        return set(rows)

    def mark_scanned(self, listing_ids: Iterable[str]) -> int:
        """Record card ids the scraper walked over (upsert, bump counters)."""
        count = 0
        now = datetime.utcnow()
        with self.session() as s:
            for lid in listing_ids:
                row = s.get(ScannedRow, lid)
                if row is None:
                    s.add(
                        ScannedRow(
                            listing_id=lid,
                            first_scanned_at=now,
                            last_scanned_at=now,
                            times_scanned=1,
                        )
                    )
                else:
                    row.last_scanned_at = now
                    row.times_scanned = (row.times_scanned or 0) + 1
                count += 1
            s.commit()
        return count

    def scanned_count(self) -> int:
        with self.session() as s:
            return len(s.scalars(select(ScannedRow.listing_id)).all())

    def seen_count(self) -> int:
        with self.session() as s:
            return len(s.scalars(select(SeenRow.listing_id)).all())

    def clear_seen(self) -> int:
        with self.session() as s:
            rows = s.scalars(select(SeenRow)).all()
            n = len(rows)
            for r in rows:
                s.delete(r)
            s.commit()
            return n

    def latest_run_id(self) -> int | None:
        with self.session() as s:
            row = s.scalars(select(RunRow).order_by(RunRow.id.desc()).limit(1)).first()
            return row.id if row else None

    def shortlist_for_run(self, run_id: int, passed_only: bool = True) -> list[dict[str, Any]]:
        with self.session() as s:
            q = (
                select(ScoreRow, ListingRow)
                .join(ListingRow, ListingRow.id == ScoreRow.listing_id)
                .where(ScoreRow.run_id == run_id)
            )
            if passed_only:
                q = q.where(ScoreRow.filter_pass.is_(True))
            q = q.order_by(
                ScoreRow.transit_minutes.asc().nullslast(),
                ScoreRow.transfers.asc().nullslast(),
                ListingRow.price_pcm.asc().nullslast(),
            )
            rows = s.execute(q).all()
            out: list[dict[str, Any]] = []
            for score, listing in rows:
                out.append(
                    {
                        "listing_id": listing.id,
                        "title": listing.title,
                        "url": listing.url,
                        "price_pcm": listing.price_pcm,
                        "price_pw": listing.price_pw,
                        "area": listing.area,
                        "postcode": listing.postcode,
                        "room_type": listing.room_type,
                        "living_room": listing.living_room,
                        "bills_included": listing.bills_included,
                        "available_from": listing.available_from,
                        "nearest_station": listing.nearest_station,
                        "transit_minutes": score.transit_minutes,
                        "transfers": score.transfers,
                        "journey_summary": score.journey_summary,
                        "filter_pass": score.filter_pass,
                        "fail_reason": score.fail_reason,
                        "description": listing.description,
                        "ai_keep": score.ai_keep,
                        "ai_score": score.ai_score,
                        "ai_summary": score.ai_summary,
                    }
                )
            return out
