"""Command line interface: ``oyt --help``."""

import asyncio
import json
import logging
import webbrowser
from collections import defaultdict
from datetime import timedelta
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from onyourtips.core.models import ContentType, FeedItem, InteractionType
from onyourtips.core.settings import get_settings
from onyourtips.core.utils import utcnow
from onyourtips.ingestion.pipeline import Pipeline
from onyourtips.ingestion.sources import load_source_configs
from onyourtips.ingestion.tagging import Tagger
from onyourtips.services import build_digest
from onyourtips.storage import Database

app = typer.Typer(no_args_is_help=True, help="OnYourTips — your daily AI & software digest.")
console = Console()

TYPE_HEADINGS = {
    ContentType.NEWS: "📰 News",
    ContentType.PROJECT: "🛠  Projects",
    ContentType.RESEARCH_PAPER: "📄 Research",
    ContentType.BLOG_POST: "✍️  Blog posts",
    ContentType.DISCUSSION: "💬 Discussions",
}


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )
    logging.getLogger("httpx").setLevel(logging.INFO if verbose else logging.WARNING)


def _db() -> Database:
    return Database(get_settings().db_path)


def _print_item(n: int, item: FeedItem) -> None:
    a, st = item.article, item.state
    marks = ("★ " if st.saved else "") + (f"[{st.rating}/5] " if st.rating else "")
    srcs = ", ".join(sg.source_id for sg in item.sightings)
    console.print(
        f"[bold]{n:>2}. {marks}{a.title}[/]  "
        f"[dim]{a.id[:8]} · {a.content_type.value} · score {item.score:.2f}[/]"
    )
    console.print(f"    [link={a.url}]{a.url}[/link]")
    meta = [f"via {srcs}"]
    if a.topics:
        meta.append("#" + " #".join(a.topics))
    console.print(f"    [cyan]{'  ·  '.join(meta)}[/]")
    if a.summary:
        console.print(f"    [dim]{a.summary[:200]}[/]")
    console.print()


def _dump(items: list[FeedItem]) -> None:
    print(json.dumps([i.model_dump(mode="json") for i in items], indent=2))


# ------------------------------------------------------------------ setup / ingestion


@app.command()
def sources() -> None:
    """List configured sources."""
    s = get_settings()
    table = Table("id", "name", "kind", "type", "weight", "limit", "enabled")
    for c in load_source_configs(s.sources_file):
        table.add_row(
            c.id,
            c.name,
            c.kind.value,
            c.content_type.value,
            f"{c.weight:g}",
            str(c.limit),
            "✓" if c.enabled else "—",
        )
    console.print(table)


@app.command()
def topics() -> None:
    """List taxonomy topics."""
    tagger = Tagger.from_yaml(get_settings().taxonomy_file)
    table = Table("slug", "label", "#keywords")
    for t in tagger.topics:
        table.add_row(t.slug, t.label, str(len(t.patterns)))
    console.print(table)


@app.command()
def ingest(
    source: Annotated[
        list[str] | None, typer.Option("--source", "-s", help="Only these source ids")
    ] = None,
) -> None:
    """Fetch all enabled sources and store new/updated articles."""
    s = get_settings()
    configs = load_source_configs(s.sources_file)
    tagger = Tagger.from_yaml(s.taxonomy_file)
    with Database(s.db_path) as db:
        with console.status("Ingesting…"):
            report = asyncio.run(
                Pipeline(s, configs, tagger, db).run(set(source) if source else None)
            )

    table = Table(
        "source",
        "status",
        "fetched",
        "new",
        "merged",
        "updated",
        "skipped",
        "secs",
        title=f"Run {report.run_id} · {report.started_at:%Y-%m-%d %H:%M} UTC",
    )
    for r in report.results:
        status = "[green]ok[/]" if r.ok else f"[red]error[/] {r.error[:60]}"
        table.add_row(
            r.source_id,
            status,
            str(r.fetched),
            str(r.new),
            str(r.merged),
            str(r.updated),
            str(r.skipped),
            f"{r.duration_s:.1f}",
        )
    t = report.totals
    table.add_section()
    table.add_row(
        "[bold]total",
        "",
        *(str(t[k]) for k in ("fetched", "new", "merged", "updated", "skipped")),
        "",
    )
    console.print(table)


# ------------------------------------------------------------------ reading


@app.command()
def feed(
    hours: Annotated[int, typer.Option(help="Look-back window")] = 24,
    topic: Annotated[list[str] | None, typer.Option("--topic", "-t")] = None,
    type_: Annotated[list[ContentType] | None, typer.Option("--type")] = None,
    source: Annotated[list[str] | None, typer.Option("--source", "-s")] = None,
    query: Annotated[str | None, typer.Option("--query", "-q", help="Text search")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 20,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Latest ranked feed (recency-weighted). Dismissed items are hidden."""
    s = get_settings()
    with _db() as db:
        items = db.feed(
            since=utcnow() - timedelta(hours=hours),
            topics=topic,
            content_types=type_,
            source_ids=source,
            query=query,
            limit=limit,
            half_life_hours=s.half_life_hours,
        )
    if as_json:
        return _dump(items)
    if not items:
        console.print("[yellow]Nothing found. Run `oyt ingest` first or widen --hours.")
        return
    for n, item in enumerate(items, 1):
        _print_item(n, item)


@app.command()
def digest(
    days: Annotated[int, typer.Option(help="Window length in days")] = 7,
    limit: Annotated[int | None, typer.Option("--limit", "-n")] = None,
    min_signal: Annotated[float | None, typer.Option(help="Quality threshold")] = None,
    max_per_topic: Annotated[int | None, typer.Option(help="Cap per primary topic")] = None,
    topic: Annotated[list[str] | None, typer.Option("--topic", "-t")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Best of the week: no recency decay, quality threshold, topic diversity, type quotas."""
    s = get_settings()
    with _db() as db:
        items = build_digest(
            db,
            s,
            days=days,
            limit=limit,
            min_signal=min_signal,
            max_per_topic=max_per_topic,
            topics=topic,
        )
    if as_json:
        return _dump(items)
    if not items:
        console.print(
            "[yellow]Nothing above the quality threshold. Try --min-signal 1 or ingest more."
        )
        return
    console.print(f"[bold magenta]Best of the last {days} days[/] — {len(items)} picks\n")
    groups: dict[ContentType, list[FeedItem]] = defaultdict(list)
    for item in items:
        groups[item.article.content_type].append(item)
    n = 0
    for ctype in TYPE_HEADINGS:
        if groups.get(ctype):
            console.rule(TYPE_HEADINGS[ctype], align="left")
            for item in groups[ctype]:
                n += 1
                _print_item(n, item)


# ------------------------------------------------------------------ your interactions


def _act(
    article: str, action: InteractionType, value: int | None = None, note: str | None = None
) -> str:
    with _db() as db:
        try:
            aid = db.resolve_id(article)
            db.add_interaction(aid, action, value=value, note=note)
            art = db.get_article(aid)
        except (KeyError, ValueError) as exc:
            console.print(f"[red]{exc}")
            raise typer.Exit(1) from None
    console.print(f"[green]{action.value}[/] · {art.title}")
    return art.url


@app.command("open")
def open_(
    article: Annotated[str, typer.Argument(help="Article id (8-char prefix is fine)")],
    browser: Annotated[bool, typer.Option(help="Open in your browser")] = True,
) -> None:
    """Record that you opened an article, and open it."""
    url = _act(article, InteractionType.OPEN)
    console.print(url)
    if browser:
        webbrowser.open(url)


@app.command()
def save(
    article: Annotated[str, typer.Argument()],
    note: Annotated[str | None, typer.Option("--note", "-m", help="Why it's interesting")] = None,
) -> None:
    """Save an article (signals interest; feeds the future recommender)."""
    _act(article, InteractionType.SAVE, note=note)


@app.command()
def unsave(article: Annotated[str, typer.Argument()]) -> None:
    """Remove an article from saved."""
    _act(article, InteractionType.UNSAVE)


@app.command()
def dismiss(article: Annotated[str, typer.Argument()]) -> None:
    """Hide an article from feed and digest (signals disinterest)."""
    _act(article, InteractionType.DISMISS)


@app.command()
def undismiss(article: Annotated[str, typer.Argument()]) -> None:
    """Bring a dismissed article back."""
    _act(article, InteractionType.UNDISMISS)


@app.command()
def rate(
    article: Annotated[str, typer.Argument()],
    stars: Annotated[int, typer.Argument(min=1, max=5)],
) -> None:
    """Rate an article 1–5."""
    _act(article, InteractionType.RATE, value=stars)


@app.command()
def saved(
    days: Annotated[int | None, typer.Option(help="Only items saved in the last N days")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Your saved articles, newest first."""
    with _db() as db:
        items = db.saved(since=utcnow() - timedelta(days=days) if days else None)
    if as_json:
        return _dump(items)
    if not items:
        console.print("[yellow]Nothing saved yet. Use `oyt save <id>`.")
        return
    for n, item in enumerate(items, 1):
        _print_item(n, item)


@app.command()
def interests(days: Annotated[int, typer.Option()] = 7) -> None:
    """Topics you engaged with recently (saves +3, opens +1, rating ±2, dismiss −2)."""
    with _db() as db:
        profile = db.interest_profile(since=utcnow() - timedelta(days=days))
    if not profile:
        console.print("[yellow]No interactions in this window yet.")
        return
    table = Table("topic", "interest", title=f"Your interests · last {days} days")
    for t, score in profile.items():
        table.add_row(t, f"{score:+.1f}")
    console.print(table)


def _lan_ip() -> str | None:
    """Best-effort local network IP (no packets are sent)."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None
    finally:
        s.close()


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Bind address (default 0.0.0.0)")] = None,
    port: Annotated[int | None, typer.Option(help="Port (default 8000)")] = None,
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes")] = False,
) -> None:
    """Serve the API + web app on your network, so your phone can open it."""
    import uvicorn

    s = get_settings()
    host, port = host or s.api_host, port or s.api_port
    console.print(f"[bold]OnYourTips[/] on http://localhost:{port}")
    if host == "0.0.0.0" and (ip := _lan_ip()):
        console.print(f"[bold green]On your phone (same Wi-Fi):[/] http://{ip}:{port}")
    if not (s.frontend_dist / "index.html").exists():
        console.print("[yellow]UI not built yet — run: cd frontend && npm install && npm run build")
    console.print(f"API docs: http://localhost:{port}/docs\n")
    uvicorn.run("onyourtips.api.main:create_app", factory=True, host=host, port=port, reload=reload)


@app.command()
def stats() -> None:
    """Database statistics."""
    with _db() as db:
        st = db.stats()
    console.print(
        f"[bold]Articles:[/] {st['articles']}   [bold]Sightings:[/] {st['sightings']}   "
        f"[bold]Interactions:[/] {st['interactions']}   [bold]Saved:[/] {st['saved']}"
    )
    for key in ("by_content_type", "by_topic", "by_source"):
        table = Table("name", "count", title=key.replace("_", " "))
        for k, v in st[key].items():
            table.add_row(str(k), str(v))
        console.print(table)
    if st["last_run"]:
        r = st["last_run"][0]
        failed = [x["source_id"] for x in st["last_run"] if not x["ok"]]
        console.print(f"Last run {r['started_at']} UTC · failed: {', '.join(failed) or 'none'}")


# ------------------------------------------------------------------ daily refresh


@app.command()
def nightly(
    trigger: Annotated[str, typer.Option(help="scheduled (1 AM job) or manual")] = "scheduled",
) -> None:
    """Run the daily refresh: ingest → extract full text → retry → retag → alerts."""
    from onyourtips.services.nightly import next_refresh_at, run_nightly

    s = get_settings()
    with _db() as db:
        with console.status("Refreshing: ingest, extract, retag…"):
            r = run_nightly(s, db, trigger=trigger)
    color = {"ok": "green", "partial": "yellow", "failed": "red", "skipped": "yellow"}.get(
        r.status, "white"
    )
    secs = (r.finished_at - r.started_at).total_seconds() if r.finished_at else 0
    console.print(f"[bold {color}]Refresh {r.status}[/] ({r.trigger}, run {r.run_id}, {secs:.0f}s)")
    if r.error:
        console.print(f"[red]{r.error}")
    if r.network_outage:
        console.print(
            "[yellow]The network dropped during this run: failures caused by it were not "
            "counted and will be retried next time."
        )
    i, e, t = r.ingest, r.extract, r.retag
    if i:
        console.print(
            f"Ingest:  {i.get('fetched', 0)} fetched, {i.get('new', 0)} new, "
            f"{i.get('merged', 0)} merged; failed sources: "
            f"{', '.join(i.get('failed_sources', [])) or 'none'}"
        )
    if e:
        console.print(
            f"Extract: {e['candidates']} to do ({e['retries']} retries) → {e['ok']} full text, "
            f"{e['excerpt']} excerpt, {e['skipped']} skipped, {e['failed']} failed; "
            f"{e['retry_scheduled']} will retry tomorrow, {e['gave_up']} given up"
        )
    if t:
        console.print(f"Retag:   {t['changed']} of {t['checked']} stories got different topics")
    if r.tokens:
        k = r.tokens
        console.print(
            f"Tokens:  {k['estimated']} summary-size counts ({_tokenizer_label(k['tokenizer'])})"
        )
    for a in r.new_alerts:
        console.print(f"[yellow]Alert:[/] {a.message}")
    console.print(f"[dim]Next scheduled refresh: {next_refresh_at(s):%a %d %b, %H:%M}[/]")
    if r.status == "failed":
        raise typer.Exit(1)


@app.command()
def alerts(
    resolve: Annotated[int | None, typer.Option(help="Mark this alert id as handled")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Include resolved alerts")] = False,
) -> None:
    """Things to check: sources failing two refreshes in a row, pages given up on."""
    from onyourtips.services.nightly import list_alerts, resolve_alert

    with _db() as db:
        if resolve is not None:
            ok = resolve_alert(db, resolve)
            console.print(
                f"[green]Resolved alert {resolve}" if ok else f"[red]No open alert {resolve}"
            )
            return
        items = list_alerts(db, include_resolved=all_)
    if not items:
        console.print("[green]No open alerts.")
        return
    table = Table("id", "when", "kind", "subject", "message", "resolved")
    for a in items:
        table.add_row(
            str(a.id),
            f"{a.created_at:%d %b %H:%M}",
            a.kind,
            a.subject,
            a.message[:90],
            f"{a.resolved_at:%d %b}" if a.resolved_at else "",
        )
    console.print(table)
    console.print("[dim]Mark one as handled: oyt alerts --resolve <id>[/]")


# ------------------------------------------------------------------ full text


@app.command()
def extract(
    article: Annotated[
        list[str] | None, typer.Argument(help="Article ids (8-char prefix ok)")
    ] = None,
    digest: Annotated[bool, typer.Option(help="Extract this week's digest picks")] = False,
    saved: Annotated[bool, typer.Option(help="Extract your saved articles")] = False,
    force: Annotated[bool, typer.Option(help="Re-extract even if already done")] = False,
    show: Annotated[str | None, typer.Option(help="Print the passages of one article")] = None,
) -> None:
    """Fetch the full text of selected articles and split it into numbered passages."""
    from onyourtips.services import extract_articles, get_content, select_for_extraction

    s = get_settings()
    with _db() as db:
        if show:
            view = get_content(db, show)
            if view is None:
                console.print("[yellow]Not extracted yet. Run: oyt extract " + show)
                raise typer.Exit(1)
            c = view.content
            console.print(
                f"[bold]{c.status.value}[/] via {c.extractor}, {c.word_count} words, "
                f"{len(view.passages)} passages" + (f"  [dim]({c.error})[/]" if c.error else "")
            )
            for p in view.passages:
                console.print(f"\n[bold cyan][P{p.idx}][/] {p.text}")
            return
        try:
            ids = select_for_extraction(db, s, ids=article, digest=digest, saved=saved)
        except (KeyError, ValueError) as exc:
            console.print(f"[red]{exc}")
            raise typer.Exit(1) from None
        if not ids:
            console.print("Nothing selected. Pass article ids, --digest or --saved.")
            raise typer.Exit(1)
        with console.status(f"Extracting {len(ids)} articles…"):
            results = asyncio.run(extract_articles(db, s, ids, force=force))

    table = Table("id", "title", "status", "via", "words", "passages", "note")
    colors = {"ok": "green", "excerpt": "yellow", "skipped": "dim", "failed": "red"}
    for r in results:
        table.add_row(
            r.article_id[:8],
            r.title[:48],
            f"[{colors.get(r.status, 'white')}]{r.status}[/]",
            r.extractor,
            str(r.words),
            str(r.passages),
            ("cached" if r.cached else "") + (f" {r.note[:50]}" if r.note else ""),
        )
    console.print(table)
    console.print("[dim]See the passages of one article: oyt extract --show <id>[/]")


# ------------------------------------------------------------------ AI (LLM gateway)

llm_app = typer.Typer(no_args_is_help=True, help="Local AI models: status, quick test, compare.")
app.add_typer(llm_app, name="llm")


def _gb(n: int | None) -> str:
    return f"{n / 1e9:.1f} GB" if n else "-"


@llm_app.command("status")
def llm_status_cmd() -> None:
    """Is the AI provider running, and which models are downloaded?"""
    from onyourtips.services.llm import llm_status

    st = llm_status(get_settings())
    if not st.reachable:
        console.print(f"[red]Not reachable:[/] {st.hint}")
        console.print(
            "Install: brew install ollama · then: ollama serve · ollama pull " + st.default_model
        )
        raise typer.Exit(1)
    console.print(f"[green]{st.provider} {st.version or ''} is running[/] at {st.url}")
    table = Table("model", "size", "parameters", "quantization", "default")
    for m in st.models:
        is_default = m.name in (st.default_model, f"{st.default_model}:latest")
        table.add_row(
            m.name,
            _gb(m.size_bytes),
            m.parameter_size or "-",
            m.quantization or "-",
            "✓" if is_default else "",
        )
    console.print(table)
    if st.hint:
        console.print(f"[yellow]{st.hint}")


@llm_app.command("ping")
def llm_ping(
    prompt: Annotated[str, typer.Argument()] = "In one sentence, what is a large language model?",
    model: Annotated[str | None, typer.Option("--model", "-m")] = None,
) -> None:
    """Send one prompt through the gateway and show the answer, tokens, speed and cost."""
    from onyourtips.llm import LLMError, LLMGateway, LLMRequest

    s = get_settings()
    with _db() as db:
        with console.status(f"Asking {model or s.llm_model}…"):
            try:
                res = LLMGateway(s, db).complete(LLMRequest.simple("ping", prompt, model=model))
            except LLMError as exc:
                console.print(f"[red]{exc}")
                raise typer.Exit(1) from None
    console.print(res.text.strip())
    load = res.timings.get("load_ms")
    console.print(
        f"\n[dim]{res.model} · {res.usage.input_tokens} in / {res.usage.output_tokens} out · "
        f"${res.cost_usd:.4f} · {res.latency_ms / 1000:.1f}s"
        + (f" (model load {load / 1000:.1f}s)" if load and load > 200 else "")
        + (f" · {res.tokens_per_sec:.0f} tok/s" if res.tokens_per_sec else "")
        + f" · ledger #{res.call_id}[/]"
    )


@llm_app.command("compare")
def llm_compare(
    model: Annotated[list[str], typer.Option("--model", "-m", help="Repeat for each model")],
    article: Annotated[
        str | None, typer.Option(help="Use this article's text (id prefix ok)")
    ] = None,
    text: Annotated[str | None, typer.Option(help="Use this text instead")] = None,
    structured: Annotated[bool, typer.Option(help="Ask for JSON and check it's valid")] = False,
) -> None:
    """Run the same summarization task on several models and save a side-by-side report."""
    from onyourtips.services import get_content
    from onyourtips.services.llm import SAMPLE_TEXT, compare_models, write_compare_report

    s = get_settings()
    with _db() as db:
        title, article_id = "sample text", None
        if article:
            view = get_content(db, article)
            art = db.get_article(db.resolve_id(article))
            if view and view.content.text:
                input_text = view.content.text
            elif art:
                input_text = f"{art.title}\n\n{art.summary or ''}".strip()
                console.print(
                    "[yellow]Using title + feed description. "
                    f"For full text run: oyt extract {article}[/]"
                )
            else:
                console.print(f"[red]No article {article}")
                raise typer.Exit(1)
            title, article_id = (art.title if art else article), (art.id if art else None)
        else:
            input_text = text or SAMPLE_TEXT
            title = "custom text" if text else "sample text"
        input_text = input_text[:12000]  # keep within a small model's context
        results = []
        for m in model:
            with console.status(f"{m}…"):
                results += compare_models(
                    s, db, [m], input_text, structured=structured, article_id=article_id
                )
    table = Table("model", "result", "time", "load", "in / out", "speed", "valid JSON")
    for r in results:
        table.add_row(
            r.model,
            "[green]ok[/]" if r.ok else f"[red]failed[/] {(r.error or '')[:40]}",
            f"{r.latency_ms / 1000:.1f}s",
            f"{(r.load_ms or 0) / 1000:.1f}s",
            f"{r.input_tokens} / {r.output_tokens}",
            f"{r.tokens_per_sec:.0f} tok/s" if r.tokens_per_sec else "-",
            "-" if r.valid_json is None else ("yes" if r.valid_json else "[red]no[/]"),
        )
    console.print(table)
    for r in results:
        if r.ok:
            console.rule(r.model, align="left")
            console.print(r.text.strip())
    path = write_compare_report(
        s.db_path.parent / "compare", results, input_text, structured=structured, title=title
    )
    console.print(f"\n[green]Report saved:[/] {path}")


def _tokenizer_label(name: str) -> str:
    if name.startswith("hf:"):
        return f"exact: {name[3:]} tokenizer"
    if name.startswith("ratio:"):
        return f"estimate: {name[6:]} chars/token"
    return name


tokens_app = typer.Typer(help="How many tokens a summary of each article would use.")
app.add_typer(tokens_app, name="tokens")


@tokens_app.callback(invoke_without_command=True)
def tokens_report_cmd(
    ctx: typer.Context,
    days: Annotated[int, typer.Option(help="Articles first seen in the last N days")] = 7,
) -> None:
    """Token use per article for a summary: typical, high end, largest, over the limit."""
    if ctx.invoked_subcommand:
        return
    from onyourtips.services.tokens import token_report

    s = get_settings()
    with _db() as db:
        r = token_report(s, db, days=days)
    if not r.articles:
        console.print("No estimates yet. Run `oyt tokens estimate` (or wait for the nightly run).")
        return
    basis = _tokenizer_label(r.tokenizer)
    if r.estimate_error_pct is not None:
        basis += (
            f"; within ±{r.estimate_error_pct}% of Ollama's real counts "
            f"({r.calibration_samples} articles)"
        )
    else:
        basis += "; not checked against Ollama yet (`oyt tokens calibrate`)"
    console.print(f"[bold]Summary input per article[/], last {days} days, model {r.model}")
    console.print(f"[dim]{basis}; prompt {r.prompt_version}[/]")
    table = Table("", "tokens", title=None, show_header=False)
    table.add_row("Articles with text", f"{r.articles} ({r.excerpt_articles} excerpt only)")
    table.add_row("Instructions (every call)", f"{r.system_tokens:,}")
    table.add_row("Typical article (median)", f"{r.p50_input:,}")
    table.add_row("High end (90th percentile)", f"{r.p90_input:,}")
    table.add_row("Largest", f"{r.max_input:,}  [dim]{(r.max_title or '')[:50]}[/]")
    table.add_row("Expected summary (output)", f"{r.output_budget:,}")
    table.add_row(
        f"Too long for {r.num_ctx:,} context",
        f"[{'yellow' if r.over_ctx else 'green'}]{r.over_ctx}[/] articles would be cut off",
    )
    table.add_row(
        "Per day, if every article", f"{r.per_day_articles:g} articles → {r.per_day_input:,} in"
    )
    console.print(table)
    if r.projections:
        pt = Table(
            "provider",
            "model",
            "per article",
            "every article / day",
            "digest / week",
            title="Projected cost (token counts from the local tokenizer: ±15%)",
        )
        for pr in r.projections:
            pt.add_row(
                pr.provider,
                pr.model,
                f"${pr.per_article_usd:.5f}",
                f"${pr.per_day_all_usd:.3f}",
                f"${pr.per_week_digest_usd:.3f}",
            )
        console.print(pt)
    else:
        console.print(
            "[dim]Add a paid model's prices to config/pricing.yaml to see projected cost."
        )


@tokens_app.command("estimate")
def tokens_estimate_cmd() -> None:
    """Recompute the free estimate for every article with text (no model calls)."""
    from onyourtips.services.tokens import estimate_articles

    with _db() as db:
        k = estimate_articles(get_settings(), db)
    console.print(f"Counted {k['estimated']} articles ({_tokenizer_label(k['tokenizer'])}).")


@tokens_app.command("calibrate")
def tokens_calibrate_cmd(
    model: Annotated[
        str | None, typer.Option("-m", "--model", help="Default: OYT_LLM_MODEL")
    ] = None,
    sample: Annotated[int | None, typer.Option(help="How many articles to measure")] = None,
) -> None:
    """Measure real token counts with a local model for a sample of articles."""
    from onyourtips.services.tokens import calibrate

    s = get_settings()
    with _db() as db:
        with console.status("Counting tokens with the model (one short call per article)…"):
            r = calibrate(s, db, model=model, sample=sample)
    for e in r.errors[:5]:
        console.print(f"[red]{e}")
    if r.system_tokens is None:
        console.print("Is Ollama running? Try: oyt llm status")
        raise typer.Exit(1)
    console.print(
        f"{r.model}: measured {r.measured} articles ({r.skipped} skipped); "
        f"instructions = {r.system_tokens} tokens"
    )
    if r.chars_per_token:
        before = f" (was {r.previous_chars_per_token:.3f})" if r.previous_chars_per_token else ""
        console.print(
            f"Ratio: [bold]{r.chars_per_token} chars/token[/] over {r.total_samples} articles"
            f"{before}. Re-estimated {r.re_estimated} articles."
        )
    console.print("[dim]See the result: oyt tokens · the calls: oyt costs --by purpose")


@app.command()
def costs(
    days: Annotated[int, typer.Option(help="Look-back window")] = 7,
    by: Annotated[str, typer.Option(help="model | purpose | day")] = "model",
) -> None:
    """AI usage from the cost ledger: calls, tokens, cost, speed."""
    from onyourtips.services.llm import cost_summary

    if by not in ("model", "purpose", "day"):
        console.print("[red]--by must be model, purpose or day")
        raise typer.Exit(1)
    with _db() as db:
        summary = cost_summary(db, days=days, by=by)
    if not summary.rows:
        console.print("No AI calls yet. Try: oyt llm ping")
        return
    table = Table(
        by,
        "calls",
        "ok",
        "invalid",
        "errors",
        "tokens in",
        "tokens out",
        "cost",
        "avg time",
        "avg speed",
        title=f"AI usage, last {days} days",
    )
    for r in summary.rows:
        table.add_row(
            r.key,
            str(r.calls),
            str(r.ok),
            str(r.invalid),
            str(r.errors),
            f"{r.input_tokens:,}",
            f"{r.output_tokens:,}",
            f"${r.cost_usd:.4f}",
            f"{(r.avg_latency_ms or 0) / 1000:.1f}s",
            f"{r.avg_tokens_per_sec:.0f} tok/s" if r.avg_tokens_per_sec else "-",
        )
    table.add_section()
    table.add_row(
        "[bold]total",
        str(summary.total_calls),
        "",
        "",
        "",
        "",
        "",
        f"[bold]${summary.total_cost_usd:.4f}",
        "",
        "",
    )
    console.print(table)


if __name__ == "__main__":
    app()
