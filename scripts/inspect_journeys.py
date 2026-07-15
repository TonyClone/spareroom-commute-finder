from sqlalchemy import select

from flatfinder.config import load_config
from flatfinder.db import Database, JourneyRow, ListingRow


def main() -> None:
    db = Database(load_config().resolved_db_path())
    with db.session() as s:
        print("=== JOURNEYS ===")
        for j in s.scalars(select(JourneyRow)).all():
            err = (j.error or "")[:200]
            print(
                f"status={j.status!r} dur={j.duration_minutes} "
                f"origin_pc={j.origin_postcode} lat={j.origin_lat} lon={j.origin_lon}\n"
                f"  err={err!r}\n  summary={j.summary!r}"
            )
        print("\n=== LISTINGS ===")
        for li in s.scalars(select(ListingRow)).all():
            print(
                f"{li.id} £{li.price_pcm} pc={li.postcode!r} "
                f"lat={li.lat} lon={li.lon} conf={li.geo_confidence}\n"
                f"  {li.title[:60]!r}"
            )


if __name__ == "__main__":
    main()
