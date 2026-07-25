from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Ensure src on path when launched via streamlit
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from flatfinder.config import load_config, load_env
from flatfinder.db import Database
from flatfinder.pipeline import run_pipeline

st.set_page_config(page_title="Flatfinder — SpareRoom + TfL", layout="wide")

config = load_config()
env = load_env()
db = Database(config.resolved_db_path())

st.title("London flatfinder")
st.caption(
    f"Rooms on SpareRoom within **£{config.budget.max_pcm:.0f}/mo** and "
    f"**≤{config.commute.max_minutes} min** door-to-door PT to "
    f"**{config.office.name}** ({config.office.postcode})"
)

with st.sidebar:
    st.header("Filters")
    # Clamp configured values into each slider's range — Streamlit raises if the
    # default lies outside it (e.g. max_minutes: 90 in config.yaml).
    max_min = st.slider("Max commute (min)", 10, 60, min(60, max(10, config.commute.max_minutes)), 5)
    max_pcm = st.slider("Max rent (£/mo)", 500, 2500, min(2500, max(500, int(config.budget.max_pcm))), 50)
    max_pages = st.slider("Search pages", 1, 30, min(30, max(1, config.search.max_pages)))
    max_listings = st.slider("Max listings", 20, 400, min(400, max(20, config.search.max_listings)), 10)
    show_all = st.checkbox("Show rejected too", value=False)
    st.divider()
    st.markdown(
        f"**Office**  \n{config.office.address}  \n"
        f"`{config.office.lat:.5f}, {config.office.lon:.5f}`  \n"
        f"Arrive by **{config.commute.time}**"
    )
    if not env.tfl_app_key:
        st.caption("Running TfL keyless (no key needed). Add `TFL_APP_KEY` to `.env` for higher rate limits.")
    use_proxy = st.checkbox("Use residential proxy", value=config.scraper.use_proxy)
    run_clicked = st.button("Run search", type="primary")

if run_clicked:
    config.commute.max_minutes = max_min
    config.budget.max_pcm = float(max_pcm)
    config.search.max_pages = max_pages
    config.search.max_listings = max_listings
    config.scraper.use_proxy = use_proxy
    status = st.status("Running pipeline…", expanded=True)
    logs: list[str] = []

    def progress(msg: str) -> None:
        logs.append(msg)
        status.write(msg)

    try:
        run_id, scored = run_pipeline(config=config, env=env, progress=progress)
        status.update(label=f"Run #{run_id} complete", state="complete")
        st.session_state["last_run_id"] = run_id
    except Exception as e:
        status.update(label="Failed", state="error")
        st.exception(e)

run_id = st.session_state.get("last_run_id") or db.latest_run_id()
if run_id is None:
    st.info("No runs yet. Click **Run search** in the sidebar to start (no API key required).")
    st.stop()

st.subheader(f"Results — run #{run_id}")
rows = db.shortlist_for_run(run_id, passed_only=not show_all)

# Apply UI-side filters on top of stored scores
filtered = []
for r in rows:
    if r.get("price_pcm") is not None and r["price_pcm"] > max_pcm:
        if not show_all:
            continue
    if (
        r.get("transit_minutes") is not None
        and r["transit_minutes"] > max_min
        and r.get("filter_pass")
    ):
        # still show if show_all; for passed_only view re-check
        if not show_all:
            continue
    if not show_all and not r.get("filter_pass"):
        continue
    if not show_all:
        if r.get("transit_minutes") is None or r["transit_minutes"] > max_min:
            continue
        if r.get("price_pcm") is not None and r["price_pcm"] > max_pcm:
            continue
    filtered.append(r)

st.metric("Shown", len(filtered))

if not filtered:
    st.warning("No rows match. Re-run with more pages or higher max minutes.")
else:
    df = pd.DataFrame(filtered)
    cols = [
        c
        for c in [
            "transit_minutes",
            "transfers",
            "price_pcm",
            "area",
            "postcode",
            "title",
            "available_from",
            "room_type",
            "living_room",
            "bills_included",
            "journey_summary",
            "url",
            "fail_reason",
            "filter_pass",
        ]
        if c in df.columns
    ]
    st.dataframe(
        df[cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("SpareRoom"),
            "transit_minutes": st.column_config.NumberColumn("Min"),
            "price_pcm": st.column_config.NumberColumn("£/mo", format="£%.0f"),
        },
    )
    st.download_button(
        "Download CSV",
        df[cols].to_csv(index=False).encode("utf-8"),
        file_name=f"shortlist_run_{run_id}.csv",
        mime="text/csv",
    )

    st.subheader("Top picks")
    for r in filtered[:15]:
        mins = r.get("transit_minutes")
        price = r.get("price_pcm")
        # Both can be None (unparsed price, unevaluated journey with show_all).
        mins_txt = f"{mins} min" if mins is not None else "? min"
        price_txt = f"£{price:.0f}/mo" if price is not None else "£?/mo"
        st.markdown(
            f"**{mins_txt}** · {price_txt} · {r.get('area') or ''} `{r.get('postcode') or ''}`  \n"
            f"[{r.get('title')}]({r.get('url')})  \n"
            f"{r.get('journey_summary') or ''}"
        )
