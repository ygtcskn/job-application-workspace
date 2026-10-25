# Job Application Pipeline

Turns job ads into a tailored CV and cover letter for each job, in English or German. AI agents
make the judgment calls; Python does the mechanical work and checks every AI answer.

## 1. How it works

The employer only sees your CV, a **signal** of your fit. The goal is to make that signal match
your real fit: raise what the CV *shows* up to what you *have*, never above it.

For each job:

1. Python detects the ad's language (German or English).
2. **Agent J** analyses the ad against your CV.
3. **Agent A** writes the cover letter.
4. **Agent B** tailors the CV.
5. Python builds both PDFs and checks their length: CV ≤ 2 pages, letter 1 page.
6. **Agent C** reviews both as a hiring manager (only with `--review`).

**Inputs**
- Job ads: `../Job_ad/job_description_N.pdf`, a folder next to this project.
- Your CV and cover letter templates: `input/` (`cv`, `cv_de`, `cl`, `cl_de`).
- AI settings: `run_settings.yaml`.

**Output**
- `output/job_N/`: the CV, the cover letter and a `tailoring_report.md`.

## 2. How the agents work

Each agent is one AI call with its own instructions in `agents/`.

| Agent | Does |
|-------|------|
| J: Analyst | Lists the key requirements, scores your real match and your CV's shown match, plans the edits, and lists claims the CV can't support |
| A: Writer | Fills in the company, address, salutation and job-specific sentences of the cover letter |
| B: Tailor | Rewords and reorders CV bullets, the summary and the skills, using J's plan |
| C: Reviewer | Gives hiring-manager feedback in an Excel file (optional) |

It's a **principal–agent** setup. The agents only *propose* changes in a fixed answer form.
Python checks each answer and rejects anything untruthful, such as new claims, changed numbers or
unsupported skills. A rejected agent gets one chance to fix its answer.

## 3. How the AIs are used (CLI)

The agents run through the command-line versions of **Claude Code**, **Codex** or
**Gemini CLI**. Python starts the CLI in the background, sends the prompt and reads back the
answer. You sign in to the CLI once with your own account; no API keys are needed.

All three get the same instructions and checks, so the provider is interchangeable. Choose it in
`run_settings.yaml` or with `--provider`.

## 4. How to run

**Setup:**
- Install **Python 3.12** and **MiKTeX** (for `pdflatex`).
- Install one AI CLI and sign in. For example, Claude Code: run `irm https://claude.ai/install.ps1 | iex` in PowerShell, then run `claude`.
- Set up the project:

```powershell
git clone https://github.com/ygtcskn/job-application-workspace.git job_py
cd job_py
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

**Run:**

```powershell
.venv\Scripts\python -m src.run_job              # all unfinished jobs
.venv\Scripts\python -m src.run_job 5-8 12       # specific jobs
.venv\Scripts\python -m src.run_job --review     # also run Agent C
.venv\Scripts\python -m src.run_job --provider codex --language de
```
