"""Final-order MCP confidence sizing must never increase approved risk."""

from core.order_manager import _mcp_confidence_size_multiplier


def test_high_confidence_and_layers_do_not_boost_after_risk_checks():
    assert _mcp_confidence_size_multiplier(0.99, 5) == 1.0
    assert _mcp_confidence_size_multiplier(0.85, 4) == 1.0


def test_marginal_confidence_can_only_reduce_exposure():
    assert _mcp_confidence_size_multiplier(0.59, 5) == 0.8
    assert _mcp_confidence_size_multiplier(0.01, 1) == 0.8


def test_missing_or_malformed_confidence_never_changes_size():
    assert _mcp_confidence_size_multiplier(0.0, 5) == 1.0
    assert _mcp_confidence_size_multiplier(None, None) == 1.0
    assert _mcp_confidence_size_multiplier("bad", 5) == 1.0
    assert _mcp_confidence_size_multiplier(0.9, "bad") == 1.0
