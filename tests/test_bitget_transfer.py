"""BitgetClient.transfer ccxt-4.5 contract (2026-05-26).

Regression for the `[Bitget] Transfer failed: 'r'` warnings.

Two failure modes existed after the ccxt 4.3.43 -> 4.5.x venv upgrade:

  1. Method 1 passed the *exchange-native* account string "usdt-futures"
     to ccxt's native transfer(), which expects a *unified* account name
     and looks it up in options['accountsByType']. "usdt-futures" is not
     a key there (the unified name is "swap" -> "usdt_futures"), so toType
     resolved to None and Bitget rejected the request.

  2. Method 2 (the visible warning) called the low-level request() with
     api="private" (a string). ccxt 4.5's bitget.sign() expects `api` to be
     a 2-element list [access, endpoint_group]; indexing the string gives
     api[1] == 'r', and self.urls['api']['r'] raises KeyError: 'r'.

The fix: call ccxt's native transfer() with *unified* account names
("spot"/"swap") and drop the broken raw-request fallback. These tests pin
that contract using a fake exchange that mirrors ccxt 4.5 bitget semantics.
"""

from __future__ import annotations

from exchanges.bitget_client import BitgetClient

# ccxt 4.5 bitget options['accountsByType'] keys (the only account names
# ccxt's native transfer() accepts; anything else maps to None and fails).
_CCXT_UNIFIED_ACCOUNTS = {
    "spot",
    "cross",
    "isolated",
    "swap",
    "usdc_swap",
    "future",
    "p2p",
}


class _FakeBitgetCcxt:
    """Mirrors the parts of ccxt.bitget that transfer() touches, including
    both failure modes that produced the production warnings."""

    def __init__(self):
        self.transfer_calls: list[tuple] = []
        self.request_calls: list[tuple] = []

    def transfer(self, code, amount, fromAccount, toAccount, params=None):
        self.transfer_calls.append((code, amount, fromAccount, toAccount, params))
        # ccxt maps fromAccount/toAccount through accountsByType; an unknown
        # name -> None -> Bitget rejects. Emulate that rejection.
        if fromAccount not in _CCXT_UNIFIED_ACCOUNTS or toAccount not in _CCXT_UNIFIED_ACCOUNTS:
            raise RuntimeError(f"bitget transfer: unknown account {fromAccount!r}/{toAccount!r}")
        return {"id": "1", "status": "ok"}

    def request(self, path, api="public", method="GET", params=None, **kw):
        self.request_calls.append((path, api, method, params))
        # ccxt 4.5 bitget.sign(): `api` must be a 2-element list. A bare string
        # gets indexed char-by-char -> api[1] == 'r' for "private" -> KeyError.
        if isinstance(api, str):
            raise KeyError(api[1] if len(api) > 1 else api)
        return {"code": "00000"}


def _client_with(fake) -> BitgetClient:
    c = BitgetClient.__new__(BitgetClient)
    c._connected = True
    c.exchange = fake
    return c


def test_ccxt_error_diagnostics_redact_credentials(monkeypatch):
    monkeypatch.setenv("BITGET_API_KEY", "ENV_KEY_SHOULD_NOT_LEAK")
    monkeypatch.setenv("BITGET_SECRET_KEY", "ENV_SECRET_SHOULD_NOT_LEAK")
    monkeypatch.setenv("BITGET_PASSPHRASE", "ENV_PASS_SHOULD_NOT_LEAK")

    class _HttpState:
        last_http_response = (
            '{"accessToken":"TOKEN_SHOULD_NOT_LEAK",'
            '"secret":"ENV_SECRET_SHOULD_NOT_LEAK"}'
        )
        last_request_url = (
            "https://api.bitget.com/private?apiKey=ENV_KEY_SHOULD_NOT_LEAK"
            "&signature=SIGNATURE_SHOULD_NOT_LEAK"
        )

    client = BitgetClient.__new__(BitgetClient)
    client.exchange = _HttpState()
    detail = client._describe_ccxt_error(
        RuntimeError("Authorization: Bearer BEARER_SHOULD_NOT_LEAK")
    )

    for secret in (
        "ENV_KEY_SHOULD_NOT_LEAK",
        "ENV_SECRET_SHOULD_NOT_LEAK",
        "ENV_PASS_SHOULD_NOT_LEAK",
        "TOKEN_SHOULD_NOT_LEAK",
        "SIGNATURE_SHOULD_NOT_LEAK",
        "BEARER_SHOULD_NOT_LEAK",
    ):
        assert secret not in detail
    assert "[REDACTED]" in detail


def test_spot_to_futures_uses_unified_swap_name():
    fake = _FakeBitgetCcxt()
    client = _client_with(fake)

    ok = client.transfer(10.0, "spot", "futures")

    assert ok is True
    assert len(fake.transfer_calls) == 1
    code, amount, frm, to, _ = fake.transfer_calls[0]
    assert code == "USDT"
    assert amount == 10.0
    assert frm == "spot"
    assert to == "swap"  # unified name, NOT "usdt-futures"


def test_futures_to_spot_uses_unified_swap_name():
    fake = _FakeBitgetCcxt()
    client = _client_with(fake)

    ok = client.transfer(7.5, "futures", "spot")

    assert ok is True
    code, amount, frm, to, _ = fake.transfer_calls[0]
    assert frm == "swap"
    assert to == "spot"


def test_never_uses_broken_raw_request_path():
    """The raw request(api='private') fallback is the source of KeyError 'r'.
    A successful native transfer must mean request() is never called."""
    fake = _FakeBitgetCcxt()
    client = _client_with(fake)

    client.transfer(5.0, "spot", "futures")

    assert fake.request_calls == [], (
        "transfer() must not fall back to the broken raw request(api='private') "
        "path; that path raises KeyError 'r' on ccxt 4.5"
    )


def test_mix_usdt_alias_maps_to_swap():
    """Some callers use the legacy 'mix_usdt' account name."""
    fake = _FakeBitgetCcxt()
    client = _client_with(fake)

    ok = client.transfer(3.0, "spot", "mix_usdt")

    assert ok is True
    _, _, frm, to, _ = fake.transfer_calls[0]
    assert frm == "spot"
    assert to == "swap"


def test_returns_false_on_exchange_error():
    """A genuine ccxt error must be swallowed into a False return, not raised."""

    class _Boom(_FakeBitgetCcxt):
        def transfer(self, *a, **k):
            raise RuntimeError("insufficient balance")

    client = _client_with(_Boom())
    assert client.transfer(10.0, "spot", "futures") is False


def test_no_op_when_not_connected():
    client = BitgetClient.__new__(BitgetClient)
    client._connected = False
    client.exchange = None
    assert client.transfer(10.0, "spot", "futures") is False
