from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

import pandas as pd

from .csv_loader import load_agent_data

class AgentType(str, Enum):
    CONSUMER    = "Consumer"
    PRODUCER    = "Producer"
    GRID        = "Grid"

class EnergySource(str, Enum):
    WIND        = "Wind"
    SOLAR       = "Solar"
    GAS         = "Gas"
    COAL        = "Coal"
    GRID        = "Grid"
    NA          = "-"

class Community(int, Enum):
    ONE         = 1
    TWO         = 2
    THREE       = 3
    GRID        = -1

REP_INIT            = 50     # matches Solidity REP_INITIAL = 50
REP_MIN             = 0
REP_MAX             = 100
REP_DELTA_SUCCESS   = +5     # matches Solidity REP_DELTA_SUCCESS   = 5
REP_DELTA_TIMEOUT   = -2     # proportional to old -0.02 on 100-scale
REP_DELTA_FAIL_DLVR = -10    # matches Solidity REP_DELTA_FAILURE   = 10
REP_DELTA_FRAUD     = -20    # matches Solidity REP_DELTA_FRAUD     = 20
REP_SUSPENSION_THRESHOLD = 10  # matches Solidity REP_SUSPENSION_THRESHOLD = 10

GWEI_PER_TOKEN      = 1_000_000_000          # 1 ETH = 1e9 gwei
INITIAL_WALLET_GWEI = 1_000_000_000          # 1 ETH in gwei per peer (Req 2)
STAKE_RATE          = 0.10                   # stake = 10 % of trade price (Req 6)

CONTRACT_WALLET_MAP: Dict[int, str] = {
    1:  "0x1000000000000000000000000000000000000001",
    2:  "0x1000000000000000000000000000000000000002",
    3:  "0x1000000000000000000000000000000000000003",
    4:  "0x1000000000000000000000000000000000000004",
    5:  "0x1000000000000000000000000000000000000005",
    6:  "0x1000000000000000000000000000000000000006",
    7:  "0x1000000000000000000000000000000000000007",
    8:  "0x1000000000000000000000000000000000000008",
    9:  "0x1000000000000000000000000000000000000009",
    10: "0x100000000000000000000000000000000000000a",
    11: "0x100000000000000000000000000000000000000b",
    12: "0x100000000000000000000000000000000000000c",
    13: "0x100000000000000000000000000000000000000d",
    14: "0x100000000000000000000000000000000000000e",
    15: "0x100000000000000000000000000000000000000f",
    16: "0x1000000000000000000000000000000000000010",
    17: "0x1000000000000000000000000000000000000011",
    18: "0x1000000000000000000000000000000000000012",
    19: "0x1000000000000000000000000000000000000013",
    # Agent 20 is the grid node; use a dedicated address
    20: "0x1000000000000000000000000000000000000014",
}


# ── trade state (mirrors Solidity TradeStatus enum, Req 11) ───────────────────

class TradeState(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"  # 0
    ACTIVE      = "ACTIVE"       # 1
    COMPLETED   = "COMPLETED"    # 2
    FAILURE     = "FAILURE"      # 3
    FRAUD       = "FRAUD"        # 4
    CANCELLED   = "CANCELLED"    # 5

    @classmethod
    def from_uint8(cls, value: int) -> "TradeState":
        mapping = {
            0: cls.IN_PROGRESS,
            1: cls.ACTIVE,
            2: cls.COMPLETED,
            3: cls.FAILURE,
            4: cls.FRAUD,
            5: cls.CANCELLED,
        }
        return mapping[value]


# ── trade-history record ──────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    """Lightweight record appended to a peer's history after every trade."""
    trade_id:       str
    timestamp:      datetime
    role:           str             # "seller" or "buyer"
    counterpart_id: int
    energy_mw:      float
    price_gwei:     int             # price in gwei (Req 2)
    state:          TradeState      # final settled state (Req 11)

    @property
    def value_gwei(self) -> int:
        return self.price_gwei


# ── core Peer dataclass ───────────────────────────────────────────────────────

@dataclass
class Peer:

    # — from dataset —
    agent_id:       int
    agent_type:     AgentType
    energy_source:  EnergySource
    bus:            int
    community:      Community
    pmin:           float
    pmax:           float
    a_cost:         float
    b_cost:         float

    # — runtime state —
    wallet_address:         str   = field(default="")
    wallet_balance:         int   = field(default=INITIAL_WALLET_GWEI)   # gwei (Req 2)
    stake_locked:           int   = field(default=0)                     # gwei (Req 2)
    reputation:             int   = field(default=REP_INIT)              # 0–100 (Req 4)
    suspended:              bool  = field(default=False)
    intervals_since_trade:  int   = field(default=0)
    trade_history:          List[TradeRecord] = field(default_factory=list)

    # Optional back-reference to ContractAdapter (set by build_registry)
    _contract_adapter:      object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        # Assign wallet address from deterministic map (Req 1)
        if not self.wallet_address:
            mapped = CONTRACT_WALLET_MAP.get(self.agent_id)
            if mapped:
                self.wallet_address = mapped
            else:
                # Fallback placeholder (Req 1: use placeholder if none exists)
                self.wallet_address = "0xDEAD" + format(self.agent_id, "036x")

    # — convenience —

    @property
    def is_consumer(self) -> bool:
        return self.agent_type == AgentType.CONSUMER

    @property
    def is_producer(self) -> bool:
        return self.agent_type == AgentType.PRODUCER

    @property
    def is_grid(self) -> bool:
        return self.agent_type == AgentType.GRID

    @property
    def available_balance(self) -> int:
        """Spendable gwei (excludes locked stake)."""
        return self.wallet_balance - self.stake_locked

    # ── reputation (Req 3, 4) ─────────────────────────────────────────────────

    def update_reputation(self, delta: int) -> None:
        self.reputation = max(REP_MIN, min(REP_MAX, self.reputation + delta))
        if self.reputation <= REP_SUSPENSION_THRESHOLD:
            self.suspended = True

    def refresh_reputation(self, contract_value: int) -> None:
        self.reputation = max(REP_MIN, min(REP_MAX, int(contract_value)))
        self.suspended  = self.reputation <= REP_SUSPENSION_THRESHOLD

    # ── scoring helper (used by matching engine) ──────────────────────────────

    @property
    def reputation_normalised(self) -> float:
        return self.reputation / REP_MAX

    # ── stake management (Req 6) ──────────────────────────────────────────────

    def lock_stake(self, trade_price_gwei: int) -> int:
        required = max(1, int(trade_price_gwei * STAKE_RATE))
        if self.available_balance < required:
            return 0
        self.stake_locked += required
        return required

    def release_stake(self, amount_gwei: int) -> None:
        self.stake_locked = max(0, self.stake_locked - amount_gwei)

    def slash_stake(self, amount_gwei: int) -> int:
        slash = min(amount_gwei, self.stake_locked)
        self.stake_locked    -= slash
        self.wallet_balance  -= slash
        return slash

    # ── payment ───────────────────────────────────────────────────────────────

    def debit(self, amount_gwei: int) -> bool:
        if self.available_balance < amount_gwei:
            return False
        self.wallet_balance -= amount_gwei
        return True

    def credit(self, amount_gwei: int) -> None:
        self.wallet_balance += amount_gwei

    # ── trade history ─────────────────────────────────────────────────────────

    def record_trade(self, record: TradeRecord) -> None:
        self.trade_history.append(record)

        if record.state == TradeState.COMPLETED:
            self.intervals_since_trade = 0

    def tick_no_trade(self) -> None:
        self.intervals_since_trade += 1

    # ── display ───────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        status = "SUSPENDED" if self.suspended else f"rep={self.reputation}/100"
        return (
            f"Peer(id={self.agent_id}, {self.agent_type.value}, "
            f"bus={self.bus}, {status}, "
            f"balance={self.wallet_balance}gwei, "
            f"addr={self.wallet_address[:10]}...)"
        )


# ── registry ──────────────────────────────────────────────────────────────────

class PeerRegistry:
    """Dict-like store of all Peer objects."""

    def __init__(self, peers: List[Peer]) -> None:
        self._store: Dict[int, Peer] = {p.agent_id: p for p in peers}

    def __getitem__(self, agent_id: int) -> Peer:
        return self._store[agent_id]

    def __contains__(self, agent_id: int) -> bool:
        return agent_id in self._store

    def __iter__(self):
        return iter(self._store.values())

    def __len__(self) -> int:
        return len(self._store)

    def get(self, agent_id: int) -> Optional[Peer]:
        return self._store.get(agent_id)

    @property
    def consumers(self) -> List[Peer]:
        return [p for p in self._store.values() if p.is_consumer]

    @property
    def producers(self) -> List[Peer]:
        return [p for p in self._store.values() if p.is_producer]

    @property
    def grid(self) -> Optional[Peer]:
        candidates = [p for p in self._store.values() if p.is_grid]
        return candidates[0] if candidates else None

    @property
    def active(self) -> List[Peer]:
        return [p for p in self._store.values() if not p.suspended]

    def active_consumers(self) -> List[Peer]:
        return [p for p in self.consumers if not p.suspended]

    def active_producers(self) -> List[Peer]:
        return [p for p in self.producers if not p.suspended]

    def tick_all(self, trading_agent_ids: List[int]) -> None:
        for peer in self._store.values():
            if peer.agent_id not in trading_agent_ids:
                peer.tick_no_trade()

    def summary(self) -> pd.DataFrame:
        rows = []
        for p in self._store.values():
            rows.append({
                "agent_id":       p.agent_id,
                "type":           p.agent_type.value,
                "bus":            p.bus,
                "reputation":     p.reputation,          # 0–100 (Req 4)
                "balance_gwei":   p.wallet_balance,      # gwei  (Req 2)
                "stake_locked":   p.stake_locked,
                "suspended":      p.suspended,
                "wait":           p.intervals_since_trade,
                "trades":         len(p.trade_history),
                "wallet_address": p.wallet_address,      # Req 1
            })
        return pd.DataFrame(rows).sort_values("agent_id").reset_index(drop=True)


# ── factory ───────────────────────────────────────────────────────────────────

def _parse_community(value) -> Community:
    try:
        return Community(int(value))
    except (ValueError, TypeError):
        return Community.GRID


def _parse_energy_source(src: str) -> EnergySource:
    src = str(src).strip()
    try:
        return EnergySource(src)
    except ValueError:
        return EnergySource.NA


def build_registry(
    data_dir: Optional[str] = None,
    contract_adapter=None,
) -> PeerRegistry:
    agents = load_agent_data()
    peers: List[Peer] = []

    for _, row in agents.iterrows():
        agent_id   = int(row["Agent"])
        agent_type = AgentType(str(row["Type"]).strip())
        energy_src = _parse_energy_source(row["Energy"])
        bus        = int(row["Bus"])
        community  = _parse_community(row["Community"])

        pmin   = float(row["Pmin"])   if pd.notna(row["Pmin"])   else 0.0
        pmax   = float(row["Pmax"])   if pd.notna(row["Pmax"])   else 0.0
        a_cost = float(row["a_cost"]) if pd.notna(row["a_cost"]) else 0.0
        b_cost = float(row["b_cost"]) if pd.notna(row["b_cost"]) else 0.0

        peer = Peer(
            agent_id=agent_id,
            agent_type=agent_type,
            energy_source=energy_src,
            bus=bus,
            community=community,
            pmin=pmin,
            pmax=pmax,
            a_cost=a_cost,
            b_cost=b_cost,
        )
        peers.append(peer)

    registry = PeerRegistry(peers)

    if contract_adapter:

        print(
            "[build_registry] "
            "Fetching on-chain reputations..."
        )

        for peer in registry:

            try:
                rep = (
                    contract_adapter
                    .fetch_reputation(
                        peer.agent_id
                    )
                )

                peer.refresh_reputation(
                    rep
                )

            except Exception as exc:

                print(
                    f"[WARN] "
                    f"agent "
                    f"{peer.agent_id}: "
                    f"{exc}"
                )

        print(
            "[build_registry] "
            "Registry ready."
        )

    return registry
