from web3 import Web3
import json
import time
import traceback
import configparser
from web3 import Account
import random
import requests
import os
from decimal import Decimal
import time


class DexBot:
    def __init__(self):
        self.conf = configparser.ConfigParser()
        self.conf.read("./config.txt", encoding="utf-8")

        self.rpc = self.conf.get("config", "rpc")
        self.web3 = Web3(Web3.HTTPProvider(self.rpc))

        # BSCScan API configuration
        self.bscscan_url = "https://api.bscscan.com/api"
        self.bscscan_api_key = self.conf.get("config", "bscscan_api_key")

        # Configuration parameters
        self.router_address = self.web3.to_checksum_address(
            self.conf.get("config", "routerAddress")
        )

        self.bnb_address = self.web3.to_checksum_address(
            self.conf.get("config", "bnbAddress")
        )
        self.token_address = self.web3.to_checksum_address(
            self.conf.get("config", "tokenAddress")
        )
        self.slippage = self.conf.getfloat("config", "slippage")
        self.min_amount = self.conf.getfloat("config", "minAmount")
        self.max_amount = self.conf.getfloat("config", "maxAmount")
        self.buy_open = self.conf.getint("config", "buyopen")
        self.sell_open = self.conf.getint("config", "sellopen")
        self.timegap_min = self.conf.getint("config", "timegapMin")
        self.timegap_max = self.conf.getint("config", "timegapMax")
        self.gas_price = self.conf.getint("config", "gasPrice")

        # Fetch ABIs dynamically
        self.token_abi = self.get_contract_abi(self.token_address)
        self.router_abi = self.get_contract_abi(self.router_address)

        self.dex_router = self.web3.eth.contract(
            address=self.router_address, abi=self.router_abi
        )
        self.token_contract = self.web3.eth.contract(
            address=self.token_address, abi=self.token_abi
        )

        # Wallet management attributes
        self.wallets = []
        self.successful_buys = []

    def get_random_timegap(self):
        """Returns a random time gap between min and max values"""
        return random.randint(self.timegap_min, self.timegap_max)

    def load_wallets(self, filename="private_keys.txt"):
        """Load private keys from file"""
        try:
            with open(filename, "r") as f:
                private_keys = [line.strip() for line in f if line.strip()]

            print(f"Loaded {len(private_keys)} wallets from {filename}")

            # Convert to wallet objects with addresses
            for pk in private_keys:
                account = Account.from_key(pk)
                self.wallets.append({"private_key": pk, "address": account.address})

            return True
        except Exception as e:
            print(f"Error loading wallets: {str(e)}")
            return False

    def get_contract_abi(self, contract_address):
        """Get contract ABI from BSCScan"""
        print(f"Getting contract address: {contract_address}")
        try:
            response = requests.get(
                self.bscscan_url,
                params={
                    "apikey": self.bscscan_api_key,
                    "module": "contract",
                    "action": "getabi",
                    "address": contract_address,
                },
            )
            response.raise_for_status()  # Raise exception for bad status codes
            result = response.json()

            if result["status"] == "1" and result["message"] == "OK":
                return json.loads(result["result"])
            else:
                raise Exception(f"Failed to get ABI: {result['message']}")

        except Exception as e:
            print(f"Error getting ABI from BSCScan: {str(e)}")
            # Fallback to local ABI files if BSCScan fails
            print("Falling back to local ABI files...")
            if contract_address.lower() == self.token_address.lower():
                with open("token_abi.json", "r") as f:
                    return json.load(f)
            else:
                with open("router_abi.json", "r") as f:
                    return json.load(f)

    def get_price(self):
        """Get buy and sell prices"""
        ask = self.dex_router.functions.getAmountsOut(
            self.web3.to_wei(self.max_amount, "ether"),
            [self.bnb_address, self.token_address],
        ).call()
        bid = self.dex_router.functions.getAmountsIn(
            self.web3.to_wei(self.max_amount, "ether"),
            [self.token_address, self.bnb_address],
        ).call()

        ask_amount = ask[1]
        bid_amount = bid[0]
        ask = ask[1] / ask[0]
        bid = bid[0] / bid[1]

        return [ask, bid, ask_amount, bid_amount]

    def check_tx(self, tx_hash):
        """Check if transaction is successful"""
        tx_hash = self.web3.to_hex(tx_hash)
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
        receipt = json.loads(Web3.to_json(receipt))

        if receipt["status"] == 1:
            print("Transaction successful✅")
            return True
        else:
            print("Transaction failed❌")
            return False

    def get_token_balance(self, address):
        """Get token balance in the wallet"""
        balance = self.token_contract.functions.balanceOf(address).call()
        return self.web3.from_wei(balance, "ether")

    def buy_token(self, wallet, buy_amount=None):
        """Buy tokens"""
        print(f"Buying tokens with wallet: {wallet['address']}")
        try:
            private_key = wallet["private_key"]
            address = wallet["address"]

            if buy_amount is None:
                buy_amount = self.min_amount

            gas_price = self.web3.eth.gas_price
            last_nonce = self.web3.eth.get_transaction_count(address)

            # Calculate gas cost
            gas_limit = 300000
            gas_cost = gas_limit * gas_price

            # Ensure we have enough BNB for both value and gas
            total_cost = self.web3.to_wei(buy_amount, "ether") + gas_cost
            balance = self.web3.eth.get_balance(address)

            if balance < total_cost:
                print("Insufficient funds for buy transaction")
                print(f"Required: {self.web3.from_wei(total_cost, 'ether')} BNB")
                print(f"Available: {self.web3.from_wei(balance, 'ether')} BNB")
                return False

            # Prepare transaction
            swap_tx = self.dex_router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                0,  # Set min output to 0 to ensure transaction goes through
                [self.bnb_address, self.token_address],
                address,
                int(time.time() + 60),  # 1 minute deadline
            ).build_transaction(
                {
                    "from": address,
                    "value": self.web3.to_wei(buy_amount, "ether"),
                    "gas": gas_limit,
                    "gasPrice": gas_price,
                    "nonce": last_nonce,
                }
            )

            signed_txn = self.web3.eth.account.sign_transaction(
                swap_tx, private_key=private_key
            )
            tx_token = self.web3.eth.send_raw_transaction(signed_txn.raw_transaction)
            print(f"Buy transaction hash: {self.web3.to_hex(tx_token)}")

            success = self.check_tx(tx_token)
            if success:
                # Add to successful buys for later selling
                self.successful_buys.append(wallet)
            return success

        except Exception as e:
            print(f"Error in buy_token: {str(e)}")
            traceback.print_exc()
            return False

    def sell_token(self, wallet):
        """Sell tokens"""
        print(f"Selling tokens from wallet: {wallet['address']}")
        try:
            private_key = wallet["private_key"]
            address = wallet["address"]

            balance = self.get_token_balance(address)
            if balance <= 0:
                print("No token balance available to sell")
                return False

            gas_price = self.web3.eth.gas_price
            gas_limit = 300000

            # Check if we have enough BNB for gas
            gas_cost = gas_limit * gas_price
            bnb_balance = self.web3.eth.get_balance(address)

            if bnb_balance < gas_cost:
                print("Insufficient BNB for sell transaction")
                print(f"Required: {self.web3.from_wei(gas_cost, 'ether')} BNB")
                print(f"Available: {self.web3.from_wei(bnb_balance, 'ether')} BNB")
                return False

            # First approve token spending
            approve_tx = self.token_contract.functions.approve(
                self.router_address,
                2**256 - 1,  # Max approval
            ).build_transaction(
                {
                    "from": address,
                    "gasPrice": gas_price,
                    "nonce": self.web3.eth.get_transaction_count(address),
                    "gas": 100000,  # Gas for approval
                }
            )

            signed_txn = self.web3.eth.account.sign_transaction(
                approve_tx, private_key=private_key
            )
            tx_token = self.web3.eth.send_raw_transaction(signed_txn.raw_transaction)
            print(f"Approval transaction hash: {self.web3.to_hex(tx_token)}")

            # Wait for approval to be mined
            approval_success = self.check_tx(tx_token)
            if not approval_success:
                print("Approval failed, cannot sell")
                return False

            time.sleep(5)  # Wait for approval to be recognized

            # Execute sell transaction
            swap_tx = self.dex_router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                self.web3.to_wei(balance, "ether"),
                0,  # Set min output to 0
                [self.token_address, self.bnb_address],
                address,
                int(time.time() + 60),  # 1 minute deadline
            ).build_transaction(
                {
                    "from": address,
                    "gas": gas_limit,
                    "gasPrice": gas_price,
                    "nonce": self.web3.eth.get_transaction_count(address),
                }
            )

            signed_txn = self.web3.eth.account.sign_transaction(
                swap_tx, private_key=private_key
            )
            tx_token = self.web3.eth.send_raw_transaction(signed_txn.raw_transaction)
            print(f"Sell transaction hash: {self.web3.to_hex(tx_token)}")

            return self.check_tx(tx_token)

        except Exception as e:
            print(f"Error in sell_token: {str(e)}")
            traceback.print_exc()
            return False

    def run(self):
        """Start trading bot with a pattern of 3 buys then 1 sell"""
        print("Starting bot with pattern: 3 buys -> 1 sell")

        if not self.wallets:
            print("No wallets loaded. Please load wallets first.")
            return

        wallet_index = 0
        buy_count = 0
        
        # If buying is disabled but selling is enabled, check all wallets for existing tokens
        if self.buy_open == 0 and self.sell_open == 1:
            print("Buy function disabled, checking wallets for existing tokens...")
        for wallet in self.wallets:
            balance = self.get_token_balance(wallet['address'])
            
            if balance > 0:
                print(f"Wallet {wallet['address']} has {balance} tokens")
                self.successful_buys.append(wallet)

        if not self.successful_buys:
            print("No wallets found with tokens. Cannot perform sell operations.")
            

        while wallet_index < len(self.wallets) or self.successful_buys:
            try:
                if self.buy_open == 1:
                    # Buy 3 times
                    buys_to_perform = min(3, len(self.wallets) - wallet_index)
                    print(f"\n--- Preparing to execute {buys_to_perform} buys ---")

                    for i in range(buys_to_perform):
                        current_wallet = self.wallets[wallet_index]
                        print(
                            f"Buy {i + 1}/{buys_to_perform} - Using wallet: {current_wallet['address']}"
                        )

                        success = self.buy_token(current_wallet, self.min_amount)
                        if success:
                            buy_count += 1
                            print(f"Buy successful! Total successful buys: {buy_count}")
                        else:
                            print("Buy failed")

                        wallet_index += 1

                        # Check if we've processed all wallets
                        if wallet_index >= len(self.wallets):
                            break

                        # Wait between buys with random time
                        wait_time = self.get_random_timegap()
                        print(f"Waiting {wait_time} seconds before next operation...")
                        time.sleep(wait_time)
                        
                else:
                    print("Buy function disabled, skipping buy process...")

                # After 3 buys, do a sell from the first successful buy
                if self.successful_buys and self.sell_open == 1:
                    print("\n--- Executing sell operation ---")
                    wallet_to_sell = self.successful_buys.pop(0)
                    print(f"Selling from wallet: {wallet_to_sell['address']}")

                    sell_success = self.sell_token(wallet_to_sell)
                    if sell_success:
                        print("Sell successful!")
                    else:
                        print("Sell failed, returning wallet to buy list")
                        # If sell fails, put it back for later selling
                        self.successful_buys.append(wallet_to_sell)
                else:
                    print("\n--- No wallets available for selling, continuing with buys ---")

                # Wait before next cycle with random time
                wait_time = self.get_random_timegap()
                print(f"Waiting⌛️ {wait_time} seconds before next cycle...")
                time.sleep(wait_time)

            except Exception as error:
                print(f"Error occurred: {error}")
                traceback.print_exc()
                time.sleep(1)
                continue


if __name__ == "__main__":
    bot = DexBot()

    # Load wallets from file
    if bot.load_wallets():
        # Start the bot
        bot.run()
    else:
        print("Unable to load wallets. Exiting.")