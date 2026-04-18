# Scribe -- Academic Writer

Chunked long-form writing tool powered by Claude. Turns outlines, style guides, and reference documents into polished academic prose.

## How it works

Scribe breaks long-form writing into a 5-stage pipeline:

1. **Document Review** (Opus) -- Analyses the thesis structure: problem statement, research gap, key question, themes, and how each section serves the argument
2. **Plan** (Opus) -- Reads everything and produces a chunking plan with depth levels, source assignments, and gap analysis
3. **Review** -- Interactive TUI or web UI to approve/edit the plan before writing
4. **Write** (Sonnet, parallel) -- Executes chunks concurrently, each with its thesis role and full style context
5. **Stitch** (Opus) -- Smooths transitions, enforces consistency, and assembles the final document

Every stage receives the thesis analysis from Stage 1, ensuring each section maintains the overall argument and answers the key question.

## Install

```bash
pip install -e .
```

Requires:
- Python 3.11+
- Claude Code CLI (via Max subscription or Claude desktop app)

## Quick start

### CLI

```bash
# Scaffold a new project
scribe init ~/my_paper

# Drop in your outline, style guide, and references
# Then run the full pipeline
scribe run ~/my_paper

# Or run stages individually
scribe plan ~/my_paper
scribe review ~/my_paper
scribe write ~/my_paper
scribe stitch ~/my_paper

# Launch the web UI
scribe web
```

### Web UI

```bash
scribe web
# Open http://127.0.0.1:5000
```

Upload your outline (.md, .docx, .doc), style guide, and reference files (PDF, DOCX, XLSX, MD, TXT, CSV). Click "Run Pipeline" and watch live progress.

## Project structure

```
my_project/
  outline.md (or .docx)    -- document structure with headings and bullets
  style.md (or .docx)      -- voice, rules, citation format
  refs/                     -- PDF, DOCX, XLSX, MD, TXT source documents
  config.yml                -- optional settings
  final.md                  -- generated output
  .scribe/
    document_review.json    -- thesis analysis
    plan.json               -- chunking plan
    drafts/                 -- per-chunk drafts
    state.json              -- resume state
    runs/                   -- per-run reports and logs
```

## Outline tags

Control generation with inline tags:

```markdown
## Methodology [depth:rigorous]
- Data collection [ref:refs/survey_data.xlsx]
- Analysis framework [ref:refs/framework.md#pages-5-10]
- Statistical methods [web]
- Excluded approaches [skip]
- Summary [words:300]
```

## Features

- **Thesis-aware writing**: Every chunk knows the problem statement, research gap, key question, and its role in the argument
- **Academic writing rules**: Built-in rules from Williams, Pinker, Sword, Schimel, Strunk & White, and Zinsser
- **Parallel execution**: Writes multiple chunks concurrently
- **Diff-aware re-runs**: Only regenerates chunks affected by input changes
- **Resume**: Pick up interrupted runs from the last completed chunk
- **Multiple input formats**: Outlines and style guides as .md, .docx, or .doc; refs as PDF, DOCX, XLSX, MD, TXT, CSV
- **Web UI**: Browser-based project setup and pipeline monitoring
- **Git integration**: Auto-commits at each stage with run tags

## Configuration

Optional `config.yml`:

```yaml
project_name: "My Paper"
default_depth: standard          # skim | standard | deep | rigorous
planner_model: opus
executor_model: sonnet
stitcher_model: opus
parallelism: 3
suggest_visuals: true
citation_style: harvard
git:
  auto_commit: true
```

## CLI commands

| Command | Description |
|---------|-------------|
| `scribe init <dir>` | Scaffold a new project |
| `scribe plan <dir>` | Run the planner only |
| `scribe review <dir>` | Review/edit an existing plan |
| `scribe write <dir>` | Execute the plan (write chunks) |
| `scribe stitch <dir>` | Re-stitch drafts into final.md |
| `scribe run <dir>` | Full pipeline (review + plan + write + stitch) |
| `scribe resume <dir>` | Resume an interrupted run |
| `scribe status <dir>` | Show project state |
| `scribe visuals <dir>` | List visual suggestion placeholders |
| `scribe history <dir>` | List past runs |
| `scribe web` | Launch the web UI |

## Stack

- Python 3.11+, `claude-code-sdk` (subprocess, via Max subscription)
- `typer` (CLI), `rich` (TUI), `flask` (web UI)
- `pydantic` (models), `pyyaml` (config)
- `pymupdf` (PDF), `python-docx` (DOCX), `openpyxl` (XLSX)
- `gitpython` (version history), `tiktoken` (token estimation)
