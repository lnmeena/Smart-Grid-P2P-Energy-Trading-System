# P2P Energy Trading System

Peer-to-peer energy trading simulation for the IEEE 14-bus test case.

## Project structure

- `p2p_energy/` — core Python package
  - `p2p_energy/csv_loader.py` — dataset parsing and interval snapshot builder
  - `p2p_energy/agent_mapper.py` — peer model, registry, reputation, and stake management
  - `p2p_energy/grid_graph.py` — NetworkX-based grid model for line capacities and path feasibility
  - `p2p_energy/phase2/` — Phase 2 market engines: pricing, matching, congestion, and market orchestration
  - `p2p_energy/data/` — bundled CSV dataset files
- `phase2/` — legacy compatibility package for old imports like `from phase2.matching_engine import ...`
- `tests/` — pytest test suite
  - `tests/test_phase1.py` — Phase 1 data and peer model tests
  - `tests/phase2/test_phase2.py` — Phase 2 market engine tests
- `pyproject.toml` — project metadata and dependencies
- `requirements.txt` — runtime requirements
- `README.md` — this overview

## Setup

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Run tests

```bash
python -m pytest -q
```

## What each file does

- `p2p_energy/csv_loader.py`
  - loads `AgentData.csv`, `NetworkData.csv`, and time series CSVs
  - converts date/time columns into pandas timestamps
  - builds `DatasetSnapshot` objects with consumer demand, producer supply, export/import prices
- `p2p_energy/agent_mapper.py`
  - defines `Peer`, `PeerRegistry`, and `TradeRecord`
  - tracks wallet balance, locked stake, reputation, suspension, and trade history
  - provides `build_registry()` to instantiate peers from `AgentData.csv`
- `p2p_energy/grid_graph.py`
  - reads `NetworkData.csv` and builds an undirected grid graph
  - tracks line flow, remaining capacity, and congestion state
  - exposes shortest path lookup and feasibility checks for trades
- `p2p_energy/phase2/pricing_engine.py`
  - computes dynamic market-clearing prices using supply-demand ratio (SDR)
  - applies optional reputation weighting to demand
- `p2p_energy/phase2/matching_engine.py`
  - sorts sellers and buyers by reputation + wait time
  - performs a greedy matching of available supply to demand
  - emits `TradeProposal` objects for congestion assessment
- `p2p_energy/phase2/congestion_engine.py`
  - checks trade proposals against grid line capacities
  - approves same-bus trades immediately
  - performs congestion-aware reallocation when multiple trades compete for capacity
- `p2p_energy/phase2/market_engine.py`
  - orchestrates one interval: pricing → matching → congestion
  - returns a complete `IntervalResult` for simulation and reporting
- `tests/test_phase1.py`
  - validates data loading, dataset snapshots, and peer registry behavior
- `tests/phase2/test_phase2.py`
  - validates pricing, matching, congestion, and end-to-end interval execution

## Algorithm summary

1. Data ingestion
   - time series CSVs are aligned by timestamp
   - consumer demand is averaged from min/max load
   - renewable producers use interval production data
   - dispatchable generators use their `Pmax` as a conservative supply estimate

2. Peer modeling
   - each `Peer` is initialized with reputation, wallet, and stake accounting
   - peers may be suspended if reputation falls below a threshold
   - wait counters support anti-starvation in matching

3. Pricing
   - a supply-demand ratio (SDR) is mapped to a price between export and import references
   - higher-reputation consumers slightly increase effective demand, raising price in scarcity

4. Matching
   - sellers and buyers are scored by `α × reputation + β × normalized wait`
   - higher scored peers trade sooner, while wait time prevents starvation
   - matching is greedy: sellers fill buyers until supply or demand is exhausted

5. Congestion management
   - same-bus trades bypass the grid and are approved directly
   - cross-bus trades are routed over the grid and checked against line capacities
   - if capacity is insufficient, competing trades are reallocated pro-rata with a small reputation bonus

## Notes

- `phase2/` exists to preserve compatibility with older import paths. The preferred package path is `p2p_energy.phase2`.
