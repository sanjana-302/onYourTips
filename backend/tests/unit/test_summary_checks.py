"""Deterministic checks (assertions) on summaries: repairs, gates and metrics."""

from onyourtips.core.models import Passage
from onyourtips.enrichment.summary_checks import check_summary
from onyourtips.enrichment.summary_schema import KeyPoint, Summary
from onyourtips.services.evals import cohen_kappa

P = [
    Passage(
        idx=1,
        text="Acme released Rocket 2, an open model for coding agents.",
        char_start=0,
        char_end=10,
    ),
    Passage(
        idx=2,
        text="It beats the previous best on SWE-bench by four points.",
        char_start=11,
        char_end=20,
    ),
    Passage(
        idx=3,
        text="Weights are available under the Apache 2.0 license.",
        char_start=21,
        char_end=30,
    ),
]


def kp(text, cites, quote=""):
    return KeyPoint(text=text, citations=cites, quote=quote)


def test_good_summary_passes_with_metrics():
    s = Summary(
        tldr="Acme released an open coding model.",
        key_points=[
            kp(
                "Rocket 2 is an open model for coding agents.",
                [1],
                "an open model for coding agents",
            ),
            kp(
                "It improves SWE-bench by four points.", [2], "beats the previous best on swe-bench"
            ),
            kp("Weights use Apache 2.0.", [3], "Weights are available under the Apache license"),
        ],
    )
    out, c = check_summary(s, P)
    assert c.passed and c.key_points == 3 and c.invalid_citations_removed == 0
    assert (c.quotes, c.quotes_exact, c.quotes_fuzzy) == (3, 2, 1)  # last one drops "2.0"
    assert c.quote_match_rate == 1.0 and c.coverage == 1.0 and c.cited_passages == 3


def test_invalid_citations_are_repaired_or_dropped():
    s = Summary(
        tldr="x y z",
        key_points=[
            kp("Rocket 2 is open.", [1, 9]),  # P9 doesn't exist: removed, point kept
            kp("Made-up claim.", [7]),  # nothing valid left: point dropped
        ],
    )
    out, c = check_summary(s, P)
    assert c.passed and c.invalid_citations_removed == 2 and c.points_dropped == 1
    assert [k.citations for k in out.key_points] == [[1]]


def test_no_valid_citations_fails():
    s = Summary(tldr="x y z", key_points=[kp("Claim.", [42])])
    _, c = check_summary(s, P)
    assert not c.passed and "no key point cites a real passage" in c.failure


def test_quote_in_wrong_passage_and_duplicates():
    s = Summary(
        tldr="x y z",
        key_points=[
            kp("License is Apache.", [1], "available under the Apache 2.0 license"),  # it's in P3
            kp("Rocket 2 is an open model for coding agents", [1]),
            kp("Rocket 2 is an open model for coding agents.", [1]),
        ],
    )
    _, c = check_summary(s, P)
    assert c.quote_elsewhere == 1 and c.quotes_exact == 0
    assert c.duplicate_points == 1


def test_cohen_kappa():
    assert cohen_kappa([]) is None
    assert cohen_kappa([("a", "a"), ("b", "b")]) == 1.0
    # agree 3 of 4, but one rater says "a" every time: chance agreement is high
    pairs = [("a", "a"), ("a", "a"), ("a", "a"), ("b", "a")]
    assert cohen_kappa(pairs) == 0.0
    pairs = [("s", "s"), ("s", "s"), ("u", "u"), ("u", "s"), ("p", "p"), ("s", "p")]
    assert 0.4 < cohen_kappa(pairs) < 0.6
