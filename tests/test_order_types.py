import pytest

from mt5_mcp.order_types import resolve_filling_mode, resolve_order_type


class _FakeMT5:
    ORDER_TYPE_BUY = "BUY"
    ORDER_TYPE_SELL = "SELL"
    ORDER_TYPE_BUY_LIMIT = "BUY_LIMIT"
    ORDER_TYPE_SELL_LIMIT = "SELL_LIMIT"
    ORDER_TYPE_BUY_STOP = "BUY_STOP"
    ORDER_TYPE_SELL_STOP = "SELL_STOP"
    SYMBOL_FILLING_FOK = 1
    SYMBOL_FILLING_IOC = 2
    ORDER_FILLING_FOK = "FOK"
    ORDER_FILLING_IOC = "IOC"
    ORDER_FILLING_RETURN = "RETURN"


@pytest.mark.parametrize(
    "order_type,side,expected",
    [
        ("market", "buy", "BUY"),
        ("market", "sell", "SELL"),
        ("limit", "buy", "BUY_LIMIT"),
        ("limit", "sell", "SELL_LIMIT"),
        ("stop", "buy", "BUY_STOP"),
        ("stop", "sell", "SELL_STOP"),
        ("MARKET", "BUY", "BUY"),  # case-insensitive
    ],
)
def test_resolve_order_type_supported_combinations(order_type, side, expected):
    assert resolve_order_type(_FakeMT5(), order_type, side) == expected


@pytest.mark.parametrize("order_type,side", [("stop_limit", "buy"), ("trailing_stop", "sell"), ("market", "long")])
def test_resolve_order_type_unsupported_raises(order_type, side):
    with pytest.raises(ValueError, match="Unsupported order_type"):
        resolve_order_type(_FakeMT5(), order_type, side)


def test_resolve_order_type_missing_constant_on_module_raises():
    class _IncompleteMT5:
        pass

    with pytest.raises(ValueError, match="no ORDER_TYPE_BUY constant"):
        resolve_order_type(_IncompleteMT5(), "market", "buy")


def test_resolve_filling_mode_prefers_fok_when_supported():
    fake = _FakeMT5()
    assert resolve_filling_mode(fake, symbol_filling_mode_bitmask=1) == "FOK"


def test_resolve_filling_mode_falls_back_to_ioc():
    fake = _FakeMT5()
    assert resolve_filling_mode(fake, symbol_filling_mode_bitmask=2) == "IOC"


def test_resolve_filling_mode_prefers_fok_over_ioc_when_both_supported():
    fake = _FakeMT5()
    assert resolve_filling_mode(fake, symbol_filling_mode_bitmask=3) == "FOK"


def test_resolve_filling_mode_falls_back_to_return_when_neither_supported():
    fake = _FakeMT5()
    assert resolve_filling_mode(fake, symbol_filling_mode_bitmask=0) == "RETURN"
