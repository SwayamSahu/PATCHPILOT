"""Verification tests.

These exist because of a real incident during development: the Developer agent
reported a pull request (#42, at a repository that does not exist), having never
committed, pushed, or called create_pull_request. Its code fix was correct, which
is what made the fabrication dangerous — a pipeline that trusted the summary
would have shown a human a dead link and asked them to approve a deployment on
the strength of tests that never ran.

So these tests assert the pipeline's refusal to take an agent's word for
anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from patchpilot import verification  # noqa: E402

# --------------------------------------------------------------------------
# Reproduction
# --------------------------------------------------------------------------


def test_a_described_reproduction_is_not_a_reproduction():
    verdict = verification.verify_reproduction(
        [{"tool": "get_repository_file"}, {"tool": "get_git_history"}]
    )
    assert verdict.ok is False
    assert "never run" in verdict.problems[0]


def test_an_executed_reproduction_is_accepted():
    verdict = verification.verify_reproduction([{"tool": "exec"}, {"tool": "exec"}])
    assert verdict.ok is True
    assert verdict.facts["sandbox_runs"] == 2


# --------------------------------------------------------------------------
# Regression tests
# --------------------------------------------------------------------------


def test_a_fix_without_a_new_test_is_rejected():
    diff = (
        "diff --git a/app/checkout.py b/app/checkout.py\n"
        "-    return subtotal / discount\n"
        "+    return subtotal * (1 - discount)\n"
    )
    verdict = verification.verify_regression_test(diff)
    assert verdict.ok is False
    assert "no new test function" in verdict.problems[0]


def test_an_added_test_function_is_detected():
    diff = (
        "diff --git a/tests/test_x.py b/tests/test_x.py\n"
        "+def test_zero_discount_does_not_raise():\n"
        "+    assert compute_total(Cart(100.0, 0.0)) == 100.0\n"
    )
    verdict = verification.verify_regression_test(diff)
    assert verdict.ok is True
    assert verdict.facts["tests_added"] == ["def test_zero_discount_does_not_raise():"]


def test_the_marker_lines_of_a_diff_are_not_mistaken_for_content():
    """'+++ b/tests/...' is a header, not an added line."""
    diff = "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n+def test_real():\n    pass\n"
    assert verification.verify_regression_test(diff).facts["tests_added"] == ["def test_real():"]


# --------------------------------------------------------------------------
# Pull requests
# --------------------------------------------------------------------------


def test_a_missing_pr_number_is_not_treated_as_success():
    verdict = verification.verify_pull_request(None)
    assert verdict.ok is False


@pytest.mark.parametrize("claimed", [42, 99999])
def test_an_invented_pull_request_number_is_caught(claimed, monkeypatch):
    """The exact failure observed in development: a plausible number, no PR."""

    def explode(_number):
        raise verification.GitHubError(f"GitHub returned 404 for /pulls/{_number}")

    monkeypatch.setattr(verification.github_api, "get_pull_request", explode)
    verdict = verification.verify_pull_request(claimed)
    assert verdict.ok is False
    assert "does not exist" in verdict.problems[-1]


def test_a_real_pull_request_is_confirmed_with_its_facts(monkeypatch):
    monkeypatch.setattr(
        verification.github_api,
        "get_pull_request",
        lambda n: {
            "number": n, "url": f"https://github.com/o/r/pull/{n}",
            "state": "open", "head": "fix/checkout-discount", "changed_files": 2,
        },
    )
    verdict = verification.verify_pull_request(11)
    assert verdict.ok is True
    assert verdict.facts["url"].endswith("/pull/11")


# --------------------------------------------------------------------------
# The gate into human review
# --------------------------------------------------------------------------


def test_readiness_requires_every_check_to_hold():
    verdicts = {
        "reproduction": verification.Verdict(True, "reproduced"),
        "tests": verification.Verdict.failed("tests did not pass", ["1 failed"]),
        "pull_request": verification.Verdict(True, "#11 exists"),
    }
    verdict = verification.verify_deployment_readiness(verdicts)
    assert verdict.ok is False
    assert any("tests" in problem for problem in verdict.problems)


def test_readiness_passes_only_when_everything_holds():
    verdicts = {
        "reproduction": verification.Verdict(True, "reproduced"),
        "tests": verification.Verdict(True, "25 passed"),
        "pull_request": verification.Verdict(True, "#11 exists"),
    }
    assert verification.verify_deployment_readiness(verdicts).ok is True


# --------------------------------------------------------------------------
# Event translation
# --------------------------------------------------------------------------


def test_a_tool_result_is_labelled_with_the_tool_that_produced_it():
    """The harness identifies a result by call id, not by name."""
    from patchpilot.events import EventType, from_harness_event

    names: dict = {}
    call = {
        "type": "model.message",
        "tool_calls": [{"id": "c1", "function": {"name": "get_metrics"}}],
    }
    from_harness_event(call, "detective", names)
    results = from_harness_event(
        {"type": "tool.response", "tool_call_id": "c1"}, "detective", names
    )
    assert results[0].type is EventType.TOOL_RESULT
    assert "Querying production metrics" in results[0].summary


def test_an_unidentifiable_tool_result_is_dropped_rather_than_shown_blank():
    from patchpilot.events import from_harness_event

    assert from_harness_event({"type": "tool.response"}, "detective", {}) == []
