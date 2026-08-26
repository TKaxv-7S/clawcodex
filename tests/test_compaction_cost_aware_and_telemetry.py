"""Tests for PR #903 review fixes: cost-aware compaction trigger (PR 2)
and compaction telemetry (PR 3).

Covers the paths flagged in review:
  * ``_should_auto_compact_cost_aware`` — break-even gating, circuit
    breaker, min-token floor, and no-pricing fallback.
  * ``_estimate_compaction_cost_delta`` — per-token-rate convention
    (regression: was dividing by 1e6 twice) and sign convention
    (positive = compaction increased cost).
  * ``CompactionTelemetryData`` — the three fields /context reads
    (regression: missing fields made the feature fail silently).
  * ``log_post_compaction_telemetry`` — persists post-compaction
    measurements back into bootstrap state so /context can render them.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.bootstrap.state import (
    CompactionTelemetryData,
    get_compaction_telemetry_data,
    reset_state_for_tests,
    set_compaction_telemetry_data,
    update_compaction_telemetry,
)
from src.services.compact.autocompact import (
    MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
    MIN_INPUT_TOKENS_FOR_AUTOCOMPACT,
    AutoCompactTracking,
    _should_auto_compact_cost_aware,
    get_auto_compact_threshold,
)
from src.services.compact.compact import (
    _COMPACT_SUMMARY_OUTPUT_TOKENS_ESTIMATE,
    _estimate_compaction_cost_delta,
    log_post_compaction_telemetry,
)

SONNET = "claude-sonnet-4-6"
UNKNOWN_MODEL = "definitely-not-a-real-model-xyz"


class TestEstimateCompactionCostDelta(unittest.TestCase):
    """Sign convention + per-token rate units for the cost-delta estimator."""

    def test_unknown_model_returns_none(self):
        self.assertIsNone(_estimate_compaction_cost_delta(100_000, 50_000, 50.0, None, UNKNOWN_MODEL))

    def test_returns_none_when_nothing_shed(self):
        self.assertIsNone(_estimate_compaction_cost_delta(50_000, 50_000, 50.0, None, SONNET))
        self.assertIsNone(_estimate_compaction_cost_delta(50_000, 60_000, 50.0, None, SONNET))

    def test_per_token_rates_not_redivided(self):
        # Regression: the first implementation divided by 1e6 although
        # get_pricing() already returns per-token rates, understating the
        # delta by exactly 1e6. Sonnet: input $3/M -> 3e-6/token,
        # output $15/M -> 15e-6/token.
        # shed=50k at 0% hit -> savings = 50_000*3e-6 = $0.15;
        # summary call ~= 20_000*15e-6 = $0.30; delta = 0.30 - 0.15 = 0.15.
        delta = _estimate_compaction_cost_delta(60_000, 10_000, 0.0, None, SONNET)
        self.assertAlmostEqual(delta, 0.15, places=9)

    def test_positive_when_summary_call_dominates(self):
        # Tiny shed: one-time summary cost outweighs per-turn savings.
        delta = _estimate_compaction_cost_delta(11_000, 10_000, 0.0, None, SONNET)
        self.assertGreater(delta, 0.0)

    def test_negative_for_large_uncached_shed(self):
        # Shed 500k fully-uncached tokens: savings ($1.50) dominate the
        # $0.30 summary call -> net saving, delta must be NEGATIVE.
        delta = _estimate_compaction_cost_delta(600_000, 100_000, 0.0, None, SONNET)
        self.assertAlmostEqual(delta, 0.30 - 500_000 * 3e-6, places=9)
        self.assertLess(delta, 0.0)

    def test_cached_share_of_shed_tokens_discounts_savings(self):
        # Same shed size as test_per_token_rates_not_redivided but with an
        # 80% pre-compaction hit rate: only 20% of shed tokens are billed
        # at input rate, 80% at cache-read rate.
        # savings = 10k*3e-6 + 40k*0.3e-6 = 0.042; delta = 0.30 - 0.042.
        delta = _estimate_compaction_cost_delta(60_000, 10_000, 80.0, None, SONNET)
        self.assertAlmostEqual(delta, 0.30 - 0.042, places=9)


class TestShouldAutoCompactCostAware(unittest.TestCase):
    """Break-even gating for the cost-aware trigger."""

    def setUp(self):
        reset_state_for_tests()
        # Neutralize DISABLE_COMPACT / DISABLE_AUTO_COMPACT env leakage.
        enabler = patch(
            "src.services.compact.autocompact.is_auto_compact_enabled",
            return_value=True,
        )
        enabler.start()
        self.addCleanup(enabler.stop)

    def test_disabled_autocompact_returns_false(self):
        with patch(
            "src.services.compact.autocompact.is_auto_compact_enabled",
            return_value=False,
        ):
            self.assertFalse(
                _should_auto_compact_cost_aware(500_000, 1_000_000, SONNET)
            )

    def test_below_min_input_tokens_returns_false(self):
        self.assertFalse(
            _should_auto_compact_cost_aware(
                MIN_INPUT_TOKENS_FOR_AUTOCOMPACT - 1, 1_000_000, SONNET
            )
        )

    def test_circuit_breaker_blocks_trigger(self):
        tracking = AutoCompactTracking(
            consecutive_failures=MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
        )
        self.assertFalse(
            _should_auto_compact_cost_aware(
                500_000, 1_000_000, SONNET, tracking=tracking
            )
        )

    def test_no_pricing_falls_back_to_token_threshold(self):
        below = get_auto_compact_threshold(1_000_000) - 1
        at_or_above = get_auto_compact_threshold(1_000_000)
        self.assertFalse(
            _should_auto_compact_cost_aware(below, 1_000_000, UNKNOWN_MODEL)
        )
        self.assertTrue(
            _should_auto_compact_cost_aware(at_or_above, 1_000_000, UNKNOWN_MODEL)
        )

    def test_zero_hit_rate_triggers_break_even(self):
        with patch(
            "src.services.compact.autocompact._get_recent_cache_hit_rate",
            return_value=0.0,
        ):
            # shed est = 990k*0.3 = 297k uncached tokens -> savings $0.891/turn
            # vs $0.30 summary call -> break-even well under 10 turns.
            self.assertTrue(
                _should_auto_compact_cost_aware(990_000, 1_000_000, SONNET)
            )

    def test_high_hit_rate_can_still_trigger(self):
        with patch(
            "src.services.compact.autocompact._get_recent_cache_hit_rate",
            return_value=99.0,
        ):
            # savings ~$0.097/turn -> ~3.1 turns to break even <= 10.
            self.assertTrue(
                _should_auto_compact_cost_aware(990_000, 1_000_000, SONNET)
            )

    def test_strict_break_even_budget_defers_where_threshold_would_trigger(self):
        # Token-threshold mode WOULD compact here, but a 1-turn break-even
        # budget defers: ~3.1 estimated turns > 1 allowed.
        threshold_input = get_auto_compact_threshold(1_000_000) + 1
        self.assertGreater(threshold_input, MIN_INPUT_TOKENS_FOR_AUTOCOMPACT)
        with patch(
            "src.services.compact.autocompact._get_recent_cache_hit_rate",
            return_value=99.0,
        ):
            self.assertTrue(
                _should_auto_compact_cost_aware(
                    threshold_input, 1_000_000, SONNET
                )
            )
            self.assertFalse(
                _should_auto_compact_cost_aware(
                    threshold_input, 1_000_000, SONNET, break_even_turns=1
                )
            )


class TestCompactionTelemetryDataSurface(unittest.TestCase):
    """Regression: /context reads three fields this dataclass lacked."""

    def test_has_fields_read_by_context_command(self):
        telemetry = CompactionTelemetryData()
        # Mirror of the attribute access in command_system/builtins.py;
        # used to raise AttributeError (silently swallowed upstream).
        values = {
            "cache_hit_rate_after": telemetry.cache_hit_rate_after,
            "estimated_cost_delta_usd": telemetry.estimated_cost_delta_usd,
            "cost_increased": telemetry.cost_increased,
        }
        self.assertIsNone(values["cache_hit_rate_after"])
        self.assertIsNone(values["estimated_cost_delta_usd"])
        self.assertFalse(values["cost_increased"])


class TestUpdateCompactionTelemetry(unittest.TestCase):
    """Write-back helper used by log_post_compaction_telemetry."""

    def setUp(self):
        reset_state_for_tests()

    def tearDown(self):
        reset_state_for_tests()

    def test_noop_when_no_telemetry_stored(self):
        self.assertIsNone(get_compaction_telemetry_data())
        update_compaction_telemetry(cache_hit_rate_after=90.0)  # must not raise

    def test_partial_update_sets_only_given_fields(self):
        set_compaction_telemetry_data(CompactionTelemetryData(trigger="manual"))
        update_compaction_telemetry(cache_hit_rate_after=87.5)
        stored = get_compaction_telemetry_data()
        self.assertAlmostEqual(stored.cache_hit_rate_after, 87.5)
        self.assertIsNone(stored.estimated_cost_delta_usd)
        self.assertFalse(stored.cost_increased)

    def test_full_update(self):
        set_compaction_telemetry_data(CompactionTelemetryData(trigger="auto"))
        update_compaction_telemetry(
            cache_hit_rate_after=12.0,
            estimated_cost_delta_usd=0.42,
            cost_increased=True,
        )
        stored = get_compaction_telemetry_data()
        self.assertAlmostEqual(stored.cache_hit_rate_after, 12.0)
        self.assertAlmostEqual(stored.estimated_cost_delta_usd, 0.42)
        self.assertTrue(stored.cost_increased)


class TestLogPostCompactionTelemetryPersists(unittest.TestCase):
    """End-to-end: post-compaction turn persists measurements into state."""

    def setUp(self):
        reset_state_for_tests()

    def tearDown(self):
        reset_state_for_tests()

    def _store_initial(self, **overrides):
        data = CompactionTelemetryData(
            trigger="manual",
            tokens_shed=50_000,
            pre_compact_token_count=100_000,
            post_compact_token_count=50_000,
            compaction_cost_usd=0.02,
            cache_hit_rate_before=80.0,
            model=SONNET,
        )
        for key, value in overrides.items():
            setattr(data, key, value)
        set_compaction_telemetry_data(data)

    def test_measurements_are_persisted_to_state(self):
        self._store_initial()
        log_post_compaction_telemetry(
            trigger="manual",
            tokens_shed=50_000,
            pre_compact_token_count=100_000,
            post_compact_token_count=50_000,
            compaction_cost_usd=0.02,
            cache_hit_rate_before=80.0,
            response_usage={
                "input_tokens": 1_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 9_000,
            },
            model=SONNET,
        )
        stored = get_compaction_telemetry_data()
        # Post-compaction hit rate: 9000 cached of 10k prompt -> 90%.
        self.assertAlmostEqual(stored.cache_hit_rate_after, 90.0)
        # delta = summary($0.30) - shed savings(10k uncached @$3/M +
        # 40k cached @$0.30/M = $0.042) = $0.258 -> cache-hostile vs $0.02.
        self.assertAlmostEqual(stored.estimated_cost_delta_usd, 0.258, places=9)
        self.assertTrue(stored.cost_increased)

    def test_cache_friendly_compaction_persists_negative_delta(self):
        self._store_initial(
            tokens_shed=500_000,
            pre_compact_token_count=600_000,
            post_compact_token_count=100_000,
            cache_hit_rate_before=0.0,
        )
        log_post_compaction_telemetry(
            trigger="manual",
            tokens_shed=500_000,
            pre_compact_token_count=600_000,
            post_compact_token_count=100_000,
            compaction_cost_usd=0.02,
            cache_hit_rate_before=0.0,
            response_usage={
                "input_tokens": 5_000,
                "cache_read_input_tokens": 5_000,
            },
            model=SONNET,
        )
        stored = get_compaction_telemetry_data()
        self.assertAlmostEqual(stored.estimated_cost_delta_usd, 0.30 - 1.50, places=9)
        self.assertLess(stored.estimated_cost_delta_usd, 0.0)
        self.assertFalse(stored.cost_increased)

    def test_unknown_model_still_persists_measured_hit_rate(self):
        self._store_initial()
        log_post_compaction_telemetry(
            trigger="manual",
            tokens_shed=50_000,
            pre_compact_token_count=100_000,
            post_compact_token_count=50_000,
            compaction_cost_usd=0.02,
            cache_hit_rate_before=80.0,
            response_usage={
                "input_tokens": 2_000,
                "cache_read_input_tokens": 2_000,
            },
            model=UNKNOWN_MODEL,
        )
        stored = get_compaction_telemetry_data()
        self.assertAlmostEqual(stored.cache_hit_rate_after, 50.0)
        self.assertIsNone(stored.estimated_cost_delta_usd)
        self.assertFalse(stored.cost_increased)

    def test_empty_usage_persists_none_after_without_crashing(self):
        self._store_initial()
        log_post_compaction_telemetry(
            trigger="manual",
            tokens_shed=50_000,
            pre_compact_token_count=100_000,
            post_compact_token_count=50_000,
            compaction_cost_usd=0.02,
            cache_hit_rate_before=80.0,
            response_usage={"input_tokens": 0},
            model=SONNET,
        )
        stored = get_compaction_telemetry_data()
        self.assertIsNone(stored.cache_hit_rate_after)
        # Delta still computable from pre-compaction hit rate alone.
        self.assertAlmostEqual(stored.estimated_cost_delta_usd, 0.258, places=9)


class TestContextWarningRendering(unittest.TestCase):
    """The user-visible /context warning block driven by telemetry fields."""

    def _markdown(self, telemetry):
        from src.context_system.context_analyzer import (
            ContextData,
            format_context_as_markdown,
        )

        return format_context_as_markdown(ContextData(compaction_telemetry=telemetry))

    def test_cache_hostile_warning_renders(self):
        markdown = self._markdown(
            {
                "trigger": "manual",
                "tokens_shed": 50_000,
                "cache_hit_rate_before": 80.0,
                "cache_hit_rate_after": 20.0,
                "estimated_cost_delta_usd": 0.258,
                "cost_increased": True,
            }
        )
        self.assertIn("Cache-hostile compaction detected", markdown)
        self.assertIn("50,000", markdown)
        self.assertIn("80.0%", markdown)
        self.assertIn("20.0%", markdown)

    def test_no_warning_when_cost_did_not_increase(self):
        markdown = self._markdown(
            {
                "trigger": "manual",
                "tokens_shed": 50_000,
                "cache_hit_rate_before": 80.0,
                "cache_hit_rate_after": 85.0,
                "estimated_cost_delta_usd": -1.2,
                "cost_increased": False,
            }
        )
        self.assertNotIn("Cache-hostile compaction detected", markdown)


if __name__ == "__main__":
    unittest.main()
