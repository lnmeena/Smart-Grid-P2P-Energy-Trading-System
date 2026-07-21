from __future__ import annotations

import json
import random
import threading
import time

from pathlib import Path
from typing import Dict, Optional, Set

from ..agent_mapper import TradeState
from .contract_adapter import ContractAdapterBase


class TradeOracle:

    COMPLETED = 0
    FAILURE = 1
    FRAUD = 2

    RESULT_NAMES = {
        0: "COMPLETED",
        1: "FAILURE",
        2: "FRAUD",
    }

    def __init__(
        self,
        contract_adapter: ContractAdapterBase,
        account_address: str,
        seed: Optional[int] = None,
        poll_interval: int = 1,
        state_file: str = "oracle_state.json",
    ):
        self._adapter = contract_adapter

        self.account_address = account_address

        self._rng = random.Random(seed)

        self._poll_interval = poll_interval

        self._state_file = Path(
            state_file
        )

        self._processed: Set[int] = set()

        self._results: Dict[int, dict] = {}

        self._running = False

        self._listener = None

        self._load_state()

    # ---------------------------------------------------------

    def start(self):

        if self._running:
            return


        # print("[Oracle] starting thread...")
        self._running = True

        self._listener = threading.Thread(
            target=self._event_loop,
            daemon=True,
        )

        self._listener.start()

    # ---------------------------------------------------------

    def stop(self):

        self._running = False

        if self._listener:
            self._listener.join()

    # ---------------------------------------------------------

    def _event_loop(self):

        from_block = max(
            0,
            self._adapter._w3.eth.block_number - 10
        )

        while self._running:

            # print("[Oracle] polling...")

            try:

                events = (
                    self._adapter
                    .get_trade_events(
                        from_block
                    )
                )

                # print("[Oracle] events:",len(events))

                for event in events:

                    # print("[Oracle] received:",event["args"]["tradeId"])

                    trade_id = (
                        event["args"][
                            "tradeId"
                        ]
                    )

                    block_number = (
                        event[
                            "blockNumber"
                        ]
                    )

                    if trade_id in self._processed:
                        continue

                    self._process_trade(
                        trade_id
                    )

                    from_block = (
                        block_number + 1
                    )

            except Exception as e:

                print(
                    "[Oracle] "
                    f"listener error: {e}"
                )

            time.sleep(
                self._poll_interval
            )

    # ---------------------------------------------------------

    def _process_trade(
        self,
        trade_id: int,
    ):

        # print("[Oracle] processing:",trade_id)
        result = (
            self._draw_outcome()
        )

        retries = 3

        while retries > 0:

            # print("[Oracle] submitting:",trade_id)

            # print("before submit")

            tx = (
                self._adapter
                .submit_oracle_result(
                    trade_id,
                    result,
                )
            )

            # print("[Oracle] tx:", tx)

            if tx["success"]:

                # print("before add")

                self._processed.add(
                    trade_id
                )

                # print("after add")

                # print("[Oracle] processed:",self._processed)

                self._results[
                    trade_id
                ] = {
                    "result":
                        result,
                    "result_name":
                        self.RESULT_NAMES[
                            result
                        ],
                    "gas_used":
                        tx[
                            "gas_used"
                        ],
                    "tx_hash":
                        tx[
                            "tx_hash"
                        ],
                    "execution_time":
                        tx[
                            "execution_time"
                        ],
                }

                self._save_state()

                return

            # print("[Oracle] success:",trade_id)

            retries -= 1

            time.sleep(1)

        # print(
        #     "[Oracle] "
        #     f"failed trade "
        #     f"{trade_id}"
        # )

    # ---------------------------------------------------------

    def _draw_outcome(self):

        value = (
            self._rng.random()
        )

        if value < 0.90:
            return self.COMPLETED

        if value < 0.98:
            return self.FAILURE

        return self.FRAUD

    # ---------------------------------------------------------

    def is_processed(
        self,
        trade_id: int,
    ):

        return (
            trade_id
            in self._processed
        )

    # ---------------------------------------------------------

    def all_processed(
        self,
        trade_ids,
    ):

        return all(
            trade_id
            in self._processed
            for trade_id
            in trade_ids
        )

    # ---------------------------------------------------------

    def get_result(
        self,
        trade_id,
    ):

        return self._results.get(
            trade_id
        )

    # ---------------------------------------------------------

    def get_results(
        self,
        trade_ids,
    ):

        return {
            trade_id:
            self._results.get(
                trade_id
            )
            for trade_id
            in trade_ids
        }

    # ---------------------------------------------------------

    def pending_count(
        self,
    ):

        return len(
            self._processed
        )

    # ---------------------------------------------------------

    def _load_state(self):

        if not (
            self._state_file
            .exists()
        ):
            return

        with open(
            self._state_file,
            "r",
        ) as f:

            data = json.load(
                f
            )

        self._processed = set(
            data.get(
                "processed",
                [],
            )
        )

    # ---------------------------------------------------------

    def _save_state(self):

        with open(
            self._state_file,
            "w",
        ) as f:

            json.dump(
                {
                    "processed":
                    list(
                        self._processed
                    )
                },
                f,
                indent=4,
            )
