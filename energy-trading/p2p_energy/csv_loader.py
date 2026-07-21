from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

# ── paths ─────────────────────────────────────────────────────────────────────
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

_FILES = {
    "agent":       "AgentData.csv",
    "network":     "NetworkData.csv",
    "load_max":    "LoadMaxPower.csv",
    "load_min":    "LoadMinPower.csv",
    "market":      "MarketPrice.csv",
    "renewable":   "RenewableProduction.csv",
}


def _path(key: str) -> str:
    return os.path.join(_DATA_DIR, _FILES[key])


# ── raw loaders ───────────────────────────────────────────────────────────────

def load_agent_data() -> pd.DataFrame:
    """
    Returns AgentData with typed columns.

    Columns: Agent, Type, Energy, Bus, Community, Pmin, Pmax, a_cost, b_cost
    Agent IDs: 1-11 = consumers, 12-19 = producers, 20 = grid
    """
    df = pd.read_csv(_path("agent"))
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={
        "a ($/MW^2)": "a_cost",
        "b ($/MW)":   "b_cost",
    })
    df["Agent"] = df["Agent"].astype(int)
    df["Bus"]   = df["Bus"].astype(int)
    for col in ("Pmin", "Pmax", "a_cost", "b_cost"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_network_data() -> pd.DataFrame:
    """
    Returns NetworkData with typed columns.

    Columns: Line, From, To, R, X, B, CAP, Tap_setting
    CAP is in MW.
    """
    df = pd.read_csv(_path("network"))
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={"Tap setting": "Tap_setting"})
    for col in ("Line", "From", "To"):
        df[col] = df[col].astype(int)
    for col in ("R", "X", "B", "CAP", "Tap_setting"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _parse_timeseries(filepath: str) -> pd.DataFrame:
    """Parse a CSV with 'Date' + 'Time' columns into a datetime-indexed DataFrame."""
    df = pd.read_csv(filepath)
    df.columns = [c.strip() for c in df.columns]

    # Combine date and time into a single datetime index
    date_col = df.columns[0]   # "Date (month/day/year)"
    time_col = df.columns[1]   # "Time"
    df["timestamp"] = pd.to_datetime(
        df[date_col].str.strip() + " " + df[time_col].str.strip(),
        format="%m/%d/%Y %H:%M:%S",
    )
    df = df.drop(columns=[date_col, time_col])
    df = df.set_index("timestamp").sort_index()

    # All remaining columns are numeric
    df = df.apply(pd.to_numeric, errors="coerce")
    return df


def load_load_max() -> pd.DataFrame:
    """
    Max load power per consumer agent per interval (MW).
    Columns are consumer agent IDs 1-11 (as strings).
    """
    return _parse_timeseries(_path("load_max"))


def load_load_min() -> pd.DataFrame:
    """
    Min load power per consumer agent per interval (MW).
    Columns are consumer agent IDs 1-11 (as strings).
    """
    return _parse_timeseries(_path("load_min"))


def load_market_price() -> pd.DataFrame:
    """
    Export / Import market reference prices per interval ($/MWh).
    Columns: 'Export - Market price ($/MWh)', 'Import - Market price ($/MWh)'
    Renamed to: export_price, import_price
    """
    df = _parse_timeseries(_path("market"))
    df.columns = ["export_price", "import_price"]
    return df


def load_renewable_production() -> pd.DataFrame:
    """
    Renewable generation per producer agent per interval (MW).
    Columns are producer agent IDs 12-16 (as strings).
    Agents 17-19 are dispatchable (Gas/Coal) — not in this file.
    """
    return _parse_timeseries(_path("renewable"))


# ── snapshot dataclass ────────────────────────────────────────────────────────

@dataclass
class DatasetSnapshot:
    """
    All data for one 30-minute trading interval.

    Fields
    ------
    timestamp       : interval start time
    consumer_demand : {agent_id -> demand_mw}  (avg of min/max load)
    producer_supply : {agent_id -> supply_mw}
    export_price    : $/MWh reference export price
    import_price    : $/MWh reference import price
    """
    timestamp:       datetime
    consumer_demand: Dict[int, float]   # agent_id → MW
    producer_supply: Dict[int, float]   # agent_id → MW
    export_price:    float              # $/MWh
    import_price:    float              # $/MWh

    @property
    def total_supply(self) -> float:
        return sum(self.producer_supply.values())

    @property
    def total_demand(self) -> float:
        return sum(self.consumer_demand.values())

    @property
    def sdr(self) -> float:
        """Supply-Demand Ratio. Returns inf if demand is zero."""
        d = self.total_demand
        return self.total_supply / d if d > 0 else float("inf")

    def __repr__(self) -> str:
        return (
            f"DatasetSnapshot({self.timestamp.isoformat()}, "
            f"supply={self.total_supply:.2f}MW, "
            f"demand={self.total_demand:.2f}MW, "
            f"SDR={self.sdr:.3f})"
        )


# ── main public function ───────────────────────────────────────────────────────

def load_all_timeseries(
    start: Optional[str] = None,
    end:   Optional[str] = None,
) -> List[DatasetSnapshot]:
    """
    Build a sorted list of DatasetSnapshot objects across all intervals.

    Parameters
    ----------
    start : ISO date string e.g. "2012-07-01" (inclusive).  None = all data.
    end   : ISO date string e.g. "2012-07-31" (inclusive).  None = all data.

    Returns
    -------
    List[DatasetSnapshot] sorted by timestamp ascending.

    Notes
    -----
    - Consumer demand is the average of LoadMin and LoadMax for that interval.
    - Producer supply uses RenewableProduction for agents 12-16.
      Agents 17 (Gas), 18 (Coal), 19 (Gas) are dispatchable; their supply
      is set to their Pmax from AgentData as a conservative upper bound.
    - Renewable columns use string agent IDs in the CSV; they are cast to int.
    """
    lmax = load_load_max()
    lmin = load_load_min()
    mkt  = load_market_price()
    ren  = load_renewable_production()
    agents = load_agent_data()

    # Align all timeseries on a common index (inner join)
    common_idx = lmax.index.intersection(lmin.index)\
                           .intersection(mkt.index)\
                           .intersection(ren.index)

    if start:
        common_idx = common_idx[common_idx >= pd.Timestamp(start)]
    if end:
        # Use 23:30 of the end date as the last valid interval (48th slot)
        end_ts = pd.Timestamp(end) + pd.Timedelta(hours=23, minutes=30)
        common_idx = common_idx[common_idx <= end_ts]

    common_idx = common_idx.sort_values()

    # Dispatchable producer Pmax (constant across intervals)
    dispatchable_supply: Dict[int, float] = {}
    for _, row in agents[agents["Type"] == "Producer"].iterrows():
        agent_id = int(row["Agent"])
        if agent_id not in [12, 13, 14, 15, 16]:   # not renewable
            dispatchable_supply[agent_id] = float(row["Pmax"]) if pd.notna(row["Pmax"]) else 0.0

    # Consumer agent IDs 1-11 → column names in load CSVs are "1".."11"
    consumer_ids = list(range(1, 12))
    # Renewable producer IDs 12-16 → column names in renewable CSV
    renewable_ids = [12, 13, 14, 15, 16]

    snapshots: List[DatasetSnapshot] = []

    for ts in common_idx:
        # Consumer demand: average of min and max load
        consumer_demand: Dict[int, float] = {}
        for aid in consumer_ids:
            col = str(aid)
            try:
                vmax = float(lmax.loc[ts, col])
                vmin = float(lmin.loc[ts, col])
                consumer_demand[aid] = (vmax + vmin) / 2.0
            except (KeyError, TypeError):
                consumer_demand[aid] = 0.0

        # Producer supply: renewables from CSV, dispatchable from Pmax
        producer_supply: Dict[int, float] = {}
        for aid in renewable_ids:
            col = str(aid)
            try:
                producer_supply[aid] = max(0.0, float(ren.loc[ts, col]))
            except (KeyError, TypeError):
                producer_supply[aid] = 0.0

        for aid, pmax in dispatchable_supply.items():
            producer_supply[aid] = pmax

        # Market reference prices
        ep = float(mkt.loc[ts, "export_price"])
        ip = float(mkt.loc[ts, "import_price"])

        snapshots.append(DatasetSnapshot(
            timestamp=ts.to_pydatetime(),
            consumer_demand=consumer_demand,
            producer_supply=producer_supply,
            export_price=ep,
            import_price=ip,
        ))

    return snapshots
