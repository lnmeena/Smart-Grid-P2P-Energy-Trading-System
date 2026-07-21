from __future__ import annotations
import time

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from .pricing_engine import PricingEngine, PricingResult
from .matching_engine import MatchingEngine, MatchResult
from .congestion_engine import (
    CongestionEngine,
    CongestionReport,
    CongestionResult,
)
from .contract_adapter import ContractAdapterBase
from .trade_oracle import TradeOracle


# ── interval result ───────────────────────────────────────────────────────────

# @dataclass
# class IntervalResult:
#     timestamp: datetime
#     pricing: PricingResult
#     matching: MatchResult
#     congestion: CongestionReport
#     approved_trades: List[CongestionResult]
#     oracle_summary: Optional[dict] = None
#
#     @property
#     def interval_stats(self) -> Dict:
#         return {
#             "timestamp": self.timestamp.isoformat(),
#             "market_state": self.pricing.market_state,
#             "raw_sdr": self.pricing.raw_sdr,
#             "effective_sdr": self.pricing.effective_sdr,
#             "base_price_gwei": self.pricing.base_price,
#             "final_price_gwei": self.pricing.final_price_gwei,
#             "export_ref": self.pricing.export_price,
#             "import_ref": self.pricing.import_price,
#             "proposals_generated": len(
#                 self.matching.proposals
#             ),
#             "match_rate": self.matching.match_rate,
#             "total_matched_mw": (
#                 self.matching.total_matched_mw
#             ),
#             "approved_trades": (
#                 self.congestion.approved_count
#             ),
#             "reduced_trades": (
#                 self.congestion.reduced_count
#             ),
#             "rejected_trades": (
#                 self.congestion.rejected_count
#             ),
#             "same_bus_trades": (
#                 self.congestion.same_bus_count
#             ),
#             "total_approved_mw": (
#                 self.congestion.total_approved_mw
#             ),
#             "congested_lines": len(
#                 self.congestion.congested_lines
#             ),
#             "oracle_results": 0,
#         }
#
#     def __repr__(self) -> str:
#         s = self.interval_stats
#
#         return (
#             f"IntervalResult("
#             f"{s['timestamp']}, "
#             f"state={s['market_state']}, "
#             f"price={s['final_price_gwei']}gwei, "
#             f"matched={s['total_matched_mw']:.2f}MW, "
#             f"approved={s['approved_trades']})"
#         )

@dataclass
class IntervalResult:
    timestamp: datetime
    pricing: PricingResult
    matching: MatchResult
    congestion: CongestionReport
    approved_trades: List[CongestionResult]

    trade_statistics: List[dict]

    python_time: float

    blockchain_time: float

    oracle_summary: Optional[dict] = None

    @property
    def interval_stats(self):

        return {
            "timestamp":
                self.timestamp.isoformat(),

            "market_state":
                self.pricing.market_state,

            "approved_trades":
                self.congestion.approved_count,

            "python_time":
                round(
                    self.python_time,
                    4,
                ),

            "blockchain_time":
                round(
                    self.blockchain_time,
                    4,
                ),

            "total_gas":
                sum(
                    t["gas_used"]
                    for t
                    in self.trade_statistics
                    if t["success"]
                ),
        }

    def __repr__(self):

        return (
            f"IntervalResult("
            f"{self.timestamp}, "
            f"approved="
            f"{self.congestion.approved_count}, "
            f"python="
            f"{self.python_time:.3f}s, "
            f"chain="
            f"{self.blockchain_time:.3f}s)"
        )

# ── market engine ─────────────────────────────────────────────────────────────

class MarketEngine:

    def __init__(
        self,
        pricing_engine: PricingEngine,
        matching_engine: MatchingEngine,
        congestion_engine: CongestionEngine,
        contract_adapter: Optional[
            ContractAdapterBase
        ] = None,
        oracle: Optional[
            TradeOracle
        ] = None,
    ) -> None:

        self.pricing = pricing_engine
        self.matching = matching_engine
        self.congestion = congestion_engine

        self.contract_adapter = (
            contract_adapter
        )

        self.oracle = oracle

    # ── public ────────────────────────────────────────────────────────────────

    def run_interval(
        self,
        timestamp: datetime,
        snapshot,
        registry,
    ) -> IntervalResult:

        python_start = (
            time.perf_counter()
        )

        # ------------------------------------------------------
        # Active participants
        # ------------------------------------------------------

        active_producers = (
            registry.active_producers()
        )

        active_consumers = (
            registry.active_consumers()
        )

        # ------------------------------------------------------
        # Seller supply
        # ------------------------------------------------------

        seller_supply: Dict[
            int,
            float,
        ] = {}

        for p in active_producers:

            supply = (
                snapshot.producer_supply.get(
                    p.agent_id,
                    0.0,
                )
            )

            if supply > 0:
                seller_supply[
                    p.agent_id
                ] = supply

        # ------------------------------------------------------
        # Buyer demand
        # ------------------------------------------------------

        buyer_demand: Dict[
            int,
            float,
        ] = {}

        for c in active_consumers:

            demand = (
                snapshot.consumer_demand.get(
                    c.agent_id,
                    0.0,
                )
            )

            if demand > 0:
                buyer_demand[
                    c.agent_id
                ] = demand

        # ------------------------------------------------------
        # Pricing
        # ------------------------------------------------------

        consumer_reps = {
            c.agent_id: c.reputation
            for c in active_consumers
        }

        # print("Pricing....")

        pricing_result = (
            self.pricing.compute(
                total_supply_mw=sum(
                    seller_supply.values()
                ),
                consumer_demand=buyer_demand,
                export_price=snapshot.export_price,
                import_price=snapshot.import_price,
                consumer_reputations=consumer_reps,
            )
        )

        # ------------------------------------------------------
        # Matching inputs
        # ------------------------------------------------------

        seller_bus = {
            p.agent_id: p.bus
            for p in active_producers
        }

        buyer_bus = {
            c.agent_id: c.bus
            for c in active_consumers
        }

        seller_reps = {
            p.agent_id: p.reputation
            for p in active_producers
        }

        seller_waits = {
            p.agent_id: p.intervals_since_trade
            for p in active_producers
        }

        buyer_waits = {
            c.agent_id: c.intervals_since_trade
            for c in active_consumers
        }

        suspended_ids = [
            p.agent_id
            for p in registry
            if p.suspended
        ]

        # ------------------------------------------------------
        # Fetch balances
        # ------------------------------------------------------

        buyer_balances = {}

        if self.contract_adapter:

            buyer_balances = (
                self.contract_adapter
                .fetch_all_balances(
                    list(
                        buyer_demand.keys()
                    )
                )
            )

        # ------------------------------------------------------
        # Matching
        # ------------------------------------------------------

        # print("Matching....")

        match_result = (
            self.matching.match(
                seller_supply=seller_supply,
                buyer_demand=buyer_demand,
                seller_bus=seller_bus,
                buyer_bus=buyer_bus,
                seller_reputations=seller_reps,
                buyer_reputations=consumer_reps,
                seller_waits=seller_waits,
                buyer_waits=buyer_waits,
                price_per_mwh=(
                    pricing_result.export_price
                ),
                price_gwei=(
                    pricing_result.final_price_gwei
                ),
                suspended_ids=suspended_ids,
                buyer_balances_gwei=(
                    buyer_balances
                ),
            )
        )

        # ------------------------------------------------------
        # Congestion
        # ------------------------------------------------------

        # print("Congestion check....")

        congestion_report = (
            self.congestion.check(
                proposals=(
                    match_result.proposals
                ),
                seller_reputations=(
                    seller_reps
                ),
                buyer_reputations=(
                    consumer_reps
                ),
            )
        )

        # print("Creating trades....")

        approved_trades = (
            congestion_report
            .approved_proposals()
        )

        # ------------------------------------------------------
        # Create on-chain trades
        # ------------------------------------------------------

        # if self.contract_adapter:
        #
        #     for trade in approved_trades:
        #
        #         proposal = (
        #             trade.proposal
        #         )
        #
        #         trade_id = int(
        #             proposal.proposal_id[:15],
        #             16,
        #         )
        #
        #         success = (
        #             self.contract_adapter
        #             .create_trade(
        #                 trade_id=trade_id,
        #                 seller=(
        #                     proposal.seller_id
        #                 ),
        #                 buyer=(
        #                     proposal.buyer_id
        #                 ),
        #                 energy_kwh=max(
        #                     1,
        #                     int(
        #                         trade.approved_mw
        #                         * 1000
        #                     ),
        #                 ),
        #                 base_price_gwei=(
        #                     proposal.price_gwei
        #                 ),
        #                 price_gwei=(
        #                     proposal.price_gwei
        #                 ),
        #                 stake_gwei=(
        #                     proposal.stake_gwei
        #                 ),
        #             )
        #         )
        #
        #         if not success:
        #             # print(
        #                 f"[MarketEngine] "
        #                 f"failed to create "
        #                 f"trade {trade_id}"
        #             )

        trade_statistics = []

        blockchain_start = (
            time.perf_counter()
        )

        if self.contract_adapter:

            for trade in approved_trades:


                proposal = (
                    trade.proposal
                )

                trade_id = int(
                    proposal.proposal_id[:15],
                    16,
                )

                # print("Creating", trade_id)

                tx = (
                    self.contract_adapter
                    .create_trade(
                        trade_id=trade_id,
                        seller=proposal.seller_id,
                        buyer=proposal.buyer_id,
                        energy_kwh=max(
                            1,
                            int(
                                trade.approved_mw
                                * 1000
                            ),
                        ),
                        base_price_gwei=(
                            proposal.price_gwei
                        ),
                        price_gwei=(
                            proposal.price_gwei
                        ),
                        stake_gwei=(
                            proposal.stake_gwei
                        ),
                    )
                )

                # print("Done", trade_id)

                tx["energy_kwh"] = max(
                    1,
                    int(
                        trade.approved_mw
                        * 1000
                    ),
                )

                trade_statistics.append(
                    tx
                )

        blockchain_time = (
            time.perf_counter()
            - blockchain_start
        )

        python_time = (
            time.perf_counter()
            - python_start
        )

        # ------------------------------------------------------
        # Refresh balances
        # ------------------------------------------------------

        if self.contract_adapter:

            for peer in registry:

                try:
                    peer.wallet_balance = (
                        self.contract_adapter
                        .fetch_balance(
                            peer.agent_id
                        )
                    )
                except Exception:
                    pass

        # ------------------------------------------------------
        # Result
        # ------------------------------------------------------

        # return IntervalResult(
        #     timestamp=timestamp,
        #     pricing=pricing_result,
        #     matching=match_result,
        #     congestion=congestion_report,
        #     approved_trades=approved_trades,
        #     oracle_summary=None,
        # )

        return IntervalResult(
            timestamp=timestamp,
            pricing=pricing_result,
            matching=match_result,
            congestion=congestion_report,
            approved_trades=approved_trades,
            trade_statistics=trade_statistics,
            python_time=python_time,
            blockchain_time=blockchain_time,
            oracle_summary=None,
        )

    # ── convenience factory ───────────────────────────────────────────────────

    @classmethod
    def build_default(
        cls,
        grid,
        contract_adapter=None,
        oracle=None,
    ) -> "MarketEngine":

        return cls(
            pricing_engine=PricingEngine(
                gamma=0.10,
                sdr_cap=2.0,
                sdr_floor=0.5,
            ),
            matching_engine=MatchingEngine(
                alpha=0.70,
                beta=0.30,
                min_trade_mw=0.001,
            ),
            congestion_engine=CongestionEngine(
                grid=grid,
                rep_advantage=0.20,
                min_trade_mw=0.001,
            ),
            contract_adapter=contract_adapter,
            oracle=oracle,
        )
