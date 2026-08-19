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
# Bitget Unified Trading Account (UTA). Classic Account API keys fail with
# code 40085 on UTA accounts. Values: auto | true | false
# auto = try classic auth, on 40085 rebuild ccxt with uta=True and retry.
_BITGET_UTA_RAW = (os.getenv("BITGET_UTA", "auto") or "auto").strip().lower()
if _BITGET_UTA_RAW in ("1", "true", "yes", "on"):
    BITGET_UTA = True
elif _BITGET_UTA_RAW in ("0", "false", "no", "off"):
    BITGET_UTA = False
else:
    BITGET_UTA = "auto"  # type: ignore[assignment]

