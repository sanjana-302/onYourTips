from datetime import timedelta

from onyourtips.core.models import Engagement, Sighting
from onyourtips.ingestion.tagging import Tagger
from onyourtips.ranking.scoring import compute_signal, engagement_value, rank_score
from tests.helpers import NOW


def test_title_hit_tags(tagger):
    assert "llms" in tagger.tag("Mistral releases a new open-weight LLM")


def test_single_summary_mention_is_not_enough(tagger):
    tags = tagger.tag("Our quarterly update", "We also briefly mention kubernetes.")
    assert "cloud-infra" not in tags


def test_two_summary_hits_are_enough(tagger):
    tags = tagger.tag("Our quarterly update", "Moving from Docker to Kubernetes on AWS.")
    assert "cloud-infra" in tags


def test_source_tags_count(tagger):
    assert "rag-retrieval" in tagger.tag("cool-repo", None, ["vector-database"])


def test_word_boundaries(tagger):
    # "rust" must not match "trust", "agent" must not match "agentur" etc.
    tags = tagger.tag("How to build trust with your team")
    assert "programming-languages" not in tags


def test_regex_keyword():
    t = Tagger.from_dict({"topics": {"gpt": {"keywords": ["re:\\bgpt-?\\d"]}}})
    assert t.tag("GPT-5 is here") == ["gpt"]
    assert t.tag("gpts store") == []


def test_signal_rewards_engagement_and_multiple_sources():
    w = {"hn": 1.0, "rss": 2.0}
    hn = Sighting(source_id="hn", engagement=Engagement(points=300, comments=100), seen_at=NOW)
    rss = Sighting(source_id="rss", seen_at=NOW)
    assert compute_signal([rss], w) == 2.0
    assert compute_signal([hn, rss], w) > compute_signal([hn], w) > compute_signal([rss], w)
    assert engagement_value(Engagement()) == 0


def test_rank_score_half_life():
    assert rank_score(10, NOW, NOW, 24) == 10
    assert rank_score(10, NOW - timedelta(hours=24), NOW, 24) == 5
    assert rank_score(10, NOW + timedelta(hours=1), NOW, 24) == 10  # future dates clamp
