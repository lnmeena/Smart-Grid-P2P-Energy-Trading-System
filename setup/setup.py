# import json
# from web3 import Web3
#
# RPC_URL = "http://127.0.0.1:8545"
# CONTRACT_ADDRESS = "0xACc3c20A313B96F59a198aC98588Dfdb53425085"
# PYTHON_ENGINE_ADDRESS = "0xA56160A359F2EAa66f5c9df5245542B07339A9a6"
# ENGINE_ORACLE_ADDRESS = "0xA56160A359F2EAa66f5c9df5245542B07339A9a6"
#
# PRIVATE_KEY = (
#     "0x0000000000000000000000000000000000000000000000000000000000000001"
# )
#
# REGISTER_COUNT = 19
# PAY_AMOUNT = Web3.to_wei(10, "ether")
#
# w3 = Web3(Web3.HTTPProvider(RPC_URL))
#
# if not w3.is_connected():
#     raise Exception("Failed to connect to RPC")
#
# with open("contract_abi.json", "r") as f:
#     abi = json.load(f)
#
# contract = w3.eth.contract(
#     address=Web3.to_checksum_address(CONTRACT_ADDRESS),
#     abi=abi
# )
#
# account = w3.eth.account.from_key(PRIVATE_KEY)
# address = account.address
#
# print(f"Using wallet: {address}")
#
# try:
#     print(f"Setting Python Engine to {PYTHON_ENGINE_ADDRESS}...")
#
#     nonce = w3.eth.get_transaction_count(address)
#
#     tx = contract.functions.setPythonEngine(
#         Web3.to_checksum_address(PYTHON_ENGINE_ADDRESS)
#     ).build_transaction({
#         "from": address,
#         "nonce": nonce,
#         "gas": 300000,
#         "gasPrice": w3.eth.gas_price,
#         "chainId": w3.eth.chain_id,
#     })
#
#     signed_tx = account.sign_transaction(tx)
#     tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
#     receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
#
#     if receipt.status == 1:
#         print("Python Engine successfully set.")
#     else:
#         print("Failed to set Python Engine.")
#
# except Exception as e:
#     print(f"Error setting Python Engine: {e}")
#
# try:
#     print(f"Setting Engine Oracle to {ENGINE_ORACLE_ADDRESS}...")
#
#     nonce = w3.eth.get_transaction_count(address)
#
#     tx = contract.functions.setEngineOracle(
#         Web3.to_checksum_address(ENGINE_ORACLE_ADDRESS)
#     ).build_transaction({
#         "from": address,
#         "nonce": nonce,
#         "gas": 300000,
#         "gasPrice": w3.eth.gas_price,
#         "chainId": w3.eth.chain_id,
#     })
#
#     signed_tx = account.sign_transaction(tx)
#     tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
#     receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
#
#     if receipt.status == 1:
#         print("Engine Oracle successfully set.")
#     else:
#         print("Failed to set Engine Oracle.")
#
# except Exception as e:
#     print(f"Error setting Engine Oracle: {e}")
#
# for meter_id in range(1, REGISTER_COUNT + 1):
#     try:
#         print(f"\nMeter ID: {meter_id}")
#         print(f"Wallet: {address}")
#
#         if contract.functions.isUserRegistered(meter_id).call():
#             print("Already registered. Skipping registration.")
#         else:
#             nonce = w3.eth.get_transaction_count(address)
#
#             tx = contract.functions.registerUser(
#                 meter_id
#             ).build_transaction({
#                 "from": address,
#                 "nonce": nonce,
#                 "gas": 300000,
#                 "gasPrice": w3.eth.gas_price,
#                 "chainId": w3.eth.chain_id,
#             })
#
#             signed_tx = account.sign_transaction(tx)
#
#             tx_hash = w3.eth.send_raw_transaction(
#                 signed_tx.raw_transaction
#             )
#
#             receipt = w3.eth.wait_for_transaction_receipt(
#                 tx_hash
#             )
#
#             if receipt.status == 1:
#                 print("User registered.")
#             else:
#                 print("Registration failed.")
#                 continue
#
#         nonce = w3.eth.get_transaction_count(address)
#
#         tx = contract.functions.payAmount(
#             meter_id
#         ).build_transaction({
#             "from": address,
#             "value": PAY_AMOUNT,
#             "nonce": nonce,
#             "gas": 300000,
#             "gasPrice": w3.eth.gas_price,
#             "chainId": w3.eth.chain_id,
#         })
#
#         signed_tx = account.sign_transaction(tx)
#
#         tx_hash = w3.eth.send_raw_transaction(
#             signed_tx.raw_transaction
#         )
#
#         receipt = w3.eth.wait_for_transaction_receipt(
#             tx_hash
#         )
#
#         if receipt.status == 1:
#             balance = contract.functions.getUserBalance(
#                 meter_id
#             ).call()
#
#             print(
#                 f"Deposit successful. Stored balance: "
#                 f"{Web3.from_wei(balance, 'ether')} ETH"
#             )
#         else:
#             print("Payment failed.")
#
#     except Exception as e:
#         print(f"Meter {meter_id} failed:")
#         print(e)


























import json
from web3 import Web3

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

RPC_URL = "http://127.0.0.1:8545"

CONTRACT_ADDRESS = (
    "0xF2E246BB76DF876Cef8b38ae84130F4F55De395b"
)

ACCOUNT_ADDRESS = (
    "0xA56160A359F2EAa66f5c9df5245542B07339A9a6"
)

OWNER_PRIVATE_KEY = (
    "0x0000000000000000000000000000000000000000000000000000000000000001"
)

REGISTER_COUNT = 19

PAY_AMOUNT = Web3.to_wei(
    10,
    "ether",
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
    raise Exception(
        "Failed to connect to RPC"
    )

with open(
    "contract_abi.json",
    "r",
) as f:
    abi = json.load(f)

contract = w3.eth.contract(
    address=Web3.to_checksum_address(
        CONTRACT_ADDRESS
    ),
    abi=abi,
)

# ------------------------------------------------------------------
# Owner account
# ------------------------------------------------------------------

owner_account = (
    w3.eth.account.from_key(
        OWNER_PRIVATE_KEY
    )
)

owner_address = (
    owner_account.address
)

print(
    f"Owner wallet: "
    f"{owner_address}"
)

# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def send_transaction(
    account,
    tx,
):
    signed_tx = (
        account.sign_transaction(
            tx
        )
    )

    tx_hash = (
        w3.eth.send_raw_transaction(
            signed_tx.raw_transaction
        )
    )

    return (
        w3.eth.wait_for_transaction_receipt(
            tx_hash
        )
    )

# ------------------------------------------------------------------
# Set Python Engine
# ------------------------------------------------------------------

try:

    print(
        f"Setting Python Engine:"
        f" {ACCOUNT_ADDRESS}"
    )

    nonce = (
        w3.eth.get_transaction_count(
            owner_address
        )
    )

    tx = (
        contract.functions
        .setPythonEngine(
            Web3.to_checksum_address(
                ACCOUNT_ADDRESS
            )
        )
        .build_transaction({
            "from": owner_address,
            "nonce": nonce,
            "gas": 300000,
            "gasPrice": (
                w3.eth.gas_price
            ),
            "chainId": (
                w3.eth.chain_id
            ),
        })
    )

    receipt = send_transaction(
        owner_account,
        tx,
    )

    if receipt.status == 1:
        print(
            "Python Engine set."
        )

except Exception as e:
    print(
        "Failed to set "
        f"Python Engine: {e}"
    )

# ------------------------------------------------------------------
# Set Engine Oracle
# ------------------------------------------------------------------

try:

    print(
        f"Setting Engine Oracle:"
        f" {ACCOUNT_ADDRESS}"
    )

    nonce = (
        w3.eth.get_transaction_count(
            owner_address
        )
    )

    tx = (
        contract.functions
        .setEngineOracle(
            Web3.to_checksum_address(
                ACCOUNT_ADDRESS
            )
        )
        .build_transaction({
            "from": owner_address,
            "nonce": nonce,
            "gas": 300000,
            "gasPrice": (
                w3.eth.gas_price
            ),
            "chainId": (
                w3.eth.chain_id
            ),
        })
    )

    receipt = send_transaction(
        owner_account,
        tx,
    )

    if receipt.status == 1:
        print(
            "Engine Oracle set."
        )

except Exception as e:
    print(
        "Failed to set "
        f"Engine Oracle: {e}"
    )

# ------------------------------------------------------------------
# Register users
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# Register users and deposit funds
# ------------------------------------------------------------------

for meter_id in range(
    1,
    REGISTER_COUNT + 1,
):

    try:

        print(f"\nMeter {meter_id}")

        # --------------------------------------------------
        # User account
        # meter 1 -> key 0x...02
        # meter 2 -> key 0x...03
        # ...
        # meter 19 -> key 0x...14
        # --------------------------------------------------

        user_private_key = (
            "0x"
            + format(
                meter_id + 1,
                "064x",
            )
        )

        user_account = (
            w3.eth.account.from_key(
                user_private_key
            )
        )

        user_address = (
            user_account.address
        )

        print(
            f"User wallet: "
            f"{user_address}"
        )

        # --------------------------------------------------
        # Register user
        # --------------------------------------------------

        if (
            contract.functions
            .isUserRegistered(
                meter_id
            )
            .call()
        ):

            print(
                "Already registered."
            )

        else:

            nonce = (
                w3.eth.get_transaction_count(
                    user_address
                )
            )

            tx = (
                contract.functions
                .registerUser(
                    meter_id
                )
                .build_transaction({
                    "from": user_address,
                    "nonce": nonce,
                    "gas": 300000,
                    "gasPrice": (
                        w3.eth.gas_price
                    ),
                    "chainId": (
                        w3.eth.chain_id
                    ),
                })
            )

            receipt = (
                send_transaction(
                    user_account,
                    tx,
                )
            )

            if receipt.status == 1:

                print(
                    "User registered."
                )

            else:

                print(
                    "Registration failed."
                )

                continue

        # --------------------------------------------------
        # Deposit funds
        # --------------------------------------------------

        nonce = (
            w3.eth.get_transaction_count(
                user_address
            )
        )

        tx = (
            contract.functions
            .payAmount(
                meter_id
            )
            .build_transaction({
                "from": user_address,
                "value": PAY_AMOUNT,
                "nonce": nonce,
                "gas": 300000,
                "gasPrice": (
                    w3.eth.gas_price
                ),
                "chainId": (
                    w3.eth.chain_id
                ),
            })
        )

        receipt = (
            send_transaction(
                user_account,
                tx,
            )
        )

        if receipt.status == 1:

            balance = (
                contract.functions
                .getUserBalance(
                    meter_id
                )
                .call()
            )

            print(
                "Deposit successful."
            )

            print(
                "Balance:",
                Web3.from_wei(
                    balance,
                    "ether",
                ),
                "ETH",
            )

        else:

            print(
                "Payment failed."
            )

    except Exception as e:

        print(
            f"Meter {meter_id} failed:"
        )

        print(e)
