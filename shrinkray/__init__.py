"""shrinkray: delta-debugging input minimizer with an empirical 1-minimality certificate.

The public surface is intentionally small:

    minimize(items, test)          -> Result        # generic sequence
    minimize_text(text, test)      -> Result        # lines, or characters
    minimize_bytes(data, test)     -> Result        # raw bytes
    certify(items, test)           -> Certificate   # check, do not shrink

Every returned :class:`Result` carries a *certificate*: the reported input is
still reproduced by ``test``, and deleting any single element makes ``test``
stop reproducing.  The certificate is checked by actually calling ``test``
once per element, so it is evidence, not a claim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, List, Sequence, Tuple

__all__ = [
    "BudgetExceeded",
    "Certificate",
    "Result",
    "Stats",
    "certify",
    "minimize",
    "minimize_bytes",
    "minimize_text",
]


class BudgetExceeded(RuntimeError):
    """Raised when the number of ``test`` invocations exceeds ``max_calls``."""


@dataclass(frozen=True)
class Stats:
    """Call accounting for one minimization run."""

    calls: int
    rounds: int
    initial_size: int
    final_size: int

    @property
    def reduction(self) -> float:
        """Fraction of the input removed (0.0 means nothing was removed)."""
        if self.initial_size == 0:
            return 0.0
        return 1.0 - (self.final_size / self.initial_size)

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "rounds": self.rounds,
            "initial_size": self.initial_size,
            "final_size": self.final_size,
            "reduction": round(self.reduction, 6),
        }


@dataclass(frozen=True)
class Certificate:
    """Result of checking that an input is 1-minimal for a predicate."""

    reproduces: bool
    one_minimal: bool
    tested_deletions: int
    calls: int
    violation_index: int | None

    def to_dict(self) -> dict:
        return {
            "reproduces": self.reproduces,
            "one_minimal": self.one_minimal,
            "tested_deletions": self.tested_deletions,
            "calls": self.calls,
            "violation_index": self.violation_index,
        }


@dataclass(frozen=True)
class Result:
    """A minimized artefact plus the evidence that it is 1-minimal."""

    value: Any
    size: int
    stats: Stats
    certificate: Certificate
    mode: str

    @property
    def one_minimal(self) -> bool:
        return self.certificate.one_minimal

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "size": self.size,
            "stats": self.stats.to_dict(),
            "certificate": self.certificate.to_dict(),
        }

    def summary(self) -> str:
        s = self.stats
        return (
            f"{self.mode}: {s.initial_size} -> {s.final_size} elements "
            f"({s.reduction:.1%} removed), {s.calls} test calls, "
            f"1-minimal={self.certificate.one_minimal}"
        )


def _split(seq: Sequence[Any], n: int) -> List[List[Any]]:
    """Split ``seq`` into ``n`` contiguous, non-empty chunks."""
    if n <= 1 or len(seq) <= 1:
        return [list(seq)]
    n = min(n, len(seq))
    base, extra = divmod(len(seq), n)
    out: List[List[Any]] = []
    start = 0
    for i in range(n):
        size = base + (1 if i < extra else 0)
        out.append(list(seq[start : start + size]))
        start += size
    return [c for c in out if c]


def certify(items: Sequence[Any], test: Callable[[List[Any]], bool]) -> Certificate:
    """Check 1-minimality of ``items`` without shrinking.

    ``test`` must be a predicate that returns True when the failure reproduces.
    A *violation* is any single-element deletion that still reproduces; the
    returned certificate reports the first such index.
    """
    items = list(items)
    calls = 0

    def run(candidate: List[Any]) -> bool:
        nonlocal calls
        calls += 1
        return bool(test(candidate))

    reproduces = run(items)
    if not reproduces or not items:
        return Certificate(
            reproduces=reproduces,
            one_minimal=reproduces,
            tested_deletions=0,
            calls=calls,
            violation_index=None,
        )

    for i in range(len(items)):
        without = items[:i] + items[i + 1 :]
        if run(without):
            return Certificate(
                reproduces=True,
                one_minimal=False,
                tested_deletions=i + 1,
                calls=calls,
                violation_index=i,
            )
    return Certificate(
        reproduces=True,
        one_minimal=True,
        tested_deletions=len(items),
        calls=calls,
        violation_index=None,
    )


def _ddmin(
    items: Sequence[Any],
    test: Callable[[List[Any]], bool],
    max_calls: int,
) -> Tuple[List[Any], int, int]:
    calls = 0
    rounds = 0

    def run(candidate: List[Any]) -> bool:
        nonlocal calls
        calls += 1
        if calls > max_calls:
            raise BudgetExceeded(
                f"test budget of {max_calls} calls exhausted after {calls} calls"
            )
        return bool(test(candidate))

    def empty_reproduces() -> bool:
        return run([])

    cur = list(items)
    if empty_reproduces():
        return [], calls, rounds
    if not run(cur):
        raise ValueError("the initial input does not reproduce the failure")

    n = 2
    while len(cur) >= 2:
        rounds += 1
        chunks = _split(cur, n)
        reduced = False
        for chunk in chunks:
            if run(chunk):
                cur = chunk
                n = max(2, n - 1)
                reduced = True
                break
        if reduced:
            continue
        for i in range(len(chunks)):
            complement = [x for j, ch in enumerate(chunks) if j != i for x in ch]
            if complement and run(complement):
                cur = complement
                n = max(2, n - 1)
                reduced = True
                break
        if reduced:
            continue
        if n >= len(cur):
            break
        n = min(len(cur), n * 2)

    return cur, calls, rounds


def minimize(
    items: Sequence[Any],
    test: Callable[[List[Any]], bool],
    *,
    max_calls: int = 200_000,
) -> Result:
    """Shrink a generic sequence to a 1-minimal reproducing prefix/subsequence.

    The predicate must be callable as ``test(list_of_items) -> bool`` and return
    True when the failure reproduces.  The original sequence is never mutated.
    """
    initial = list(items)
    reduced, calls, rounds = _ddmin(initial, test, max_calls)
    cert = certify(reduced, test)
    if calls + cert.calls > max_calls:
        raise BudgetExceeded(
            f"test budget of {max_calls} calls exhausted while certifying"
        )
    stats = Stats(
        calls=calls + cert.calls,
        rounds=rounds,
        initial_size=len(initial),
        final_size=len(reduced),
    )
    return Result(
        value=reduced,
        size=len(reduced),
        stats=stats,
        certificate=cert,
        mode="items",
    )


def minimize_text(
    text: str,
    test: Callable[[str], bool],
    *,
    granularity: str = "lines",
    max_calls: int = 200_000,
) -> Result:
    """Shrink a string, either line by line (keeps line endings) or character by character."""
    if granularity not in ("lines", "chars"):
        raise ValueError("granularity must be 'lines' or 'chars'")
    if granularity == "lines":
        units = text.splitlines(keepends=True)
    else:
        units = list(text)

    res = minimize(units, lambda us: test("".join(us)), max_calls=max_calls)
    out = "".join(res.value)
    stats = Stats(
        calls=res.stats.calls,
        rounds=res.stats.rounds,
        initial_size=len(text),
        final_size=len(out),
    )
    return Result(
        value=out,
        size=len(out),
        stats=stats,
        certificate=res.certificate,
        mode=f"text/{granularity}",
    )


def minimize_bytes(
    data: bytes,
    test: Callable[[bytes], bool],
    *,
    granularity: str = "bytes",
    max_calls: int = 200_000,
) -> Result:
    """Shrink ``bytes`` either one byte at a time or as ``b"\\n"``-separated chunks."""
    if granularity not in ("bytes", "lines"):
        raise ValueError("granularity must be 'bytes' or 'lines'")
    if granularity == "lines":
        units = data.split(b"\n")
        # Re-add separators so the join is byte-exact; the final chunk stays bare.
        framed = [(u, i < len(units) - 1) for i, u in enumerate(units)]
        res = minimize(
            framed,
            lambda us: test(b"".join(u + (b"\n" if had else b"") for u, had in us)),
            max_calls=max_calls,
        )
    else:
        units = list(data)
        res = minimize(units, lambda us: test(bytes(us)), max_calls=max_calls)

    if granularity == "lines":
        out = b"".join(u + (b"\n" if had else b"") for u, had in res.value)
    else:
        out = bytes(res.value)
    stats = Stats(
        calls=res.stats.calls,
        rounds=res.stats.rounds,
        initial_size=len(data),
        final_size=len(out),
    )
    return Result(
        value=out,
        size=len(out),
        stats=stats,
        certificate=res.certificate,
        mode=f"bytes/{granularity}",
    )


def result_json(result: Result) -> str:
    """Serialize a result summary (never the payload) as one JSON line."""
    return json.dumps(result.to_dict(), sort_keys=True)