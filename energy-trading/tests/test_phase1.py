"""
tests/test_phase1.py  —  Phase 1 test suite
Run from /home/claude/p2p_energy/:
    python -m pytest tests/test_phase1.py -v
"""

import pytest
from datetime import datetime

from p2p_energy.csv_loader import (
    load_agent_data, load_network_data,
    load_load_max, load_load_min,
    load_market_price, load_renewable_production,
    load_all_timeseries, DatasetSnapshot,
)
from p2p_energy.agent_mapper import (
    build_registry, Peer, PeerRegistry,
    AgentType, EnergySource, Community,
    INITIAL_WALLET_TOKENS, REP_INIT, STAKE_RATE,
    REP_DELTA_SUCCESS, REP_DELTA_FAIL_DLVR, REP_DELTA_FRAUD,
    TradeRecord,
)
from p2p_energy.grid_graph import build_grid_graph, GridGraph


# ══════════════════════════════════════════════════════════════════════════════
# csv_loader tests
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentDataLoader:
    def setup_method(self):
        self.df = load_agent_data()

    def test_row_count(self):
        assert len(self.df) == 20, "Expected 20 agents (11 consumers + 8 producers + 1 grid)"

    def test_consumer_count(self):
        consumers = self.df[self.df["Type"] == "Consumer"]
        assert len(consumers) == 11

    def test_producer_count(self):
        producers = self.df[self.df["Type"] == "Producer"]
        assert len(producers) == 8

    def test_grid_count(self):
        grid = self.df[self.df["Type"] == "Grid"]
        assert len(grid) == 1
        assert int(grid.iloc[0]["Bus"]) == 1

    def test_producer_agent_ids(self):
        producers = self.df[self.df["Type"] == "Producer"]
        ids = set(producers["Agent"].astype(int).tolist())
        assert ids == {12, 13, 14, 15, 16, 17, 18, 19}

    def test_energy_source_values(self):
        producers = self.df[self.df["Type"] == "Producer"]
        sources = set(producers["Energy"].str.strip().tolist())
        assert sources == {"Wind", "Solar", "Gas", "Coal"}

    def test_renewable_have_zero_cost(self):
        renewable = self.df[self.df["Agent"].isin([12, 13, 14, 15, 16])]
        assert (renewable["a_cost"] == 0).all()
        assert (renewable["b_cost"] == 0).all()

    def test_bus_range(self):
        assert self.df["Bus"].between(1, 14).all()

    def test_pmax_positive_for_consumers(self):
        consumers = self.df[self.df["Type"] == "Consumer"]
        assert (consumers["Pmax"] > 0).all()


class TestNetworkDataLoader:
    def setup_method(self):
        self.df = load_network_data()

    def test_row_count(self):
        assert len(self.df) == 20, "IEEE 14-Bus has 20 transmission lines"

    def test_bus_range(self):
        assert self.df["From"].between(1, 14).all()
        assert self.df["To"].between(1, 14).all()

    def test_cap_non_negative(self):
        assert (self.df["CAP"] >= 0).all()

    def test_line_ids_unique(self):
        assert self.df["Line"].nunique() == 20

    def test_high_cap_line(self):
        # Line 1 (bus 1→2) should have CAP=120, highest in network
        line1 = self.df[self.df["Line"] == 1]
        assert float(line1["CAP"].iloc[0]) == 120.0


class TestTimeseriesLoaders:
    def test_load_max_shape(self):
        df = load_load_max()
        assert df.shape[1] == 11, "11 consumer columns"
        assert len(df) == 17520

    def test_load_min_shape(self):
        df = load_load_min()
        assert df.shape[1] == 11
        assert len(df) == 17520

    def test_market_price_columns(self):
        df = load_market_price()
        assert list(df.columns) == ["export_price", "import_price"]
        assert len(df) == 17520

    def test_renewable_shape(self):
        df = load_renewable_production()
        assert df.shape[1] == 5, "Agents 12-16"
        assert len(df) == 17520

    def test_datetime_index(self):
        df = load_load_max()
        # pandas >= 2.0 uses datetime64[us]; older uses datetime64[ns]
        assert "datetime64" in str(df.index.dtype)

    def test_market_price_mostly_positive(self):
        # Real dataset has ~46 zero-price intervals in May 2013 (genuine data)
        df = load_market_price()
        positive_frac = (df["export_price"] > 0).mean()
        assert positive_frac > 0.99, "Over 99% of intervals should have positive export price"

    def test_import_mostly_gte_export(self):
        # A handful of intervals have identical zero prices in the real dataset
        df = load_market_price()
        ok_frac = (df["import_price"] >= df["export_price"]).mean()
        assert ok_frac > 0.999, "Import price should almost always be >= export price"


class TestDatasetSnapshot:
    def setup_method(self):
        self.snapshots = load_all_timeseries(start="2012-07-01", end="2012-07-01")

    def test_count(self):
        # July 1 has 48 half-hour intervals
        assert len(self.snapshots) == 48

    def test_snapshot_type(self):
        assert isinstance(self.snapshots[0], DatasetSnapshot)

    def test_consumer_demand_count(self):
        snap = self.snapshots[0]
        assert len(snap.consumer_demand) == 11

    def test_producer_supply_count(self):
        snap = self.snapshots[0]
        # 5 renewable + 3 dispatchable = 8 producers
        assert len(snap.producer_supply) == 8

    def test_demand_positive(self):
        for snap in self.snapshots:
            assert all(v >= 0 for v in snap.consumer_demand.values())

    def test_supply_positive(self):
        for snap in self.snapshots:
            assert all(v >= 0 for v in snap.producer_supply.values())

    def test_sdr_computable(self):
        for snap in self.snapshots:
            assert snap.sdr > 0

    def test_export_lt_import(self):
        # July 1 has all positive prices (zero-price anomalies are in May 2013)
        for snap in self.snapshots:
            assert snap.export_price <= snap.import_price

    def test_timestamps_ordered(self):
        ts = [s.timestamp for s in self.snapshots]
        assert ts == sorted(ts)

    def test_date_filter(self):
        one_month = load_all_timeseries(start="2012-07-01", end="2012-07-31")
        # July has 31 days × 48 intervals = 1488
        assert len(one_month) == 31 * 48

    def test_full_dataset_size(self):
        all_snaps = load_all_timeseries()
        # July 1 2012 → June 30 2013 = 365 days × 48 = 17520
        assert len(all_snaps) == 17520

    def test_repr(self):
        snap = self.snapshots[0]
        r = repr(snap)
        assert "DatasetSnapshot" in r
        assert "SDR" in r


# ══════════════════════════════════════════════════════════════════════════════
# agent_mapper tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPeerRegistry:
    def setup_method(self):
        self.registry = build_registry()

    def test_total_peers(self):
        assert len(self.registry) == 20

    def test_consumers_count(self):
        assert len(self.registry.consumers) == 11

    def test_producers_count(self):
        assert len(self.registry.producers) == 8

    def test_grid_exists(self):
        g = self.registry.grid
        assert g is not None
        assert g.is_grid
        assert g.bus == 1

    def test_access_by_id(self):
        peer = self.registry[5]
        assert peer.agent_id == 5
        assert peer.is_consumer

    def test_wallet_unique(self):
        addresses = [p.wallet_address for p in self.registry]
        assert len(set(addresses)) == 20

    def test_initial_balance(self):
        for peer in self.registry:
            assert peer.wallet_balance == INITIAL_WALLET_TOKENS

    def test_initial_reputation(self):
        for peer in self.registry:
            assert peer.reputation == REP_INIT

    def test_initial_stake_zero(self):
        for peer in self.registry:
            assert peer.stake_locked == 0.0

    def test_no_suspended_initially(self):
        for peer in self.registry:
            assert not peer.suspended

    def test_renewable_producers(self):
        for aid in [12, 13, 14, 15, 16]:
            p = self.registry[aid]
            assert p.is_producer
            assert p.energy_source in (EnergySource.WIND, EnergySource.SOLAR)

    def test_dispatchable_producers(self):
        for aid in [17, 18, 19]:
            p = self.registry[aid]
            assert p.is_producer
            assert p.energy_source in (EnergySource.GAS, EnergySource.COAL)

    def test_community_assignment(self):
        # Community 1: buses 2,3,4,5  → agents 1-4, 12,13,15,17,18
        assert self.registry[1].community == Community.ONE
        assert self.registry[12].community == Community.ONE
        # Community 2: buses 7,8,9,10,14
        assert self.registry[6].community == Community.TWO   # bus 9
        # Community 3: buses 6,11,12,13
        assert self.registry[5].community == Community.THREE  # bus 6


class TestPeerReputationAndStake:
    def setup_method(self):
        self.peer = build_registry()[1]   # consumer, bus 2

    def test_update_reputation_positive(self):
        self.peer.reputation = 0.80
        self.peer.update_reputation(REP_DELTA_SUCCESS)
        assert abs(self.peer.reputation - 0.81) < 1e-9

    def test_update_reputation_clamp_max(self):
        self.peer.reputation = 1.0
        self.peer.update_reputation(+0.5)
        assert self.peer.reputation == 1.0

    def test_update_reputation_clamp_min(self):
        self.peer.reputation = 0.0
        self.peer.update_reputation(-0.5)
        assert self.peer.reputation == 0.0

    def test_suspension_trigger(self):
        self.peer.reputation = 0.15
        self.peer.update_reputation(REP_DELTA_FRAUD)   # -0.20
        assert self.peer.suspended

    def test_lock_stake_success(self):
        locked = self.peer.lock_stake(trade_value=100.0)
        expected = 100.0 * STAKE_RATE
        assert abs(locked - expected) < 1e-9
        assert abs(self.peer.stake_locked - expected) < 1e-9

    def test_lock_stake_insufficient_balance(self):
        self.peer.wallet_balance = 5.0
        locked = self.peer.lock_stake(trade_value=1_000.0)
        assert locked == 0.0
        assert self.peer.stake_locked == 0.0

    def test_release_stake(self):
        self.peer.lock_stake(100.0)
        self.peer.release_stake(10.0)
        assert abs(self.peer.stake_locked - 0.0) < 1e-9

    def test_slash_stake(self):
        self.peer.lock_stake(100.0)   # locks 10 tokens
        slashed = self.peer.slash_stake(5.0)
        assert slashed == 5.0
        assert self.peer.stake_locked == 5.0
        assert self.peer.wallet_balance == INITIAL_WALLET_TOKENS - 5.0

    def test_debit_credit(self):
        self.peer.debit(200.0)
        assert self.peer.wallet_balance == INITIAL_WALLET_TOKENS - 200.0
        self.peer.credit(50.0)
        assert self.peer.wallet_balance == INITIAL_WALLET_TOKENS - 150.0

    def test_debit_insufficient(self):
        self.peer.wallet_balance = 5.0
        result = self.peer.debit(100.0)
        assert result is False
        assert self.peer.wallet_balance == 5.0

    def test_trade_record_success(self):
        rec = TradeRecord(
            trade_id="T001",
            timestamp=datetime(2012, 7, 1, 0, 0),
            role="buyer",
            counterpart_id=12,
            energy_mw=5.0,
            price_per_mwh=50.0,
            success=True,
        )
        self.peer.record_trade(rec)
        assert len(self.peer.trade_history) == 1
        assert self.peer.intervals_since_trade == 0
        assert abs(self.peer.reputation - (REP_INIT + REP_DELTA_SUCCESS)) < 1e-9

    def test_trade_record_failure(self):
        self.peer.reputation = 0.80
        rec = TradeRecord(
            trade_id="T002",
            timestamp=datetime(2012, 7, 1, 0, 30),
            role="buyer",
            counterpart_id=13,
            energy_mw=3.0,
            price_per_mwh=45.0,
            success=False,
        )
        self.peer.record_trade(rec)
        assert abs(self.peer.reputation - (0.80 + REP_DELTA_FAIL_DLVR)) < 1e-9

    def test_tick_no_trade(self):
        self.peer.intervals_since_trade = 0
        self.peer.tick_no_trade()
        self.peer.tick_no_trade()
        assert self.peer.intervals_since_trade == 2

    def test_available_balance(self):
        self.peer.lock_stake(200.0)   # locks 20 tokens
        assert abs(self.peer.available_balance - (INITIAL_WALLET_TOKENS - 20.0)) < 1e-9


class TestRegistryBulkOps:
    def setup_method(self):
        self.registry = build_registry()

    def test_summary_shape(self):
        df = self.registry.summary()
        assert len(df) == 20
        assert "reputation" in df.columns
        assert "balance" in df.columns

    def test_tick_all_increments_non_traders(self):
        # Only agents 1 and 12 traded this interval
        self.registry.tick_all(trading_agent_ids=[1, 12])
        for peer in self.registry:
            if peer.agent_id in (1, 12):
                assert peer.intervals_since_trade == 0
            else:
                assert peer.intervals_since_trade == 1

    def test_active_excludes_suspended(self):
        self.registry[3].suspended = True
        active = self.registry.active
        ids = [p.agent_id for p in active]
        assert 3 not in ids
        assert len(active) == 19


# ══════════════════════════════════════════════════════════════════════════════
# grid_graph tests
# ══════════════════════════════════════════════════════════════════════════════

class TestGridGraph:
    def setup_method(self):
        self.gg = build_grid_graph()

    def test_bus_count(self):
        assert len(self.gg.buses) == 14

    def test_line_count(self):
        assert len(self.gg.lines) == 20

    def test_all_buses_present(self):
        assert self.gg.buses == list(range(1, 15))

    def test_connected(self):
        import networkx as nx
        assert nx.is_connected(self.gg.graph)

    def test_find_path_direct(self):
        path = self.gg.find_path(1, 2)
        assert path[0] == 1
        assert path[-1] == 2

    def test_find_path_multi_hop(self):
        # Bus 12 → 14 requires going through 13 or other hops
        path = self.gg.find_path(12, 14)
        assert path[0] == 12
        assert path[-1] == 14
        assert len(path) >= 3

    def test_find_path_same_bus(self):
        assert self.gg.find_path(5, 5) == []

    def test_initial_flow_zero(self):
        for u, v in self.gg.lines:
            assert self.gg.flow(u, v) == 0.0

    def test_cap_line1(self):
        # Line 1 (bus 1-2) has CAP=120
        assert self.gg.cap(1, 2) == 120.0

    def test_cap_lowest_line(self):
        # Line 18 (bus 10-11), 19, 20 have CAP=12 (lowest)
        assert self.gg.cap(10, 11) == 12.0

    def test_add_flow(self):
        path = self.gg.find_path(1, 2)   # [1, 2]
        self.gg.add_flow(path, 10.0)
        assert self.gg.flow(1, 2) == 10.0

    def test_add_flow_multi_hop(self):
        path = [1, 2, 3]
        self.gg.add_flow(path, 5.0)
        assert self.gg.flow(1, 2) == 5.0
        assert self.gg.flow(2, 3) == 5.0

    def test_remove_flow(self):
        path = [1, 2]
        self.gg.add_flow(path, 15.0)
        self.gg.remove_flow(path, 5.0)
        assert self.gg.flow(1, 2) == 10.0

    def test_remove_flow_clamp_zero(self):
        path = [1, 2]
        self.gg.remove_flow(path, 999.0)   # can't go negative
        assert self.gg.flow(1, 2) == 0.0

    def test_reset_flows(self):
        path = self.gg.find_path(1, 5)
        self.gg.add_flow(path, 20.0)
        self.gg.reset_flows()
        for u, v in self.gg.lines:
            assert self.gg.flow(u, v) == 0.0

    def test_utilization(self):
        # Line 1 has CAP=120, add 60 → utilization = 0.5
        path = [1, 2]
        self.gg.add_flow(path, 60.0)
        assert abs(self.gg.utilization(1, 2) - 0.5) < 1e-9

    def test_available_capacity(self):
        self.gg.add_flow([1, 2], 50.0)
        assert abs(self.gg.available_capacity(1, 2) - 70.0) < 1e-9

    def test_is_path_feasible_true(self):
        path = self.gg.find_path(1, 2)
        assert self.gg.is_path_feasible(path, 10.0)

    def test_is_path_feasible_false(self):
        # Line 1 CAP=120, requesting 200 → not feasible
        path = [1, 2]
        assert not self.gg.is_path_feasible(path, 200.0)

    def test_max_feasible_flow(self):
        # Path 12→13 has CAP=12 (tightest bottleneck)
        path = [12, 13]
        max_f = self.gg.max_feasible_flow(path)
        assert max_f == 12.0

    def test_congested_edges_empty_initially(self):
        assert self.gg.congested_edges() == []

    def test_congested_edges_detected(self):
        # Fill line 10-11 (CAP=12) to capacity
        self.gg.add_flow([10, 11], 12.0)
        congested = self.gg.congested_edges()
        assert (10, 11) in congested

    def test_summary_shape(self):
        df = self.gg.summary()
        assert len(df) == 20
        assert "util_%" in df.columns
        assert "congested" in df.columns

    def test_find_all_paths(self):
        paths = self.gg.find_all_paths(1, 14)
        assert len(paths) > 1   # multiple routes exist


# ══════════════════════════════════════════════════════════════════════════════
# integration: snapshot + registry + graph working together
# ══════════════════════════════════════════════════════════════════════════════

class TestPhase1Integration:
    def setup_method(self):
        self.snaps    = load_all_timeseries(start="2012-07-01", end="2012-07-01")
        self.registry = build_registry()
        self.gg       = build_grid_graph()

    def test_snapshot_supply_maps_to_registry_producers(self):
        snap = self.snaps[0]
        producer_ids_in_snap = set(snap.producer_supply.keys())
        registry_producer_ids = {p.agent_id for p in self.registry.producers}
        assert producer_ids_in_snap == registry_producer_ids

    def test_snapshot_demand_maps_to_registry_consumers(self):
        snap = self.snaps[0]
        consumer_ids_in_snap = set(snap.consumer_demand.keys())
        registry_consumer_ids = {p.agent_id for p in self.registry.consumers}
        assert consumer_ids_in_snap == registry_consumer_ids

    def test_producer_bus_in_graph(self):
        for peer in self.registry.producers:
            assert peer.bus in self.gg.buses

    def test_consumer_bus_in_graph(self):
        for peer in self.registry.consumers:
            assert peer.bus in self.gg.buses

    def test_path_between_any_producer_consumer(self):
        """Every producer should be able to reach every consumer via the grid."""
        for prod in self.registry.producers:
            for cons in self.registry.consumers:
                if prod.bus != cons.bus:
                    path = self.gg.find_path(prod.bus, cons.bus)
                    assert len(path) >= 2, (
                        f"No path: producer {prod.agent_id} (bus {prod.bus}) "
                        f"→ consumer {cons.agent_id} (bus {cons.bus})"
                    )

    def test_sdr_across_all_intervals(self):
        """SDR should be finite and positive for all 17520 intervals."""
        all_snaps = load_all_timeseries()
        for snap in all_snaps:
            assert snap.sdr > 0
            assert snap.total_supply > 0
            assert snap.total_demand > 0


if __name__ == "__main__":
    import subprocess
    subprocess.run(["python", "-m", "pytest", __file__, "-v"], check=True)
