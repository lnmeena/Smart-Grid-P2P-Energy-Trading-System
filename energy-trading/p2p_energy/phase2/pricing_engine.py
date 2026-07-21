from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

REP_BASELINE = 50
REP_SCALE    = 100

PRICE_TO_GWEI = 1_000_000

@dataclass(frozen=True)
class PricingResult:
    raw_sdr:            float
    effective_sdr:      float
    base_price_gwei:    int
    final_price_gwei:   int
    pricing_factor:     float
    export_price:       float
    import_price:       float
    price_floor_gwei:   int
    price_ceiling_gwei: int
    market_state:       str
    reputation_premium: float

    @property
    def base_price(self) -> int:
        return self.base_price_gwei

    def __repr__(self) -> str:
        return (
            f"PricingResult(state={self.market_state}, "
            f"SDR={self.effective_sdr:.3f}, "
            f"base={self.base_price_gwei}gwei, "
            f"final={self.final_price_gwei}gwei, "
            f"factor={self.pricing_factor:.3f})"
        )


class PricingEngine:
    """
    Stateless pricing engine.  Call compute() each interval.

    Parameters
    ----------
    gamma           : reputation sensitivity [0, 1].  0 = pure SDR pricing.
    sdr_cap         : SDR above which price hits export floor (default 2.0).
    sdr_floor       : SDR below which price hits import ceiling (default 0.5).
    pricing_factor  : Multiplier applied to base_price → final_price (Req 2).
                      Default 1.0.  Must be ≥ 0.
    """

    def __init__(
        self,
        gamma: float = 0.10,
        sdr_cap: float = 2.0,
        sdr_floor: float = 0.5,
        pricing_factor: float = 1.0,
    ) -> None:
        if not 0.0 <= gamma <= 1.0:
            raise ValueError(f"gamma must be in [0, 1], got {gamma}")
        if pricing_factor < 0:
            raise ValueError(f"pricing_factor must be >= 0, got {pricing_factor}")
        self.gamma          = gamma
        self.sdr_cap        = sdr_cap
        self.sdr_floor      = sdr_floor
        self.pricing_factor = pricing_factor

    # ── public ────────────────────────────────────────────────────────────────

    def compute(
        self,
        total_supply_mw: float,
        consumer_demand: Dict[int, float],
        export_price: float,
        import_price: float,
        consumer_reputations: Optional[Dict[int, float]] = None,
        pricing_factor: Optional[float] = None,
    ) -> PricingResult:
        """
        Compute market-clearing price for this interval.

        Parameters
        ----------
        total_supply_mw       : total available production (MW).
        consumer_demand       : {agent_id → demand_mw}.
        export_price          : reference export price ($/MWh).
        import_price          : reference import price ($/MWh).
        consumer_reputations  : {agent_id → reputation [0–100]} (Req 4).
                                If None or gamma=0, plain SDR is used.
        pricing_factor        : Override per-interval multiplier (Req 2).
                                If None, uses self.pricing_factor.

        Returns
        -------
        PricingResult with gwei-denominated prices (Req 2).
        """
        factor = pricing_factor if pricing_factor is not None else self.pricing_factor
        if factor < 0:
            raise ValueError(f"pricing_factor must be >= 0, got {factor}")

        total_demand = sum(consumer_demand.values())

        raw_sdr = (
            total_supply_mw / total_demand if total_demand > 0 else float("inf")
        )

        effective_sdr, rep_premium = self._effective_sdr(
            total_supply_mw, consumer_demand, consumer_reputations, raw_sdr
        )

        # Base price ($/MWh float) from SDR
        base_price_float = self._sdr_to_price(effective_sdr, export_price, import_price)

        # Convert to gwei integer; clamp to non-negative (Req 2)
        base_price_gwei  = max(0, int(round(base_price_float * PRICE_TO_GWEI)))

        # final_price = base_price × pricing_factor (Req 2)
        final_price_gwei = max(0, int(round(base_price_gwei * factor)))

        price_floor_gwei   = max(0, int(round(export_price * PRICE_TO_GWEI)))
        price_ceiling_gwei = max(0, int(round(import_price * PRICE_TO_GWEI)))

        if effective_sdr > 1.02:
            state = "oversupply"
        elif effective_sdr < 0.98:
            state = "undersupply"
        else:
            state = "balanced"

        return PricingResult(
            raw_sdr=round(raw_sdr, 5),
            effective_sdr=round(effective_sdr, 5),
            base_price_gwei=base_price_gwei,
            final_price_gwei=final_price_gwei,
            pricing_factor=factor,
            export_price=export_price,
            import_price=import_price,
            price_floor_gwei=price_floor_gwei,
            price_ceiling_gwei=price_ceiling_gwei,
            market_state=state,
            reputation_premium=round(rep_premium, 6),
        )

    # ── internal ──────────────────────────────────────────────────────────────

    def _effective_sdr(
        self,
        supply: float,
        demand: Dict[int, float],
        reputations: Optional[Dict[int, float]],
        raw_sdr: float,
    ):
        """
        Compute reputation-weighted SDR.

        Reputation is on 0–100 scale (Req 4).
        Weight formula (updated from 0–1 to 0–100 scale):
            weight = 1 + γ × (reputation − REP_BASELINE) / REP_SCALE

        PREVIOUS (0–1 scale, now commented out):
            # weight = 1.0 + self.gamma * (rep - REP_BASELINE)
            # where REP_BASELINE was 0.75
        """
        if self.gamma == 0.0 or not reputations or not demand:
            return raw_sdr, 0.0

        weighted_demand = 0.0
        for agent_id, dmw in demand.items():
            rep    = reputations.get(agent_id, REP_BASELINE)
            # Normalise rep shift to [−0.5, +0.5] relative to baseline
            weight = 1.0 + self.gamma * (rep - REP_BASELINE) / REP_SCALE
            weighted_demand += dmw * weight

        if weighted_demand <= 0:
            return raw_sdr, 0.0

        eff_sdr = supply / weighted_demand
        premium = (raw_sdr - eff_sdr) / raw_sdr if raw_sdr > 0 else 0.0
        return eff_sdr, premium

    def _sdr_to_price(
        self,
        sdr: float,
        export_price: float,
        import_price: float,
    ) -> float:
        spread = import_price - export_price
        if spread <= 0:
            return (export_price + import_price) / 2.0

        sdr_clamped = max(self.sdr_floor, min(self.sdr_cap, sdr))
        t = (sdr_clamped - self.sdr_floor) / (self.sdr_cap - self.sdr_floor)
        price = import_price - t * spread
        return max(export_price, min(import_price, price))
