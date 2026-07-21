from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .matching_engine import TradeProposal

# import gridgraph lazily to allow testing without the full package
try:
    from ..grid_graph import GridGraph
except ImportError:
    GridGraph = None   # type: ignore

# changed this one
# rep_baseline    = 0.75
REP_BASELINE    = 50
REP_SCALE       = 100
REP_ADV_CAP     = 1.0    # reputation advantage multiplier is capped at 1 + rep_adv_cap


# ── per-proposal outcome ──────────────────────────────────────────────────────

@dataclass
class CongestionResult:
    """
    outcome of the congestion check for one trade proposal.

    fields
    ------
    proposal        : original TradeProposal
    approved_mw     : energy approved after congestion checks (≤ proposal.energy_mw)
    path            : bus path used for this trade (empty for same-bus trades)
    status          : "approved" | "reduced" | "rejected" | "same_bus"
    bottleneck_edge : (u, v) of the most congested edge, or None
    utilisation_pct : utilisation % of bottleneck edge after this trade
    note            : human-readable reason
    """
    proposal:           TradeProposal
    approved_mw:        float
    path:               List[int]
    status:             str         # approved | reduced | rejected | same_bus
    bottleneck_edge:    Optional[Tuple[int, int]] = None
    utilisation_pct:    float = 0.0
    note:               str = ""

    @property
    def is_approved(self) -> bool:
        return self.status in ("approved", "same_bus", "reduced") and self.approved_mw > 1e-6

    @property
    def reduction_mw(self) -> float:
        return max(0.0, self.proposal.energy_mw - self.approved_mw)

    def __repr__(self) -> str:
        return (
            f"CongestionResult({self.status}, "
            f"id={self.proposal.proposal_id[:8]}, "
            f"approved={self.approved_mw:.3f}MW, "
            f"path={self.path})"
        )


# ── interval report ───────────────────────────────────────────────────────────

@dataclass
class CongestionReport:
    """
    Full congestion output for one trading interval.

    Fields
    ------
    results             : CongestionResult per proposal (same order as input)
    approved_count      : proposals fully approved
    reduced_count       : proposals partially reduced
    rejected_count      : proposals fully rejected (no capacity at all)
    same_bus_count      : proposals bypassing grid (same-bus trades)
    total_approved_mw   : sum of all approved_mw
    congested_lines     : (u, v) edges that hit capacity this interval
    """
    results:            List[CongestionResult]
    approved_count:     int
    reduced_count:      int
    rejected_count:     int
    same_bus_count:     int
    total_approved_mw:  float
    congested_lines:    List[Tuple[int, int]]

    def __repr__(self) -> str:
        return (
            f"CongestionReport("
            f"approved={self.approved_count}, "
            f"reduced={self.reduced_count}, "
            f"rejected={self.rejected_count}, "
            f"same_bus={self.same_bus_count}, "
            f"total_mw={self.total_approved_mw:.2f})"
        )

    def approved_proposals(self) -> List[CongestionResult]:
        return [r for r in self.results if r.is_approved]


# ── congestion engine ─────────────────────────────────────────────────────────

class CongestionEngine:
    """
    Grid-aware congestion checker and reallocator.

    Parameters
    ----------
    grid            : GridGraph instance (built from NetworkData.csv)
    rep_advantage   : δ — max reputation advantage multiplier (default 0.20)
                      A rep-1.0 peer gets at most 1 + 0.20×(1.0-0.75) = 1.05×
                      share vs a neutral-rep peer during reallocation.
    min_trade_mw    : trades below this threshold after reallocation are rejected
    """

    def __init__(
        self,
        grid,                          # GridGraph
        rep_advantage: float = 0.20,
        min_trade_mw:  float = 0.001,
    ) -> None:
        self._grid         = grid
        self.rep_advantage = rep_advantage
        self.min_trade_mw  = min_trade_mw

    # ── public ────────────────────────────────────────────────────────────────

    def check(
        self,
        proposals: List[TradeProposal],
        seller_reputations: Dict[int, float],
        buyer_reputations:  Dict[int, float],
    ) -> CongestionReport:
        """
        Run congestion checks for all proposals this interval.

        The GridGraph's flows are reset at the start of the call,
        then incrementally updated as proposals are approved.

        Parameters
        ----------
        proposals           : output from MatchingEngine.match()
        seller_reputations  : {agent_id → reputation}
        buyer_reputations   : {agent_id → reputation}

        Returns
        -------
        CongestionReport
        """
        self._grid.reset_flows()

        # Split into same-bus (free) and cross-bus (needs path)
        same_bus:   List[TradeProposal] = []
        cross_bus:  List[TradeProposal] = []

        for p in proposals:
            if p.seller_bus == p.buyer_bus:
                same_bus.append(p)
            else:
                cross_bus.append(p)

        results: List[CongestionResult] = []

        # Same-bus trades are always approved
        for p in same_bus:
            results.append(CongestionResult(
                proposal=p,
                approved_mw=p.energy_mw,
                path=[],
                status="same_bus",
                note="Same bus — no grid path required",
            ))

        # Cross-bus trades go through two-pass allocation
        cross_results = self._allocate_cross_bus(
            cross_bus, seller_reputations, buyer_reputations
        )
        results.extend(cross_results)

        # Re-order to match input order
        order = {p.proposal_id: i for i, p in enumerate(proposals)}
        results.sort(key=lambda r: order.get(r.proposal.proposal_id, 9999))

        # Summary stats
        approved_count  = sum(1 for r in results if r.status == "approved")
        reduced_count   = sum(1 for r in results if r.status == "reduced")
        rejected_count  = sum(1 for r in results if r.status == "rejected")
        same_bus_count  = sum(1 for r in results if r.status == "same_bus")
        total_mw        = sum(r.approved_mw for r in results)

        return CongestionReport(
            results=results,
            approved_count=approved_count,
            reduced_count=reduced_count,
            rejected_count=rejected_count,
            same_bus_count=same_bus_count,
            total_approved_mw=round(total_mw, 6),
            congested_lines=self._grid.congested_edges(),
        )

    # ── internal: two-pass cross-bus allocation ───────────────────────────────

    def _allocate_cross_bus(
        self,
        proposals: List[TradeProposal],
        seller_reps: Dict[int, float],
        buyer_reps:  Dict[int, float],
    ) -> List[CongestionResult]:
        """
        Pass 1: try to commit each proposal's full energy.
        Pass 2: reallocate congested proposals pro-rata with rep bonus.
        """
        if not proposals:
            return []

        # ── Pass 1: find paths, attempt full allocation ───────────────────────
        working: List[_WorkItem] = []

        for p in proposals:
            path = self._grid.find_path(p.seller_bus, p.buyer_bus)

            if not path:
                working.append(_WorkItem(proposal=p, path=[], status="rejected",
                                         note="No path between buses"))
                continue

            rep = self._avg_reputation(p, seller_reps, buyer_reps)

            if self._grid.is_path_feasible(path, p.energy_mw):
                # Full energy fits — commit immediately
                self._grid.add_flow(path, p.energy_mw)
                working.append(_WorkItem(
                    proposal=p, path=path,
                    approved_mw=p.energy_mw,
                    status="approved",
                    reputation=rep,
                    note="Full capacity available",
                ))
            else:
                # Partially or fully congested — defer to Pass 2
                working.append(_WorkItem(
                    proposal=p, path=path,
                    status="pending",
                    reputation=rep,
                    note="Congestion detected — queued for reallocation",
                ))

        # ── Pass 2: reallocate pending proposals ──────────────────────────────
        pending = [w for w in working if w.status == "pending"]

        if pending:
            self._reallocate(pending)
            # Commit approved flows from reallocation
            for w in pending:
                if w.approved_mw > self.min_trade_mw:
                    self._grid.add_flow(w.path, w.approved_mw)

        # Build results
        results: List[CongestionResult] = []
        for w in working:
            bottleneck, util_pct = self._bottleneck(w.path) if w.path else (None, 0.0)
            results.append(CongestionResult(
                proposal=w.proposal,
                approved_mw=round(w.approved_mw, 6),
                path=w.path,
                status=w.status,
                bottleneck_edge=bottleneck,
                utilisation_pct=round(util_pct * 100, 2),
                note=w.note,
            ))

        return results

    # ── internal: pro-rata reallocation ──────────────────────────────────────

    def _reallocate(self, pending: List["_WorkItem"]) -> None:
        """
        For each pending item, determine how much capacity is actually available
        on its bottleneck edge and apply a reputation-weighted pro-rata split
        among all proposals that share that bottleneck.

        This handles the case where multiple trades compete for the same line.
        """
        # Group pending proposals by their bottleneck edge
        edge_groups: Dict[Tuple[int, int], List[_WorkItem]] = {}
        for w in pending:
            bottleneck = self._path_bottleneck_edge(w.path)
            if bottleneck is None:
                # No constraint (e.g. CAP=0 unconstrained line)
                w.approved_mw = w.proposal.energy_mw
                w.status = "approved"
                w.note = "Unconstrained path — full approval"
                continue
            edge_groups.setdefault(bottleneck, []).append(w)

        for edge, group in edge_groups.items():
            avail = self._grid.available_capacity(*edge)

            if avail <= 0:
                # Zero capacity left — reject all in this group
                for w in group:
                    w.approved_mw = 0.0
                    w.status = "rejected"
                    w.note = f"No remaining capacity on line {edge}"
                continue

            # Reputation-weighted pro-rata shares
            raw_total   = sum(w.proposal.energy_mw for w in group)
            # Changed this one as well
            rep_weights  = [
                # 1.0 + self.rep_advantage * (w.reputation - REP_BASELINE)
                1.0 + self.rep_advantage * ((w.reputation - REP_BASELINE)/REP_SCALE)
                for w in group
            ]
            # Cap each weight at (1 + REP_ADV_CAP) so no one gets >2× share
            rep_weights = [min(w, 1.0 + REP_ADV_CAP) for w in rep_weights]
            weight_sum   = sum(rep_weights)

            for w, rw in zip(group, rep_weights):
                # Share of available capacity proportional to weighted demand
                share      = (w.proposal.energy_mw / raw_total) * (rw / (weight_sum / len(group)))
                alloc      = min(w.proposal.energy_mw, avail * share)
                alloc      = max(0.0, alloc)

                if alloc < self.min_trade_mw:
                    w.approved_mw = 0.0
                    w.status = "rejected"
                    w.note = f"Reallocation too small (<{self.min_trade_mw} MW)"
                else:
                    w.approved_mw = alloc
                    w.status = "reduced" if alloc < w.proposal.energy_mw - 1e-9 else "approved"
                    w.note = (
                        f"Reallocated: {alloc:.4f} MW of {w.proposal.energy_mw:.4f} MW requested "
                        f"(rep={w.reputation:.2f}, edge={edge})"
                    )

    # ── internal: helpers ─────────────────────────────────────────────────────

    def _avg_reputation(
        self,
        proposal: TradeProposal,
        seller_reps: Dict[int, float],
        buyer_reps:  Dict[int, float],
    ) -> float:
        s = seller_reps.get(proposal.seller_id, REP_BASELINE)
        b = buyer_reps.get(proposal.buyer_id,   REP_BASELINE)
        return (s + b) / 2.0

    def _path_bottleneck_edge(
        self, path: List[int]
    ) -> Optional[Tuple[int, int]]:
        """Return the edge in path with least available capacity (the bottleneck)."""
        if len(path) < 2:
            return None
        min_avail = float("inf")
        bottleneck = None
        for i in range(len(path) - 1):
            avail = self._grid.available_capacity(path[i], path[i + 1])
            if avail < min_avail:
                min_avail = avail
                bottleneck = (
                    min(path[i], path[i + 1]),
                    max(path[i], path[i + 1]),
                )
        return bottleneck

    def _bottleneck(self, path: List[int]) -> Tuple[Optional[Tuple[int, int]], float]:
        """Return (bottleneck_edge, utilisation_ratio) for reporting."""
        if len(path) < 2:
            return None, 0.0
        max_util = 0.0
        bottleneck = None
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            util = self._grid.utilization(u, v)
            if util > max_util:
                max_util = util
                bottleneck = (min(u, v), max(u, v))
        return bottleneck, max_util


# ── internal work item ────────────────────────────────────────────────────────

@dataclass
class _WorkItem:
    """Mutable scratch object used during allocation passes."""
    proposal:    TradeProposal
    path:        List[int]
    status:      str = "pending"        # pending | approved | reduced | rejected
    approved_mw: float = 0.0
    reputation:  float = REP_BASELINE
    note:        str = ""
