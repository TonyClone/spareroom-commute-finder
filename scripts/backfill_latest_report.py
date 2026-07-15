"""One-shot: build latest.json from the most recent DB run (for runs before logging)."""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select

from flatfinder.config import load_config
from flatfinder.db import Database, ListingRow, ScoreRow, RunRow
from flatfinder.models import FailReason, JourneyResult, Listing, ScoredListing
from flatfinder.runlog import write_run_report


def main() -> None:
    cfg = load_config()
    db = Database(cfg.resolved_db_path())
    with db.session() as s:
        run = s.scalars(select(RunRow).order_by(RunRow.id.desc()).limit(1)).first()
        if not run:
            print("No runs")
            return
        rows = s.execute(
            select(ScoreRow, ListingRow)
            .join(ListingRow, ListingRow.id == ScoreRow.listing_id)
            .where(ScoreRow.run_id == run.id)
        ).all()
        scored: list[ScoredListing] = []
        opened_ids: list[str] = []
        for sc, li in rows:
            listing = Listing(
                id=li.id,
                url=li.url,
                title=li.title,
                price_pcm=li.price_pcm,
                price_pw=li.price_pw,
                postcode=li.postcode,
                lat=li.lat,
                lon=li.lon,
                area=li.area,
                room_type=li.room_type,
            )
            journey = None
            if sc.transit_minutes is not None:
                journey = JourneyResult(
                    duration_minutes=sc.transit_minutes,
                    transfers=sc.transfers,
                    summary=sc.journey_summary or "",
                    status="OK",
                )
            try:
                reason = FailReason(sc.fail_reason)
            except ValueError:
                reason = FailReason.OTHER
            item = ScoredListing(
                listing=listing,
                journey=journey,
                filter_pass=sc.filter_pass,
                fail_reason=reason,
            )
            scored.append(item)
            if sc.filter_pass:
                opened_ids.append(li.id)

        path = write_run_report(
            run_id=run.id,
            command="daily",
            config_snapshot={
                "office": cfg.office.model_dump(),
                "budget": cfg.budget.model_dump(),
                "commute": cfg.commute.model_dump(),
            },
            scored=scored,
            opened_ids=opened_ids[:15],
            ai_spend_note="(backfilled; see data/ai_budget.json)",
            extra={"backfilled": True, "fail_reasons": dict(Counter(x.fail_reason.value for x in scored))},
        )
        print("Wrote", path)
        print("pass", sum(1 for x in scored if x.filter_pass), "total", len(scored))


if __name__ == "__main__":
    main()
