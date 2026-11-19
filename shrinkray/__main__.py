"""Command line entry point: ``python -m shrinkray``."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

from . import minimize_bytes, minimize_text, result_json


def _make_predicate(cmd: str, timeout: float, mode: str):
    def predicate(payload) -> bool:
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        fd, path = tempfile.mkstemp(prefix="shrinkray-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            argv = cmd.replace("{input}", path)
            proc = subprocess.run(
                argv,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
            return proc.returncode != 0
        except subprocess.TimeoutExpired:
            return False
        finally:
            os.unlink(path)

    return predicate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="shrinkray",
        description="Shrink a failing input while proving 1-minimality.",
    )
    parser.add_argument("input", help="file containing the failing input")
    parser.add_argument(
        "--cmd",
        required=True,
        help="reproduction command; {input} is replaced by the candidate path; "
        "a non-zero exit status means the failure reproduced",
    )
    parser.add_argument(
        "--mode",
        choices=["lines", "chars", "bytes", "byte-lines"],
        default="lines",
        help="decomposition to shrink over (default: lines)",
    )
    parser.add_argument("--output", help="write the minimized input here (default: stdout)")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-run timeout in seconds")
    parser.add_argument("--json", action="store_true", help="print the certificate as JSON on stderr")
    args = parser.parse_args(argv)

    with open(args.input, "rb") as fh:
        raw = fh.read()

    predicate = _make_predicate(args.cmd, args.timeout, args.mode)
    probe = raw.decode("utf-8", "surrogateescape") if args.mode in ("lines", "chars") else raw
    if not predicate(probe):
        print("error: the input does not reproduce the failure", file=sys.stderr)
        return 2
    if args.mode == "bytes":
        result = minimize_bytes(raw, predicate, granularity="bytes")
    elif args.mode == "byte-lines":
        result = minimize_bytes(raw, predicate, granularity="lines")
    else:
        text = raw.decode("utf-8", "surrogateescape")
        result = minimize_text(text, predicate, granularity=args.mode)

    payload = result.value.encode("utf-8", "surrogateescape") if isinstance(result.value, str) else result.value
    if args.output:
        with open(args.output, "wb") as fh:
            fh.write(payload)
    else:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()

    if args.json:
        print(result_json(result), file=sys.stderr)
    else:
        print(result.summary(), file=sys.stderr)

    if not result.certificate.reproduces:
        print("error: the minimized input no longer reproduces the failure", file=sys.stderr)
        return 2
    if not result.certificate.one_minimal:
        print(
            f"error: not 1-minimal; deleting element "
            f"{result.certificate.violation_index} still reproduces",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())