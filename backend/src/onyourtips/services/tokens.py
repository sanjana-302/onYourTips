"""How many tokens would a summary of each article use? (ADR 0003, cost calibration)

Summaries aren't built yet, but their input is: the draft summary prompt plus the article's
numbered passages. Counting that now gives a real baseline, and prices can be applied later.

- estimate_articles: count for every article without calling a model, nightly. Uses the model's
                     own Hugging Face tokenizer when config/tokenizers.yaml names one (exact),
                     else characters / chars-per-token.
- calibrate:         real prompt token counts from the model (Ollama) for a sample of articles.
                     They check the tokenizer's accuracy, and set the ratio for the fallback.
- token_report:      typical / high-end / largest article, how many exceed the context
                     window, daily totals, and projected cost for every priced model.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel

from onyourtips.core.settings import Settings
from onyourtips.core.utils import utcnow
from onyourtips.enrichment.summary_prompt import (
    SUMMARY_PROMPT_VERSION,
    SUMMARY_SYSTEM,
    summary_user_message,
)
from onyourtips.enrichment.tokens import (
    DEFAULT_CHARS_PER_TOKEN,
    TEMPLATE_OVERHEAD_TOKENS,
    HFCounter,
    RatioCounter,
    TokenCounter,
    estimate_tokens,
    load_hf_tokenizer,
    percentile,
    tokenizer_repo,
)
from onyourtips.llm import LLMError, LLMGateway, LLMRequest
from onyourtips.llm.pricing import Pricing
from onyourtips.storage import Database

# Calibration only measures articles in this size range: long enough that the chat template's
# few tokens don't skew the ratio, short enough to fit the context window without truncation.
CALIBRATE_MIN_CHARS = 2_000
CALIBRATE_MAX_CHARS = 24_000
CALIBRATE_MAX_CTX = 32_768


def current_ratio(settings: Settings, db: Database) -> tuple[float, int]:
    """Chars per token to use for estimates: measured for the default model, else 4.0."""
    ratio, n = db.measured_ratio(settings.llm_model, SUMMARY_PROMPT_VERSION)
    return (ratio, n) if ratio else (DEFAULT_CHARS_PER_TOKEN, 0)


def counter_for(settings: Settings, db: Database, model: str | None = None) -> TokenCounter:
    """The model's own tokenizer if configured and loadable, else the (calibrated) ratio."""
    model = model or settings.llm_model
    repo = tokenizer_repo(settings.tokenizers_file, model)
    if repo:
        tok = load_hf_tokenizer(repo)
        if tok is not None:
            return HFCounter(tok, repo)
    ratio, _ = db.measured_ratio(model, SUMMARY_PROMPT_VERSION)
    return RatioCounter(ratio or DEFAULT_CHARS_PER_TOKEN)


def count_prompt(counter: TokenCounter, system: str, article: str) -> tuple[int, int, int]:
    """(system tokens, article tokens, total input tokens incl. the chat template)."""
    s, a = counter.count(system), counter.count(article)
    return s, a, s + a + TEMPLATE_OVERHEAD_TOKENS


def _prompt_parts(db: Database, article_id: str, content_hash: str) -> tuple[str, str] | None:
    art = db.get_article(article_id)
    passages = db.get_passages(content_hash)
    if art is None or not passages:
        return None
    return SUMMARY_SYSTEM, summary_user_message(art.title, passages)


# ----------------------------------------------------------------------------- estimate


def estimate_articles(
    settings: Settings,
    db: Database,
    *,
    since: datetime | None = None,
    counter: TokenCounter | None = None,
) -> dict:
    """Store (or refresh) a count for every article with usable text. No model call, no cost."""
    _, samples = current_ratio(settings, db)
    counter = counter or counter_for(settings, db)
    measured_system = db.measured_system_tokens(settings.llm_model, SUMMARY_PROMPT_VERSION)
    now = utcnow()
    count = 0
    for article_id, content_hash in db.usable_contents(since):
        parts = _prompt_parts(db, article_id, content_hash)
        if parts is None:
            continue
        system, article = parts
        system_tokens, article_tokens, total = count_prompt(counter, system, article)
        if measured_system and isinstance(counter, RatioCounter):
            # an estimate can borrow the instructions' real count, measured once by calibrate
            total += measured_system - system_tokens
            system_tokens = measured_system
        db.save_token_count(
            {
                "article_id": article_id,
                "content_hash": content_hash,
                "prompt_version": SUMMARY_PROMPT_VERSION,
                "method": "estimate",
                "model": settings.llm_model,
                "chars_per_token": round(len(article) / article_tokens, 4)
                if article_tokens
                else None,
                "system_chars": len(system),
                "article_chars": len(article),
                "system_tokens": system_tokens,
                "article_tokens": article_tokens,
                "input_tokens": total,
                "output_budget": settings.summary_output_tokens,
                "created_at": now.isoformat(timespec="seconds"),
                "tokenizer": counter.name,
            }
        )
        count += 1
    return {"estimated": count, "tokenizer": counter.name, "calibration_samples": samples}


# ----------------------------------------------------------------------------- calibrate


class CalibrationResult(BaseModel):
    model: str
    measured: int = 0
    skipped: int = 0  # suspicious counts (prompt cache hit) or errors
    errors: list[str] = []
    system_tokens: int | None = None
    chars_per_token: float | None = None  # after this run, over all samples for the model
    total_samples: int = 0
    previous_chars_per_token: float | None = None
    re_estimated: int = 0


def _measure(gw: LLMGateway, text: str, model: str, purpose_id: str | None) -> tuple[int, int]:
    """Real prompt token count: send the text as the only message and generate 1 token."""
    est = estimate_tokens(len(text), 3.0)  # pessimistic, to size the context window
    req = LLMRequest.simple(
        "token_count",
        text,
        model=model,
        prompt_version=SUMMARY_PROMPT_VERSION,
        article_id=purpose_id,
        temperature=0.0,
        max_tokens=1,
        num_ctx=min(CALIBRATE_MAX_CTX, max(2048, int(est * 1.3) + 256)),
    )
    res = gw.complete(req)
    return res.usage.input_tokens, res.call_id


def calibrate(
    settings: Settings,
    db: Database,
    *,
    model: str | None = None,
    sample: int | None = None,
    gateway: LLMGateway | None = None,
) -> CalibrationResult:
    """Measure real prompt token counts for a sample of articles with the given model.

    Two catches handled here:
    - Ollama skips prompt tokens it already has cached (a repeated prefix), and reports only the
      new ones. So the system prompt and each article are measured as separate, single messages,
      already-measured articles are skipped, and a count that is far too low is discarded.
    - A prompt longer than the context window is cut off silently, so the window is sized per
      article and only articles of 2k–24k characters are measured.
    """
    model = model or settings.llm_model
    n = sample or settings.token_calibration_sample
    no_retry = settings.model_copy(update={"llm_retries": 0})
    gw = gateway or LLMGateway(no_retry, db)
    previous, _ = db.measured_ratio(model, SUMMARY_PROMPT_VERSION)
    result = CalibrationResult(model=model, previous_chars_per_token=previous)

    try:
        system_tokens, _ = _measure(gw, SUMMARY_SYSTEM, model, None)
        result.system_tokens = system_tokens
    except LLMError as exc:
        result.errors.append(f"system prompt: {exc}")
        return result

    done = db.measured_article_ids(model, SUMMARY_PROMPT_VERSION)
    candidates = []
    for article_id, content_hash in db.usable_contents():
        if article_id in done:
            continue
        parts = _prompt_parts(db, article_id, content_hash)
        if parts and CALIBRATE_MIN_CHARS <= len(parts[1]) <= CALIBRATE_MAX_CHARS:
            candidates.append((article_id, content_hash, parts[1]))
    # Spread the sample over short and long articles instead of taking the first N.
    candidates.sort(key=lambda c: len(c[2]))
    step = max(1, len(candidates) // n) if candidates else 1
    chosen = candidates[::step][:n]

    now = utcnow().isoformat(timespec="seconds")
    for article_id, content_hash, text in chosen:
        try:
            tokens, call_id = _measure(gw, text, model, article_id)
        except LLMError as exc:
            result.skipped += 1
            result.errors.append(f"{article_id[:8]}: {exc}")
            continue
        if tokens < len(text) / 10:  # ~10+ chars per token is not real text: a cache hit
            result.skipped += 1
            continue
        db.save_token_count(
            {
                "article_id": article_id,
                "content_hash": content_hash,
                "prompt_version": SUMMARY_PROMPT_VERSION,
                "method": "measured",
                "model": model,
                "chars_per_token": round(len(text) / tokens, 4),
                "system_chars": len(SUMMARY_SYSTEM),
                "article_chars": len(text),
                "system_tokens": system_tokens,
                "article_tokens": tokens,
                "input_tokens": system_tokens + tokens,
                "output_budget": settings.summary_output_tokens,
                "llm_call_id": call_id,
                "created_at": now,
                "tokenizer": "ollama",
            }
        )
        result.measured += 1

    ratio, total = db.measured_ratio(model, SUMMARY_PROMPT_VERSION)
    result.chars_per_token = round(ratio, 3) if ratio else None
    result.total_samples = total
    if model == settings.llm_model and result.measured:
        result.re_estimated = estimate_articles(settings, db)["estimated"]
    return result


# ----------------------------------------------------------------------------- report


class Projection(BaseModel):
    provider: str
    model: str
    per_article_usd: float  # typical (median) article
    per_day_all_usd: float  # summarizing every new article
    per_week_digest_usd: float  # summarizing only the weekly digest (30 picks)


class TokenReport(BaseModel):
    model: str
    prompt_version: str
    days: int
    articles: int
    excerpt_articles: int
    tokenizer: str  # what produced the counts: hf:<repo> (exact) or ratio:<chars/token>
    calibrated: bool
    chars_per_token: float
    calibration_samples: int
    system_tokens: int
    output_budget: int
    p50_input: int
    p90_input: int
    max_input: int
    max_title: str | None = None
    total_input: int
    per_day_articles: float
    per_day_input: int
    num_ctx: int
    over_ctx: int  # input + output would not fit in the context window
    estimate_error_pct: float | None = None  # measured vs estimate on the calibration sample
    projections: list[Projection] = []


def token_report(settings: Settings, db: Database, days: int = 7) -> TokenReport:
    since = utcnow() - timedelta(days=days)
    model, pv = settings.llm_model, SUMMARY_PROMPT_VERSION
    rows = db.token_counts(model, pv, "estimate", since)
    ratio, samples = current_ratio(settings, db)
    inputs = [r["input_tokens"] for r in rows]
    out = settings.summary_output_tokens
    biggest = max(rows, key=lambda r: r["input_tokens"], default=None)

    # How far off is the estimate? Compare against measured rows (same model, same text).
    measured = {r["article_id"]: r for r in db.token_counts(model, pv, "measured")}
    estimates = {r["article_id"]: r for r in db.token_counts(model, pv, "estimate")}
    pairs = [
        (estimates[a]["article_tokens"], m["article_tokens"])
        for a, m in measured.items()
        if a in estimates
    ]
    error = round(100 * sum(abs(e - m) / m for e, m in pairs) / len(pairs), 1) if pairs else None

    per_day_articles = len(rows) / days
    p50 = percentile(inputs, 50)
    report = TokenReport(
        model=model,
        prompt_version=pv,
        days=days,
        articles=len(rows),
        excerpt_articles=sum(1 for r in rows if r["status"] == "excerpt"),
        tokenizer=(rows[0]["tokenizer"] if rows else None) or "-",
        calibrated=samples > 0,
        chars_per_token=round(ratio, 2),
        calibration_samples=samples,
        system_tokens=rows[0]["system_tokens"] if rows else 0,
        output_budget=out,
        p50_input=p50,
        p90_input=percentile(inputs, 90),
        max_input=biggest["input_tokens"] if biggest else 0,
        max_title=biggest["title"] if biggest else None,
        total_input=sum(inputs),
        per_day_articles=round(per_day_articles, 1),
        per_day_input=round(sum(inputs) / days),
        num_ctx=settings.llm_num_ctx,
        over_ctx=sum(1 for t in inputs if t + out > settings.llm_num_ctx),
        estimate_error_pct=error,
    )

    pricing = Pricing.from_yaml(settings.pricing_file)
    for provider, models in pricing.table.items():
        for name, price in models.items():
            if name == "*" or (price.input == 0 and price.output == 0):
                continue
            per_article = (p50 * price.input + out * price.output) / 1_000_000
            report.projections.append(
                Projection(
                    provider=provider,
                    model=name,
                    per_article_usd=round(per_article, 6),
                    per_day_all_usd=round(
                        (report.per_day_input * price.input + per_day_articles * out * price.output)
                        / 1_000_000,
                        4,
                    ),
                    per_week_digest_usd=round(30 * per_article, 4),
                )
            )
    return report
