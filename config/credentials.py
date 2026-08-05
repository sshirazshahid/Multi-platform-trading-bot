"""Exchange API credentials."""
import os

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")
BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "false").lower() == "true"


BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_SECRET_KEY = os.getenv("BYBIT_SECRET_KEY", "")

BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET_KEY = os.getenv("BITGET_SECRET_KEY", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")
