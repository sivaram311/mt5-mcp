from mt5_mcp.envelope import err, ok


def test_ok_default_shape():
    result = ok({"foo": "bar"})
    assert result["success"] is True
    assert result["error_code"] is None
    assert result["error_message"] is None
    assert result["retryable"] is False
    assert result["data"] == {"foo": "bar"}
    assert result["request_id"]


def test_ok_uses_provided_request_id():
    result = ok(request_id="req-123")
    assert result["request_id"] == "req-123"


def test_ok_generates_unique_request_ids():
    assert ok()["request_id"] != ok()["request_id"]


def test_err_default_shape():
    result = err("invalid_symbol", "Unknown symbol XYZ")
    assert result["success"] is False
    assert result["error_code"] == "invalid_symbol"
    assert result["error_message"] == "Unknown symbol XYZ"
    assert result["retryable"] is False
    assert result["data"] is None
    assert result["request_id"]


def test_err_retryable_flag():
    result = err("connection_lost", "MT5 terminal unreachable", retryable=True)
    assert result["retryable"] is True
