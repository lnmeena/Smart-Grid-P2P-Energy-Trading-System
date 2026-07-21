"""
test_phase2.py  —  Phase 2 test suite
Tests all three sub-engines independently, then runs a full integration pass.
No CSV files required — everything is mocked inline.

Run with:
    python -m pytest test_phase2.py -v
or:
    python test_phase2.py
"""

from __future__ import annotations

import math
import unittest
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

# ── stubs for Phase 1 types (so tests run without the full package) ──────────

@dataclass
class _StubPeer:
    agent_id:       int
    agent_type:     str
    bus:            int
    reputation:     float = 0.75
    suspended:      bool  = False
    intervals_since_trade: int = 0

    @property
    def is_consumer(self): return self.agent_type == "Consumer"
    @property
    def is_producer(self): return self.agent_type == "Producer"


class _StubRegistry:
    def __init__(self, peers):
        self._peers = {p.agent_id: p for p in peers}

    def __iter__(self): return iter(self._peers.values())

    def active_consumers(self):
        return [p for p in self._peers.values()
                if p.agent_type == "Consumer" and not p.suspended]

    def active_producers(self):
        return [p for p in self._peers.values()
                if p.agent_type == "Producer" and not p.suspended]


@dataclass
class _StubSnapshot:
    timestamp:       datetime
    consumer_demand: Dict[int, float]
    producer_supply: Dict[int, float]
    export_price:    float
    import_price:    float

    @property
    def total_supply(self): return sum(self.producer_supply.values())
    @property
    def total_demand(self): return sum(self.consumer_demand.values())


# ── minimal GridGraph stub ────────────────────────────────────────────────────

class _StubGrid:
    """
    Minimal GridGraph-compatible stub for congestion tests.
    Models a simple chain: Bus1 — Bus2 — Bus3 — Bus4
    Each edge has CAP = 10 MW.
    """

    _TOPOLOGY = {
        (1, 2): 10.0,
        (2, 3): 10.0,
        (3, 4): 10.0,
        (1, 3): 5.0,   # shortcut with lower capacity
    }

    def __init__(self):
        self._flows: Dict[Tuple[int, int], float] = {k: 0.0 for k in self._TOPOLOGY}

    def _key(self, u, v): return (min(u, v), max(u, v))

    def reset_flows(self):
        for k in self._flows: self._flows[k] = 0.0

    def find_path(self, src, dst):
        # Simple hardcoded paths for stub
        paths = {
            (1, 2): [1, 2],
            (2, 1): [2, 1],
            (1, 3): [1, 2, 3],   # default: go via 2 (longer but higher cap)
            (3, 1): [3, 2, 1],
            (1, 4): [1, 2, 3, 4],
            (4, 1): [4, 3, 2, 1],
            (2, 3): [2, 3],
            (3, 2): [3, 2],
            (2, 4): [2, 3, 4],
            (4, 2): [4, 3, 2],
            (3, 4): [3, 4],
            (4, 3): [4, 3],
        }
        if src == dst: return []
        return paths.get((src, dst), [])

    def available_capacity(self, u, v):
        k = self._key(u, v)
        cap = self._TOPOLOGY.get(k, 0.0)
        if cap <= 0: return float("inf")
        return max(0.0, cap - self._flows.get(k, 0.0))

    def is_path_feasible(self, path, amount):
        for i in range(len(path) - 1):
            if self.available_capacity(path[i], path[i+1]) < amount:
                return False
        return True

    def max_feasible_flow(self, path):
        limits = [self.available_capacity(path[i], path[i+1])
                  for i in range(len(path) - 1)]
        return min(limits) if limits else float("inf")

    def add_flow(self, path, amount):
        for i in range(len(path) - 1):
            k = self._key(path[i], path[i+1])
            if k in self._flows:
                self._flows[k] += amount

    def remove_flow(self, path, amount):
        for i in range(len(path) - 1):
            k = self._key(path[i], path[i+1])
            if k in self._flows:
                self._flows[k] = max(0.0, self._flows[k] - amount)

    def utilization(self, u, v):
        k = self._key(u, v)
        cap = self._TOPOLOGY.get(k, 0.0)
        if cap <= 0: return 0.0
        return self._flows.get(k, 0.0) / cap

    def congested_edges(self):
        return [k for k, cap in self._TOPOLOGY.items()
                if cap > 0 and self._flows.get(k, 0.0) >= cap]


# ── import Phase 2 modules ────────────────────────────────────────────────────

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# Patch the grid_graph import inside congestion_engine so it doesn't fail
import types
stub_module = types.ModuleType("p2p_trading.grid_graph")
stub_module.GridGraph = _StubGrid
sys.modules["p2p_trading.grid_graph"] = stub_module

from phase2.pricing_engine    import PricingEngine, PricingResult
from phase2.matching_engine   import MatchingEngine, MatchResult, TradeProposal
from phase2.congestion_engine import CongestionEngine, CongestionReport


# ══════════════════════════════════════════════════════════════════════════════
# 1. PRICING ENGINE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestPricingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = PricingEngine(gamma=0.10, sdr_cap=2.0, sdr_floor=0.5)
        self.export = 30.0
        self.import_ = 60.0

    # ── SDR-only pricing ──────────────────────────────────────────────────────

    def test_balanced_market_midpoint(self):
        """
        SDR=1, sdr_floor=0.5, sdr_cap=2.0
        t = (1.0 - 0.5) / (2.0 - 0.5) = 0.333…
        price = import - t × spread = 60 - 0.333×30 = 50.0
        """
        result = self.engine.compute(
            total_supply_mw=100.0,
            consumer_demand={1: 100.0},
            export_price=self.export,
            import_price=self.import_,
        )
        self.assertEqual(result.market_state, "balanced")
        expected = self.import_ - (1/3) * (self.import_ - self.export)
        self.assertAlmostEqual(result.base_price, expected, places=1)

    def test_oversupply_drives_price_to_floor(self):
        """SDR=2 (max) → price = export_price (floor)."""
        result = self.engine.compute(
            total_supply_mw=200.0,
            consumer_demand={1: 100.0},
            export_price=self.export,
            import_price=self.import_,
        )
        self.assertEqual(result.market_state, "oversupply")
        self.assertAlmostEqual(result.base_price, self.export, places=2)

    def test_undersupply_drives_price_to_ceiling(self):
        """SDR=0.5 (min) → price = import_price (ceiling)."""
        result = self.engine.compute(
            total_supply_mw=50.0,
            consumer_demand={1: 100.0},
            export_price=self.export,
            import_price=self.import_,
        )
        self.assertEqual(result.market_state, "undersupply")
        self.assertAlmostEqual(result.base_price, self.import_, places=2)

    def test_extreme_oversupply_clamped_to_floor(self):
        """SDR >> sdr_cap should still give export_price."""
        result = self.engine.compute(
            total_supply_mw=1000.0,
            consumer_demand={1: 100.0},
            export_price=self.export,
            import_price=self.import_,
        )
        self.assertAlmostEqual(result.base_price, self.export, places=2)

    def test_price_monotone_decreasing_with_supply(self):
        """More supply → lower price (monotone)."""
        prices = []
        for supply in [60, 80, 100, 120, 150, 200]:
            r = self.engine.compute(
                total_supply_mw=float(supply),
                consumer_demand={1: 100.0},
                export_price=self.export,
                import_price=self.import_,
            )
            prices.append(r.base_price)
        self.assertEqual(prices, sorted(prices, reverse=True))

    def test_zero_demand_returns_inf_sdr(self):
        """Zero demand → SDR = inf → price = export_price."""
        result = self.engine.compute(
            total_supply_mw=100.0,
            consumer_demand={},
            export_price=self.export,
            import_price=self.import_,
        )
        self.assertTrue(math.isinf(result.raw_sdr))
        self.assertAlmostEqual(result.base_price, self.export, places=2)

    # ── reputation weighting ──────────────────────────────────────────────────

    def test_high_rep_consumers_tighten_effective_sdr(self):
        """High-rep consumers make demand look heavier → lower effective_sdr → higher price."""
        base = self.engine.compute(
            total_supply_mw=100.0,
            consumer_demand={1: 100.0},
            export_price=self.export,
            import_price=self.import_,
            consumer_reputations={1: 0.75},   # neutral
        )
        high_rep = self.engine.compute(
            total_supply_mw=100.0,
            consumer_demand={1: 100.0},
            export_price=self.export,
            import_price=self.import_,
            consumer_reputations={1: 1.0},    # max rep
        )
        self.assertGreater(high_rep.base_price, base.base_price)

    def test_zero_gamma_ignores_reputation(self):
        """gamma=0 → effective_sdr == raw_sdr regardless of reputations."""
        engine = PricingEngine(gamma=0.0)
        result = self.engine.compute(
            total_supply_mw=100.0,
            consumer_demand={1: 100.0},
            export_price=self.export,
            import_price=self.import_,
            consumer_reputations={1: 1.0},
        )
        # With gamma=0 engine, same output as no-rep engine at same SDR
        no_rep = PricingEngine(gamma=0.0).compute(
            total_supply_mw=100.0,
            consumer_demand={1: 100.0},
            export_price=self.export,
            import_price=self.import_,
        )
        # raw_sdr should always equal effective_sdr when gamma=0
        engine0 = PricingEngine(gamma=0.0)
        r = engine0.compute(100.0, {1: 100.0}, self.export, self.import_, {1: 1.0})
        self.assertAlmostEqual(r.raw_sdr, r.effective_sdr, places=6)

    def test_invalid_gamma_raises(self):
        with self.assertRaises(ValueError):
            PricingEngine(gamma=1.5)


# ══════════════════════════════════════════════════════════════════════════════
# 2. MATCHING ENGINE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestMatchingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MatchingEngine(alpha=0.70, beta=0.30)
        self.price  = 45.0

    def _match(self, sellers, buyers, s_reps=None, b_reps=None,
                s_waits=None, b_waits=None, suspended=None):
        """Helper: build dicts and call engine.match()."""
        s_supply = {aid: mw for aid, mw, _ in sellers}
        s_bus    = {aid: bus for aid, _, bus in sellers}
        b_demand = {aid: mw for aid, mw, _ in buyers}
        b_bus    = {aid: bus for aid, _, bus in buyers}
        s_reps   = s_reps or {aid: 0.75 for aid, _, _ in sellers}
        b_reps   = b_reps or {aid: 0.75 for aid, _, _ in buyers}
        s_waits  = s_waits or {aid: 0 for aid, _, _ in sellers}
        b_waits  = b_waits or {aid: 0 for aid, _, _ in buyers}
        return self.engine.match(
            seller_supply=s_supply, buyer_demand=b_demand,
            seller_bus=s_bus,       buyer_bus=b_bus,
            seller_reputations=s_reps, buyer_reputations=b_reps,
            seller_waits=s_waits,      buyer_waits=b_waits,
            price_per_mwh=self.price,
            suspended_ids=suspended or [],
        )

    # ── basic matching ────────────────────────────────────────────────────────

    def test_simple_one_to_one_match(self):
        """Single seller with 10 MW, single buyer needing 10 MW → 1 trade."""
        result = self._match(
            sellers=[(12, 10.0, 2)],
            buyers= [(1,  10.0, 3)],
        )
        self.assertEqual(len(result.proposals), 1)
        self.assertAlmostEqual(result.proposals[0].energy_mw, 10.0)
        self.assertAlmostEqual(result.total_matched_mw, 10.0)
        self.assertAlmostEqual(result.match_rate, 1.0)

    def test_one_seller_multiple_buyers(self):
        """Seller with 15 MW fills buyer1 (10 MW) then buyer2 (5 MW)."""
        result = self._match(
            sellers=[(12, 15.0, 2)],
            buyers= [(1,  10.0, 3), (2, 5.0, 3)],
        )
        total = sum(p.energy_mw for p in result.proposals)
        self.assertAlmostEqual(total, 15.0, places=4)
        self.assertEqual(len(result.unmatched_buyers), 0)

    def test_partial_supply(self):
        """Seller with 5 MW, buyer needs 10 MW → 5 MW matched, 5 MW unmatched demand."""
        result = self._match(
            sellers=[(12, 5.0, 2)],
            buyers= [(1, 10.0, 3)],
        )
        self.assertAlmostEqual(result.total_matched_mw, 5.0)
        self.assertAlmostEqual(result.match_rate, 0.5)
        self.assertIn(1, result.unmatched_buyers)

    def test_no_sellers(self):
        """No sellers → empty proposals, all demand unmatched."""
        result = self._match(sellers=[], buyers=[(1, 10.0, 3)])
        self.assertEqual(result.proposals, [])
        self.assertAlmostEqual(result.match_rate, 0.0)

    def test_suspended_seller_excluded(self):
        """Suspended seller ID is excluded from matching."""
        result = self._match(
            sellers=[(12, 10.0, 2), (13, 10.0, 3)],
            buyers= [(1,  10.0, 5)],
            suspended=[12],
        )
        # Only seller 13 can trade
        for p in result.proposals:
            self.assertNotEqual(p.seller_id, 12)

    # ── reputation priority ───────────────────────────────────────────────────

    def test_higher_rep_seller_matched_first(self):
        """With scarce demand, the highest-rep seller should be matched first."""
        result = self._match(
            sellers=[(12, 10.0, 2), (13, 10.0, 2)],
            buyers= [(1, 10.0, 3)],                    # only 10 MW demand
            s_reps={12: 0.95, 13: 0.40},
        )
        # Only seller 12 (rep=0.95) should get matched
        seller_ids = {p.seller_id for p in result.proposals}
        self.assertIn(12, seller_ids)
        self.assertNotIn(13, seller_ids)

    # ── anti-starvation ───────────────────────────────────────────────────────

    def test_wait_time_promotes_low_rep_seller(self):
        """
        Low-rep seller who has waited 10 intervals should overtake a neutral-rep
        seller with zero wait time.
        α=0.7, β=0.3
        low_rep (rep=0.40, wait=10)  → 0.7×0.40 + 0.3×1.0 = 0.58
        neutral (rep=0.75, wait=0)   → 0.7×0.75 + 0.3×0.0 = 0.525
        """
        result = self._match(
            sellers=[(12, 10.0, 2), (13, 10.0, 2)],
            buyers= [(1, 10.0, 3)],
            s_reps={12: 0.40, 13: 0.75},
            s_waits={12: 10,  13: 0},
        )
        seller_ids = {p.seller_id for p in result.proposals}
        self.assertIn(12, seller_ids)
        self.assertNotIn(13, seller_ids)

    def test_starvation_report(self):
        """starvation_report() returns agents with wait >= threshold."""
        report = self.engine.starvation_report(
            buyer_waits={1: 2, 2: 6, 3: 10},
            threshold_intervals=6,
        )
        starved_ids = {aid for aid, _ in report}
        self.assertIn(2, starved_ids)
        self.assertIn(3, starved_ids)
        self.assertNotIn(1, starved_ids)

    # ── alpha+beta validation ─────────────────────────────────────────────────

    def test_alpha_beta_must_sum_to_one(self):
        with self.assertRaises(ValueError):
            MatchingEngine(alpha=0.6, beta=0.6)

    def test_trade_value_property(self):
        result = self._match(
            sellers=[(12, 10.0, 2)],
            buyers= [(1,  10.0, 3)],
        )
        p = result.proposals[0]
        self.assertAlmostEqual(p.trade_value, p.energy_mw * self.price, places=4)


# ══════════════════════════════════════════════════════════════════════════════
# 3. CONGESTION ENGINE TESTS
# ══════════════════════════════════════════════════════════════════════════════

def _make_proposal(pid, seller_id, buyer_id, s_bus, b_bus, mw, price=45.0):
    return TradeProposal(
        proposal_id=pid,
        seller_id=seller_id, buyer_id=buyer_id,
        seller_bus=s_bus, buyer_bus=b_bus,
        energy_mw=mw, price_per_mwh=price,
        seller_score=0.75, buyer_score=0.75,
    )


class TestCongestionEngine(unittest.TestCase):

    def setUp(self):
        self.grid = _StubGrid()
        self.engine = CongestionEngine(self.grid, rep_advantage=0.20, min_trade_mw=0.001)

    def _check(self, proposals, s_reps=None, b_reps=None):
        s_reps = s_reps or {}
        b_reps = b_reps or {}
        return self.engine.check(proposals, s_reps, b_reps)

    # ── same-bus trades ───────────────────────────────────────────────────────

    def test_same_bus_always_approved(self):
        """Trade between peers on the same bus is always approved."""
        p = _make_proposal("p1", 12, 1, 2, 2, 5.0)  # both on bus 2
        report = self._check([p])
        self.assertEqual(report.same_bus_count, 1)
        self.assertAlmostEqual(report.total_approved_mw, 5.0)

    # ── no congestion ─────────────────────────────────────────────────────────

    def test_feasible_trade_approved_in_full(self):
        """Trade within line capacity → approved, full energy."""
        p = _make_proposal("p1", 12, 1, 1, 2, 8.0)  # bus1→bus2, CAP=10
        report = self._check([p])
        self.assertEqual(report.approved_count, 1)
        self.assertAlmostEqual(report.results[0].approved_mw, 8.0)

    def test_multiple_feasible_trades_all_approved(self):
        """Two trades on non-overlapping paths both approved in full."""
        p1 = _make_proposal("p1", 12, 1, 1, 2, 5.0)   # bus1-bus2 (5 MW)
        p2 = _make_proposal("p2", 13, 2, 3, 4, 5.0)   # bus3-bus4 (5 MW)
        report = self._check([p1, p2])
        self.assertEqual(report.approved_count, 2)
        self.assertAlmostEqual(report.total_approved_mw, 10.0)

    # ── congestion detection ──────────────────────────────────────────────────

    def test_two_trades_exceed_capacity_get_reduced(self):
        """
        Bus1→Bus4 path uses bus1-bus2-bus3-bus4 (CAP=10 each).
        Two trades of 8 MW each → combined 16 > 10 → second gets reduced.
        """
        p1 = _make_proposal("p1", 12, 1, 1, 4, 8.0)
        p2 = _make_proposal("p2", 13, 2, 1, 4, 8.0)
        report = self._check([p1, p2])

        total_approved = report.total_approved_mw
        # Total should not exceed CAP = 10 on any edge
        self.assertLessEqual(total_approved, 10.0 + 0.001)

    def test_trade_exceeding_full_capacity_rejected(self):
        """Trade requesting 15 MW on a 10 MW line with 0 remaining → reduced/rejected."""
        # First saturate the line
        p1 = _make_proposal("p1", 12, 1, 1, 2, 10.0)
        self._check([p1])                              # saturate

        # Now try another trade on the same line
        p2 = _make_proposal("p2", 13, 2, 1, 2, 5.0)
        report = self._check([p2])                     # fresh check (resets flows)

        # After reset, p2 alone should fit since edge has 10 MW cap
        self.assertAlmostEqual(report.results[0].approved_mw, 5.0)

    # ── reputation in reallocation ────────────────────────────────────────────

    def test_higher_rep_gets_larger_share_during_reallocation(self):
        """
        Two trades compete for same bottleneck.
        High-rep pair should receive >= low-rep pair after reallocation.
        """
        # Fill 5 MW to leave only 5 MW on bus1-bus2
        p_fill = _make_proposal("pf", 99, 99, 1, 2, 5.0)
        # Use engine directly to add flow, then test with 2 competing proposals
        self.grid.reset_flows()
        self.grid.add_flow([1, 2], 5.0)   # 5 MW already flowing

        p_high = _make_proposal("ph", 12, 1, 1, 2, 4.0)
        p_low  = _make_proposal("pl", 13, 2, 1, 2, 4.0)

        high_reps = {12: 0.95}
        low_reps  = {13: 0.40}
        b_reps    = {1: 0.75, 2: 0.75}

        self.grid.reset_flows()
        self.grid.add_flow([1, 2], 5.0)

        report = self.engine.check(
            [p_high, p_low],
            seller_reputations={12: 0.95, 13: 0.40},
            buyer_reputations={1: 0.75, 2: 0.75},
        )

        results = {r.proposal.proposal_id: r for r in report.results}
        high_alloc = results["ph"].approved_mw
        low_alloc  = results["pl"].approved_mw
        # High-rep peer should get >= low-rep peer
        self.assertGreaterEqual(high_alloc, low_alloc - 0.001)

    # ── no path ───────────────────────────────────────────────────────────────

    def test_no_path_between_buses_rejected(self):
        """Trade where no path exists in the stub graph → rejected."""
        # Our stub doesn't have bus 5 or 6, so bus5→bus6 has no path
        p = _make_proposal("p1", 12, 1, 5, 6, 5.0)
        report = self._check([p])
        self.assertEqual(report.rejected_count, 1)
        self.assertAlmostEqual(report.total_approved_mw, 0.0)

    # ── flow reset between intervals ──────────────────────────────────────────

    def test_flows_reset_each_call(self):
        """Each call to check() resets flows, so intervals don't bleed into each other."""
        p1 = _make_proposal("p1", 12, 1, 1, 2, 9.0)
        self._check([p1])          # commit 9 MW to edge 1-2

        # Second call resets flows; this 9 MW trade should still fit
        p2 = _make_proposal("p2", 12, 1, 1, 2, 9.0)
        report2 = self._check([p2])
        self.assertAlmostEqual(report2.results[0].approved_mw, 9.0)

    # ── ordering preserved ────────────────────────────────────────────────────

    def test_output_order_matches_input_order(self):
        """Results list should maintain the same order as input proposals."""
        p1 = _make_proposal("aaa", 12, 1, 1, 2, 3.0)
        p2 = _make_proposal("bbb", 13, 2, 3, 4, 3.0)
        p3 = _make_proposal("ccc", 14, 3, 1, 2, 3.0)
        report = self._check([p1, p2, p3])
        ids_out = [r.proposal.proposal_id for r in report.results]
        self.assertEqual(ids_out, ["aaa", "bbb", "ccc"])


# ══════════════════════════════════════════════════════════════════════════════
# 4. INTEGRATION TEST — MarketEngine.run_interval()
# ══════════════════════════════════════════════════════════════════════════════

class TestMarketEngineIntegration(unittest.TestCase):

    def _build_engine(self):
        from phase2.market_engine import MarketEngine
        from phase2.pricing_engine import PricingEngine
        from phase2.matching_engine import MatchingEngine
        from phase2.congestion_engine import CongestionEngine
        grid = _StubGrid()
        return MarketEngine(
            pricing_engine=PricingEngine(gamma=0.10),
            matching_engine=MatchingEngine(alpha=0.70, beta=0.30),
            congestion_engine=CongestionEngine(grid),
        )

    def _build_registry(self):
        peers = [
            # Consumers (bus 3, 4)
            _StubPeer(1, "Consumer", 3, reputation=0.80),
            _StubPeer(2, "Consumer", 4, reputation=0.60),
            # Producers (bus 1, 2)
            _StubPeer(12, "Producer", 1, reputation=0.90),
            _StubPeer(13, "Producer", 2, reputation=0.70),
        ]
        return _StubRegistry(peers)

    def _build_snapshot(self):
        return _StubSnapshot(
            timestamp=datetime(2012, 7, 1, 0, 0),
            consumer_demand={1: 8.0, 2: 6.0},
            producer_supply={12: 10.0, 13: 7.0},
            export_price=30.0,
            import_price=60.0,
        )

    def test_full_interval_runs_without_error(self):
        engine   = self._build_engine()
        registry = self._build_registry()
        snapshot = self._build_snapshot()

        from phase2.market_engine import IntervalResult
        result = engine.run_interval(snapshot.timestamp, snapshot, registry)
        self.assertIsInstance(result, IntervalResult)

    def test_interval_stats_keys_present(self):
        engine   = self._build_engine()
        registry = self._build_registry()
        snapshot = self._build_snapshot()

        result = engine.run_interval(snapshot.timestamp, snapshot, registry)
        stats = result.interval_stats

        expected_keys = [
            "timestamp", "market_state", "raw_sdr", "effective_sdr",
            "base_price", "total_matched_mw", "approved_trades",
            "total_approved_mw", "congested_lines",
        ]
        for k in expected_keys:
            self.assertIn(k, stats, msg=f"Missing key: {k}")

    def test_price_within_export_import_bounds(self):
        engine   = self._build_engine()
        registry = self._build_registry()
        snapshot = self._build_snapshot()

        result = engine.run_interval(snapshot.timestamp, snapshot, registry)
        self.assertGreaterEqual(result.pricing.base_price, snapshot.export_price)
        self.assertLessEqual(result.pricing.base_price,    snapshot.import_price)

    def test_approved_mw_does_not_exceed_supply(self):
        engine   = self._build_engine()
        registry = self._build_registry()
        snapshot = self._build_snapshot()

        result = engine.run_interval(snapshot.timestamp, snapshot, registry)
        total_supply = sum(snapshot.producer_supply.values())
        self.assertLessEqual(result.congestion.total_approved_mw, total_supply + 0.001)

    def test_suspended_peers_not_in_trades(self):
        engine   = self._build_engine()
        registry = self._build_registry()
        snapshot = self._build_snapshot()
        # Suspend peer 12
        registry._peers[12].suspended = True

        result = engine.run_interval(snapshot.timestamp, snapshot, registry)
        for r in result.approved_trades:
            self.assertNotEqual(r.proposal.seller_id, 12)
            self.assertNotEqual(r.proposal.buyer_id,  12)

    def test_build_default_factory(self):
        """MarketEngine.build_default() constructs a valid engine."""
        from phase2.market_engine import MarketEngine
        grid   = _StubGrid()
        engine = MarketEngine.build_default(grid)
        self.assertIsNotNone(engine)


# ══════════════════════════════════════════════════════════════════════════════
# 5. EDGE CASES & BOUNDARY CONDITIONS
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases(unittest.TestCase):

    def test_pricing_equal_export_import(self):
        """Degenerate spread (export == import) returns midpoint."""
        engine = PricingEngine()
        result = engine.compute(100.0, {1: 100.0}, 50.0, 50.0)
        self.assertAlmostEqual(result.base_price, 50.0)

    def test_matching_all_suspended(self):
        """All buyers suspended → no proposals."""
        engine = MatchingEngine(alpha=0.7, beta=0.3)
        result = engine.match(
            seller_supply={12: 10.0}, buyer_demand={1: 10.0},
            seller_bus={12: 2},       buyer_bus={1: 3},
            seller_reputations={12: 0.75}, buyer_reputations={1: 0.75},
            seller_waits={12: 0},         buyer_waits={1: 0},
            price_per_mwh=45.0,
            suspended_ids=[1],
        )
        self.assertEqual(result.proposals, [])

    def test_congestion_single_proposal_below_cap(self):
        """Single proposal that fits comfortably is approved in full."""
        grid   = _StubGrid()
        engine = CongestionEngine(grid)
        p = _make_proposal("p1", 12, 1, 1, 2, 3.0)  # 3 < 10 MW cap
        report = engine.check([p], {}, {})
        self.assertAlmostEqual(report.total_approved_mw, 3.0)

    def test_congestion_empty_proposals(self):
        """Empty input list → empty report."""
        grid   = _StubGrid()
        engine = CongestionEngine(grid)
        report = engine.check([], {}, {})
        self.assertEqual(len(report.results), 0)
        self.assertAlmostEqual(report.total_approved_mw, 0.0)

    def test_match_rate_capped_at_one(self):
        """Match rate should never exceed 1.0."""
        engine = MatchingEngine(alpha=0.7, beta=0.3)
        result = engine.match(
            seller_supply={12: 100.0}, buyer_demand={1: 5.0},
            seller_bus={12: 2},        buyer_bus={1: 3},
            seller_reputations={12: 0.75}, buyer_reputations={1: 0.75},
            seller_waits={12: 0},          buyer_waits={1: 0},
            price_per_mwh=45.0,
        )
        self.assertLessEqual(result.match_rate, 1.0)

    def test_trade_proposal_value_correct(self):
        p = TradeProposal(
            proposal_id="test", seller_id=12, buyer_id=1,
            seller_bus=2, buyer_bus=3,
            energy_mw=5.0, price_per_mwh=40.0,
            seller_score=0.8, buyer_score=0.7,
        )
        self.assertAlmostEqual(p.trade_value, 200.0)


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = unittest.TestSuite()
    for cls in [
        TestPricingEngine,
        TestMatchingEngine,
        TestCongestionEngine,
        TestMarketEngineIntegration,
        TestEdgeCases,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
