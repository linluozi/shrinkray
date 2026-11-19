import json
import os
import subprocess
import sys

import pytest

from shrinkray import (
    BudgetExceeded,
    certify,
    minimize,
    minimize_bytes,
    minimize_text,
    result_json,
)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def test_empty_input_reproducing_returns_empty():
    res = minimize([], lambda xs: True)
    assert res.value == []
    assert res.one_minimal is True
    assert res.stats.final_size == 0
    assert res.certificate.tested_deletions == 0


def test_shrinks_noise_down_to_the_culprit():
    items = ["noise"] * 40 + ["BUG"] + ["tail"] * 40
    res = minimize(items, lambda xs: "BUG" in xs)
    assert res.value == ["BUG"]
    assert res.one_minimal is True
    assert res.certificate.tested_deletions == 1
    assert res.stats.final_size == 1
    assert res.stats.initial_size == 81
    assert res.stats.reduction == pytest.approx(80 / 81)


def test_already_minimal_input_is_untouched():
    res = minimize(["BUG"], lambda xs: "BUG" in xs)
    assert res.value == ["BUG"]
    assert res.one_minimal is True
    assert res.stats.calls >= 1


def test_certificate_detects_removable_element():
    cert = certify([1, 2, 3], lambda xs: 1 in xs or 3 in xs)
    assert cert.reproduces is True
    assert cert.one_minimal is False
    # deleting index 0 leaves [2, 3] which still reproduces via 3
    assert cert.violation_index == 0


def test_certificate_reports_non_reproduction():
    cert = certify([1], lambda xs: False)
    assert cert.reproduces is False
    assert cert.one_minimal is False
    assert cert.tested_deletions == 0


def test_does_not_mutate_input():
    items = ["a", "BUG", "b"]
    snapshot = list(items)
    minimize(items, lambda xs: "BUG" in xs)
    assert items == snapshot


def test_budget_is_enforced():
    with pytest.raises(BudgetExceeded):
        minimize(list(range(500)), lambda xs: len(xs) > 0, max_calls=3)


def test_text_lines_are_preserved_byte_for_byte():
    text = "".join(f"log line {i}\n" for i in range(30))
    text += "Traceback: boom\n"
    text += "".join(f"tail {i}\r\n" for i in range(10))
    res = minimize_text(text, lambda s: "Traceback: boom" in s, granularity="lines")
    assert res.value == "Traceback: boom\n"
    assert res.one_minimal is True
    assert res.stats.final_size == len("Traceback: boom\n")


def test_text_chars_handle_non_ascii():
    text = "prefix " + "é" * 5 + " suffix"
    res = minimize_text(text, lambda s: "é" in s, granularity="chars")
    assert res.value == "é"
    assert res.one_minimal is True


def test_bytes_lines_keep_crlf_and_encoding():
    data = b"".join(b"noise\r\n" for _ in range(20)) + b"HEADER\r\n"
    data += "caf\u00e9 \u2713\r\n".encode() + b"tail\r\n"
    res = minimize_bytes(data, lambda b: "caf\u00e9".encode() in b, granularity="lines")
    assert res.value == "caf\u00e9 \u2713\r\n".encode()
    assert res.one_minimal is True


def test_bytes_mode_is_byte_exact():
    data = bytes(range(256)) * 3
    res = minimize_bytes(data, lambda b: bytes([0x00, 0x01]) in b, granularity="bytes")
    assert res.value == bytes([0x00, 0x01])
    assert res.one_minimal is True


def test_determinism_same_input_same_result():
    items = list(range(200))
    pred = lambda xs: 7 in xs and 123 in xs  # noqa: E731
    a = minimize(items, pred)
    b = minimize(items, pred)
    assert a.value == b.value
    assert a.stats.calls == b.stats.calls
    assert a.certificate.to_dict() == b.certificate.to_dict()


def test_property_sweep_monotone_predicates():
    import random

    rng = random.Random(20261120)
    for _ in range(100):
        size = rng.randint(0, 60)
        items = [rng.randint(0, 30) for _ in range(size)]
        required = set(rng.sample(range(31), rng.randint(1, 3)))
        items.append(rng.choice(sorted(required)))
        res = minimize(items, lambda xs, req=required: any(x in req for x in xs))
        assert any(x in required for x in res.value)
        assert res.certificate.reproduces is True
        assert res.certificate.one_minimal is True
        assert res.stats.final_size <= res.stats.initial_size
        assert all(x in items for x in res.value)


def test_result_json_is_machine_readable():
    res = minimize(["a", "BUG", "b"], lambda xs: "BUG" in xs)
    doc = json.loads(result_json(res))
    assert doc["mode"] == "items"
    assert doc["certificate"]["one_minimal"] is True
    assert doc["stats"]["final_size"] == 1
    assert "value" not in doc


CLI_PREDICATE = """\
import sys
data = open(sys.argv[1], "rb").read()
sys.exit(1 if b"CRASH" in data and b"trigger" in data else 0)
"""


def test_cli_end_to_end(tmp_path):
    check = tmp_path / "check.py"
    check.write_text(CLI_PREDICATE)
    failing = tmp_path / "input.log"
    payload = b"".join(b"noise line %d\n" % i for i in range(200))
    payload += b"CRASH trigger here\n"
    payload += b"".join(b"tail %d\n" % i for i in range(50))
    failing.write_bytes(payload)

    out = tmp_path / "min.log"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "shrinkray",
            str(failing),
            "--cmd",
            f"{sys.executable} {check} {{input}}",
            "--mode",
            "lines",
            "--output",
            str(out),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.read_bytes() == b"CRASH trigger here\n"
    doc = json.loads(proc.stderr.strip().splitlines()[-1])
    assert doc["certificate"]["one_minimal"] is True
    assert doc["stats"]["final_size"] < doc["stats"]["initial_size"]


def test_cli_reports_failure_that_does_not_reproduce(tmp_path):
    check = tmp_path / "check.py"
    check.write_text("import sys; sys.exit(0)\n")
    failing = tmp_path / "input.txt"
    failing.write_text("nothing to see\n")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "shrinkray",
            str(failing),
            "--cmd",
            f"{sys.executable} {check} {{input}}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2
    assert "does not reproduce" in proc.stderr