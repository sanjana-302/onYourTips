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


def _db_open() -> Database:
    """For one-shot commands: the connection closes when the process exits."""
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
    summaries: Annotated[
        bool | None,
        typer.Option("--summaries/--no-summaries", help="Summarize new stories (default: on)"),
    ] = None,
) -> None:
    """Run the daily refresh: ingest → extract → retag → alerts → summaries until 08:30."""
    from onyourtips.services.nightly import next_refresh_at, run_nightly

    s = get_settings()
    with _db() as db:
        with console.status("Refreshing: ingest, extract, retag, summarize…"):
            r = run_nightly(s, db, trigger=trigger, summarize=summaries)
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
    if r.summaries:
        m = r.summaries
        if m.get("skipped"):
            console.print(f"Summary: skipped, {m['skipped']}")
        else:
            console.print(
                f"Summary: {m['candidates']} to do → {m['done']} done "
                f"({m['map_reduce']} map-reduce), {m['failed']} failed, "
                f"{m['not_reached']} not reached by {m['deadline']}; {m.get('minutes', 0)} min "
                f"with {m['model']}"
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


# ------------------------------------------------------------------ summaries


def _print_summary(view) -> None:
    if view.status != "ready":
        msg = view.description or "not summarized yet (oyt summarize <id>)"
        console.print(f"[yellow]{view.status}:[/] {msg}")
        return
    console.print(f"[bold]{view.tldr}[/]")
    for i, kp in enumerate(view.key_points, 1):
        cites = " ".join(f"[P{c}]" for c in kp.citations)
        console.print(f" {i}. {kp.text} [cyan]{cites}[/]")
        if kp.quote:
            console.print(f"    [dim]“{kp.quote}”[/]")
    if view.why_it_matters:
        console.print(f"[italic]Why it matters:[/] {view.why_it_matters}")
    meta = [view.model or "", view.mode or ""]
    if view.partial:
        meta.append("partial (first parts only)")
    if view.input_tokens is not None:
        meta.append(f"{view.input_tokens:,} in / {view.output_tokens:,} out")
    if view.cost_usd is not None:
        meta.append(f"${view.cost_usd:.4f}")
    if view.latency_ms:
        meta.append(f"{view.latency_ms / 1000:.1f}s")
    if view.quote_match_rate is not None:
        meta.append(f"quotes found {view.quote_match_rate:.0%}")
    console.print(f"[dim]{' · '.join(m for m in meta if m)}[/]")


@app.command()
def summarize(
    article_id: Annotated[str, typer.Argument(help="Article id (prefix ok)")],
    model: Annotated[str | None, typer.Option("-m", "--model")] = None,
    force: Annotated[bool, typer.Option(help="Summarize again even if cached")] = False,
    plan: Annotated[
        bool, typer.Option(help="Only show tokens, cost and mode; no model call")
    ] = False,
) -> None:
    """Summarize one story with citations (cached per text + prompt + model)."""
    from onyourtips.services.summaries import get_summary_view, plan_summary, summarize_article

    s = get_settings() if model is None else get_settings().model_copy(update={"llm_model": model})
    with _db() as db:
        if plan:
            p = plan_summary(s, db, article_id)
            console.print(
                f"{p.title}\nmode {p.mode}"
                + (f" ({len(p.chunks)} of {p.total_chunks} chunks)" if p.chunks else "")
                + f" · ≈{p.estimated_input_tokens:,} in / {p.estimated_output_tokens:,} out"
                f" · ≈${p.estimated_cost_usd:.4f} · {p.tokenizer}"
                + (f"\n[yellow]{p.reason}" if p.reason else "")
            )
            return
        with console.status(f"Summarizing with {s.llm_model}…"):
            out = summarize_article(s, db, article_id, force=force)
        if out.cached:
            console.print("[dim](cached)[/]")
        _print_summary(get_summary_view(s, db, out.article_id))


summaries_app = typer.Typer(help="Summary attempts: cost report, list, show.")
app.add_typer(summaries_app, name="summaries")


@summaries_app.callback(invoke_without_command=True)
def summaries_report_cmd(
    ctx: typer.Context,
    days: Annotated[int, typer.Option(help="Attempts in the last N days")] = 1,
    model: Annotated[str | None, typer.Option("-m", "--model")] = None,
) -> None:
    """What summarizing cost: success, tokens, laptop time, estimate accuracy, projections."""
    if ctx.invoked_subcommand:
        return
    from onyourtips.services.summaries import summary_report

    r = summary_report(get_settings(), _db_open(), days=days, model=model)
    if not r.attempts:
        console.print(f"No summary attempts in the last {days} day(s).")
        return
    t = Table("", "", show_header=False, title=f"Summaries, last {days} day(s)")
    t.add_row("Attempts", f"{r.attempts}: [green]{r.done} done[/], [red]{r.failed} failed[/]")
    for reason, n in r.failure_reasons.items():
        t.add_row("", f"[dim]{n} × {reason}[/]")
    t.add_row("How", f"{r.single} single pass, {r.map_reduce} map-reduce ({r.partial} partial)")
    t.add_row("Model calls", f"{r.calls} (incl. map steps and retries)")
    t.add_row(
        "Tokens",
        f"{r.input_tokens:,} in / {r.output_tokens:,} out "
        f"(avg {r.avg_input_tokens:,} / {r.avg_output_tokens:,} per summary)",
    )
    t.add_row(
        "Laptop time",
        f"{r.laptop_minutes} min busy; {r.avg_seconds}s per summary"
        + (f"; {r.tokens_per_sec} tokens/s" if r.tokens_per_sec else ""),
    )
    t.add_row("Real cost", f"${r.real_cost_usd:.4f}")
    if r.estimate_error_pct is not None:
        t.add_row("Estimate accuracy", f"input tokens off by {r.estimate_error_pct}% on average")
    if r.avg_quote_match is not None:
        t.add_row(
            "Checks",
            f"quotes found {r.avg_quote_match:.0%}, coverage "
            f"{(r.avg_coverage or 0):.0%}, {r.repaired} with invalid citations removed",
        )
    console.print(t)
    if r.projections:
        pt = Table(
            "provider",
            "model",
            "these summaries",
            "per summary",
            "per month",
            title="Same tokens on paid models",
        )
        for p in r.projections:
            pt.add_row(
                p.provider,
                p.model,
                f"${p.total_usd:.3f}",
                f"${p.per_summary_usd:.5f}",
                f"${p.per_month_usd:.2f}",
            )
        console.print(pt)
    else:
        console.print("[dim]Add paid models to config/pricing.yaml to see what this would cost.")


@summaries_app.command("list")
def summaries_list_cmd(
    article_id: Annotated[str | None, typer.Argument()] = None,
    limit: Annotated[int, typer.Option()] = 20,
) -> None:
    """Recent attempts: done yes/no, reason, estimated vs real tokens."""
    from onyourtips.services.summaries import list_summaries

    t = Table("id", "article", "model", "mode", "done", "est in", "real in", "out", "s", "reason")
    for r in list_summaries(_db_open(), article_id, limit):
        t.add_row(
            str(r["id"]),
            r["article_id"][:8],
            r["model"],
            r["mode"] or "",
            "[green]yes" if r["summarization_done"] == "yes" else "[red]no",
            f"{r['estimated_input_tokens']:,}",
            f"{r['real_input_tokens'] or 0:,}",
            f"{r['real_output_tokens'] or 0:,}",
            f"{(r['latency_ms'] or 0) / 1000:.1f}",
            (r["description"] or "")[:50],
        )
    console.print(t)


@summaries_app.command("show")
def summaries_show_cmd(article_id: Annotated[str, typer.Argument()]) -> None:
    """Show a story's summary with its citations."""
    from onyourtips.services.summaries import get_summary_view

    _print_summary(get_summary_view(get_settings(), _db_open(), article_id))


# ------------------------------------------------------------------ evals

eval_app = typer.Typer(help="Evals: golden set, your labels, LLM judge, agreement, compare.")
app.add_typer(eval_app, name="eval")


@eval_app.command("golden")
def eval_golden_cmd(
    n: Annotated[int, typer.Option(help="Stories to add")] = 20,
    show: Annotated[bool, typer.Option(help="Only list the current set")] = False,
) -> None:
    """Pick a stratified golden set: short, medium and long (map-reduce) stories."""
    from onyourtips.services import evals

    s, db = get_settings(), _db_open()
    if not show:
        added = evals.pick_golden(s, db, n)
        console.print(f"Added {len(added)} stories to the golden set.")
    t = Table("article", "bucket", "title")
    for g in db.golden():
        a = db.get_article(g["article_id"])
        t.add_row(g["article_id"][:8], g["bucket"], (a.title if a else "")[:70])
    console.print(t)
    console.print("[dim]Next: oyt eval label (summarizes them first if needed)")


@eval_app.command("label")
def eval_label_cmd(
    model: Annotated[str | None, typer.Option("-m", "--model")] = None,
) -> None:
    """Judge golden claims yourself: s = supported, p = partial, u = unsupported."""
    from rich.prompt import IntPrompt, Prompt

    from onyourtips.services import evals

    s = get_settings() if model is None else get_settings().model_copy(update={"llm_model": model})
    db = _db_open()
    with console.status("Making sure every golden story has a summary…"):
        evals.golden_summaries(s, db)
    queue = evals.label_queue(s, db)
    if not queue:
        console.print("Nothing to label. Add stories with `oyt eval golden`.")
        return
    console.print(f"{len(queue)} claims to label. Ctrl-C to stop; progress is saved.\n")
    keys = {"s": "supported", "p": "partial", "u": "unsupported"}
    last_summary = None
    for i, item in enumerate(queue, 1):
        if item.summary_id != last_summary:
            if last_summary is not None:
                use = IntPrompt.ask("Whole summary: how useful, 1–5", choices=list("12345"))
                db.save_rating(last_summary, evals.HUMAN, use, None)
            console.rule(item.title[:80])
            last_summary = item.summary_id
        console.print(f"[bold]Claim {item.point_idx}:[/] {item.claim}")
        if item.quote:
            console.print(f"[dim]quote: “{item.quote}”[/]")
        for idx, text in item.passages.items():
            console.print(f"[cyan][P{idx}][/] {text}")
        k = Prompt.ask(f"[{i}/{len(queue)}] supported / partial / unsupported", choices=list("spu"))
        note = Prompt.ask("note (optional)", default="") or None
        evals.save_label(db, item, keys[k], note)
        console.print()
    if last_summary is not None:
        use = IntPrompt.ask("Whole summary: how useful, 1–5", choices=list("12345"))
        db.save_rating(last_summary, evals.HUMAN, use, None)
    console.print("[green]Done.[/] Next: oyt eval judge --judge <another model>")


@eval_app.command("judge")
def eval_judge_cmd(
    judge: Annotated[str, typer.Option("--judge", "-j", help="Judge model (not the summarizer)")],
    model: Annotated[str | None, typer.Option("-m", "--model", help="Whose summaries")] = None,
    recent: Annotated[int, typer.Option(help="Also judge N recent non-golden summaries")] = 0,
) -> None:
    """LLM-as-judge: verdict + reason per claim, against its cited passages."""
    from onyourtips.services import evals

    s = get_settings()
    model = model or s.llm_model
    db = _db_open()
    rows = evals.golden_summaries(s, db, model, create=False)
    if recent:
        seen = {r["id"] for r in rows}
        rows += [r for r in evals.judge_rows_for_window(db, model, recent) if r["id"] not in seen]
    with console.status(f"{judge} is judging {len(rows)} summaries…"):
        run = evals.judge_summaries(s, db, judge, rows)
    if run.self_judging:
        console.print("[yellow]The judge is also the summarizer: expect it to favour itself.")
    console.print(
        f"Judged {run.judged} claims ({run.skipped_existing} already judged, {run.errors} errors)."
    )
    f, n = evals.faithfulness(db, judge, [r["id"] for r in rows])
    if f is not None:
        console.print(f"Faithfulness according to {judge}: [bold]{f:.0%}[/] of {n} claims")
    console.print("[dim]Next: oyt eval agreement --judge " + judge)


@eval_app.command("agreement")
def eval_agreement_cmd(
    judge: Annotated[str, typer.Option("--judge", "-j")],
) -> None:
    """How well the LLM judge agrees with your labels (accuracy, Cohen's kappa, confusion)."""
    from onyourtips.services import evals

    a = evals.agreement(_db_open(), judge)
    if not a.n:
        console.print("No claims judged by both you and this judge yet.")
        return
    console.print(
        f"{judge} vs you on {a.n} claims: accuracy {a.accuracy:.0%}, "
        f"Cohen's kappa [bold]{a.kappa}[/]"
    )
    t = Table("you \\ judge", *evals.VERDICTS)
    for r in evals.VERDICTS:
        t.add_row(r, *(str(a.confusion[r][j]) for j in evals.VERDICTS))
    console.print(t)
    if a.unsupported_recall is not None:
        console.print(f"It caught {a.unsupported_recall:.0%} of the claims you marked unsupported.")
    verdict = (
        "trust it for screening"
        if (a.kappa or 0) >= 0.6
        else "use with care"
        if (a.kappa or 0) >= 0.4
        else "don't rely on it yet"
    )
    console.print(f"[dim]kappa ≥ 0.6 substantial · 0.4–0.6 moderate · < 0.4 weak → {verdict}")


@eval_app.command("compare")
def eval_compare_cmd(
    models: Annotated[list[str], typer.Option("-m", "--model", help="Repeat for each model")],
    judge: Annotated[str | None, typer.Option("--judge", "-j")] = None,
) -> None:
    """Summarize the golden set with each model and score them; writes a markdown report."""
    from onyourtips.services import evals

    s, db = get_settings(), _db_open()
    scores = []
    for m in models:
        with console.status(f"{m}: summarizing and scoring the golden set…"):
            scores.append(evals.score_model(s.model_copy(update={"llm_model": m}), db, m, judge))
    agr = evals.agreement(db, judge) if judge else None
    path = evals.write_compare_report(scores, judge, agr, s.db_path.parent / "evals")
    t = Table("model", "summarized", "faithful (judge)", "faithful (you)", "quotes", "avg s")
    for sc in scores:
        t.add_row(
            sc.model,
            f"{sc.summarized}/{sc.stories}",
            f"{sc.faithfulness_judge:.0%}" if sc.faithfulness_judge is not None else "–",
            f"{sc.faithfulness_human:.0%}" if sc.faithfulness_human is not None else "–",
            f"{sc.quote_match:.0%}" if sc.quote_match is not None else "–",
            str(sc.avg_seconds or "–"),
        )
    console.print(t)
    console.print(f"Report: {path}")


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
