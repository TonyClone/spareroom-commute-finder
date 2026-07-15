"""Inspect latest flatfinder run — personal diagnostics."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select

from flatfinder.config import ROOT, load_config
from flatfinder.db import Database, JourneyRow, ListingRow, RunRow, ScoreRow, SeenRow


def main() -> None:
    cfg = load_config()
    db_path = cfg.resolved_db_path()
    print(f"DB: {db_path} exists={db_path.exists()}")
    db = Database(db_path)

    with db.session() as s:
        runs = s.scalars(select(RunRow).order_by(RunRow.id.desc()).limit(5)).all()
        print("\n=== RUNS (latest first) ===")
        if not runs:
            print("  (no runs found — app may have failed before pipeline started)")
        for r in runs:
            print(
                f"  run#{r.id} started={r.started_at} finished={r.finished_at} "
                f"listings={r.listing_count} pass={r.pass_count}"
            )
            try:
                params = json.loads(r.params_json or "{}")
                office = (params.get("office") or {}).get("postcode")
                mins = (params.get("commute") or {}).get("max_minutes")
                budget = (params.get("budget") or {}).get("max_pcm")
                print(f"    office={office} max_min={mins} budget={budget}")
            except Exception as e:
                print(f"    params err: {e}")

        seen_n = s.scalar(select(func.count()).select_from(SeenRow)) or 0
        list_n = s.scalar(select(func.count()).select_from(ListingRow)) or 0
        jour_n = s.scalar(select(func.count()).select_from(JourneyRow)) or 0
        score_n = s.scalar(select(func.count()).select_from(ScoreRow)) or 0
        print(f"\ncounts: seen={seen_n} listings={list_n} journeys={jour_n} scores={score_n}")

        if not runs:
            return

        rid = runs[0].id
        rows = s.execute(
            select(ScoreRow.fail_reason, func.count())
            .where(ScoreRow.run_id == rid)
            .group_by(ScoreRow.fail_reason)
        ).all()
        print(f"\n=== FAIL REASONS run#{rid} ===")
        for reason, cnt in sorted(rows, key=lambda x: -x[1]):
            print(f"  {reason}: {cnt}")

        passed = s.execute(
            select(ScoreRow, ListingRow)
            .join(ListingRow, ListingRow.id == ScoreRow.listing_id)
            .where(ScoreRow.run_id == rid, ScoreRow.filter_pass.is_(True))
            .order_by(ScoreRow.transit_minutes.asc().nullslast())
            .limit(25)
        ).all()
        print(f"\n=== PASSED ({len(passed)} shown) run#{rid} ===")
        for sc, li in passed:
            print(
                f"  {sc.transit_minutes}m xfer={sc.transfers} £{li.price_pcm} "
                f"{li.postcode} | {(li.title or '')[:48]} | ai_score={sc.ai_score} keep={sc.ai_keep}"
            )

        print("\n=== SAMPLE REJECTS ===")
        for reason in (
            "TOO_FAR",
            "OVER_COMMUTE",
            "OVER_BUDGET",
            "AI_REJECTED",
            "NO_LOCATION",
            "NO_JOURNEY",
            "UNREACHABLE",
        ):
            sample = s.execute(
                select(ScoreRow, ListingRow)
                .join(ListingRow, ListingRow.id == ScoreRow.listing_id)
                .where(ScoreRow.run_id == rid, ScoreRow.fail_reason == reason)
                .limit(3)
            ).all()
            if not sample:
                continue
            print(f"  [{reason}]")
            for sc, li in sample:
                print(
                    f"    {li.postcode} £{li.price_pcm} {(li.title or '')[:40]} "
                    f"min={sc.transit_minutes} ai={sc.ai_summary[:40] if sc.ai_summary else ''}"
                )

        print("\n=== SEEN BY SOURCE ===")
        for src, cnt in s.execute(select(SeenRow.source, func.count()).group_by(SeenRow.source)):
            print(f"  {src}: {cnt}")
        opened = (
            s.scalar(select(func.count()).select_from(SeenRow).where(SeenRow.opened.is_(True)))
            or 0
        )
        print(f"  opened=True: {opened}")

    budget_path = ROOT / "data" / "ai_budget.json"
    print("\n=== AI BUDGET ===")
    if budget_path.exists():
        print(budget_path.read_text(encoding="utf-8"))
    else:
        print("(no data/ai_budget.json — AI may not have run or budget not recorded)")


if __name__ == "__main__":
    main()
