"""Run a project's test suite and parse pass/fail (spec §7.1).

Pass/fail is driven by the process return code (0 = pass) — the reliable signal
across pytest / npm / vitest. Counts are parsed best-effort for the summary.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

from .executor import CodeExecutor, ExecResult

logger = logging.getLogger(__name__)

# Optimization 2a: Compile regexes once at module load
_PYTEST_PASS = re.compile(r"(\d+)\s+passed")
_PYTEST_FAIL = re.compile(r"(\d+)\s+failed")
_PYTEST_ERROR = re.compile(r"(\d+)\s+error")

# Optimization 2b: Cache will be populated after TestResult is defined
_TEST_RESULT_CACHE: dict[str, str] = {}


@dataclass
class TestResult:
    ok: bool
    returncode: int
    summary: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    timed_out: bool = False
    raw: str = ""

    def tail(self, n: int = 2000) -> str:
        return self.raw[-n:]


def parse_test_output(output: str, returncode: int,
                      timed_out: bool = False) -> TestResult:
    # Optimization 2b: Check cache first (use last 1000 chars + returncode + timeout as signature)
    cache_input = output[-1000:] + str(returncode) + str(timed_out)
    cache_sig = hashlib.md5(cache_input.encode()).hexdigest()

    if cache_sig in _TEST_RESULT_CACHE:
        logger.debug("Test result cache hit")
        return _TEST_RESULT_CACHE[cache_sig]

    passed = _first_int(_PYTEST_PASS, output)
    failed = _first_int(_PYTEST_FAIL, output)
    errors = _first_int(_PYTEST_ERROR, output)
    ok = (returncode == 0) and not timed_out
    if timed_out:
        summary = "tests timed out"
    elif passed or failed or errors:
        summary = f"{passed} passed, {failed} failed, {errors} errors"
    else:
        summary = f"exit code {returncode}"
    result = TestResult(ok=ok, returncode=returncode, summary=summary,
                        passed=passed, failed=failed, errors=errors,
                        timed_out=timed_out, raw=output)

    # Keep cache bounded to last 50 entries
    if len(_TEST_RESULT_CACHE) > 50:
        oldest_key = next(iter(_TEST_RESULT_CACHE))
        del _TEST_RESULT_CACHE[oldest_key]

    _TEST_RESULT_CACHE[cache_sig] = result
    return result


def _first_int(pattern: re.Pattern, text: str) -> int:
    m = pattern.search(text)
    return int(m.group(1)) if m else 0


def run_tests(executor: CodeExecutor, repo_path: str, test_command: str,
              session_id: str | None = None,
              timeout: int = 600) -> TestResult:
    if not test_command:
        return TestResult(ok=True, returncode=0, summary="no test command")
    result: ExecResult = executor.run(test_command, cwd=repo_path,
                                      timeout=timeout, session_id=session_id)
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    return parse_test_output(combined, result.returncode, result.timed_out)
