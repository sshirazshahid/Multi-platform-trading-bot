import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "audit_constructor_safety.py"
SPEC = importlib.util.spec_from_file_location("audit_constructor_safety", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_allows_read_only_constructor_calls():
    source = """
class Adapter:
    def __init__(self):
        self._init_exchange()
    def _init_exchange(self):
        self.exchange.load_markets()
        self.exchange.load_time_difference()
        self.exchange.fetch_positions()
        self.exchange.set_sandbox_mode(True)
"""
    assert MODULE.audit_source(source) == []


def test_flags_direct_account_mode_mutation():
    source = """
class Adapter:
    def _init_exchange(self):
        self.exchange.set_position_mode(False)
"""
    findings = MODULE.audit_source(source, "adapter.py")
    assert len(findings) == 1
    assert findings[0]["call"] == "self.exchange.set_position_mode"
    assert findings[0]["path"] == "adapter.py"


def test_follows_same_class_helpers_from_constructor():
    source = """
class Adapter:
    def __init__(self):
        self._configure()
    def _configure(self):
        self.exchange.set_leverage(5, "BTC/USDT:USDT")
"""
    findings = MODULE.audit_source(source)
    assert [item["call"] for item in findings] == ["self.exchange.set_leverage"]


def test_does_not_flag_unreachable_runtime_order_method():
    source = """
class Adapter:
    def __init__(self):
        self.exchange.load_markets()
    def place(self):
        self.exchange.create_order("BTC/USDT:USDT", "limit", "buy", 1, 1)
"""
    assert MODULE.audit_source(source) == []


def test_flags_raw_private_post_reachable_from_init():
    source = """
class Adapter:
    def _init_exchange(self):
        self.exchange.fapiPrivatePostPositionSideDual({"dualSidePosition": False})
"""
    findings = MODULE.audit_source(source)
    assert findings[0]["call"].endswith("fapiPrivatePostPositionSideDual")
