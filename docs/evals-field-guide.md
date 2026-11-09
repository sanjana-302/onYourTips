# Evals field guide: learning by evaluating our own summaries

An **eval** is a repeatable test of an AI system's output quality. Ordinary tests check that code
does what it should; evals measure how *good* output is when there's no single right answer.
This guide explains each eval term with how it shows up in OnYourTips, the command that runs it,
and how to turn the numbers into a decision.

## The loop at a glance

```
          ┌──────────── every summary, free ────────────┐
story ─► summarize ─► deterministic checks ─► summaries table (+ checks_json)
                                   │
         golden set (20 stories) ──┼─► your labels ─────────┐
                                   ├─► LLM judge verdicts ──┼─► agreement (kappa)
                                   │                        │      │
         several models ───────────┴─► comparison report ◄──┘      ▼
                                              │             can the judge be trusted?
                                              ▼
                                         a decision
```

```bash
uv run oyt eval golden                     # 1. pick ~20 stories (short / medium / long)
uv run oyt eval label                      # 2. label every claim yourself (s / p / u) + 1–5
uv run oyt eval judge -j qwen3.5:4b        # 3. a different model judges the same claims
uv run oyt eval agreement -j qwen3.5:4b    # 4. how often does it agree with you?
uv run oyt eval compare -m llama3.2:3b -m qwen3.5:4b -m granite4.2:3b -j qwen3.5:4b
uv run oyt summaries                       # cost: tokens, laptop time, projected $
```

---

## 1. The basics

**Output contract (schema).** The shape the model must return: `tldr`, `key_points[]` (each
with `citations` and a `quote`), and `why_it_matters`. It's enforced twice: the gateway asks
Ollama for JSON matching the schema (**structured output**), and pydantic validates the result.
A failure here is the cheapest kind to catch. *Here:* `enrichment/summary_schema.py`.

**Prompt version.** Every prompt has a version string (`summary-v1`, `judge-v1`). Summaries,
token counts and judgments are stored per version, so changing a prompt never overwrites old
results. You can always compare v1 with v2. *Rule:* change the text, bump the version.

**Grounding / groundedness.** Output is grounded when every claim comes from the provided
source, not from the model's memory. Citations make grounding checkable.

**Hallucination.** A claim not supported by the source. In summaries it's usually subtle: a
number changed, a qualifier dropped ("may" → "will"), or two facts merged into one false one.

**Faithfulness.** The share of claims that are supported by the source: the opposite of
hallucination rate. It's the main quality metric for summaries. *Here:* supported = 1,
partial = 0.5, unsupported = 0, averaged over claims.

**Usefulness.** A faithful summary can still be useless (true but trivial). That's why
labelling also asks for a 1–5 score per summary. Faithfulness is the **floor**, usefulness the
**goal**.

## 2. Deterministic checks (code-based graders, assertions)

Checks written in code that run on every output, for free, with identical results every time.
They can't tell whether a claim is *true*, but they catch whole classes of failure.

| Check | Kind | What it catches |
|---|---|---|
| Schema valid | Gate | Broken JSON, missing fields |
| Citation validity | Repair + gate | Citing P14 when the story has 9 passages: the number is removed; a point with nothing left is dropped |
| Quote match (exact / fuzzy) | Metric | The quote isn't in the cited passage: a sign the claim may not be either |
| Quote elsewhere | Metric | The quote exists, but in a passage that isn't cited: wrong citation |
| Coverage | Metric | Share of passages cited: low coverage on a long story means it only read the start |
| Duplicates | Metric | Two key points saying the same thing |

**Gate vs metric.** A gate blocks the output (`summarization_done = no`); a metric is only
recorded. Gate only what makes output unusable; measure everything else. Too many gates and a
small model fails constantly; too few and broken output reaches the screen.

**Repair vs reject.** When output is *fixable* (one invalid citation among valid ones), fixing
it is often better than throwing it away. Always count the repairs (`invalid_citations_removed`),
because a rising repair rate is an early warning.

**Fuzzy matching.** Small models paraphrase quotes slightly. "Fuzzy" means at least 80% of the
quote's words appear in the cited passages. The exact/fuzzy split shows how literal each model is.

## 3. Golden set and labels (ground truth)

**Golden set.** A small, fixed set of inputs with trusted judgments, used every time you change
a prompt or model. It's fixed so results are comparable over time. *Here:* `eval_golden`, about 20
stories.

**Stratified sampling.** Pick examples from each kind of input on purpose, instead of at random:
40% short, 40% medium, 20% long (map-reduce), mixing news, projects and papers. Random picks
would be mostly short news and would never exercise map-reduce.

**Ground truth / reference labels.** The answers you trust. For summaries, that's your verdict
on each claim: **supported**, **partial** or **unsupported**, checked against the cited passages.
*Here:* `claim_judgments` with `judge = 'human'`.

**Claim-level vs summary-level.** Judge each claim separately, not "is this summary good?".
Claims are smaller, easier to judge consistently, and show *where* it fails. The 1–5 usefulness
score covers the whole.

**Labelling guidelines (rubric).** Decide the rules once and write them down, or your own labels
will drift. A suggested rubric:
- **supported:** every part of the claim is in the cited passages, including numbers and
  qualifiers.
- **partial:** the gist is there, but something is missing, overstated or merged.
- **unsupported:** not in the cited passages. Even if it's true elsewhere in the article, the
  citation is wrong, so it counts.

**Inter-annotator agreement.** When two people label the same items, how often do they agree?
This sets the ceiling: no judge can agree with you more than another careful person would. With
only you labelling, re-label 10 claims a week later to see how consistent you are (**intra-rater
agreement**).

## 4. LLM-as-judge

**LLM-as-judge.** A model grades another model's output. It's cheap enough to run on every
summary, which humans can't do. *Here:* `oyt eval judge -j <model>` gives each claim plus its
cited passages to the judge with prompt `judge-v1`, and gets back `{reason, verdict}`.

**Reason before verdict.** The judge writes its reason first, then the verdict. Asking for the
explanation first tends to give more careful verdicts, a light form of chain-of-thought.

**Reference-based vs reference-free.** Our judge is *reference-based*: it compares the claim with
the source text. A reference-free judge ("is this a good summary?") is easier but much less
reliable.

**Known judge biases:**
- **Self-preference:** a model rates its own output higher. Use a different model as judge; the
  CLI warns when the judge is the summarizer.
- **Verbosity bias:** longer answers look better to judges.
- **Position bias:** when comparing A vs B, judges favour whichever comes first. This matters
  for pairwise comparisons, which we don't do yet.
- **Leniency:** small judges say "supported" too easily. The confusion matrix shows it.

**Calibrating the judge.** Never assume a judge is right. Measure it against your labels first.

## 5. Agreement metrics

**Accuracy.** The share of claims where the judge and you gave the same verdict. It's misleading
on its own: if 90% of claims are supported, a judge that always says "supported" scores 90%.

**Cohen's kappa (κ).** Agreement corrected for chance:
`κ = (observed agreement − expected by chance) / (1 − expected by chance)`.
1 is perfect, 0 is no better than guessing from the label frequencies, and below 0 is worse
than chance. The always-"supported" judge above gets κ = 0.

| κ | Reading | What to do |
|---|---|---|
| ≥ 0.8 | Almost perfect | Use it unattended; spot-check monthly |
| 0.6–0.8 | Substantial | Use it for screening: trust "supported", review its "unsupported" |
| 0.4–0.6 | Moderate | Use it for trends only (is v2 better than v1?), not single verdicts |
| < 0.4 | Weak | Don't rely on it: fix the judge prompt or try a bigger judge |

**Confusion matrix.** A table of your verdicts (rows) against the judge's (columns). The
diagonal is agreement. The off-diagonal cells say *how* it's wrong. Many claims in row
unsupported, column supported means the judge misses hallucinations, the worst failure for us.

**Precision and recall for "unsupported".** The class we care about most.
- **Recall:** of the claims you marked unsupported, the share the judge caught. Low recall means
  hallucinations slip through.
- **Precision:** of the judge's "unsupported" calls, the share that really are. Low precision
  means false alarms.

## 6. Comparing models and prompts

**Offline eval.** Run on a fixed dataset before shipping (the golden set). **Online eval**
measures real use after shipping: here, your opens, saves, ratings, and taps on citation chips.
Both matter; offline is where you decide, online is where you find out whether you were right.

**Regression eval.** Re-run the golden set after every prompt or model change, to make sure
nothing got worse. Ideally this runs in CI on recorded outputs, so it's deterministic and free.

**Ablation.** Change one thing at a time (the model *or* the prompt), so you know what caused a
difference.

**Leakage / overfitting to the eval.** If you tweak the prompt until the golden set looks great,
the golden set stops measuring anything. The fix is a **dev set** to tune on and a separate
**test set** to report on. *Not built yet; next step.*

**Sample size and noise.** 20 stories × 4 claims is 80 claims. At around 80% faithfulness, the
uncertainty is roughly ±9 points (95% interval). Treat gaps under about 10 points as ties,
and use more stories to separate close models.

**Determinism.** Temperature 0.2 for summaries and 0 for the judge make reruns similar, but
not identical. Don't over-read a 2-point change.

## 7. Cost as an eval dimension

Quality without cost is half the picture. `oyt summaries` reports, per day:
- success rate and failure reasons
- tokens in / out, including map calls and retries
- laptop minutes (the real local cost) and seconds per summary
- estimate accuracy: counted vs real input tokens
- projected cost on paid models listed in `config/pricing.yaml`

The useful compound metric is **cost per useful summary**: total spend ÷ summaries you opened
or rated ≥ 4.

## 8. Making the judgement

A process for deciding, using the comparison report (`data/evals/summary-compare-*.md`):

1. **Gate on the floor.** Drop any model with a success rate under 90%, or quotes found under
   60%. It isn't reliable enough to run unattended.
2. **Rank on faithfulness.** Use your labels where you have them, else the judge, but only if
   κ ≥ 0.6. A wrong summary is worse than none.
3. **Break ties (< 10 points) on usefulness**, then on time per story. At 500 stories a night,
   15 s vs 5 s is 2 hours vs 40 minutes.
4. **Check cost and the deadline.** Projected laptop time must fit before 08:30. If a paid model
   is in the running, compare its monthly projection with the quality gain.
5. **Write it down.** Record the decision, the numbers and the date in the design log book, so
   the next change has a baseline.

## Where everything lives

| Thing | Table / file | Command |
|---|---|---|
| Summaries + checks | `summaries` (`checks_json`) | `oyt summarize <id>`, `oyt summaries list` |
| Golden set | `eval_golden` | `oyt eval golden` |
| Claim verdicts (you and judges) | `claim_judgments` | `oyt eval label`, `oyt eval judge` |
| Usefulness 1–5 | `summary_ratings` | `oyt eval label` |
| Judge calls, map/reduce calls | `llm_calls` (purposes `judge`, `summarize*`) | `oyt costs --by purpose` |
| Comparison reports | `backend/data/evals/*.md` | `oyt eval compare` |
