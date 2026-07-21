from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import networkx as nx
import pandas as pd

from .csv_loader import load_network_data


# ── edge attribute keys (used as dict keys in the graph) ─────────────────────
_R   = "R"
_X   = "X"
_B   = "B"
_CAP = "CAP"
_TAP = "Tap_setting"
_FLOW = "flow"
_LINE = "line_id"


# ── GridGraph ─────────────────────────────────────────────────────────────────

class GridGraph:
    """
    Undirected weighted graph of the IEEE 14-Bus transmission network.

    Nodes  : bus IDs 1-14
    Edges  : transmission lines from NetworkData.csv
             Each edge carries: R, X, B, CAP (MW), Tap_setting, flow (MW)

    Flow accounting
    ---------------
    Flow is tracked per undirected edge (u, v) where u < v.
    The congestion engine calls add_flow() when a trade is allocated and
    remove_flow() when a trade is cancelled or completed.
    reset_flows() is called at the start of every 30-min interval.
    """

    def __init__(self, G: nx.Graph) -> None:
        self._G = G

    # ── graph accessors ───────────────────────────────────────────────────────

    @property
    def graph(self) -> nx.Graph:
        return self._G

    @property
    def buses(self) -> List[int]:
        return sorted(self._G.nodes())

    @property
    def lines(self) -> List[Tuple[int, int]]:
        """Return all edges as (u, v) pairs with u < v."""
        return [(u, v) for u, v in self._G.edges() if u < v]

    # ── path finding ──────────────────────────────────────────────────────────

    def find_path(self, from_bus: int, to_bus: int) -> List[int]:
        """
        Return the shortest path (fewest hops) between two buses.
        Returns an empty list if the buses are identical or disconnected.

        The path is a list of bus IDs: [from_bus, ..., to_bus]
        The congestion engine iterates consecutive pairs as edges.
        """
        if from_bus == to_bus:
            return []
        try:
            return nx.shortest_path(self._G, from_bus, to_bus)
        except nx.NetworkXNoPath:
            return []

    def find_all_paths(
        self,
        from_bus: int,
        to_bus: int,
        max_length: int = 8,
    ) -> List[List[int]]:
        """
        Return all simple paths up to max_length hops (for advanced routing).
        Used when the primary path is congested and alternates are needed.
        """
        if from_bus == to_bus:
            return []
        try:
            return list(nx.all_simple_paths(
                self._G, from_bus, to_bus, cutoff=max_length
            ))
        except nx.NetworkXNoPath:
            return []

    # ── edge access ───────────────────────────────────────────────────────────

    def _edge_key(self, u: int, v: int) -> Tuple[int, int]:
        """Canonical edge key: smaller bus ID first."""
        return (min(u, v), max(u, v))

    def get_edge(self, u: int, v: int) -> Optional[Dict]:
        """Return edge attribute dict or None if edge doesn't exist."""
        key = self._edge_key(u, v)
        if self._G.has_edge(*key):
            return dict(self._G.edges[key])
        return None

    def cap(self, u: int, v: int) -> float:
        """Return CAP (MW) of edge (u,v). Returns 0 if edge not found."""
        edge = self.get_edge(u, v)
        return edge[_CAP] if edge else 0.0

    def flow(self, u: int, v: int) -> float:
        """Return current flow (MW) on edge (u,v)."""
        edge = self.get_edge(u, v)
        return edge[_FLOW] if edge else 0.0

    def utilization(self, u: int, v: int) -> float:
        """
        Return utilization ratio: flow / CAP.
        Returns 0 if CAP is zero (transformer lines with CAP=0 are unconstrained).
        """
        cap = self.cap(u, v)
        if cap <= 0:
            return 0.0
        return self.flow(u, v) / cap

    def available_capacity(self, u: int, v: int) -> float:
        """Return remaining capacity on edge (u,v) in MW."""
        cap = self.cap(u, v)
        if cap <= 0:
            return float("inf")     # unconstrained edge
        return max(0.0, cap - self.flow(u, v))

    # ── flow management ───────────────────────────────────────────────────────

    def _set_flow(self, u: int, v: int, value: float) -> None:
        key = self._edge_key(u, v)
        if self._G.has_edge(*key):
            self._G.edges[key][_FLOW] = max(0.0, value)

    def add_flow(self, path: List[int], amount_mw: float) -> None:
        """
        Add amount_mw to every edge along path.
        Path is a list of bus IDs from find_path().
        """
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            key = self._edge_key(u, v)
            if self._G.has_edge(*key):
                self._G.edges[key][_FLOW] += amount_mw

    def remove_flow(self, path: List[int], amount_mw: float) -> None:
        """Remove amount_mw from every edge along path (clamp to 0)."""
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            key = self._edge_key(u, v)
            if self._G.has_edge(*key):
                current = self._G.edges[key][_FLOW]
                self._G.edges[key][_FLOW] = max(0.0, current - amount_mw)

    def reset_flows(self) -> None:
        """Zero all edge flows. Call at the start of every trading interval."""
        for u, v in self._G.edges():
            self._G.edges[u, v][_FLOW] = 0.0

    # ── congestion queries ────────────────────────────────────────────────────

    def congested_edges(self) -> List[Tuple[int, int]]:
        """Return list of (u,v) edges where flow >= CAP (capacity exceeded)."""
        result = []
        for u, v in self._G.edges():
            cap = self._G.edges[u, v][_CAP]
            flw = self._G.edges[u, v][_FLOW]
            if cap > 0 and flw >= cap:
                result.append(self._edge_key(u, v))
        return result

    def is_path_feasible(self, path: List[int], amount_mw: float) -> bool:
        """
        Return True if adding amount_mw to every edge along path
        would not exceed any edge's CAP.
        Edges with CAP=0 are treated as unconstrained.
        """
        for i in range(len(path) - 1):
            avail = self.available_capacity(path[i], path[i + 1])
            if avail < amount_mw:
                return False
        return True

    def max_feasible_flow(self, path: List[int]) -> float:
        """
        Return the maximum additional MW that can flow along path
        without exceeding any edge capacity. Returns inf if unconstrained.
        """
        limits = []
        for i in range(len(path) - 1):
            avail = self.available_capacity(path[i], path[i + 1])
            if avail < float("inf"):
                limits.append(avail)
        if not limits:
            return float("inf")
        return min(limits)

    # ── reporting ─────────────────────────────────────────────────────────────

    def summary(self) -> pd.DataFrame:
        """DataFrame of all edges with current utilisation."""
        rows = []
        for u, v, data in self._G.edges(data=True):
            if u > v:
                continue    # skip duplicate direction
            rows.append({
                "line_id":     data.get(_LINE, "?"),
                "from_bus":    u,
                "to_bus":      v,
                "CAP_MW":      data[_CAP],
                "flow_MW":     round(data[_FLOW], 4),
                "util_%":      round(self.utilization(u, v) * 100, 1),
                "available_MW":round(self.available_capacity(u, v), 4),
                "congested":   data[_CAP] > 0 and data[_FLOW] >= data[_CAP],
            })
        return pd.DataFrame(rows).sort_values("line_id").reset_index(drop=True)

    def __repr__(self) -> str:
        return (
            f"GridGraph(buses={len(self._G.nodes)}, "
            f"lines={len(self._G.edges)}, "
            f"congested={len(self.congested_edges())})"
        )


# ── factory ───────────────────────────────────────────────────────────────────

def build_grid_graph() -> GridGraph:
    """
    Read NetworkData.csv and build an undirected GridGraph.

    Every edge is added in both directions (undirected), with attributes:
        R, X, B, CAP, Tap_setting, flow (initialised to 0.0), line_id

    Lines with CAP = 0 in the CSV (e.g. transformer lines 8, 9, 10)
    are kept but treated as unconstrained by available_capacity().
    """
    net = load_network_data()
    G = nx.Graph()

    # Add all 14 buses as nodes
    for bus_id in range(1, 15):
        G.add_node(bus_id, bus_id=bus_id)

    # Add transmission lines as edges
    for _, row in net.iterrows():
        u = int(row["From"])
        v = int(row["To"])
        G.add_edge(u, v,
            **{
                _LINE: int(row["Line"]),
                _R:    float(row["R"]),
                _X:    float(row["X"]),
                _B:    float(row["B"]),
                _CAP:  float(row["CAP"]),
                _TAP:  float(row["Tap_setting"]),
                _FLOW: 0.0,
            }
        )

    return GridGraph(G)
