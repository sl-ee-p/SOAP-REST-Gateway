import json
import logging

from app.logging_config import JsonFormatter, RequestIdFilter, request_id_var


def make_record(msg="hello", exc_info=None, **extra) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, msg, None, exc_info)
    record.__dict__.update(extra)
    return record


def test_output_is_one_json_object_with_the_standard_fields():
    line = JsonFormatter().format(make_record("hello"))
    payload = json.loads(line)
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["timestamp"].endswith("+00:00")
    assert "\n" not in line


def test_extra_fields_become_top_level_keys():
    payload = json.loads(JsonFormatter().format(make_record(operation="X", duration_ms=1.5)))
    assert (payload["operation"], payload["duration_ms"]) == ("X", 1.5)


def test_non_serialisable_extras_do_not_break_logging():
    payload = json.loads(JsonFormatter().format(make_record(thing=object())))
    assert "object object" in payload["thing"]


def test_exceptions_are_included():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = make_record(exc_info=sys.exc_info())
    assert "ValueError: boom" in json.loads(JsonFormatter().format(record))["exception"]


def test_filter_adds_the_current_request_id():
    token = request_id_var.set("req-1")
    try:
        record = make_record()
        assert RequestIdFilter().filter(record)
        assert record.request_id == "req-1"
    finally:
        request_id_var.reset(token)
