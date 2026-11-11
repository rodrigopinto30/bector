from datetime import UTC, datetime, timedelta

from healer.domain.patch import FileEdit, Patch
from healer.domain.proposal import Proposal, TokenUsage
from healer.domain.ranking import Tally, ranked, results_of
from healer.domain.repair import Attempt, AttemptOutcome, AttemptSource
from healer.domain.sharing import Evidence, ReceivedSolution, SharedSolution

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def fix(new: str, *, hours: int = 0) -> ReceivedSolution:
    at = NOW + timedelta(hours=hours)
    return ReceivedSolution(
        solution=SharedSolution(
            node="laptop-ana",
            published_at=at,
            signature="a" * 64,
            error="KeyError: 'db'",
            patch=Patch(edits=(FileEdit(path="app/cfg.py", old="a", new=new),)),
            explanation="",
            model="m",
            tokens_spent=1,
            evidence=Evidence(command="pytest", exit_code_before=1, verified_at=at),
        ),
        received_at=at,
    )


def test_a_score_starts_in_the_middle_and_one_result_never_decides_alone() -> None:
    assert Tally().score == 0.5
    assert Tally(worked=1).score == 2 / 3
    assert Tally(failed=1).score == 1 / 3
    assert Tally(worked=9, failed=1).score > Tally(worked=1).score


def test_fixes_are_tried_by_success_then_by_how_many_then_newest_first() -> None:
    failed, untried_old, untried_new, worked_once, worked_twice = (
        fix("failed", hours=5),
        fix("untried old", hours=1),
        fix("untried new", hours=2),
        fix("worked once"),
        fix("worked twice"),
    )
    tallies = {
        failed.solution.fix_id: Tally(failed=1),
        worked_once.solution.fix_id: Tally(worked=1),
        worked_twice.solution.fix_id: Tally(worked=2, failed=1),
    }
    order = ranked([failed, untried_old, untried_new, worked_once, worked_twice], tallies)
    assert order == [worked_once, worked_twice, untried_new, untried_old, failed]


def attempt(
    outcome: AttemptOutcome, source: AttemptSource = AttemptSource.TEAM, fix_id: str = "f" * 64
) -> Attempt:
    return Attempt(
        number=1,
        proposal=Proposal(
            patch=Patch(edits=(FileEdit(path="a.py", old="a", new="b"),)),
            explanation="",
            model="m",
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        ),
        outcome=outcome,
        source=source,
        origin="laptop-ana",
        fix=fix_id if source is AttemptSource.TEAM else "",
        fix_signature="a" * 64 if source is AttemptSource.TEAM else "",
    )


def test_a_run_tells_only_what_the_tests_said_about_team_fixes() -> None:
    found = results_of(
        [
            attempt(AttemptOutcome.FAILED, fix_id="e" * 64),
            attempt(AttemptOutcome.REJECTED, fix_id="d" * 64),
            attempt(AttemptOutcome.PASSED, AttemptSource.CACHE),
            attempt(AttemptOutcome.PASSED),
        ],
        node="laptop-luis",
        at=NOW,
    )
    assert [(r.fix_id[0], r.worked, r.node, r.signature[0]) for r in found] == [
        ("e", False, "laptop-luis", "a"),
        ("f", True, "laptop-luis", "a"),
    ]
