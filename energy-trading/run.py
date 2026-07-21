import json
import os
import time

from dotenv import load_dotenv
from web3 import Web3

from p2p_energy import build_registry
from p2p_energy.grid_graph import build_grid_graph
from p2p_energy.csv_loader import load_all_timeseries

from p2p_energy.phase2 import (
    MarketEngine,
    TradeOracle,
    Web3ContractAdapter,
)

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

WINDOW_SIZE = 10  # timestamps per window

load_dotenv()

ACCOUNT_ADDRESS = os.getenv("ACCOUNT_ADDRESS")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")
RPC_URL = os.getenv("RPC_URL")

ABI_FILE = os.getenv(
    "ABI_FILE",
    "artifacts/CentralSmartContract.json",
)

# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------

if not ACCOUNT_ADDRESS:
    raise ValueError(
        "ACCOUNT_ADDRESS not configured"
    )

if not PRIVATE_KEY:
    raise ValueError(
        "PRIVATE_KEY not configured"
    )

if not CONTRACT_ADDRESS:
    raise ValueError(
        "CONTRACT_ADDRESS not configured"
    )

if not RPC_URL:
    raise ValueError(
        "RPC_URL not configured"
    )

# ------------------------------------------------------------------
# Web3
# ------------------------------------------------------------------

w3 = Web3(
    Web3.HTTPProvider(
        RPC_URL
    )
)

if not w3.is_connected():
    raise ConnectionError(
        f"Unable to connect to RPC: {RPC_URL}"
    )

print(
    "Connected:",
    w3.is_connected()
)

# ------------------------------------------------------------------
# ABI
# ------------------------------------------------------------------

with open(
    ABI_FILE,
    "r",
) as f:

    contract_json = json.load(
        f
    )

abi = contract_json["abi"]

# ------------------------------------------------------------------
# Adapter
# ------------------------------------------------------------------

adapter = Web3ContractAdapter(
    w3=w3,
    contract_address=CONTRACT_ADDRESS,
    abi=abi,
    private_key=PRIVATE_KEY,
    confirmations=0,
)

print(
    "Signer:",
    ACCOUNT_ADDRESS,
)

print(
    "Balance:",
    w3.from_wei(
        w3.eth.get_balance(
            ACCOUNT_ADDRESS
        ),
        "ether",
    ),
)

# ------------------------------------------------------------------
# Oracle
# ------------------------------------------------------------------

oracle = TradeOracle(
    contract_adapter=adapter,
    account_address=ACCOUNT_ADDRESS,
)

oracle.start()

print(
    "Trade oracle started."
)

# ------------------------------------------------------------------
# Dataset
# ------------------------------------------------------------------

snapshots = (
    load_all_timeseries()
)

print(
    "Snapshots:",
    len(snapshots),
)

# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------

registry = build_registry()

# ------------------------------------------------------------------
# Grid
# ------------------------------------------------------------------

grid = build_grid_graph()

# ------------------------------------------------------------------
# Market Engine
# ------------------------------------------------------------------

engine = (
    MarketEngine.build_default(
        grid=grid,
        contract_adapter=adapter,
        oracle=oracle,
    )
)

# ------------------------------------------------------------------
# Windows
# ------------------------------------------------------------------

window_number = 1

for start_index in range(
    0,
    len(snapshots),
    WINDOW_SIZE,
):

    end_index = min(
        start_index + WINDOW_SIZE,
        len(snapshots),
    )

    current_window = (
        snapshots[
            start_index:end_index
        ]
    )

    print()
    print(
        "=" * 60
    )

    print(
        f"WINDOW {window_number}"
    )

    print(
        "From:",
        current_window[0]
        .timestamp
    )

    print(
        "To:",
        current_window[-1]
        .timestamp
    )

    print(
        "=" * 60
    )

    total_python_time = 0.0

    total_blockchain_time = 0.0

    total_gas = 0

    total_approved = 0

    trade_ids = []

    for snapshot in current_window:

        result = (
            engine.run_interval(
                timestamp=(
                    snapshot.timestamp
                ),
                snapshot=snapshot,
                registry=registry,
            )
        )

        total_python_time += (
            result.python_time
        )

        total_blockchain_time += (
            result.blockchain_time
        )

        total_approved += (
            result.congestion
            .approved_count
        )

        for trade in (
            result.trade_statistics
        ):

            if not trade["success"]:
                continue

            trade_ids.append(
                trade["trade_id"]
            )

            total_gas += (
                trade["gas_used"]
            )

    while not oracle.all_processed(
        trade_ids
    ):
        time.sleep(1)

    oracle_results = (
        oracle.get_results(
            trade_ids
        )
    )

    completed = 0
    failed = 0
    fraud = 0

    for result in (
        oracle_results.values()
    ):

        if result is None:
            continue

        if (
            result["result_name"]
            == "COMPLETED"
        ):
            completed += 1

        elif (
            result["result_name"]
            == "FAILURE"
        ):
            failed += 1

        elif (
            result["result_name"]
            == "FRAUD"
        ):
            fraud += 1

    print()

    print(
        "-" * 60
    )

    print(
        "Timestamps:",
        len(current_window)
    )

    print(
        "Approved Trades:",
        total_approved
    )

    print(
        "Completed:",
        completed
    )

    print(
        "Failed:",
        failed
    )

    print(
        "Fraud:",
        fraud
    )

    print(
        "Python Engine Time:",
        round(
            total_python_time,
            4,
        ),
        "sec",
    )

    print(
        "Blockchain Time:",
        round(
            total_blockchain_time,
            4,
        ),
        "sec",
    )

    print(
        "Total Gas Used:",
        total_gas,
    )

    print(
        "-" * 60
    )

    window_number += 1

print()

print(
    "Simulation completed."
)

oracle.stop()
