#!/usr/bin/env python3
"""
tests/test_runner.py - Autonomous AI Game Studio Standalone E2E Test Runner
Supports selective tier execution, verbose microsecond reporting, ANSI colors,
structured JSON report generation, and strict exit code semantics.

Usage:
  python3 tests/test_runner.py [--tier {1,2,3,4,5,all}] [-v|--verbose]
                               [--json-report PATH] [--fail-fast] [--filter PATTERN]
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import platform
import sys
import time
import traceback
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Tier directory mapping
TIER_DIRS = {
    "1": ("tier1_features", "Tier 1 (Features)"),
    "2": ("tier2_boundaries", "Tier 2 (Boundaries)"),
    "3": ("tier3_combinations", "Tier 3 (Combinations)"),
    "4": ("tier4_applications", "Tier 4 (Applications)"),
    "5": ("tier5_adversarial", "Tier 5 (Adversarial)"),
}

# ANSI color codes
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
CLR_RESET = "\033[0m" if USE_COLOR else ""
CLR_GREEN = "\033[92m" if USE_COLOR else ""
CLR_RED = "\033[91m" if USE_COLOR else ""
CLR_YELLOW = "\033[93m" if USE_COLOR else ""
CLR_MAGENTA = "\033[95m" if USE_COLOR else ""
CLR_CYAN = "\033[96m" if USE_COLOR else ""
CLR_BOLD = "\033[1m" if USE_COLOR else ""


class TestRecord:
    """Stores execution metrics and outcome for an individual test."""

    def __init__(self, test_id: str, tier: str) -> None:
        self.test_id = test_id
        self.tier = tier
        self.status = "PENDING"
        self.start_time: float = 0.0
        self.stop_time: float = 0.0
        self.duration_seconds: float = 0.0
        self.error: Optional[str] = None
        self.traceback: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.test_id,
            "tier": self.tier,
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 6),
            "error": self.error,
            "traceback": self.traceback,
        }


class StudioTestResult(unittest.TestResult):
    """
    Custom TestResult capturing microsecond timings, ANSI reporting,
    and tier-level metric breakdowns.
    """

    def __init__(self, verbose: bool = False, fail_fast: bool = False) -> None:
        super().__init__()
        self.verbose = verbose
        self.failfast = fail_fast
        self.records: List[TestRecord] = []
        self._current_record: Optional[TestRecord] = None
        self.tier_counts: Dict[str, Dict[str, Any]] = {
            "tier1_features": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "duration": 0.0},
            "tier2_boundaries": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "duration": 0.0},
            "tier3_combinations": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "duration": 0.0},
            "tier4_applications": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "duration": 0.0},
            "tier5_adversarial": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "duration": 0.0},
        }

    def _determine_tier(self, test: unittest.TestCase) -> str:
        mod_name = test.__class__.__module__
        for tier_key in ("tier1_features", "tier2_boundaries", "tier3_combinations", "tier4_applications", "tier5_adversarial"):
            if tier_key in mod_name:
                return tier_key
        return "tier1_features"

    def startTest(self, test: unittest.TestCase) -> None:
        super().startTest(test)
        tier = self._determine_tier(test)
        record = TestRecord(test.id(), tier)
        record.start_time = time.perf_counter()
        self._current_record = record
        self.records.append(record)

    def stopTest(self, test: unittest.TestCase) -> None:
        super().stopTest(test)
        if self._current_record:
            self._current_record.stop_time = time.perf_counter()
            self._current_record.duration_seconds = max(
                0.0, self._current_record.stop_time - self._current_record.start_time
            )
            tier = self._current_record.tier
            if tier in self.tier_counts:
                self.tier_counts[tier]["total"] += 1
                self.tier_counts[tier]["duration"] += self._current_record.duration_seconds

            if self.verbose:
                status_color = CLR_GREEN if self._current_record.status == "PASS" else CLR_RED
                if self._current_record.status == "SKIP":
                    status_color = CLR_YELLOW
                elif self._current_record.status == "ERROR":
                    status_color = CLR_MAGENTA

                duration_str = f"({self._current_record.duration_seconds * 1000.0:.2f}ms)"
                print(
                    f"  {status_color}[{self._current_record.status}]{CLR_RESET} "
                    f"{self._current_record.test_id} {duration_str}"
                )
            else:
                mark_map = {"PASS": ".", "FAIL": "F", "ERROR": "E", "SKIP": "s"}
                mark = mark_map.get(self._current_record.status, "?")
                sys.stdout.write(mark)
                sys.stdout.flush()

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        if self._current_record:
            self._current_record.status = "PASS"
            tier = self._current_record.tier
            if tier in self.tier_counts:
                self.tier_counts[tier]["passed"] += 1

    def addFailure(self, test: unittest.TestCase, err: Tuple[Any, ...]) -> None:
        super().addFailure(test, err)
        if self._current_record:
            self._current_record.status = "FAIL"
            self._current_record.error = str(err[1])
            self._current_record.traceback = "".join(traceback.format_exception(*err))
            tier = self._current_record.tier
            if tier in self.tier_counts:
                self.tier_counts[tier]["failed"] += 1

    def addError(self, test: unittest.TestCase, err: Tuple[Any, ...]) -> None:
        super().addError(test, err)
        if self._current_record:
            self._current_record.status = "ERROR"
            self._current_record.error = str(err[1])
            self._current_record.traceback = "".join(traceback.format_exception(*err))
            tier = self._current_record.tier
            if tier in self.tier_counts:
                self.tier_counts[tier]["errors"] += 1

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        if self._current_record:
            self._current_record.status = "SKIP"
            self._current_record.error = reason
            tier = self._current_record.tier
            if tier in self.tier_counts:
                self.tier_counts[tier]["skipped"] += 1


def filter_suite(suite: unittest.TestSuite, pattern: str) -> unittest.TestSuite:
    """Recursively filters tests in a TestSuite by a wildcard / substring pattern."""
    filtered = unittest.TestSuite()
    cleaned = pattern.strip("*").lower()
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            sub = filter_suite(item, pattern)
            if sub.countTestCases() > 0:
                filtered.addTest(sub)
        elif isinstance(item, unittest.TestCase):
            test_id = item.id()
            method_name = getattr(item, "_testMethodName", "")
            class_name = item.__class__.__name__
            matches = (
                cleaned in test_id.lower()
                or fnmatch.fnmatch(test_id.lower(), pattern.lower())
                or fnmatch.fnmatch(test_id.lower(), f"*{pattern.lower()}*")
                or fnmatch.fnmatch(method_name.lower(), pattern.lower())
                or fnmatch.fnmatch(class_name.lower(), pattern.lower())
            )
            if matches:
                filtered.addTest(item)
    return filtered


def discover_suite(tier_arg: str, filter_pattern: Optional[str] = None) -> unittest.TestSuite:
    """Discovers test suites based on the specified tier argument and optional filter."""
    loader = unittest.defaultTestLoader
    master_suite = unittest.TestSuite()

    selected_tiers = []
    if tier_arg == "all":
        selected_tiers = ["1", "2", "3", "4", "5"]
    elif tier_arg in TIER_DIRS:
        selected_tiers = [tier_arg]
    else:
        raise ValueError(f"Invalid tier argument: {tier_arg}")

    tests_dir = REPO_ROOT / "tests"
    for t in selected_tiers:
        dir_name, _ = TIER_DIRS[t]
        target_path = tests_dir / dir_name
        if target_path.is_dir():
            suite = loader.discover(
                start_dir=str(target_path),
                pattern="test_*.py",
                top_level_dir=str(REPO_ROOT),
            )
            master_suite.addTest(suite)

    if filter_pattern:
        master_suite = filter_suite(master_suite, filter_pattern)

    return master_suite


def print_summary_table(result: StudioTestResult, total_duration: float) -> None:
    """Prints the rich summary report table."""
    print("\n" + "=" * 80)
    print(f"{CLR_BOLD}           AUTONOMOUS AI GAME STUDIO — E2E TEST EXECUTION REPORT{CLR_RESET}")
    print("=" * 80)
    print(f"{'Tier':<24} {'Total':>7} {'Passed':>8} {'Failed':>8} {'Errors':>8} {'Skipped':>8} {'Duration':>10}")
    print("-" * 80)

    total_all = 0
    passed_all = 0
    failed_all = 0
    errors_all = 0
    skipped_all = 0

    for t_key, (dir_name, display_name) in TIER_DIRS.items():
        counts = result.tier_counts.get(dir_name, {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "duration": 0.0})
        total_all += counts["total"]
        passed_all += counts["passed"]
        failed_all += counts["failed"]
        errors_all += counts["errors"]
        skipped_all += counts["skipped"]
        dur_str = f"{counts['duration']:.3f}s"
        print(
            f"{display_name:<24} "
            f"{counts['total']:>7} "
            f"{counts['passed']:>8} "
            f"{counts['failed']:>8} "
            f"{counts['errors']:>8} "
            f"{counts['skipped']:>8} "
            f"{dur_str:>10}"
        )

    print("-" * 80)
    total_dur_str = f"{total_duration:.3f}s"
    print(
        f"{'TOTAL':<24} "
        f"{total_all:>7} "
        f"{passed_all:>8} "
        f"{failed_all:>8} "
        f"{errors_all:>8} "
        f"{skipped_all:>8} "
        f"{total_dur_str:>10}"
    )
    print("=" * 80)

    # Outcome line
    is_success = (failed_all == 0 and errors_all == 0 and total_all > 0)
    if is_success:
        pass_rate = 100.0 if total_all > 0 else 0.0
        print(
            f"{CLR_BOLD}RESULT: {CLR_GREEN}SUCCESS{CLR_RESET} "
            f"({pass_rate:.1f}% pass rate) in {total_duration:.2f}s"
        )
    elif total_all == 0:
        print(f"{CLR_BOLD}RESULT: {CLR_YELLOW}NO TESTS EXECUTED{CLR_RESET}")
    else:
        print(
            f"{CLR_BOLD}RESULT: {CLR_RED}FAILURE{CLR_RESET} "
            f"({failed_all} failed, {errors_all} errors) in {total_duration:.2f}s"
        )
    print("=" * 80)

    # Print failure details if any
    failures_and_errors = [r for r in result.records if r.status in ("FAIL", "ERROR")]
    if failures_and_errors:
        print(f"\n{CLR_BOLD}{CLR_RED}FAILURES & ERRORS DETAIL:{CLR_RESET}\n")
        for rec in failures_and_errors:
            print(f"{CLR_RED}======================================================================{CLR_RESET}")
            print(f"{rec.status}: {rec.test_id}")
            print(f"{CLR_RED}----------------------------------------------------------------------{CLR_RESET}")
            if rec.traceback:
                print(rec.traceback)
            elif rec.error:
                print(rec.error)
            print()


def write_json_report(
    path: Path,
    result: StudioTestResult,
    total_duration: float,
    success: bool,
) -> None:
    """Writes detailed structured JSON report."""
    total_all = sum(c["total"] for c in result.tier_counts.values())
    passed_all = sum(c["passed"] for c in result.tier_counts.values())
    failed_all = sum(c["failed"] for c in result.tier_counts.values())
    errors_all = sum(c["errors"] for c in result.tier_counts.values())
    skipped_all = sum(c["skipped"] for c in result.tier_counts.values())

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "summary": {
            "total": total_all,
            "passed": passed_all,
            "failed": failed_all,
            "errors": errors_all,
            "skipped": skipped_all,
            "duration_seconds": round(total_duration, 4),
            "success": success,
        },
        "tier_breakdown": {
            k: {
                "total": v["total"],
                "passed": v["passed"],
                "failed": v["failed"],
                "errors": v["errors"],
                "skipped": v["skipped"],
                "duration": round(v["duration"], 4),
            }
            for k, v in result.tier_counts.items()
        },
        "tests": [r.to_dict() for r in result.records],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Autonomous Multi-Agent AI Game Studio E2E Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tier",
        choices=["1", "2", "3", "4", "5", "all"],
        default="all",
        help="Test tier to execute (default: all)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print verbose per-test execution timings and names",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        metavar="PATH",
        help="Write structured JSON test results to specified path",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop execution on first failure or error",
    )
    parser.add_argument(
        "--filter",
        type=str,
        metavar="PATTERN",
        help="Filter test cases by pattern or substring",
    )

    try:
        args = parser.parse_args()
    except SystemExit as e:
        return 2

    # Discover tests
    try:
        suite = discover_suite(args.tier, args.filter)
    except Exception as e:
        sys.stderr.write(f"Error discovering tests: {e}\n")
        return 2

    total_cases = suite.countTestCases()
    tier_desc = f"Tier {args.tier}" if args.tier != "all" else "All Tiers"
    filter_desc = f" (filter: '{args.filter}')" if args.filter else ""
    print(f"\n{CLR_BOLD}Running Autonomous AI Game Studio E2E Test Suite [{tier_desc}]{filter_desc}{CLR_RESET}")
    print(f"Discovered {total_cases} test cases across tests/ ...\n")

    result = StudioTestResult(verbose=args.verbose, fail_fast=args.fail_fast)
    start_time = time.perf_counter()

    try:
        suite.run(result)
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Test execution cancelled by user.")
        return 1

    total_duration = time.perf_counter() - start_time

    # Print summary table
    print_summary_table(result, total_duration)

    is_success = (len(result.failures) == 0 and len(result.errors) == 0 and total_cases > 0)

    # Write JSON report if requested
    if args.json_report:
        try:
            write_json_report(args.json_report, result, total_duration, is_success)
            print(f"Wrote structured JSON report to: {args.json_report}")
        except Exception as e:
            sys.stderr.write(f"Failed to write JSON report to {args.json_report}: {e}\n")

    return 0 if is_success else 1


if __name__ == "__main__":
    sys.exit(main())
