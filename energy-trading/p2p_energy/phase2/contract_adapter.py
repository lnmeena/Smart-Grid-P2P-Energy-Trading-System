from __future__ import annotations

import time
import random
import json
from typing import Dict, List, Optional

from ..agent_mapper import (
    CONTRACT_WALLET_MAP,
    REP_INIT,
    INITIAL_WALLET_GWEI,
    TradeState,
)

# ── trade state uint8 → Python enum mapping (Req 11) ─────────────────────────

_SOLIDITY_STATUS_MAP = {
    0: TradeState.IN_PROGRESS,
    1: TradeState.ACTIVE,
    2: TradeState.COMPLETED,
    3: TradeState.FAILURE,
    4: TradeState.FRAUD,
    5: TradeState.CANCELLED,
}

# # ── base interface ────────────────────────────────────────────────────────────

class ContractAdapterBase:
    """Abstract interface; concrete classes below."""

    # ── Req 10: explicit getters ──────────────────────────────────────────────

    def fetch_reputation(self, meter_id: int) -> int:
        raise NotImplementedError

    def fetch_wallet(self, meter_id: int) -> str:
        raise NotImplementedError

    def fetch_balance(self, meter_id: int) -> int:
        raise NotImplementedError

    def fetch_user_info(self, meter_id: int) -> dict:
        raise NotImplementedError

    def fetch_all_balances(self, meter_ids: List[int]) -> Dict[int, int]:
        """Bulk balance fetch — one call per agent (Req 5)."""
        return {mid: self.fetch_balance(mid) for mid in meter_ids}

    # ── Req 11: trade state sync ──────────────────────────────────────────────

    def get_trade_state(self, trade_id: int) -> TradeState:
        raise NotImplementedError

    def create_trade(
        self,
        trade_id:       int,
        seller:         int,
        buyer:          int,
        energy_kwh:     int,
        base_price_gwei: int,
        price_gwei:     int,
        stake_gwei:     int,
    ) -> bool:
        raise NotImplementedError

    def submit_oracle_result(
        self,
        trade_id: int,
        result: int,   # 0=COMPLETED, 1=FAILURE, 2=FRAUD
    ) -> bool:
        raise NotImplementedError

    # ── Req 3: reputation update ──────────────────────────────────────────────

    def update_reputation(self, meter_id: int, new_reputation: int) -> bool:
        raise NotImplementedError

    def submit_window(
        self,
        timestamp,
        trade_ids,
    ) -> None:
        raise NotImplementedError

    def resolve_window(
        self,
        timestamp,
    ) -> None:
        raise NotImplementedError

    def flush(self) -> None:
        raise NotImplementedError

# # ── simulated adapter (offline/testing) ──────────────────────────────────────

class SimulatedContractAdapter(ContractAdapterBase):
    """
    In-memory simulation of the smart contract.
    Used when no live Ethereum node is available.
    Faithfully reproduces the on-chain state machine for integration testing.
    """

    def __init__(self) -> None:
        # {meter_id: {wallet, username, reputation, balance_gwei}}
        self._users:  Dict[int, dict] = {}
        # {trade_id: {seller, buyer, energy, base_price, price, stake, status, ...}}
        self._trades: Dict[int, dict] = {}
        self.window_size = 1

        self.pending_windows = {}
        # {
        #     timestamp: [trade_id1, trade_id2]
        # }

        self.trade_results = {}
        # {
        #     trade_id: TradeState
        # }

        self.trade_timestamp = {}
        # {
        #     trade_id: timestamp
        # }

    # ── Req 10 ────────────────────────────────────────────────────────────────

    def fetch_reputation(self, meter_id: int) -> int:
        """Fetch reputation from simulated contract (Req 3, 10)."""
        return self._users.get(meter_id, {}).get("reputation", REP_INIT)

    def fetch_wallet(self, meter_id: int) -> str:
        """Fetch wallet address (Req 10)."""
        return self._users.get(meter_id, {}).get(
            "wallet",
            CONTRACT_WALLET_MAP.get(meter_id, f"0xDEAD{meter_id:036x}")
        )

    def fetch_balance(self, meter_id: int) -> int:
        """Fetch gwei balance (Req 10)."""
        return self._users.get(meter_id, {}).get("balance_gwei", INITIAL_WALLET_GWEI)

    def fetch_user_info(self, meter_id: int) -> dict:
        """Bulk getter (Req 10)."""
        user = self._users.get(meter_id, {})
        return {
            "wallet":     user.get("wallet",     CONTRACT_WALLET_MAP.get(meter_id, f"0xDEAD{meter_id:036x}")),
            "reputation": user.get("reputation", REP_INIT),
            "balance":    user.get("balance_gwei", INITIAL_WALLET_GWEI),
        }

    def fetch_all_balances(self, meter_ids: List[int]) -> Dict[int, int]:
        return {mid: self.fetch_balance(mid) for mid in meter_ids}

    # ── Req 3 ─────────────────────────────────────────────────────────────────

    def update_reputation(self, meter_id: int, new_reputation: int) -> bool:
        if meter_id not in self._users:
            self._users[meter_id] = {
                "wallet": CONTRACT_WALLET_MAP.get(meter_id, f"0xDEAD{meter_id:036x}"),
                "reputation": REP_INIT,
                "balance_gwei": INITIAL_WALLET_GWEI
            }
        self._users[meter_id]["reputation"] = max(0, min(100, int(new_reputation)))
        return True

    def credit_balance(self, meter_id: int, amount_gwei: int) -> None:
        """Helper for testing: add gwei to a user's simulated balance."""
        if meter_id not in self._users:
            self._users[meter_id] = {
                "wallet": CONTRACT_WALLET_MAP.get(meter_id, f"0xDEAD{meter_id:036x}"),
                "reputation": REP_INIT,
                "balance_gwei": INITIAL_WALLET_GWEI
            }
        self._users[meter_id]["balance_gwei"] += amount_gwei

    # ── Req 11 ────────────────────────────────────────────────────────────────

    def get_trade_state(self, trade_id: int) -> TradeState:
        trade = self._trades.get(trade_id)
        if trade is None:
            raise KeyError(f"Trade {trade_id} not found")
        return _SOLIDITY_STATUS_MAP[trade["status"]]

    def create_trade(
        self,
        trade_id: int,
        seller: int,
        buyer: int,
        energy_kwh: int,
        base_price_gwei: int,
        price_gwei: int,
        stake_gwei: int,
    ) -> bool:
        if trade_id in self._trades:
            return False

        # Verify stake >= 10% of price (mirrors Solidity require)
        min_stake = (price_gwei * 10) // 100
        if stake_gwei < min_stake:
            return False

        # Ensure users exist in simulation
        for agent_id in (buyer, seller):
            if agent_id not in self._users:
                self._users[agent_id] = {
                    "wallet": CONTRACT_WALLET_MAP.get(agent_id, f"0xDEAD{agent_id:036x}"),
                    "reputation": REP_INIT,
                    "balance_gwei": INITIAL_WALLET_GWEI
                }

        # Check buyer can afford price + stake (Req 5, 6)
        buyer_balance = self.fetch_balance(buyer)
        if buyer_balance < price_gwei + stake_gwei:
            return False

        # Check seller can afford stake
        seller_balance = self.fetch_balance(seller)
        if seller_balance < stake_gwei:
            return False

        # Collect stakes
        self._users[buyer]["balance_gwei"]  -= stake_gwei
        self._users[seller]["balance_gwei"] -= stake_gwei
        # Collect price from buyer
        self._users[buyer]["balance_gwei"]  -= price_gwei

        self._trades[trade_id] = {
            "seller":         seller,
            "buyer":          buyer,
            "energy_kwh":     energy_kwh,
            "base_price":     base_price_gwei,
            "price":          price_gwei,
            "stake":          stake_gwei,
            "status":         1,              # ACTIVE (funds collected)
            "money_collected": True,
            "expiry":         time.time() + 3600,
        }
        self.trade_timestamp[trade_id] = None
        return True

    def submit_oracle_result(self, trade_id: int, result: int) -> bool:
        """
        Simulate checkOracle() on-chain (Req 8).

        result: 0=COMPLETED, 1=FAILURE, 2=FRAUD
        """
        trade = self._trades.get(trade_id)
        if trade is None or trade["status"] != 1:  # must be ACTIVE
            return False

        seller = trade["seller"]
        buyer  = trade["buyer"]
        price  = trade["price"]
        stake  = trade["stake"]

        if result == 0:    # COMPLETED (90%) → Req 8
            trade["status"] = 2
            # Pay seller
            self._users[seller]["balance_gwei"] += price + stake
            # Return buyer stake
            self._users[buyer]["balance_gwei"]  += stake
            # Reputation boost +5 each (Req 4)
            self._adjust_rep(buyer,  +5)
            self._adjust_rep(seller, +5)

        elif result == 1:  # FAILURE (8%) → Req 8
            trade["status"] = 3
            # Refund buyer price + stake; return seller stake
            self._users[buyer]["balance_gwei"]  += price + stake
            self._users[seller]["balance_gwei"] += stake
            # Reputation penalty -10 on seller (Req 4)
            self._adjust_rep(seller, -10)

        elif result == 2:  # FRAUD (2%) → Req 8
            trade["status"] = 4
            # Refund buyer price + stake
            self._users[buyer]["balance_gwei"]  += price + stake
            # Slash 10% of seller stake; return rest
            slash  = (stake * 10) // 100
            self._users[seller]["balance_gwei"] += stake - slash
            # Heavy reputation penalty -20 for seller (Req 4)
            self._adjust_rep(seller, -20)
        else:
            return False

        return True

    # ── internal ──────────────────────────────────────────────────────────────

    def _adjust_rep(self, meter_id: int, delta: int) -> None:
        if meter_id in self._users:
            current = self._users[meter_id]["reputation"]
            self._users[meter_id]["reputation"] = max(0, min(100, current + delta))

    def save_results(
        self,
        filename="trade_results.json",
    ):
        data = {}

        for trade_id, state in self.trade_results.items():
            data[str(trade_id)] = str(state)

        with open(filename, "w") as f:
            json.dump(
                data,
                f,
                indent=4,
            )

    def submit_window(
        self,
        timestamp,
        trade_ids,
    ):
        self.pending_windows[timestamp] = trade_ids

        if len(self.pending_windows) >= self.window_size:
            oldest = next(iter(self.pending_windows))
            self.resolve_window(oldest)

    def resolve_window(
        self,
        timestamp,
    ):
        if timestamp not in self.pending_windows:
            return

        trade_ids = self.pending_windows[timestamp]

        for trade_id in trade_ids:

            state = self.get_trade_state(trade_id)

            if state != TradeState.ACTIVE:
                continue

            # Your oracle probabilities
            r = random.random()

            if r < 0.90:
                result = 0
            elif r < 0.98:
                result = 1
            else:
                result = 2

            self.submit_oracle_result(
                trade_id,
                result,
            )

            self.trade_results[trade_id] = (
                self.get_trade_state(trade_id)
            )

        del self.pending_windows[timestamp]

    def flush(self):
        timestamps = list(
            self.pending_windows.keys()
        )

        for ts in timestamps:
            self.resolve_window(ts)

# class Web3ContractAdapter(ContractAdapterBase):
#     def __init__(
#         self,
#         w3,
#         contract_address: str,
#         abi: list,
#         private_key: str,
#         confirmations: int = 1,
#         max_retries: int = 3,
#     ) -> None:
#         self._w3 = w3
#
#         self._contract = w3.eth.contract(
#             address=w3.to_checksum_address(
#                 contract_address
#             ),
#             abi=abi,
#         )
#
#         self._account = (
#             w3.eth.account.from_key(
#                 private_key
#             )
#         )
#
#         self._address = self._account.address
#
#         self._confirmations = confirmations
#         self._max_retries = max_retries
#
#     # ------------------------------------------------------------------
#     # transaction helpers
#     # ------------------------------------------------------------------
#
#     def _build_transaction(
#         self,
#         fn,
#     ):
#         return fn.build_transaction(
#             {
#                 "from": self._address,
#                 "nonce": self._w3.eth.get_transaction_count(
#                     self._address,
#                     "pending",
#                 ),
#                 "gas": 500000,
#                 "gasPrice": self._w3.eth.gas_price,
#             }
#         )
#
#     def _send(
#         self,
#         fn,
#     ):
#         last_error = None
#
#         for attempt in range(
#             self._max_retries
#         ):
#             try:
#                 tx = self._build_transaction(
#                     fn
#                 )
#
#                 signed = (
#                     self._account.sign_transaction(
#                         tx
#                     )
#                 )
#
#                 tx_hash = (
#                     self._w3.eth.send_raw_transaction(
#                         signed.raw_transaction
#                     )
#                 )
#
#                 receipt = (
#                     self._w3.eth.wait_for_transaction_receipt(
#                         tx_hash
#                     )
#                 )
#
#                 current_block = (
#                     self._w3.eth.block_number
#                 )
#
#                 while (
#                     current_block
#                     < receipt.blockNumber
#                     + self._confirmations
#                 ):
#                     time.sleep(1)
#
#                     current_block = (
#                         self._w3.eth.block_number
#                     )
#
#                 return receipt
#
#             except Exception as e:
#                 last_error = e
#
#                 print(
#                     f"[Adapter] "
#                     f"attempt {attempt + 1} "
#                     f"failed: {e}"
#                 )
#
#                 time.sleep(2)
#
#         raise RuntimeError(
#             f"Transaction failed: "
#             f"{last_error}"
#         )
#
#     # ------------------------------------------------------------------
#     # getters
#     # ------------------------------------------------------------------
#
#     def fetch_reputation(
#         self,
#         meter_id: int,
#     ) -> int:
#         return (
#             self._contract.functions
#             .getUserReputation(
#                 meter_id
#             )
#             .call()
#         )
#
#     def fetch_wallet(
#         self,
#         meter_id: int,
#     ) -> str:
#         return (
#             self._contract.functions
#             .getUserWallet(
#                 meter_id
#             )
#             .call()
#         )
#
#     def fetch_balance(
#         self,
#         meter_id: int,
#     ) -> int:
#         return (
#             self._contract.functions
#             .getUserBalance(
#                 meter_id
#             )
#             .call()
#         )
#
#     def fetch_user_info(
#         self,
#         meter_id: int,
#     ) -> dict:
#         wallet, reputation, balance = (
#             self._contract.functions
#             .getUserInfo(
#                 meter_id
#             )
#             .call()
#         )
#
#         return {
#             "wallet": wallet,
#             "reputation": reputation,
#             "balance": balance,
#         }
#
#     def fetch_all_balances(
#         self,
#         meter_ids: List[int],
#     ) -> Dict[int, int]:
#         return {
#             meter_id: self.fetch_balance(
#                 meter_id
#             )
#             for meter_id in meter_ids
#         }
#
#     # ------------------------------------------------------------------
#     # reputation
#     # ------------------------------------------------------------------
#
#     def update_reputation(
#         self,
#         meter_id: int,
#         new_reputation: int,
#     ) -> bool:
#         try:
#             receipt = self._send(
#                 self._contract.functions
#                 .updateReputation(
#                     meter_id,
#                     new_reputation,
#                 )
#             )
#
#             return receipt.status == 1
#
#         except Exception as e:
#             print(
#                 "[Adapter] "
#                 f"update reputation failed: "
#                 f"{e}"
#             )
#
#             return False
#
#     # ------------------------------------------------------------------
#     # trade state
#     # ------------------------------------------------------------------
#
#     def get_trade_state(
#         self,
#         trade_id: int,
#     ) -> TradeState:
#         status = (
#             self._contract.functions
#             .getTradeStatus(
#                 trade_id
#             )
#             .call()
#         )
#
#         return _SOLIDITY_STATUS_MAP.get(
#             status,
#             TradeState.CANCELLED,
#         )
#
#     # ------------------------------------------------------------------
#     # trade creation
#     # ------------------------------------------------------------------
#
#     def create_trade(
#         self,
#         trade_id: int,
#         seller: int,
#         buyer: int,
#         energy_kwh: int,
#         base_price_gwei: int,
#         price_gwei: int,
#         stake_gwei: int,
#     ) -> bool:
#         try:
#             receipt = self._send(
#                 self._contract.functions
#                 .createTrade(
#                     trade_id,
#                     seller,
#                     buyer,
#                     energy_kwh,
#                     price_gwei,
#                     stake_gwei,
#                 )
#             )
#
#             return receipt.status == 1
#
#         except Exception as e:
#             print(
#                 "[Adapter] "
#                 f"create_trade failed: "
#                 f"{e}"
#             )
#
#             return False
#
#     # ------------------------------------------------------------------
#     # oracle submission
#     # ------------------------------------------------------------------
#
#     def submit_oracle_result(
#         self,
#         trade_id: int,
#         result: int,
#     ) -> bool:
#         try:
#             receipt = self._send(
#                 self._contract.functions
#                 .checkOracle(
#                     trade_id,
#                     result,
#                 )
#             )
#
#             return receipt.status == 1
#
#         except Exception as e:
#             print(
#                 "[Adapter] "
#                 f"oracle submission failed: "
#                 f"{e}"
#             )
#
#             return False
#
#     # ------------------------------------------------------------------
#     # event access
#     # ------------------------------------------------------------------
#
#     def get_trade_events(
#         self,
#         from_block,
#         to_block="latest",
#     ):
#         return (
#             self._contract.events
#             .TradeInitiated
#             .get_logs(
#                 from_block=from_block,
#                 to_block=to_block,
#             )
#         )
#
#     # ------------------------------------------------------------------
#     # transaction status
#     # ------------------------------------------------------------------
#
#     def wait_for_receipt(
#         self,
#         tx_hash,
#     ):
#         return (
#             self._w3.eth
#             .wait_for_transaction_receipt(
#                 tx_hash
#             )
#         )
#
#     # ------------------------------------------------------------------
#     # compatibility
#     # ------------------------------------------------------------------
#
#     def submit_window(
#         self,
#         timestamp,
#         trade_ids,
#     ):
#         pass
#
#     def resolve_window(
#         self,
#         timestamp,
#     ):
#         pass
#
#     def flush(
#         self,
#     ):
#         pass
#
#     def save_results(
#         self,
#         filename="trade_results.json",
#     ):
#         pass
















class Web3ContractAdapter(ContractAdapterBase):

    def __init__(
        self,
        w3,
        contract_address: str,
        abi: list,
        private_key: str,
        confirmations: int = 0,
        max_retries: int = 3,
    ) -> None:

        self._w3 = w3

        self._contract = w3.eth.contract(
            address=w3.to_checksum_address(
                contract_address
            ),
            abi=abi,
        )

        self._account = (
            w3.eth.account.from_key(
                private_key
            )
        )

        self._address = (
            self._account.address
        )

        self._confirmations = confirmations
        self._max_retries = max_retries

    # ----------------------------------------------------------

    def _build_transaction(
        self,
        fn,
    ):
        return fn.build_transaction(
            {
                "from": self._address,
                "nonce": self._w3.eth.get_transaction_count(
                    self._address,
                    "pending",
                ),
                "gas": 500000,
                "gasPrice": (
                    self._w3.eth.gas_price
                ),
                "chainId": (
                    self._w3.eth.chain_id
                ),
            }
        )

    # ----------------------------------------------------------

    def _send(
        self,
        fn,
    ):

        last_error = None

        for attempt in range(
            self._max_retries
        ):

            try:

                start_time = time.perf_counter()

                tx = (
                    self._build_transaction(
                        fn
                    )
                )

                signed = (
                    self._account
                    .sign_transaction(
                        tx
                    )
                )

                tx_hash = (
                    self._w3.eth
                    .send_raw_transaction(
                        signed.raw_transaction
                    )
                )

                receipt = (
                    self._w3.eth
                    .wait_for_transaction_receipt(
                        tx_hash
                    )
                )

                elapsed = (
                    time.perf_counter()
                    - start_time
                )

                current_block = (
                    self._w3.eth.block_number
                )

                while (
                    self._confirmations > 0
                    and current_block
                    < receipt.blockNumber
                    + self._confirmations
                ):
                    time.sleep(0.1)

                    current_block = (
                        self._w3.eth.block_number
                    )

                return {
                    "success":
                        receipt.status == 1,
                    "receipt":
                        receipt,
                    "tx_hash":
                        receipt.transactionHash.hex(),
                    "gas_used":
                        receipt.gasUsed,
                    "execution_time":
                        elapsed,
                }

            except Exception as e:

                last_error = e

                # print(
                #     f"[Adapter] "
                #     f"attempt "
                #     f"{attempt + 1} "
                #     f"failed: {e}"
                # )

                time.sleep(1)

        raise RuntimeError(
            f"Transaction failed: "
            f"{last_error}"
        )

    # ----------------------------------------------------------
    # getters
    # ----------------------------------------------------------

    def fetch_reputation(
        self,
        meter_id: int,
    ) -> int:
        return (
            self._contract.functions
            .getUserReputation(
                meter_id
            )
            .call()
        )

    def fetch_wallet(
        self,
        meter_id: int,
    ) -> str:
        return (
            self._contract.functions
            .getUserWallet(
                meter_id
            )
            .call()
        )

    def fetch_balance(
        self,
        meter_id: int,
    ) -> int:
        return (
            self._contract.functions
            .getUserBalance(
                meter_id
            )
            .call()
        )

    def fetch_user_info(
        self,
        meter_id: int,
    ) -> dict:

        wallet, reputation, balance = (
            self._contract.functions
            .getUserInfo(
                meter_id
            )
            .call()
        )

        return {
            "wallet": wallet,
            "reputation": reputation,
            "balance": balance,
        }

    def fetch_all_balances(
        self,
        meter_ids: List[int],
    ) -> Dict[int, int]:

        return {
            meter_id:
            self.fetch_balance(
                meter_id
            )
            for meter_id in meter_ids
        }

    # ----------------------------------------------------------
    # reputation
    # ----------------------------------------------------------

    def update_reputation(
        self,
        meter_id: int,
        new_reputation: int,
    ) -> bool:

        try:

            result = (
                self._send(
                    self._contract
                    .functions
                    .updateReputation(
                        meter_id,
                        new_reputation,
                    )
                )
            )

            return result["success"]

        except Exception as e:

            # print(
            #     "[Adapter] "
            #     f"update reputation "
            #     f"failed: {e}"
            # )

            return False

    # ----------------------------------------------------------
    # trade state
    # ----------------------------------------------------------

    def get_trade_state(
        self,
        trade_id: int,
    ) -> TradeState:

        status = (
            self._contract.functions
            .getTradeStatus(
                trade_id
            )
            .call()
        )

        return (
            _SOLIDITY_STATUS_MAP.get(
                status,
                TradeState.CANCELLED,
            )
        )

    # ----------------------------------------------------------
    # create trade
    # ----------------------------------------------------------

    def create_trade(
        self,
        trade_id: int,
        seller: int,
        buyer: int,
        energy_kwh: int,
        base_price_gwei: int,
        price_gwei: int,
        stake_gwei: int,
    ):

        try:

            result = (
                self._send(
                    self._contract
                    .functions
                    .createTrade(
                        trade_id,
                        seller,
                        buyer,
                        energy_kwh,
                        price_gwei,
                        stake_gwei,
                    )
                )
            )

            result["trade_id"] = (
                trade_id
            )

            result["seller"] = (
                seller
            )

            result["buyer"] = (
                buyer
            )

            return result

        except Exception as e:

            # print(
            #     "[Adapter] "
            #     f"create_trade "
            #     f"failed: {e}"
            # )

            return {
                "success": False,
                "trade_id": trade_id,
                "error": str(e),
            }

    # ----------------------------------------------------------
    # oracle
    # ----------------------------------------------------------

    def submit_oracle_result(
        self,
        trade_id: int,
        result: int,
    ):

        try:

            tx = (
                self._send(
                    self._contract
                    .functions
                    .checkOracle(
                        trade_id,
                        result,
                    )
                )
            )

            tx["trade_id"] = (
                trade_id
            )

            tx["oracle_result"] = (
                result
            )

            return tx

        except Exception as e:

            # print(
            #     "[Adapter] "
            #     f"oracle failed: "
            #     f"{e}"
            # )

            return {
                "success": False,
                "trade_id": trade_id,
            }

    # ----------------------------------------------------------
    # events
    # ----------------------------------------------------------

    def get_trade_events(
        self,
        from_block,
        to_block="latest",
    ):

        return (
            self._contract.events
            .TradeInitiated
            .get_logs(
                from_block=from_block,
                to_block=to_block,
            )
        )

    # ----------------------------------------------------------

    def wait_for_receipt(
        self,
        tx_hash,
    ):

        return (
            self._w3.eth
            .wait_for_transaction_receipt(
                tx_hash
            )
        )

    # ----------------------------------------------------------
    # compatibility
    # ----------------------------------------------------------

    def submit_window(
        self,
        timestamp,
        trade_ids,
    ):
        pass

    def resolve_window(
        self,
        timestamp,
    ):
        pass

    def flush(
        self,
    ):
        pass

    def save_results(
        self,
        filename="trade_results.json",
    ):
        pass
