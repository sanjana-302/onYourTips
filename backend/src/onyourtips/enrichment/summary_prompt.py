"""Draft summary prompt (ADR 0002), used today only to count tokens.

Summaries aren't built yet. This draft exists so token counts measure what a real summary call
will send: the instructions plus the article as numbered passages ([P1] ...). When the real
prompt is written, bump SUMMARY_PROMPT_VERSION; counts are stored per prompt version.
"""

from __future__ import annotations

from onyourtips.core.models import Passage

SUMMARY_PROMPT_VERSION = "summary-draft-1"

SUMMARY_SYSTEM = """You summarize one article for a busy software engineer.
The article is given as numbered passages: [P1], [P2], ...
Return JSON only, with this shape:
{"tldr": "one-sentence takeaway",
 "key_points": [{"text": "a claim from the article", "citations": [1, 3]}],
 "why_it_matters": "one or two sentences",
 "topics": ["llms", "ai-coding"]}
Rules:
- 3 to 5 key points. Every key point cites at least one passage number it is based on.
- Use only facts stated in the passages. Don't add outside knowledge.
- Plain, neutral language. No hype."""


def render_passages(passages: list[Passage]) -> str:
    return "\n\n".join(f"[P{p.idx}] {p.text}" for p in passages)


def summary_user_message(title: str, passages: list[Passage]) -> str:
    return f"Title: {title}\n\n{render_passages(passages)}"
