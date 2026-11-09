"""Summary prompts (ADR 0002, ADR 0006).

The model gets the article as numbered passages ([P1] ...) and returns JSON with citations to
those numbers plus a short verbatim quote per key point, so every claim can be checked.

Long articles use map-reduce: MAP extracts cited notes from each chunk of passages, REDUCE
merges the notes into one summary. Passage numbers stay global, so citations keep pointing at
the real passages. Changing any prompt text means bumping SUMMARY_PROMPT_VERSION: summaries,
token counts and evals are all stored per prompt version, so old and new can be compared.
"""

from __future__ import annotations

from onyourtips.core.models import Passage

SUMMARY_PROMPT_VERSION = "summary-v1"

SUMMARY_SYSTEM = """You summarize one article for a busy software engineer.
The article is given as numbered passages: [P1], [P2], ...
Return JSON only, with this shape:
{"tldr": "one-sentence takeaway",
 "key_points": [{"text": "a claim from the article", "citations": [1, 3],
                 "quote": "a short exact phrase copied from a cited passage"}],
 "why_it_matters": "one or two sentences"}
Rules:
- 3 to 5 key points, most important first.
- Every key point cites the passage numbers it is based on. Only cite passages that exist.
- "quote" is copied word for word from one of the cited passages, at most 20 words.
- Use only facts stated in the passages. Don't add outside knowledge.
- Plain, neutral language. No hype."""

MAP_SYSTEM = """You take notes on one part of a longer article.
The passages are numbered: [P12], [P13], ...
Return JSON only: {"points": [{"text": "a fact from these passages", "citations": [12],
"quote": "a short exact phrase copied from a cited passage"}]}
Rules:
- Up to 4 points: the most important facts in these passages only.
- Cite the passage numbers exactly as given. "quote" is copied word for word, at most 20 words.
- Use only facts stated in the passages."""

REDUCE_SYSTEM = """You write the final summary of an article from notes taken on its parts.
Each note has the passage numbers it came from and a quote.
Return JSON only:
{"tldr": "one-sentence takeaway",
 "key_points": [{"text": "...", "citations": [3, 17], "quote": "copied from a note's quote"}],
 "why_it_matters": "one or two sentences"}
Rules:
- 3 to 5 key points, most important first, merging notes that say the same thing.
- Keep each note's passage numbers as citations. Never invent new numbers.
- "quote" is copied from one of the merged notes' quotes.
- Use only facts in the notes."""


def render_passages(passages: list[Passage]) -> str:
    return "\n\n".join(f"[P{p.idx}] {p.text}" for p in passages)


def summary_user_message(title: str, passages: list[Passage]) -> str:
    return f"Title: {title}\n\n{render_passages(passages)}"


def map_user_message(title: str, passages: list[Passage], part: int, parts: int) -> str:
    return f"Title: {title}\nPart {part} of {parts}\n\n{render_passages(passages)}"


def reduce_user_message(title: str, notes: list) -> str:
    lines = [f"Title: {title}", "", "Notes:"]
    for i, n in enumerate(notes, 1):
        cites = ", ".join(f"P{c}" for c in n.citations)
        lines.append(f'[N{i}] {n.text} (from {cites}; quote: "{n.quote}")')
    return "\n".join(lines)
