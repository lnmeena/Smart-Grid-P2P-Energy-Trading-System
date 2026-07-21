# Smart Grid P2P Energy Trading System — Technical README

A blockchain-integrated, Peer-to-Peer (P2P) energy trading simulation built on the **IEEE 14-Bus test network**. The system pairs a Python market engine (pricing, matching, congestion management) with Solidity smart contracts deployed on a local Hardhat blockchain, enabling trustless, reputation-aware energy trades between prosumers and consumers.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Algorithm Overview](#algorithm-overview)
3. [Project Flow Diagram](#project-flow-diagram)
4. [File-to-Algorithm Mapping](#file-to-algorithm-mapping)
5. [Smart Contract Architecture](#smart-contract-architecture)
6. [Data Files](#data-files)
7. [Setup & Execution Order](#setup--execution-order)

---

## Project Structure

```
smartgrids-blockchain-project/
│
├── readme.md                          ← Top-level setup guide
│
├── remix/                             ← Solidity smart contracts (Remix IDE project)
│   ├── contract/
│   │   ├── EnergyTradingPlatform.sol  ← Contract entry point (CentralSmartContract)
│   │   ├── TradeModule.sol            ← Trade lifecycle, oracle settlement, reputation
│   │   └── UserModule.sol             ← User registration, balances, reputation storage
│   └── scripts/
│       └── deploy_with_ethers.ts      ← Deployment script for Hardhat network
│
├── setup/
│   └── setup.py                       ← Blockchain bootstrap: register users, fund wallets, set roles
│
└── energy-trading/                    ← Python market engine
    ├── run.py                         ← Main entry point: connects to chain, runs simulation windows
    ├── requirements.txt
    └── p2p_energy/
        ├── __init__.py                ← Package exports (build_registry)
        ├── csv_loader.py              ← Dataset ingestion & DatasetSnapshot builder
        ├── agent_mapper.py            ← Peer model, registry, reputation constants, TradeState
        ├── grid_graph.py              ← IEEE 14-bus NetworkX graph, flow tracking, path finding
        └── phase2/
            ├── __init__.py            ← Phase 2 exports (MarketEngine, TradeOracle, adapters)
            ├── pricing_engine.py      ← SDR-based dynamic market-clearing pricing
            ├── matching_engine.py     ← Reputation + anti-starvation greedy matching
            ├── congestion_engine.py   ← Two-pass grid-aware congestion allocation
            ├── market_engine.py       ← Interval orchestrator: pricing → matching → congestion → chain
            ├── contract_adapter.py    ← Web3 / simulated adapter bridging Python ↔ Solidity
            └── trade_oracle.py        ← Off-chain oracle thread: polls events, submits results
```

---

## Algorithm Overview

The system runs in discrete **time intervals** (30-minute slots). Every interval goes through five algorithmic stages.

---

### Stage 1 — Data Ingestion

**Files:** `csv_loader.py`

Time-series CSVs are loaded and aligned by timestamp into `DatasetSnapshot` objects. Each snapshot captures:

- **Consumer demand** — averaged from `LoadMinPower.csv` and `LoadMaxPower.csv` for agents 1–11.
- **Producer supply** — renewable output from `RenewableProduction.csv` for wind/solar agents; dispatchable generators (gas, coal) use their `Pmax` as a conservative supply estimate.
- **Market reference prices** — export price (floor) and import price (ceiling) from `MarketPrice.csv`.
- **Network topology** — bus assignments and line capacities from `NetworkData.csv`.

---

### Stage 2 — Peer Modeling & Registry

**Files:** `agent_mapper.py`

Each grid participant is represented as a `Peer` object with:

- **Reputation score** — integer on 0–100 scale; initialised at 50. Updated by the smart contract after each trade outcome: +10 on success, −10 on failure, −20 on fraud detection.
- **Suspension** — peers whose reputation falls below threshold 10 are excluded from the current interval.
- **Anti-starvation counter** (`intervals_since_trade`) — tracks how long a peer has been unmatched; used in matching scoring to prevent indefinite exclusion.
- **Wallet balance** — read from the smart contract via `contract_adapter.py` before each interval; used for affordability checks.

The `PeerRegistry` provides filtered views: `active_producers()` and `active_consumers()` exclude suspended peers.

---

### Stage 3 — Dynamic Pricing

**Files:** `pricing_engine.py`

A **Supply-Demand Ratio (SDR)** model determines the market-clearing price for each interval.

**Step 3a — Compute raw SDR:**

```
raw_SDR = total_supply_MW / total_demand_MW
```

**Step 3b — Apply reputation weighting (effective SDR):**

Higher-reputation consumers are modelled as representing slightly more effective demand, which tightens supply and raises the price in scarcity conditions:

```
weight_i = 1 + γ × (reputation_i − 50) / 100
weighted_demand = Σ (demand_i × weight_i)
effective_SDR = total_supply / weighted_demand
```

where `γ = 0.10` (reputation sensitivity parameter).

**Step 3c — Map SDR to price:**

The SDR is linearly interpolated between an export price floor and an import price ceiling, clamped within `[sdr_floor=0.5, sdr_cap=2.0]`:

```
t = (SDR_clamped − sdr_floor) / (sdr_cap − sdr_floor)
price = import_price − t × (import_price − export_price)
```

The final price is expressed in **gwei** (integer) for on-chain settlement:

```
price_gwei = round(price_$/MWh × 1_000_000)
```

Market state is labelled: `oversupply` (SDR > 1.02), `undersupply` (SDR < 0.98), or `balanced`.

---

### Stage 4 — Reputation-Weighted Greedy Matching

**Files:** `matching_engine.py`

The matching engine pairs sellers with buyers in priority order.

**Step 4a — Score each peer:**

```
score = α × (reputation / 100) + β × (wait / max_wait)
```

where `α = 0.70` (reputation weight) and `β = 0.30` (anti-starvation weight). Both sellers and buyers are scored independently and sorted descending.

**Step 4b — Affordability check:**

Before creating a proposal, the buyer's on-chain balance is checked against the required total:

```
stake_gwei = ceil(trade_price_gwei × 0.10)     ← 10% stake requirement
required   = trade_price_gwei + stake_gwei
```

If `buyer_balance < required`, the buyer is skipped and recorded in `skipped_buyers`.

**Step 4c — Greedy fill:**

The highest-scored seller iterates through buyers in score order. Each matched pair produces a `TradeProposal` recording the seller ID, buyer ID, bus locations, energy in MW, price in gwei, and stake in gwei. A seller may partially fill multiple buyers until its supply is exhausted.

The `match_rate` is computed as `total_matched_MW / total_demand_MW`.

---

### Stage 5 — Congestion Management

**Files:** `congestion_engine.py`, `grid_graph.py`

The congestion engine validates and reallocates trade proposals against the physical IEEE 14-bus transmission network.

**Step 5a — Same-bus bypass:**

Proposals where `seller_bus == buyer_bus` are approved immediately without grid path checks; no line capacity is consumed.

**Step 5b — Pass 1 (full allocation attempt):**

For each cross-bus proposal, the shortest path between the seller's bus and buyer's bus is computed using NetworkX BFS. If the path has sufficient remaining capacity for the full trade volume, it is committed immediately and line flows are updated.

**Step 5c — Pass 2 (pro-rata reallocation):**

Proposals that fail full allocation are grouped by their **bottleneck edge** (the edge with the least remaining capacity along their path). Within each bottleneck group, available capacity is divided among competing proposals proportional to their requested energy, with a **reputation-weighted bonus**:

```
rep_weight = 1 + rep_advantage × (avg_reputation − 50) / 100
share_i    = (energy_i / total_energy) × (rep_weight_i / mean_weight)
alloc_i    = min(energy_i, available_capacity × share_i)
```

where `rep_advantage = 0.20`. Allocations below `0.001 MW` are rejected. Partially filled proposals are marked `reduced`; fully blocked ones are `rejected`.

---

### Stage 6 — Blockchain Settlement

**Files:** `market_engine.py`, `contract_adapter.py`, `trade_oracle.py`

**Step 6a — On-chain trade creation:**

Each approved `CongestionResult` generates a `createTrade()` call to the `CentralSmartContract`. The contract:
1. Locks stake from both buyer and seller (`_getStakes`).
2. Deducts the trade price from the buyer (`_getMoneyFromBuyer`).
3. Sets trade status to `ACTIVE` and emits `TradeInitiated`.

If either stake or payment fails due to insufficient funds, the trade is cancelled on-chain and reputation is penalised.

**Step 6b — Oracle settlement:**

A background `TradeOracle` thread polls the chain for `TradeInitiated` events. For each new trade ID, it simulates a delivery outcome (90% COMPLETED, 8% FAILURE, 2% FRAUD) and calls `checkOracle()` on the contract:

- **COMPLETED** — seller receives payment; both parties get reputation +10; stakes returned.
- **FAILURE** — full refund to buyer; stakes returned; no reputation change.
- **FRAUD** — buyer refunded; seller stake slashed by 10%; seller reputation penalised up to 50 points.

**Step 6c — Window reporting:**

After all timestamps in a window (10 intervals) are processed, the engine waits for all oracle confirmations before printing a window summary: approved trades, completed/failed/fraud counts, Python engine time, blockchain time, and total gas used.

---

## Project Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          SYSTEM STARTUP (one-time)                              │
│                                                                                 │
│  Hardhat Node ──► Deploy CentralSmartContract (Remix) ──► setup.py              │
│                                                           ├─ registerUser x19   │
│                                                           ├─ fundWallets        │
│                                                           ├─ setPythonEngine    │
│                                                           └─ setEngineOracle    │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                              run.py  (simulation loop)                         │
│                                                                                │
│  load_all_timeseries() ──► [DatasetSnapshot, ...]  (csv_loader.py)             │
│  build_registry()      ──► PeerRegistry            (agent_mapper.py)           │
│  build_grid_graph()    ──► GridGraph               (grid_graph.py)             │
│  Web3ContractAdapter   ──► on-chain RPC bridge     (contract_adapter.py)       │
│  TradeOracle.start()   ──► background event thread (trade_oracle.py)           │
│                                                                                │
│  ┌──────────────────── for each WINDOW (10 timestamps) ──────────────────────┐ │
│  │                                                                           │ │
│  │  ┌────────────── for each INTERVAL (timestamp) ────────────────────────┐  │ │
│  │  │                                                                     │  │ │
│  │  │  1. Collect active producers & consumers (registry)                 │  │ │
│  │  │  2. Fetch buyer balances from contract                              │  │ │
│  │  │                                                                     │  │ │
│  │  │  ┌── PRICING (pricing_engine.py) ──────────────────────────────┐    │  │ │
│  │  │  │  raw_SDR → reputation-weighted SDR → price_gwei             │    │  │ │
│  │  │  └─────────────────────────────────────────────────────────────┘    │  │ │
│  │  │                            │                                        │  │ │
│  │  │                            ▼                                        │  │ │
│  │  │  ┌── MATCHING (matching_engine.py) ────────────────────────────┐    │  │ │
│  │  │  │  score peers → affordability check → greedy fill            │    │  │ │
│  │  │  │  → [TradeProposal, ...]                                     │    │  │ │
│  │  │  └─────────────────────────────────────────────────────────────┘    │  │ │
│  │  │                            │                                        │  │ │
│  │  │                            ▼                                        │  │ │
│  │  │  ┌── CONGESTION (congestion_engine.py + grid_graph.py) ────────┐    │  │ │
│  │  │  │  same-bus bypass → pass 1 full alloc → pass 2 pro-rata      │    │  │ │
│  │  │  │  → [CongestionResult, ...]   (approved / reduced / rejected)│    │  │ │
│  │  │  └─────────────────────────────────────────────────────────────┘    │  │ │
│  │  │                            │                                        │  │ │
│  │  │                            ▼                                        │  │ │
│  │  │  ┌── BLOCKCHAIN (contract_adapter.py) ────────────────────────┐     │  │ │
│  │  │  │  createTrade() per approved result                         │     │  │ │
│  │  │  │  contract: lock stake + deduct price → ACTIVE              │     │  │ │
│  │  │  │  emit TradeInitiated(tradeId)                              │     │  │ │
│  │  │  └────────────────────────────────────────────────────────────┘     │  │ │
│  │  │                                                                     │  │ │
│  │  └─────────────────────────────────────────────────────────────────────┘  │ │
│  │                                                                           │ │
│  │  wait oracle.all_processed(trade_ids)                                     │ │
│  │                                                                           │ │
│  │  ┌── ORACLE THREAD (trade_oracle.py) ──────────────────────────────────┐  │ │
│  │  │  poll TradeInitiated events                                         │  │ │
│  │  │  draw outcome (90% COMPLETED / 8% FAILURE / 2% FRAUD)               │  │ │
│  │  │  checkOracle(tradeId, result) → contract settles:                   │  │ │
│  │  │    COMPLETED → pay seller, return stakes, rep +10                   │  │ │
│  │  │    FAILURE   → refund buyer, return stakes                          │  │ │
│  │  │    FRAUD     → refund buyer, slash seller stake, rep penalty        │  │ │
│  │  └─────────────────────────────────────────────────────────────────────┘  │ │
│  │                                                                           │ │
│  │  Print window summary (approved, completed, failed, fraud, gas, time)     │ │
│  └───────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│  oracle.stop()  →  Simulation complete                                         │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## File-to-Algorithm Mapping

| File | Algorithm / Responsibility |
|------|---------------------------|
| `energy-trading/run.py` | **Main simulation loop.** Initialises Web3 connection, loads snapshots, builds registry and grid, starts the oracle thread, then iterates over 10-timestamp windows calling `engine.run_interval()` per snapshot. Waits for oracle confirmations between windows and prints per-window statistics. |
| `p2p_energy/csv_loader.py` | **Data ingestion.** Loads all CSV files, parses timestamps, merges load min/max into averaged consumer demand, reads renewable production for producers, and builds `DatasetSnapshot` objects for each interval. |
| `p2p_energy/agent_mapper.py` | **Peer model & registry.** Defines `Peer` (reputation, wallet, stake, suspension, wait counter), `PeerRegistry` (active producer/consumer views), and `TradeRecord`. Holds reputation constants (init=50, min=0, max=100), stake rate (10%), and the meter-ID-to-wallet mapping used across the system. |
| `p2p_energy/grid_graph.py` | **IEEE 14-bus network model.** Builds an undirected NetworkX graph from `NetworkData.csv`. Tracks per-edge flow state. Exposes `find_path()` (BFS shortest path), `is_path_feasible()`, `add_flow()`, `available_capacity()`, `utilization()`, `congested_edges()`, and `reset_flows()`. |
| `p2p_energy/phase2/pricing_engine.py` | **Stage 3 — Dynamic pricing.** Computes raw SDR, applies reputation-weighted effective SDR, maps SDR linearly to a price between export and import reference prices, and returns a `PricingResult` with gwei-denominated base and final prices. Parameters: `γ=0.10`, `sdr_floor=0.5`, `sdr_cap=2.0`. |
| `p2p_energy/phase2/matching_engine.py` | **Stage 4 — Greedy matching.** Scores peers using `α×rep + β×wait` (α=0.70, β=0.30), performs an affordability check against on-chain balances, and greedily fills sellers to buyers in score order. Produces `TradeProposal` objects with per-trade price and stake computed in gwei. Records skipped buyers. |
| `p2p_energy/phase2/congestion_engine.py` | **Stage 5 — Congestion management.** Bypasses same-bus trades; routes cross-bus trades through the grid graph. Pass 1 commits trades with available capacity; Pass 2 reallocates congested groups pro-rata with reputation weighting. Returns a `CongestionReport` with per-proposal outcomes (approved / reduced / rejected) and a list of congested lines. |
| `p2p_energy/phase2/market_engine.py` | **Stage orchestrator.** `run_interval()` chains pricing → matching → congestion → on-chain trade creation in sequence. Builds `IntervalResult` with timing (Python vs. blockchain latency), trade statistics, and gas used. `build_default()` factory sets all tunable parameters. |
| `p2p_energy/phase2/contract_adapter.py` | **Web3 bridge.** `Web3ContractAdapter` wraps the deployed `CentralSmartContract` ABI. Provides `create_trade()`, `submit_oracle_result()`, `fetch_balance()`, `fetch_all_balances()`, `update_reputation()`, and `get_trade_events()`. A `SimulatedContractAdapter` enables offline/unit testing without a live node. |
| `p2p_energy/phase2/trade_oracle.py` | **Off-chain oracle thread.** Runs as a daemon thread polling the chain for `TradeInitiated` events. For each new trade ID, draws a probabilistic outcome (COMPLETED/FAILURE/FRAUD) and calls `checkOracle()` on-chain. Persists processed trade IDs to `oracle_state.json` for crash recovery. |
| `remix/contract/EnergyTradingPlatform.sol` | **Contract entry point.** `CentralSmartContract` is the deployed contract; it inherits `TradeModule` and passes `msg.sender` to `Ownable`. Contains no additional logic. |
| `remix/contract/TradeModule.sol` | **On-chain trade lifecycle.** Implements `createTrade()` (stake locking, payment collection, status transitions), `checkOracle()` (COMPLETED/FAILURE/FRAUD settlement), `expireTrade()` (timeout cancellation), and internal helpers for reputation adjustment (`_increaseReputation`, `_decreaseReputation`) and fund settlement (`_settleTradeSuccess`, `_refundAll`, `_penalizeSellerFraud`). Enforces access control: only `pythonEngine` may create trades; only `engineOracle` may submit oracle results. |
| `remix/contract/UserModule.sol` | **On-chain user management.** Stores `User{wallet, reputation, amountStored}` per meter ID. Provides `registerUser()`, `payAmount()` (ETH deposit), `transferAmount()` (withdrawal), `modifyBalance()` (internal debit/credit), `updateReputation()`, and read accessors (`getUserInfo`, `getUserBalance`, `getUserReputation`). Inherits `ReentrancyGuard` and `Ownable` from OpenZeppelin. |
| `setup/setup.py` | **Blockchain bootstrap.** Connects to Hardhat RPC, deploys contract roles (`setPythonEngine`, `setEngineOracle`), registers all 19 agent meter IDs (`registerUserWithWallet`), and funds each wallet with ETH via `payAmount()`. Must complete before `run.py` is started. |

---

## Smart Contract Architecture

```
CentralSmartContract (EnergyTradingPlatform.sol)
        │
        └── TradeModule (TradeModule.sol)
                │   ┌─ createTrade()        ← called by pythonEngine only
                │   ├─ checkOracle()        ← called by engineOracle only
                │   ├─ expireTrade()        ← callable by anyone after timeout
                │   ├─ _getStakes()         ← lock 10% stake from both parties
                │   ├─ _getMoneyFromBuyer() ← deduct trade price from buyer
                │   ├─ _settleTradeSuccess()← pay seller, return stakes, rep +10
                │   ├─ _refundAll()         ← return price + stakes on cancel
                │   └─ _penalizeSellerFraud()← slash seller stake 10%, rep penalty
                │
                └── UserModule (UserModule.sol)
                        ├─ registerUser() / registerUserWithWallet()
                        ├─ payAmount()      ← ETH deposit (msg.value)
                        ├─ transferAmount() ← ETH withdrawal
                        ├─ modifyBalance()  ← internal debit/credit
                        └─ updateReputation()

Trade Status State Machine:
  IN_PROGRESS ──[stakes + payment OK]──► ACTIVE ──[oracle COMPLETED]──► COMPLETED
       │                                    │
       └──[stake/payment fail]──► CANCELLED │──[oracle FAILURE]──► CANCELLED
                                            │──[oracle FRAUD]────► FRAUD_REPORTED
                                            └──[timeout]─────────► CANCELLED
```

---

## Data Files

| File | Description |
|------|-------------|
| `data/AgentData.csv` | 20 agents: ID, type (Consumer/Producer/Grid), energy source, bus assignment, community, Pmin, Pmax, cost coefficients |
| `data/NetworkData.csv` | 20 IEEE 14-bus transmission lines: From/To bus, R, X, B (susceptance), CAP (MW capacity), tap setting |
| `data/LoadMaxPower.csv` | Time-series maximum load per consumer bus (MW), one row per 30-min interval |
| `data/LoadMinPower.csv` | Time-series minimum load per consumer bus (MW), averaged with LoadMax to get demand |
| `data/RenewableProduction.csv` | Time-series renewable output per producer agent (MW) — wind and solar agents |
| `data/MarketPrice.csv` | Time-series export price (floor) and import price (ceiling) per interval ($/MWh) |
| `P2P_IEEE_14_bus_system_dataset/` | Reference IEEE 14-bus diagram image and ODS summary of the test case parameters |

---

## Setup & Execution Order

```
1. Replace server/hardhat.config.ts with the provided configuration
   (creates 30 deterministic accounts, 10,000 ETH each)

2. cd server && npx hardhat --init
   npx hardhat node           ← keep running in background

3. Open remix/ in Remix IDE
   Compile & deploy CentralSmartContract to localhost:8545
   Copy deployed contract address and ABI

4. Save ABI:  energy-trading/contract_abi.json
   Save artifact:  project/artifacts/CentralSmartContract.json

5. cd setup && python setup.py
   (registers 19 users, funds wallets, sets pythonEngine + engineOracle roles)

6. cd energy-trading && python run.py
   (starts market engine; processes all intervals in 10-timestamp windows)
```

> **Important:** The Python Engine address and Oracle address must be the same Ethereum address. The Hardhat node must remain running for the entire duration of `run.py`.

---

## Key Algorithm Parameters

| Parameter | Value | Location | Effect |
|-----------|-------|----------|--------|
| `γ` (gamma) | 0.10 | `PricingEngine` | Reputation sensitivity in SDR weighting |
| `sdr_floor` | 0.5 | `PricingEngine` | SDR below which price hits import ceiling |
| `sdr_cap` | 2.0 | `PricingEngine` | SDR above which price hits export floor |
| `α` (alpha) | 0.70 | `MatchingEngine` | Reputation weight in peer scoring |
| `β` (beta) | 0.30 | `MatchingEngine` | Anti-starvation wait weight in peer scoring |
| `min_trade_mw` | 0.001 | `MatchingEngine`, `CongestionEngine` | Minimum viable trade size (MW) |
| `STAKE_RATE` | 0.10 | `agent_mapper.py`, `matching_engine.py` | Stake as fraction of trade price (10%) |
| `rep_advantage` | 0.20 | `CongestionEngine` | Max reputation bonus in congestion reallocation |
| `REP_INIT` | 50 | `agent_mapper.py` | Starting reputation for all peers |
| `REP_SUSPENSION_THRESHOLD` | 10 | `agent_mapper.py` | Reputation below which a peer is suspended |
| `WINDOW_SIZE` | 10 | `run.py` | Timestamps per simulation window |
| Oracle outcome | 90/8/2% | `trade_oracle.py` | COMPLETED / FAILURE / FRAUD probability distribution |
