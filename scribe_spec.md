# Scribe: Chunked Long-Form Writing Tool

**Build target:** A Python CLI tool that uses the Claude Agent SDK (via Max subscription) to turn a bulleted outline, a style guide, and a folder of reference documents into polished long-form prose, section by section, with an agentic planner, human review, live status TUI, and git-backed version history.

**Why this exists:** LLM output quality degrades over long single-call generations. Chunked generation with a shared style guide, per-chunk scoping, and a dedicated planner call produces consistently higher quality prose than asking for the whole document at once.

---

## 1. High-level architecture

```
inputs/
  outline.md        bulleted structure, optional per-bullet tags
  style.md          voice, rules, citation format, hard prohibitions
  refs/             PDFs, DOCX, XLSX, MD, TXT used as sources
  config.yml        (optional) run settings

          │
          ▼
   ┌────────────┐
   │  PLANNER   │   Opus, one call, reads everything
   └─────┬──────┘
         ▼
   plan.json + plan_review.md
         │
         ▼
   ┌────────────┐
   │  REVIEW    │   TUI: approve / edit / replan / tweak
   └─────┬──────┘
         ▼
   ┌────────────┐
   │  EXECUTOR  │   Sonnet, N chunks, live verbose TUI
   └─────┬──────┘
         ▼
   drafts/NN_chunkid.md
         │
         ▼
   ┌────────────┐
   │  STITCHER  │   Opus, one call, smooths + enforces style
   └─────┬──────┘
         ▼
   final.md + run_report.md
         │
         ▼
   git commit
```

## 2. Core principles

- **One style guide, injected every call.** Cached for cost efficiency. Never drifts.
- **Planner decides chunking.** Manual tags are overrides, not the default.
- **Verbose by default.** Every tool call, every file read, every word count tick is visible.
- **Resumable.** A failed or interrupted run can be picked up per-chunk.
- **Diff-aware.** Re-running after an outline edit only regenerates changed chunks.
- **Max subscription only.** No API key handling. Auth via local Claude Code CLI session.

## 3. Stack

- Python 3.11+
- `claude-agent-sdk` (authenticates through local Claude Code CLI)
- `rich` for the TUI (tables, spinners, progress bars, live layout)
- `typer` for the CLI
- `pyyaml` for config
- `gitpython` for version history
- `tiktoken` for token estimation (rough, used for cost preview only)

## 4. CLI surface

```
scribe plan     <project_dir>    # run planner only, write plan.json
scribe review   <project_dir>    # open TUI to review/edit existing plan
scribe write    <project_dir>    # execute plan (assumes plan approved)
scribe run      <project_dir>    # plan + review + write + stitch (default flow)
scribe resume   <project_dir>    # resume an interrupted run
scribe stitch   <project_dir>    # re-stitch existing drafts
scribe status   <project_dir>    # show current state, last run, cost estimate
scribe init     <project_dir>    # scaffold a new project folder
```

Flags:
- `--dry-run` plan only, no writing
- `--no-review` skip TUI review step
- `--force` regenerate all chunks even if unchanged
- `--chunk <id>` operate on a single chunk
- `--verbose / --quiet` override default verbose TUI

## 5. Project folder layout

```
my_project/
├── outline.md
├── style.md
├── refs/
│   ├── ihs_data.xlsx
│   └── project_green.pdf
├── config.yml                  (optional)
├── .scribe/
│   ├── plan.json               current approved plan
│   ├── plan_history/           every plan version
│   ├── drafts/
│   │   ├── c1_intro.md
│   │   ├── c2_diesel.md
│   │   └── ...
│   ├── cache/                  cached file reads for diff detection
│   ├── runs/
│   │   └── 2026-04-17_1423/    per-run logs, token counts, cost
│   └── state.json              resume state
└── final.md
```

`.scribe/` is git-tracked. `refs/` is gitignored by default (may be large/confidential).

## 6. Input formats

### 6.1 outline.md

Plain markdown with headings and bullets. Optional inline tags override the planner:

```markdown
# Chapter 3: Energy demand modelling

## Introduction
- Framing of the problem
- Why existing models fail

## ADIEM Layer 4 methodology [depth:rigorous]
- Tile-level energy demand estimation [ref:refs/adiem_notes.md]
- Monte Carlo uncertainty treatment
- Calibration against Ookla data [ref:refs/ookla_sample.xlsx]

## Results [depth:deep]
- Country-level outputs
- Sensitivity analysis
```

Tag syntax:
- `[depth:skim|standard|deep|rigorous]` force depth on section or bullet
- `[ref:path/to/file]` force a source
- `[ref:path#page-range]` force specific pages
- `[web]` allow web search
- `[skip]` exclude from output
- `[words:N]` force word count

Untagged content is planned agentically.

### 6.2 style.md

User-provided. Tool passes it verbatim into every call as a cached block. Example structure:

```markdown
# Style guide

## Voice
- British English
- Concise, direct, active voice
- Dark humour acceptable in asides, never in substantive claims

## Hard rules
- No em dashes under any circumstances
- No "it's worth noting", "it's important to", "delve", "furthermore"
- No sycophantic openers

## Citations
- Harvard style, author-date
- Inline: (Jones, 2025) or (Jones, 2025, p.14) for direct attribution
- Full bibliography at end, alphabetical

## Structure
- Paragraphs should be 3-6 sentences
- One idea per paragraph
- Lead with the claim, then evidence
```

Tool does not interpret or modify this. It is law for the writers.

### 6.3 config.yml (optional)

```yaml
project_name: "ADIEM Chapter 3"
default_depth: standard
planner_model: opus
executor_model: sonnet
stitcher_model: opus
parallelism: 3              # concurrent chunk writes
citation_style: harvard     # overridden by style.md if specified there
suggest_visuals: true
git:
  auto_commit: true
  commit_template: "scribe: {stage} for {project_name}"
cost_tracking:
  estimate_tokens: true
  log_to: .scribe/runs/
```

All fields optional. Sensible defaults if omitted.

## 7. Stage specifications

### 7.1 Planner

**Model:** Opus 4.7 (via SDK).

**Input:** outline.md, style.md, full text of every file in refs/.

**Prompt (template):**
> You are planning the writing of a long-form document. Read the outline, the style guide, and the reference materials provided. Produce a chunking plan as JSON.
>
> For each chunk decide:
> - which outline bullets/sections it covers
> - depth level: skim, standard, deep, rigorous
> - target word count
> - which reference documents to use and which specific sections/pages of each
> - whether web search is needed
> - whether visuals should be suggested and roughly where
> - a one-line rationale for the chunking decision
>
> Also produce a document-level review noting:
> - bullets with no supporting source (gaps)
> - contradictions between sources
> - structural suggestions (merge, split, reorder)
> - overall estimated word count and time
>
> Respect any manual tags in the outline as hard overrides.
>
> Output valid JSON matching the schema at the end of this prompt.

**Output:**

`plan.json`:
```json
{
  "project": "ADIEM Chapter 3",
  "created": "2026-04-17T14:23:00Z",
  "estimated_words": 8400,
  "estimated_chunks": 12,
  "chunks": [
    {
      "id": "c1",
      "title": "Introduction and framing",
      "covers": ["Introduction"],
      "depth": "standard",
      "words": 600,
      "sources": [],
      "web_search": false,
      "visuals": [],
      "rationale": "Opening. No refs needed. Grouped for voice consistency."
    },
    {
      "id": "c2",
      "title": "ADIEM Layer 4 methodology",
      "covers": ["ADIEM Layer 4 methodology"],
      "depth": "rigorous",
      "words": 1800,
      "sources": [
        {"file": "refs/adiem_notes.md", "focus": "whole document"},
        {"file": "refs/ookla_sample.xlsx", "focus": "sheets: tile_agg, validation"}
      ],
      "web_search": false,
      "visuals": [
        {"suggested_location": "after methodology intro", "type": "flowchart", "purpose": "show layer dependencies"}
      ],
      "rationale": "Technical core. Two tightly-aligned sources. Rigorous depth justified."
    }
  ],
  "gaps": [
    {"bullet": "Why existing models fail", "issue": "no cited source", "suggestion": "add ref or soften to 'observation'"}
  ],
  "contradictions": [],
  "restructure_suggestions": []
}
```

`plan_review.md`: human-readable rendering of the same data for the TUI and for git history.

### 7.2 Review TUI

Built with `rich`. Renders `plan.json` as an interactive table.

```
┌────────────────────────────────────────────────────────────────┐
│  PLAN REVIEW  —  ADIEM Chapter 3                               │
│  12 chunks, ~8,400 words, ~14 min, ~45k cached tokens          │
├────────────────────────────────────────────────────────────────┤
│  ID   Title                         Depth      Words  Sources │
├────────────────────────────────────────────────────────────────┤
│  c1   Introduction and framing      standard    600   —       │
│  c2   ADIEM Layer 4 methodology     rigorous   1800   2       │
│  c3   Monte Carlo treatment         deep       1100   1       │
│  ...                                                           │
├────────────────────────────────────────────────────────────────┤
│  Gaps (1):                                                     │
│    • "Why existing models fail" — no cited source              │
│  Contradictions: none                                          │
│  Suggestions: none                                             │
├────────────────────────────────────────────────────────────────┤
│  [A]pprove  [E]dit chunk  [R]eplan  [S]how rationale           │
│  [M]erge    [P]split      [D]elete  [Q]uit                     │
└────────────────────────────────────────────────────────────────┘
```

Edit actions mutate `plan.json` in place, version previous plan to `plan_history/`.

### 7.3 Executor

**Model:** Sonnet 4.6 by default.

**Parallelism:** configurable, default 3 concurrent chunks.

**Per-chunk prompt (template):**
> Write chunk `{id}`: "{title}".
>
> Follow the style guide exactly: {style.md, cached}.
>
> Bullets to expand:
> {outline bullets for this chunk}
>
> Source material:
> {for each source in chunk.sources: file path + focus description}
>
> Read the relevant sources using the Read tool. For large files, use Grep first to locate relevant passages.
>
> Target length: {words} words. Depth: {depth}.
>
> Citation style: {from style.md or config}.
>
> {if web_search} You may use web search for recent facts not covered in the references.
>
> {if visuals} Where a chart, diagram, or image would materially help the reader, insert a markdown image placeholder with a caption describing what should go there, e.g. `![SUGGEST: flowchart of ADIEM layer dependencies](suggest)`.
>
> Write the prose now. Do not include the chunk title heading in your output; the stitcher adds headings.

**Streaming:** every SDK message is parsed and displayed in the TUI.

**Output:** `drafts/NN_chunkid.md` per chunk.

### 7.4 Live TUI during execution

Verbose by default. `rich.Live` layout:

```
┌──────────────────────────────────────────────────────────────────┐
│  SCRIBE  ·  ADIEM Chapter 3                     Elapsed: 6m 42s  │
├──────────────────────────────────────────────────────────────────┤
│  Overall: ████████████████░░░░░░░░░░  58%   7 of 12 chunks done  │
│  Tokens: ~32,400 in  ·  ~6,800 out  ·  est cost: $0.18 (cached) │
├──────────────────────────────────────────────────────────────────┤
│  ✓ c1   Introduction and framing         612w    42s            │
│  ✓ c2   ADIEM Layer 4 methodology       1834w   2m 18s          │
│  ⠋ c3   Monte Carlo treatment            ongoing                 │
│     └─ reading refs/adiem_notes.md (14,200 tokens)               │
│     └─ grep "monte carlo" in refs/                               │
│     └─ writing... 487 words so far                               │
│  ⠋ c4   Calibration vs Ookla             ongoing                 │
│     └─ reading refs/ookla_sample.xlsx, sheet tile_agg            │
│  ○ c5   Results: country-level outputs                           │
│  ○ c6   Sensitivity analysis                                     │
│  ...                                                             │
└──────────────────────────────────────────────────────────────────┘
```

States: `○` pending, `⠋` running (animated spinner), `✓` done, `✗` failed.

Tool call substream shows nested under each running chunk. When a chunk finishes, its substream collapses and a final word count + duration shows.

### 7.5 Stitcher

**Model:** Opus 4.7.

**Input:** all draft files in order, style.md, plan.json.

**Prompt:**
> You are stitching a long-form document from sectional drafts. Read each draft in order. Produce the final document with:
> - appropriate heading hierarchy derived from the outline
> - smoothed transitions between chunks (remove redundant framings, add connective tissue where needed)
> - consistent voice and terminology across the whole
> - no repetition of points made earlier
> - preserved citations exactly as written (do not reword them)
> - preserved visual suggestion placeholders exactly (lines containing `![SUGGEST:...`)
> - consolidated bibliography at the end if cited sources are used
>
> Follow the style guide: {style.md}.
>
> Do not change substantive content, only smooth joins and enforce consistency.

**Output:** `final.md`.

### 7.6 Run report

After stitching, generate `.scribe/runs/<timestamp>/run_report.md`:

```markdown
# Scribe run report

**Project:** ADIEM Chapter 3
**Date:** 2026-04-17 14:23
**Duration:** 14m 22s
**Output:** 8,247 words across 12 chunks

## Token usage (estimated)
- Planner:  12,400 in  /  2,100 out
- Executor: 184,000 in (cached: 168,000) / 9,800 out
- Stitcher: 42,000 in  /  8,400 out
- **Total:** 238,400 in / 20,300 out
- **Max-equivalent cost estimate:** $1.92 (if API) / included in Max

## Chunks
- c1 Introduction and framing — 612 words, 42s
- c2 ADIEM Layer 4 methodology — 1834 words, 2m 18s
- ...

## Git
- Commit: abc1234 "scribe: full run for ADIEM Chapter 3"
- Files changed: 14
```

## 7. Diff-aware re-runs

On `scribe run`, before invoking the planner:

1. Hash each input file (outline.md, style.md, each ref).
2. Compare to `.scribe/cache/hashes.json`.
3. If only certain refs changed, flag affected chunks via planner.
4. If outline changed, re-plan affected chunks only.
5. Unchanged chunks keep their existing drafts.
6. Stitcher always runs fresh.

`--force` skips this and regenerates everything.

## 8. Resume mode

`state.json` is updated after every SDK call. On `scribe resume`:

1. Read `state.json`.
2. Identify last completed chunk.
3. Restart from the next chunk.
4. Re-stitch at the end.

## 9. Cost and token tracking

Even on Max, log estimated tokens per call using `tiktoken` on the outgoing prompt and the received output. Log per-run to `.scribe/runs/<timestamp>/tokens.json`. Used to:
- show live cost estimate during runs
- warn if a chunk is unexpectedly large
- produce the run report

Display both "Max-equivalent API cost" (for awareness) and a running tally of Max rate-limit consumption if detectable from SDK responses.

## 10. Git integration

On `scribe init`: initialise repo, add `.gitignore` for `refs/` and `.scribe/cache/`.

After each successful stage, commit:
- after plan: `scribe: plan for {project}`
- after write: `scribe: draft for {project}`
- after stitch: `scribe: final for {project}`

Each run tagged: `run-YYYYMMDD-HHMM`.

`scribe history` lists recent runs. `scribe diff <run1> <run2>` shows prose diff between runs.

## 11. Visual suggestions

When `suggest_visuals: true` and the chunk has `visuals` in its plan entry, the executor inserts lines like:

```markdown
![SUGGEST: bar chart of diesel consumption by region, 2020-2025](suggest)
```

These survive through stitching. A separate command `scribe visuals <project_dir>` lists all suggestions in a table so you can generate or source images and drop them into `refs/images/`, then replace the suggest lines.

Auto-generation of visuals is out of scope for v1.

## 12. Multi-document projects

A project can contain sub-projects:

```
my_thesis/
├── scribe.yml              top-level config, shared style
├── style.md                inherited by all chapters
├── shared_refs/
├── chapter_1/
│   ├── outline.md
│   └── refs/               chapter-specific refs
├── chapter_2/
│   └── ...
└── final/                  stitched full thesis
```

`scribe run my_thesis/` runs each chapter, then a final cross-chapter stitch that:
- enforces consistent terminology across chapters
- de-duplicates definitions
- produces a unified bibliography
- optional cross-references if requested in outline

## 13. Error handling

- SDK call failure: retry once, then mark chunk failed, continue with others. Show in TUI.
- Malformed planner JSON: re-prompt up to 2 times with the error. Then fall back to hand-tag mode and ask the user.
- Rate limit hit: pause, surface clearly in TUI with countdown, resume automatically.
- File read failure: surface the specific file and error, skip that source, continue chunk with a warning in the draft.

All errors logged to `.scribe/runs/<timestamp>/errors.log`.

## 14. Default style behaviour

If no style.md is provided, scribe ships with a `default_style.md` that enforces:
- British English
- No em dashes
- Concise, active voice
- Harvard citations
- No AI tells ("delve", "furthermore", "it's worth noting", etc.)

User is prompted to confirm or supply their own on first run.

## 15. Testing checklist

Before declaring v1 done:

- [ ] Plan a single-chapter project with 5 refs, review plan, approve, write, stitch, verify final.md reads well
- [ ] Edit outline.md, rerun, confirm only affected chunks regenerate
- [ ] Force-fail a chunk mid-run, confirm resume picks up cleanly
- [ ] Manual tag override: force one chunk to rigorous, confirm planner respects it
- [ ] Multi-document: two-chapter project stitches with consistent terminology
- [ ] Verbose TUI renders correctly on 80-column terminal and wide
- [ ] Git commits land at the right moments with right messages
- [ ] Token estimates are within 15% of actual (where actual is retrievable)
- [ ] Visual suggestions flow through stitcher intact
- [ ] `scribe init` produces a working scaffold with dummy style and outline

## 16. Out of scope for v1

- Web UI (CLI only)
- Auto-generated visuals
- Voice/dictation input
- Collaborative editing
- Cloud sync
- Non-markdown outputs (pandoc pipeline deferred to v2)

## 17. Build order suggestion

1. Scaffold CLI with `typer`, `init` command, project folder creation.
2. Implement style.md loading and caching.
3. Implement planner stage, output plain JSON, print it (no TUI yet).
4. Build basic executor for one chunk, verify SDK streaming parses correctly.
5. Add parallel executor with `asyncio.gather`.
6. Build verbose TUI with `rich.Live`.
7. Build review TUI.
8. Build stitcher.
9. Add diff detection and resume.
10. Add git integration.
11. Add token/cost tracking.
12. Multi-document support.
13. Polish, testing checklist, write README.

## 18. Acceptance criteria

Scribe is done when David can:

1. Run `scribe init ~/projects/my_chapter`, drop in outline.md, style.md, and refs/.
2. Run `scribe run ~/projects/my_chapter`.
3. See the planner's proposed chunking in a readable TUI, tweak one chunk, approve.
4. Watch verbose live progress of all chunks being written in parallel.
5. Read `final.md` and find it indistinguishable in quality from his own careful writing, in voice, free of em dashes, correctly cited, with sensible visual suggestions marked for later insertion.
6. Re-run after editing the outline and see only changed parts regenerate.
7. Browse git history of every run.
