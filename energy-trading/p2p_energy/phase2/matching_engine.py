from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

STAKE_RATE      = 0.10    # ~10% of trade price reserved as stake (Req 6)
REP_SCALE       = 100     # reputation is 0–100; divide to get [0, 1] for scoring


# ── trade proposal ─────────────────────────────────────────────────────────────

@dataclass
class TradeProposal:
    """
    A matched trade pair produced by the matching engine.

    New fields (Req 2, 6):
        price_gwei      : final price in gwei (non-negative integer)
        stake_gwei      : stake in gwei (~10% of price_gwei)
        skipped_affordability : True if this pair was skipped due to
                                buyer inability to afford (Req 5, 6)
    """
    proposal_id:    str
    seller_id:      int
    buyer_id:       int
    seller_bus:     int
    buyer_bus:      int
    energy_mw:      float
    price_per_mwh:  float   # kept as float $/MWh for display / congestion engine
    price_gwei:     int     # price in gwei (Req 2)
    stake_gwei:     int     # stake in gwei (Req 6)
    seller_score:   float
    buyer_score:    float

    @property
    def trade_value(self) -> float:
        return round(self.energy_mw * self.price_per_mwh, 6)

    def __repr__(self) -> str:
        return (
            f"TradeProposal({self.proposal_id[:8]}, "
            f"seller={self.seller_id}→buyer={self.buyer_id}, "
            f"{self.energy_mw:.3f}MW @ {self.price_gwei}gwei, "
            f"stake={self.stake_gwei}gwei)"
        )


# ── match result ───────────────────────────────────────────────────────────────

@dataclass
class MatchResult:
    """
    Full output of one interval's matching pass.

    New field:
        skipped_buyers : {agent_id → reason} – buyers skipped for affordability
    """
    proposals:          List[TradeProposal]
    unmatched_sellers:  Dict[int, float]
    unmatched_buyers:   Dict[int, float]
    seller_scores:      Dict[int, float]
    buyer_scores:       Dict[int, float]
    total_matched_mw:   float
    match_rate:         float
    skipped_buyers:     Dict[int, str] = field(default_factory=dict)  # Req 5, 7

    def __repr__(self) -> str:
        return (
            f"MatchResult(proposals={len(self.proposals)}, "
            f"matched={self.total_matched_mw:.2f}MW, "
            f"rate={self.match_rate:.1%}, "
            f"skipped={len(self.skipped_buyers)})"
        )


# ── internal peer snapshot ────────────────────────────────────────────────────

@dataclass
class _PeerSnap:
    """Lightweight working copy used during matching."""
    agent_id:   int
    bus:        int
    reputation: int     # 0–100 (Req 4)
    wait:       int
    energy_mw:  float
    score:      float   = field(default=0.0)


# ── matching engine ───────────────────────────────────────────────────────────

class MatchingEngine:
    """
    Greedy reputation+anti-starvation matching engine with affordability checks.

    Parameters
    ----------
    alpha        : weight for reputation in score [0, 1]  (default 0.7)
    beta         : weight for wait time in score [0, 1]   (default 0.3)
    min_trade_mw : minimum trade size (default 0.001 MW)
    """

    MIN_TRADE_MW = 0.001

    def __init__(
        self,
        alpha: float = 0.70,
        beta:  float = 0.30,
        min_trade_mw: float = 0.001,
    ) -> None:
        if abs(alpha + beta - 1.0) > 1e-9:
            raise ValueError(f"alpha + beta must equal 1.0, got {alpha + beta}")
        self.alpha        = alpha
        self.beta         = beta
        self.min_trade_mw = min_trade_mw

    # ── public ────────────────────────────────────────────────────────────────

    def match(
        self,
        seller_supply:          Dict[int, float],
        buyer_demand:           Dict[int, float],
        seller_bus:             Dict[int, int],
        buyer_bus:              Dict[int, int],
        seller_reputations:     Dict[int, int],      # 0–100 (Req 4)
        buyer_reputations:      Dict[int, int],      # 0–100 (Req 4)
        seller_waits:           Dict[int, int],
        buyer_waits:            Dict[int, int],
        price_per_mwh:          float,
        price_gwei:             int,                 # Req 2: final price in gwei
        suspended_ids:          Optional[List[int]] = None,
        buyer_balances_gwei:    Optional[Dict[int, int]] = None,  # Req 5: from contract
    ) -> MatchResult:
        """
        Run one interval's matching pass with affordability checks.

        Parameters
        ----------
        price_gwei            : per-trade price in gwei (Req 2). Used for
                                affordability check = price_gwei + stake_gwei.
        buyer_balances_gwei   : {agent_id → balance_gwei} read from the smart
                                contract's UserModule (Req 5). If omitted,
                                affordability checks are skipped.

        Returns
        -------
        MatchResult with skipped_buyers populated for ineligible buyers (Req 7).
        """
        return self._match_with_affordability_check(
            seller_supply=seller_supply,
            buyer_demand=buyer_demand,
            seller_bus=seller_bus,
            buyer_bus=buyer_bus,
            seller_reputations=seller_reputations,
            buyer_reputations=buyer_reputations,
            seller_waits=seller_waits,
            buyer_waits=buyer_waits,
            price_per_mwh=price_per_mwh,
            price_gwei=price_gwei,
            suspended_ids=suspended_ids,
            buyer_balances_gwei=buyer_balances_gwei,
        )

    # ── NEW implementation: affordability-aware (Req 5, 6, 7) ─────────────────

    def _match_with_affordability_check(
        self,
        seller_supply:          Dict[int, float],
        buyer_demand:           Dict[int, float],
        seller_bus:             Dict[int, int],
        buyer_bus:              Dict[int, int],
        seller_reputations:     Dict[int, int],
        buyer_reputations:      Dict[int, int],
        seller_waits:           Dict[int, int],
        buyer_waits:            Dict[int, int],
        price_per_mwh:          float,
        price_gwei:             int,
        suspended_ids:          Optional[List[int]] = None,
        buyer_balances_gwei:    Optional[Dict[int, int]] = None,
    ) -> MatchResult:
        """
        Affordability-aware greedy matching (Req 7).

        Affordability check (Req 5, 6):
            required = price_gwei + stake_gwei
            stake_gwei = ceil(price_gwei * STAKE_RATE)  ≈ 10% of price
            buyer must have contract balance >= required

        Buyers failing the affordability check are added to skipped_buyers
        and excluded from proposals.

        Reputation scoring (Req 4):
            score = α × (reputation / 100) + β × normalised_wait
        """
        excluded     = set(suspended_ids or [])
        skipped_buyers: Dict[int, str] = {}

        # Stake per trade = ~10% of price (Req 6)
        stake_per_trade = max(1, int(price_gwei * STAKE_RATE))
        required_total  = price_gwei + stake_per_trade  # Req 6: price + stake

        # ── Affordability filter (Req 5, 6) ──────────────────────────────────
        # eligible_buyer_ids: set = set()
        # if buyer_balances_gwei is not None:
        #     for aid in buyer_demand:
        #         if aid in excluded:
        #             continue
        #         balance = buyer_balances_gwei.get(aid, 0)
        #         if balance < required_total:
        #             skipped_buyers[aid] = (
        #                 f"Insufficient balance: {balance} gwei < "
        #                 f"{required_total} gwei (price={price_gwei} + stake={stake_per_trade})"
        #             )
        #         else:
        #             eligible_buyer_ids.add(aid)
        # else:
        #     # No balance data provided; include all non-suspended buyers
        #     eligible_buyer_ids = {
        #         aid for aid in buyer_demand if aid not in excluded
        #     }

        eligible_buyer_ids: set = set()
        if buyer_balances_gwei is not None:
            for aid in buyer_demand:
                if aid not in excluded:
                    eligible_buyer_ids.add(aid)
        else:
            eligible_buyer_ids = {
                aid for aid in buyer_demand
                if aid not in excluded
            }

        # ── Build working snapshots ───────────────────────────────────────────
        sellers = [
            _PeerSnap(
                agent_id=aid,
                bus=seller_bus.get(aid, 0),
                reputation=seller_reputations.get(aid, 50),  # 0–100 (Req 4)
                wait=seller_waits.get(aid, 0),
                energy_mw=mw,
            )
            for aid, mw in seller_supply.items()
            if aid not in excluded and mw > self.min_trade_mw
        ]

        buyers = [
            _PeerSnap(
                agent_id=aid,
                bus=buyer_bus.get(aid, 0),
                reputation=buyer_reputations.get(aid, 50),   # 0–100 (Req 4)
                wait=buyer_waits.get(aid, 0),
                energy_mw=mw,
            )
            for aid, mw in buyer_demand.items()
            if aid in eligible_buyer_ids and mw > self.min_trade_mw
        ]

        if not sellers or not buyers:
            return MatchResult(
                proposals=[],
                unmatched_sellers=dict(seller_supply),
                unmatched_buyers=dict(buyer_demand),
                seller_scores={},
                buyer_scores={},
                total_matched_mw=0.0,
                match_rate=0.0,
                skipped_buyers=skipped_buyers,
            )

        # ── Score and sort ────────────────────────────────────────────────────
        self._score_peers(sellers)
        self._score_peers(buyers)
        sellers.sort(key=lambda p: p.score, reverse=True)
        buyers.sort(key=lambda p: p.score, reverse=True)

        seller_scores = {s.agent_id: round(s.score, 4) for s in sellers}
        buyer_scores  = {b.agent_id: round(b.score, 4) for b in buyers}

        # ── Greedy matching ───────────────────────────────────────────────────
        proposals: List[TradeProposal] = []
        buyer_idx = 0

        for seller in sellers:
            while buyer_idx < len(buyers) and seller.energy_mw > self.min_trade_mw:
                buyer = buyers[buyer_idx]

                if buyer.energy_mw <= self.min_trade_mw:
                    buyer_idx += 1
                    continue

                # traded_mw = min(seller.energy_mw, buyer.energy_mw)
                #
                # proposals.append(TradeProposal(
                #     proposal_id=uuid.uuid4().hex,
                #     seller_id=seller.agent_id,
                #     buyer_id=buyer.agent_id,
                #     seller_bus=seller.bus,
                #     buyer_bus=buyer.bus,
                #     energy_mw=round(traded_mw, 6),
                #     price_per_mwh=price_per_mwh,
                #     price_gwei=price_gwei,
                #     stake_gwei=stake_per_trade,
                #     seller_score=seller.score,
                #     buyer_score=buyer.score,
                # ))
                #
                # seller.energy_mw -= traded_mw
                # buyer.energy_mw  -= traded_mw

                traded_mw = min(seller.energy_mw, buyer.energy_mw)

                trade_price_gwei = max(
                    1,
                    int(price_gwei * traded_mw)
                )

                stake_per_trade = max(
                    1,
                    int(trade_price_gwei * STAKE_RATE)
                )

                required_total = (
                    trade_price_gwei +
                    stake_per_trade
                )

                if buyer_balances_gwei is not None:
                    balance = buyer_balances_gwei.get(
                        buyer.agent_id,
                        0,
                    )

                    if balance < required_total:
                        skipped_buyers[buyer.agent_id] = (
                            f"Insufficient balance: "
                            f"{balance} gwei < {required_total} gwei"
                        )
                        buyer_idx += 1
                        continue

                proposals.append(TradeProposal(
                    proposal_id=uuid.uuid4().hex,
                    seller_id=seller.agent_id,
                    buyer_id=buyer.agent_id,
                    seller_bus=seller.bus,
                    buyer_bus=buyer.bus,
                    energy_mw=round(traded_mw, 6),
                    price_per_mwh=price_per_mwh,
                    price_gwei=trade_price_gwei,
                    stake_gwei=stake_per_trade,
                    seller_score=seller.score,
                    buyer_score=buyer.score,
                ))

                seller.energy_mw -= traded_mw
                buyer.energy_mw -= traded_mw

                if buyer.energy_mw <= self.min_trade_mw:
                    buyer_idx += 1

        # ── Residuals ─────────────────────────────────────────────────────────
        unmatched_sellers = {
            s.agent_id: round(s.energy_mw, 6)
            for s in sellers
            if s.energy_mw > self.min_trade_mw
        }
        unmatched_buyers = {
            b.agent_id: round(b.energy_mw, 6)
            for b in buyers
            if b.energy_mw > self.min_trade_mw
        }
        # Also mark skipped buyers as unmatched
        for aid in skipped_buyers:
            if aid not in unmatched_buyers:
                unmatched_buyers[aid] = buyer_demand.get(aid, 0.0)

        total_matched = sum(p.energy_mw for p in proposals)
        total_demand  = sum(buyer_demand.values())
        match_rate    = total_matched / total_demand if total_demand > 0 else 0.0

        return MatchResult(
            proposals=proposals,
            unmatched_sellers=unmatched_sellers,
            unmatched_buyers=unmatched_buyers,
            seller_scores=seller_scores,
            buyer_scores=buyer_scores,
            total_matched_mw=round(total_matched, 6),
            match_rate=round(match_rate, 5),
            skipped_buyers=skipped_buyers,
        )

    # ── PREVIOUS implementation (commented out per Req 7, 13) ─────────────────
    #
    # def _match_legacy(
    #     self,
    #     seller_supply, buyer_demand, seller_bus, buyer_bus,
    #     seller_reputations, buyer_reputations,
    #     seller_waits, buyer_waits,
    #     price_per_mwh, suspended_ids=None,
    # ) -> MatchResult:
    #     """Original greedy matching without affordability checks."""
    #     excluded = set(suspended_ids or [])
    #
    #     sellers = [
    #         _PeerSnap(agent_id=aid, bus=seller_bus.get(aid, 0),
    #                   reputation=seller_reputations.get(aid, 0.75),
    #                   wait=seller_waits.get(aid, 0), energy_mw=mw)
    #         for aid, mw in seller_supply.items()
    #         if aid not in excluded and mw > self.min_trade_mw
    #     ]
    #     buyers = [
    #         _PeerSnap(agent_id=aid, bus=buyer_bus.get(aid, 0),
    #                   reputation=buyer_reputations.get(aid, 0.75),
    #                   wait=buyer_waits.get(aid, 0), energy_mw=mw)
    #         for aid, mw in buyer_demand.items()
    #         if aid not in excluded and mw > self.min_trade_mw
    #     ]
    #
    #     if not sellers or not buyers:
    #         return MatchResult(proposals=[], unmatched_sellers=dict(seller_supply),
    #                            unmatched_buyers=dict(buyer_demand),
    #                            seller_scores={}, buyer_scores={},
    #                            total_matched_mw=0.0, match_rate=0.0)
    #
    #     self._score_peers(sellers)
    #     self._score_peers(buyers)
    #     sellers.sort(key=lambda p: p.score, reverse=True)
    #     buyers.sort(key=lambda p: p.score, reverse=True)
    #
    #     seller_scores = {s.agent_id: round(s.score, 4) for s in sellers}
    #     buyer_scores  = {b.agent_id: round(b.score, 4) for b in buyers}
    #
    #     proposals = []
    #     buyer_idx = 0
    #     for seller in sellers:
    #         while buyer_idx < len(buyers) and seller.energy_mw > self.min_trade_mw:
    #             buyer = buyers[buyer_idx]
    #             if buyer.energy_mw <= self.min_trade_mw:
    #                 buyer_idx += 1
    #                 continue
    #             traded_mw = min(seller.energy_mw, buyer.energy_mw)
    #             proposals.append(TradeProposal(
    #                 proposal_id=uuid.uuid4().hex,
    #                 seller_id=seller.agent_id, buyer_id=buyer.agent_id,
    #                 seller_bus=seller.bus, buyer_bus=buyer.bus,
    #                 energy_mw=round(traded_mw, 6),
    #                 price_per_mwh=price_per_mwh,
    #                 price_gwei=0, stake_gwei=0,   # not computed in legacy
    #                 seller_score=seller.score, buyer_score=buyer.score,
    #             ))
    #             seller.energy_mw -= traded_mw
    #             buyer.energy_mw  -= traded_mw
    #             if buyer.energy_mw <= self.min_trade_mw:
    #                 buyer_idx += 1
    #
    #     unmatched_sellers = {s.agent_id: round(s.energy_mw, 6) for s in sellers
    #                          if s.energy_mw > self.min_trade_mw}
    #     unmatched_buyers  = {b.agent_id: round(b.energy_mw, 6) for b in buyers
    #                          if b.energy_mw > self.min_trade_mw}
    #     total_matched = sum(p.energy_mw for p in proposals)
    #     total_demand  = sum(buyer_demand.values())
    #     match_rate    = total_matched / total_demand if total_demand > 0 else 0.0
    #     return MatchResult(proposals=proposals,
    #                        unmatched_sellers=unmatched_sellers,
    #                        unmatched_buyers=unmatched_buyers,
    #                        seller_scores=seller_scores, buyer_scores=buyer_scores,
    #                        total_matched_mw=round(total_matched, 6),
    #                        match_rate=round(match_rate, 5))
    #
    # ── end of legacy implementation ──────────────────────────────────────────

    # ── internal ──────────────────────────────────────────────────────────────

    def _score_peers(self, peers: List[_PeerSnap]) -> None:
        """
        Compute and set .score for each peer in-place.

        Score = α × (reputation / REP_SCALE) + β × normalised_wait

        Reputation is now 0–100 (Req 4); divided by REP_SCALE=100 to keep
        the score in [0.0, 1.0] so α/β weights remain intuitive.

        PREVIOUS (0–1 scale, now commented out):
            # peer.score = self.alpha * peer.reputation + self.beta * norm_wait
        """
        if not peers:
            return

        max_wait = max(p.wait for p in peers)

        for peer in peers:
            norm_wait  = peer.wait / max_wait if max_wait > 0 else 0.0
            # Updated for 0–100 reputation scale (Req 4)
            norm_rep   = peer.reputation / REP_SCALE
            peer.score = self.alpha * norm_rep + self.beta * norm_wait

    # ── convenience ───────────────────────────────────────────────────────────

    def starvation_report(
        self,
        buyer_waits: Dict[int, int],
        threshold_intervals: int = 6,
    ) -> List[Tuple[int, int]]:
        """Return (agent_id, wait_intervals) for starved buyers."""
        return [
            (aid, w)
            for aid, w in buyer_waits.items()
            if w >= threshold_intervals
        ]
