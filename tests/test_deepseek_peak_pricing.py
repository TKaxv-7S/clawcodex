"""DeepSeek V4's peak/off-peak rate schedule (issue #904).

Since 2026-08-16 DeepSeek publishes a peak and an off-peak card: every rate
doubles during 01:00-04:00 and 06:00-10:00 UTC, Monday through Friday. That
is the pricing table's third tier axis, and the only one that is not a
property of the request's content — ``input_tokens`` is prompt size and
``service_tier`` is what the provider declared in its response, so neither
could carry it.

Two things are worth pinning here and are pinned separately:

* the SCHEDULE — which instants are peak — because an off-by-one on a window
  boundary or a missed weekend rule mis-prices a whole class of requests
  silently; and
* the CARD — the absolute published rates — because the values this issue
  replaced were internally consistent (correct ratios, mirrored
  cache_creation) and still 3x low. Only an external number catches that,
  which is the same lesson the gpt-5.6-luna row in ``services/pricing.py``
  records.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.services.pricing import (
    compute_cost,
    get_pricing,
    is_deepseek_peak,
)


MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0,
         second: int = 0) -> float:
    return datetime(
        year, month, day, hour, minute, second, tzinfo=timezone.utc
    ).timestamp()


# 2026-08-24 is a Monday, 2026-08-28 a Friday, 2026-08-29 a Saturday and
# 2026-08-30 a Sunday.
MON, FRI, SAT, SUN = 24, 28, 29, 30


# --------------------------------------------------------------------------- #
# The schedule
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("hour", [1, 2, 3, 6, 7, 8, 9])
def test_weekday_peak_hours(hour: int) -> None:
    assert is_deepseek_peak(_utc(2026, 8, MON, hour)) is True


@pytest.mark.parametrize("hour", [0, 4, 5, 10, 11, 17, 23])
def test_weekday_off_peak_hours(hour: int) -> None:
    """Including 04:00 and 10:00 — the windows are half-open, so the hour a
    window ends on is already off-peak."""
    assert is_deepseek_peak(_utc(2026, 8, MON, hour)) is False


def test_window_boundaries_are_half_open() -> None:
    assert is_deepseek_peak(_utc(2026, 8, MON, 0, 59, 59)) is False
    assert is_deepseek_peak(_utc(2026, 8, MON, 1, 0, 0)) is True
    assert is_deepseek_peak(_utc(2026, 8, MON, 3, 59, 59)) is True
    assert is_deepseek_peak(_utc(2026, 8, MON, 4, 0, 0)) is False
    assert is_deepseek_peak(_utc(2026, 8, MON, 5, 59, 59)) is False
    assert is_deepseek_peak(_utc(2026, 8, MON, 6, 0, 0)) is True
    assert is_deepseek_peak(_utc(2026, 8, MON, 9, 59, 59)) is True
    assert is_deepseek_peak(_utc(2026, 8, MON, 10, 0, 0)) is False


@pytest.mark.parametrize("day", [SAT, SUN])
@pytest.mark.parametrize("hour", [1, 2, 3, 6, 7, 9])
def test_weekends_are_off_peak_even_inside_the_windows(day: int, hour: int) -> None:
    assert is_deepseek_peak(_utc(2026, 8, day, hour)) is False


def test_monday_and_friday_are_both_weekdays() -> None:
    """Fencepost on the Mon-Fri rule: a `weekday() >= 5` weekend test and a
    `1 <= weekday() <= 5` one differ only on these two days."""
    assert is_deepseek_peak(_utc(2026, 8, MON, 2)) is True
    assert is_deepseek_peak(_utc(2026, 8, FRI, 2)) is True


def test_schedule_is_evaluated_in_utc_not_local_time() -> None:
    """Friday 23:00 US/Pacific is Saturday 06:00 UTC — inside a peak window by
    the local calendar, off-peak by the vendor's."""
    assert is_deepseek_peak(_utc(2026, 8, SAT, 6)) is False


def test_off_peak_covers_133_of_168_hours() -> None:
    """The vendor's schedule leaves 35 peak hours a week — 7 hours (3 + 4) on
    each of 5 days — so 133 hours are off-peak. A sweep of every hour in a
    week is the cheapest guard against a window silently widening."""
    start = _utc(2026, 8, MON, 0)
    peak_hours = sum(
        is_deepseek_peak(start + h * 3600) for h in range(7 * 24)
    )
    assert peak_hours == 35
    assert 7 * 24 - peak_hours == 133


def test_defaults_to_now() -> None:
    """``request_time=None`` means "price at the current clock" — correct for
    the live path, which prices a response the moment it arrives."""
    import time

    now = time.time()
    assert is_deepseek_peak() is is_deepseek_peak(now)


# --------------------------------------------------------------------------- #
# The card
# --------------------------------------------------------------------------- #

# Published USD per 1M tokens, read 2026-08-25 from
# https://api-docs.deepseek.com/quick_start/pricing/
PUBLISHED = {
    "deepseek-v4-flash": {
        "off_peak": {"input": 0.22, "output": 0.66, "cache_read": 0.007},
        "peak": {"input": 0.44, "output": 1.32, "cache_read": 0.014},
    },
    "deepseek-v4-pro": {
        "off_peak": {"input": 0.66, "output": 1.98, "cache_read": 0.022},
        "peak": {"input": 1.32, "output": 3.96, "cache_read": 0.044},
    },
}

OFF_PEAK_TS = _utc(2026, 8, MON, 12)
PEAK_TS = _utc(2026, 8, MON, 2)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("window", ["off_peak", "peak"])
def test_published_rates(model: str, window: str) -> None:
    ts = OFF_PEAK_TS if window == "off_peak" else PEAK_TS
    pricing = get_pricing(model, request_time=ts)
    assert pricing is not None
    for field, dollars in PUBLISHED[model][window].items():
        assert pricing[field] == dollars / 1_000_000, field
    # No separate cache-WRITE charge on this provider: a miss is just input.
    assert pricing["cache_creation"] == pricing["input"]


@pytest.mark.parametrize("model", MODELS)
def test_peak_is_exactly_double_off_peak(model: str) -> None:
    off = get_pricing(model, request_time=OFF_PEAK_TS)
    peak = get_pricing(model, request_time=PEAK_TS)
    assert off.keys() == peak.keys()
    for field in off:
        assert peak[field] == pytest.approx(2 * off[field], rel=1e-12), field


def test_pro_is_three_times_flash_except_on_cache_read() -> None:
    """The vendor prices pro at exactly 3x flash on input and output — but NOT
    on cache read, where $0.022 against $0.007 is 22/7, not 3.

    Pinned because that is exactly the kind of near-ratio that invites
    deriving one row from the other. The cache-read rate has to be read off
    the page, and at ~96% of agentic input tokens it is the field that moves
    the bill most.
    """
    for ts in (OFF_PEAK_TS, PEAK_TS):
        flash = get_pricing("deepseek-v4-flash", request_time=ts)
        pro = get_pricing("deepseek-v4-pro", request_time=ts)
        for field in ("input", "output", "cache_creation"):
            assert pro[field] == pytest.approx(3 * flash[field], rel=1e-9), field
        assert pro["cache_read"] == pytest.approx(
            flash["cache_read"] * 22 / 7, rel=1e-9
        )
        assert pro["cache_read"] != pytest.approx(
            3 * flash["cache_read"], rel=1e-9
        )


# --------------------------------------------------------------------------- #
# Wiring: the axis reaches compute_cost, and reaches nothing else
# --------------------------------------------------------------------------- #

def _agent_mix(total: int = 1_000_000) -> dict[str, int]:
    """The token mix issue #904 measured on a real agent trace: 95.64% cache
    read, 4.07% cache miss, 0.29% output. ``cache_read`` dominating is why a
    wrong cache-read rate moved the total more than input and output combined.
    """
    return {
        "cache_read_input_tokens": round(total * 0.9564),
        "input_tokens": round(total * 0.0407),
        "output_tokens": round(total * 0.0029),
        "cache_creation_input_tokens": 0,
    }


@pytest.mark.parametrize("model", MODELS)
def test_compute_cost_doubles_inside_a_peak_window(model: str) -> None:
    usage = _agent_mix()
    off = compute_cost(model, usage, request_time=OFF_PEAK_TS)
    peak = compute_cost(model, usage, request_time=PEAK_TS)
    assert off > 0
    assert peak == pytest.approx(2 * off, rel=1e-12)


def test_agent_mix_cost_matches_the_published_card() -> None:
    """The end-to-end number issue #904 reported as 2.3x/4.5x low. Recomputed
    from the published card rather than from the tiers, so a future edit to
    ``services/pricing.py`` alone cannot make this pass."""
    usage = _agent_mix()
    for model in MODELS:
        for window, ts in (("off_peak", OFF_PEAK_TS), ("peak", PEAK_TS)):
            card = PUBLISHED[model][window]
            expected = (
                usage["input_tokens"] * card["input"]
                + usage["output_tokens"] * card["output"]
                + usage["cache_read_input_tokens"] * card["cache_read"]
            ) / 1_000_000
            got = compute_cost(model, usage, request_time=ts)
            assert got == pytest.approx(expected, rel=1e-12), (model, window)


def test_pro_off_peak_agent_mix_is_the_issues_number() -> None:
    """$0.0536 per 1M tokens off-peak, $0.1073 peak — against the $0.0237 the
    stale card returned. Pinned as an absolute so a regression to any card
    that merely has the right shape is visible as a dollar figure."""
    usage = _agent_mix()
    assert compute_cost(
        "deepseek-v4-pro", usage, request_time=OFF_PEAK_TS
    ) == pytest.approx(0.0536, abs=5e-5)
    assert compute_cost(
        "deepseek-v4-pro", usage, request_time=PEAK_TS
    ) == pytest.approx(0.1073, abs=5e-5)


def test_vendor_prefix_stripped_ids_follow_the_schedule() -> None:
    """OpenRouter's ``deepseek/…`` ids resolve to the upstream card via
    get_pricing's prefix strip, so they must carry the timestamp too."""
    for model in MODELS:
        for ts in (OFF_PEAK_TS, PEAK_TS):
            assert get_pricing(f"deepseek/{model}", request_time=ts) == (
                get_pricing(model, request_time=ts)
            )


@pytest.mark.parametrize(
    "model",
    ["claude-opus-5", "claude-sonnet-5", "MiniMax-M3", "kimi-k3",
     "gpt-5.6-luna", "muse-spark-1.1"],
)
def test_no_other_model_is_time_tiered(model: str) -> None:
    """Scope gate: the new axis is DeepSeek-only. Every other row must return
    the same card at every instant of the week."""
    baseline = get_pricing(model, request_time=OFF_PEAK_TS)
    assert baseline is not None
    start = _utc(2026, 8, MON, 0)
    for h in range(7 * 24):
        assert get_pricing(model, request_time=start + h * 3600) == baseline


def test_unknown_models_still_return_none_at_every_hour() -> None:
    start = _utc(2026, 8, MON, 0)
    for h in range(0, 7 * 24, 6):
        assert get_pricing("totally-unknown-model-xyz",
                           request_time=start + h * 3600) is None


def test_cache_savings_price_events_at_their_own_timestamp() -> None:
    """``get_cache_savings`` runs at DISPLAY time, arbitrarily later than the
    requests it sums. An off-peak session's savings must not be restated at
    peak rates because the user happened to open ``/cost`` at 02:00 UTC."""
    from src.services.cost_tracker import CostTracker

    tracker = CostTracker()
    tracker.record_usage("deepseek-v4-pro", {
        "input_tokens": 10_000,
        "output_tokens": 1_000,
        "cache_read_input_tokens": 900_000,
    })
    # Back-date the recorded event into an off-peak window, then read the
    # savings back as if the clock had since moved into a peak one.
    tracker._events[0].timestamp = OFF_PEAK_TS
    saved = tracker.get_cache_savings()
    off = get_pricing("deepseek-v4-pro", request_time=OFF_PEAK_TS)
    expected = 900_000 * (off["input"] - off["cache_read"])
    assert saved == pytest.approx(expected, rel=1e-12)


def test_leap_second_free_arithmetic_across_a_dst_shift() -> None:
    """UTC has no DST, so a fixed 24h offset lands on the same wall hour. This
    guards against anyone reimplementing the window check in local time."""
    base = _utc(2026, 3, 27, 2)  # Friday 02:00 UTC, inside a peak window
    assert is_deepseek_peak(base) is True
    day_later = (
        datetime.fromtimestamp(base, timezone.utc) + timedelta(days=1)
    ).timestamp()
    assert is_deepseek_peak(day_later) is False  # Saturday
