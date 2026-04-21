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

Three ways in, from most user-friendly to most developer-friendly.

### Option A -- Windows executable (non-technical users)

1. Download the latest `Scribe-Windows.zip` from the [Releases](https://github.com/DbanJones/scribe-academic-writer/releases) page.
2. Unzip it anywhere (e.g. `Documents/Scribe/`).
3. Double-click `Scribe.exe`. A console window shows the local URL and
   your browser opens automatically.

The executable bundles Python and all of Scribe's code, but you still need
the Claude Code CLI separately -- Scribe's launcher detects if it's missing
and prints install instructions.

### Option B -- `pipx install` (technical users, cross-platform)

```bash
pipx install git+https://github.com/DbanJones/scribe-academic-writer.git

scribe-desktop   # launches the web UI in your browser
scribe --help    # CLI equivalent
```

Requires Python 3.11+. `pipx` isolates Scribe from your system Python.

### Option C -- develop from source

```bash
git clone https://github.com/DbanJones/scribe-academic-writer.git
cd scribe-academic-writer
pip install -e .
scribe web
```

### Requirement shared by all three routes

- **Claude Code CLI** (via Max subscription or per-token API access).
  See the [Connect to Claude guide](#connect-to-claude) below.

## Connect to Claude

Scribe uses your **local** Claude Code CLI for every API call. Your documents
never leave your machine; usage is billed to your own Anthropic account or
Max subscription.

1. **Install the CLI.**
   - macOS: `brew install claude`
   - Windows: install the Claude desktop app from https://claude.ai/download
     (bundles the CLI), or `npm install -g @anthropic-ai/claude-code`
   - Linux: `npm install -g @anthropic-ai/claude-code`

2. **Launch Scribe** and open the web UI. The top-right nav shows a status
   chip:
   - Green "Connected" -- ready to go.
   - Amber "Login required" -- click it, press "Login with Claude", sign
     in in your browser.
   - Red "Install CLI" -- see step 1.

3. **Or log in from a terminal:** `claude login`. Scribe picks up the
   session automatically.

Full setup walkthrough, including troubleshooting, lives at `/help#connect-claude` in the web UI.

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
| `scribe restyle <file> --style <id>` | Restyle citations + references in a .md or .docx to a target style |
| `scribe list-styles` | List available citation styles |

## Citation restyling

Scribe ships a standalone citation engine that can convert any paper to a
target style without rewriting the prose.

```bash
# Convert a paper's citations to APA
scribe restyle paper.docx --style apa

# Use a journal house style
scribe restyle paper.md --style nature

# Target a custom preset with extra free-text rules
scribe restyle paper.docx --style custom:my-thesis-style \
    --rules "abbreviate months; DOIs on separate line"
```

Built-in styles: `harvard`, `ieee`, `apa`, `vancouver`, `chicago-author-date`,
`nature`, `science`, `jcp` (Journal of Cleaner Production), `jie` (Journal of
Industrial Ecology), `applied-energy`, `energy-policy`, `ieee-proceedings`.

Build your own style (form-driven, with a live preview and free-text rules)
from the **Restyle** page in the web UI. Saved presets live in
`src/scribe/citations/styles/custom/`.

## Building a Windows .exe to share

If you want to hand Scribe to a colleague who doesn't use Python, build
a standalone Windows bundle:

```bash
pip install pyinstaller
python scripts/build_exe.py                # onedir build (recommended)
python scripts/build_exe.py --onefile      # single-file exe, slower to launch
```

Output goes to `dist/Scribe/`. Zip that folder and send it -- your
colleague unzips it and double-clicks `Scribe.exe`.

A few things to know before you distribute:

- **Size:** ~300 MB unzipped, ~100 MB zipped. That's Python + all deps.
- **Claude CLI:** not bundled. The launcher detects if it's missing and
  shows install instructions on first run.
- **Windows SmartScreen:** an unsigned exe may trigger a "Windows protected
  your PC" warning. Recipients click "More info" -> "Run anyway". For a
  polished distribution, sign the exe with an Authenticode certificate.
- **Updates:** send a new zip when you rebuild. Users swap the folder; no
  installer to uninstall.

For technical colleagues, `pipx install git+<repo>` is lighter: they get
a 30 MB install, automatic updates via `pipx upgrade scribe`, and the
`scribe` / `scribe-desktop` commands on their PATH.

## Stack

- Python 3.11+, `claude-code-sdk` (subprocess, via Max subscription)
- `typer` (CLI), `rich` (TUI), `flask` (web UI)
- `pydantic` (models), `pyyaml` (config)
- `pymupdf` (PDF), `python-docx` (DOCX), `openpyxl` (XLSX)
- `gitpython` (version history), `tiktoken` (token estimation)
- `pyinstaller` (build toolchain for the desktop .exe)
