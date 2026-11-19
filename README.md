# shrinkray

**Shrink a failing input down to a minimal one — and prove it.** `shrinkray` is a dependency-free
delta-debugging (ddmin) minimizer for logs, JSON, source files and raw bytes. Given a predicate
("does this input still reproduce the failure?"), it returns a much smaller input **plus a
certificate that deleting any single remaining element stops reproducing**, earned by actually
re-running the predicate once per element.

Upstream: none

## Why it exists

When a test or a program fails on a 300 KB log file, the useful artifact is not the stack trace,
it is the two lines that make it fail. Existing reducers are usually tied to a test framework or a
specific data format; `shrinkray` takes any byte stream and any exit-status-based predicate, and it
refuses to call a result minimal unless it has checked every single-element deletion.

## Install

```bash
git clone git@github-linluozi:linluozi/shrinkray.git
cd shrinkray
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
```

Python ≥ 3.9, standard library only. The CLI is then available as `.venv/bin/shrinkray` or
`python -m shrinkray`.

## Use

```bash
python -m shrinkray failing.log \
  --mode lines \
  --cmd "python3 repro.py {input}" \
  --output minimal.log --json
```

* `--cmd`: anything that exits non-zero when the failure reproduces. `{input}` is replaced by the
  path of the candidate file; a fresh temporary file is written for every call.
* `--mode`: `lines` (default, keeps line endings), `chars`, `bytes`, `byte-lines`.
* `--json`: one JSON line on stderr with the call count, sizes and the certificate.
* Exit code `0` = minimized and 1-minimal; `2` = the input never reproduced; `3` = the certificate
  found a removable element (bug or budget exhaustion).

As a library:

```python
from shrinkray import minimize_text

result = minimize_text(text, lambda s: "KeyError: 'user_id'" in s, granularity="lines")
assert result.value == "KeyError: 'user_id'\n"
assert result.one_minimal                      # checked, not assumed
print(result.stats.calls, result.stats.reduction)
```

## Measured example (run on this machine, 2026-11-20)

Input: a synthetic 5,003-line log (289,804 bytes) containing one three-line incident among
4,999 noise lines. Predicate: a 6-line script that exits 1 iff the file contains both
`cache miss` and `KeyError: 'user_id'`.

| quantity | value |
| --- | --- |
| input | 5,003 lines / 289,804 bytes |
| output | 2 lines / 76 bytes |
| reduction | **99.9738 %** (`stats.reduction = 0.999738`) |
| predicate calls (ddmin + certificate) | **39** (36 + 3) |
| ddmin rounds | 14 |
| wall clock (`/usr/bin/time -v`) | **1.23 s** |
| peak RSS | **17,636 KB** |
| 1-minimal | **true** (2 deletions tested, 0 violations) |

Reproduce it:

```bash
python3 - <<'EOF'
import random
rng = random.Random(20261120)
lines = [f"2026-11-20T02:0{i%10}:00Z INFO request id={rng.randrange(10**6)} latency={rng.randrange(400)}ms\n" for i in range(3000)]
lines.insert(1777, "2026-11-20T02:03:11Z ERROR cache miss for key='user_id'\n")
lines.insert(1778, "Traceback (most recent call last):\n")
lines.insert(1779, "KeyError: 'user_id'\n")
lines += [f"2026-11-20T02:04:00Z DEBUG tail line {i} payload={rng.random():.6f}\n" for i in range(2000)]
open("input.log", "w").write("".join(lines))
EOF
printf 'import sys\nd=open(sys.argv[1],"rb").read()\nsys.exit(1 if b"KeyError: \x27user_id\x27" in d and b"cache miss" in d else 0)\n' > repro.py
python -m shrinkray input.log --mode lines --cmd "python3 repro.py {input}" --output min.log --json
```

## What the certificate means

`Result.certificate` is produced by `certify()`:

1. it re-runs the predicate on the reported input (`reproduces` must be true);
2. for every element it runs the predicate on the input with that element deleted; if any deletion
   still reproduces, `one_minimal` is false and `violation_index` names the first offender.

So the certificate costs exactly `1 + n` predicate calls for an `n`-element result, and it is
evidence rather than a claim. ddmin's theoretical 1-minimality guarantee needs a *monotone*
predicate (if a subset reproduces, every superset does). For non-monotone predicates `shrinkray`
still returns a small input, but it will honestly report `one_minimal: false` when the certificate
finds a removable element.

## Tests

```bash
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

16 tests: empty input, already-minimal input, non-mutation, budget enforcement, line-ending
byte-exactness (`\r\n`), non-ASCII text, raw-byte mode, determinism, a 100-seed property sweep over
monotone predicates (result always reproduces, is 1-minimal, and only contains original items),
JSON serialization, and two subprocess CLI tests (success and non-reproducing input). Real result
on 2026-11-20: **16 passed, 0 failed**.

## Limits (honest)

* The predicate is the oracle: if it is flaky, the result is only as reliable as that run. Run the
  final `certify()` yourself again if the predicate is timing dependent.
* ddmin is not guaranteed to find the *smallest* 1-minimal input, only *a* 1-minimal one; different
  chunking can produce different (equally minimal) results.
* `--mode chars` on megabyte inputs is slow by design: every predicate call rebuilds the whole
  string. Use `lines` or `bytes` first.
* `--json` is a summary only; the payload is written to stdout/`--output`, never into the JSON.

## License

MIT — see `LICENSE`. Copyright (c) 2026 linluozi. No upstream code was reused; `Upstream: none`.