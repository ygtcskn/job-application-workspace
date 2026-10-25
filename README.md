# Job Application Pipeline

This project turns a folder of job ads into a tailored CV and cover letter for each job, in
English or German. AI models make the judgment calls: what the job really asks for, how to word
the letter, which CV evidence to put first. Python does everything mechanical and checks the AI's
work before anything is saved.

```
 job ad (PDF) ──┐
                ├──► Agent J ──► Agent A ──► Agent B ──► build and check PDFs ──► output/job_N/
 your CV ───────┘     analyse     letter      CV          (Python)                 CV, letter, report
 (template)                                                     │
                                                                └──► Agent C: hiring review (optional)
```

---

## 1. The job application pipeline

### The idea

Hiring is a market with **asymmetric information**. The employer cannot observe how well you
really fit a job. They only see a **signal**, your CV, and a recruiter reads it for about ten
seconds. A good application makes the signal match the truth.

So the pipeline separates two things for every job:

- **Actual match**: how well your real background meets the job.
- **CV match**: how clearly your CV, as currently written, *shows* that.

Tailoring tries to raise the CV match up to the actual match, **and never above it**. Overstating
would be a false signal. It might get you an interview, but it falls apart in the interview. That
is why most of the rules below exist to keep the CV truthful.

### How the scores are calculated

Both scores are weighted averages, built like a price index. Each requirement in the ad gets a
weight for importance and a score for evidence:

| Importance  | Weight |     | Evidence | Score |
|-------------|:------:|-----|----------|:-----:|
| Must-have   | 3      |     | STRONG   | 1.0   |
| Important   | 2      |     | MODERATE | 0.7   |
| Preferred   | 1      |     | WEAK     | 0.3   |
| Contextual  | 0      |     | MISSING  | 0.0   |

```
match = Σ (weight × evidence score) / Σ weight
```

A typical result reads "job match 67%; CV showed 63% before tailoring". The 4-point gap is what
tailoring can close. These numbers decide what deserves space on the CV. They are not the
employer's applicant tracking system (ATS) score.

### What happens for one job

1. **Copy.** Python copies the templates from `input/` into a fresh working folder,
   `temp/build/job_N/`. The templates themselves are never changed.
2. **Choose the language.** Python counts common German words (*und, der, die, wir…*) and English
   words (*the, and, we, you…*) in the ad. If German words are more frequent, the job gets a
   German CV and letter; otherwise English.
3. **Agent J analyses** the ad against your CV (see section 2).
4. **Python adds software** the ad names but your CV lacks (at most four) to the Tools line.
   This only works for software tools. Programming languages are never added this way.
5. **Agent A writes** the job-specific parts of the cover letter.
6. **Agent B tailors** the CV.
7. **Python builds both PDFs** with LaTeX and checks the length: the CV may be at most 2 pages,
   the letter exactly 1. If the CV runs over, Agent B gets one try to shorten it.
8. **Python writes a report** (`tailoring_report.md`) showing what changed and why.
9. **Agent C reviews** the finished documents as a hiring manager. This step only runs if you
   add `--review`.
10. **Publish.** The PDFs are copied to `output/job_N/`, but only if every step succeeded. If a
    job fails, nothing is published for it and the batch moves on to the next job.

### Inputs you need

| Input | Where | Notes |
|-------|-------|-------|
| **Job ads** | `../Job_ad/job_description_N.pdf`, a folder called `Job_ad` **next to** this project folder | One PDF per job; `N` is the job number. The PDF must contain selectable text, not a scanned image. |
| **Your CV** | `input/cv/` (English) and `input/cv_de/` (German) | Written in LaTeX. The content is in `src/`: `summary_text`, `experience`, `projects`, `skills`, `education`, `certificates`, `languages`; your name and contact details are in `heading.tex`. **This is the only verified information about you.** Anything that is not here can never appear on a tailored CV. |
| **Cover letter** | `input/cl/` (English) and `input/cl_de/` (German) | The fixed text is in `main.tex`. The job-specific parts are `company.tex`, `towhom.tex`, `position.tex` and the bracketed gaps in `part1.tex` and `part3.tex`. |
| **Settings** | `run_settings.yaml` | Which AI, model and effort level to use (see section 3). |

The German folders only hold the files that differ from the English ones. Shared files, such as
the contact heading and the CV layout, come from `input/cv` and `input/cl`. So edit a shared file
once, but edit the *wording* in both languages.

The expected folder layout:

```
Work/
├── Job_ad/      job_description_1.pdf, job_description_2.pdf, ...
└── job_py/      this repository
```

### What you get

In `output/job_N/`:

- `yigit_coskun_cv.pdf` and `yigit_coskun_cl.pdf`: the finished CV and cover letter.
- `tailoring_report.md`:
  - what the ad says about applying (documents required, location or work-permit conditions)
  - the requirement table and both scores
  - the edit plan
  - a before-and-after of every changed CV line
  - how many of the ad's key terms the CV contains, before and after
  - the AI usage
- `completion.json`: a record that the job finished (language, reviewed or not, and a fingerprint
  of each PDF). It lets the next run skip finished jobs.
- `hiring_review.xlsx` and `review.json`, only with `--review`.

Also:

- `output/batch_summary.csv`: one row per job run, with time, status, duration, AI used and the
  scores.
- `temp/build/job_N/`: the working files, plus every prompt and reply. It is safe to delete.

> `output/` and `temp/` are **not uploaded to GitHub** (they are listed in `.gitignore`). The same
> goes for the `Job_ad` folder, which lives outside this repository. Back them up separately.

---

## 2. How the agents work

Here an **agent** is one AI call with one fixed role. Each agent has written instructions in
`agents/`, like a job description for the AI. The work is split by **division of labour**: each
agent does one task, and each gets only the information it needs. For example, Agent B never sees
the ad, because Agent J's analysis already carries what matters. Less text sent also means less
AI usage.

| Agent | Instructions | Reads | Returns |
|-------|--------------|-------|---------|
| **J: Job analyst** | `agent_j_analyst.md` | ad + CV | See the list below this table. |
| **A: Cover letter writer** | `agent_a_writer.md` | ad + letter templates | The company name and postal address, the salutation (*Dear Ms. Weber* / *Sehr geehrte Damen und Herren*), the clean job title and the bracketed gaps in the letter. A is the **only** agent allowed to search the web, and only to find a missing company address. |
| **B: CV tailor** | `agent_b_optimizer.md` | CV + J's analysis | Only the changes: a new summary, reworded bullets, a new bullet order, reordered skills. |
| **C: Hiring reviewer** (optional) | `agent_c_hiring_reviewer.md` | ad + text of the finished CV and letter | A critical hiring-manager review: evidence for each requirement, 1–5 ratings, keyword gaps, an interview recommendation and likely interview questions. Python turns it into an Excel file. |

Agent J returns:

- the application rules from the ad
- the 8–15 most important requirements, each with its importance, your CV evidence and both
  match ratings
- up to 15 key terms from the ad, each with the place on your CV where it can truthfully go
- software the ad names that your CV lacks
- a `must_not_add` list of things your CV cannot support
- an edit plan: what to reorder, rewrite, emphasise and play down

### Python is the principal, the AIs are the agents

This is a **principal–agent** setup. The AI agents do the work, but Python sets the contract and
monitors whether it is kept:

- **The contract.** Every agent must reply in a fixed format: a form (JSON) with fixed fields. Free
  text is not accepted.
- **No direct access.** Agents never open or edit files. They receive plain text and send back
  proposals; Python decides what is saved. They never see LaTeX code either. Python turns the CV
  into plain text with an id on every bullet (`E1.2` = experience entry 1, bullet 2) and turns the
  result back into LaTeX.
- **Monitoring.** Python checks every reply before saving it. A reply is rejected if, for example:
  - it uses a term from J's `must_not_add` list (a claim without evidence)
  - it changes a number (27,000 stays 27,000), moves a bullet to another job, or adds or deletes
    a bullet
  - it adds a programming language that is not already in your template (C++ is always blocked)
  - it adds, removes or rewords items on the Programming or Tools line (reordering is allowed)
  - it makes the summary more than 15% longer, or the summary states a figure that is not on the CV
  - it skips a key term that J placed, without explaining why in `omitted`
  - it changes cover letter text outside the brackets, or leaves the letter identical to the
    template
- **A second chance.** A rejected reply goes back to the agent once, together with the reason.
  If it fails again, that job fails and the batch continues with the next one.

Agent B's own instructions put truthfulness above everything else. It reorders before it rewords.
It rewords only when the ad's term honestly describes the same work, so "Supported" never becomes
"Led". Python also keeps LSTM and XGBoost on the Concepts line and removes repeated terms.

Everything mechanical is done by Python, so no AI usage is spent on it: copying the job title from
the letter to the CV, comparing before and after, counting keywords, and calculating the scores.

---

## 3. How the AIs are used through the CLI

A **CLI** (command-line interface) is a program you control by typing commands in a terminal
instead of clicking buttons. Anthropic, OpenAI and Google each offer a CLI for their AI:

| Setting    | Program     | Company   |
|------------|-------------|-----------|
| `claude`   | Claude Code | Anthropic |
| `codex`    | Codex CLI   | OpenAI    |
| `gemini`   | Gemini CLI  | Google    |

How it works:

1. You install a CLI and **sign in once** with your own account. The pipeline runs on that
   account, so usage counts against your plan's limits. No API keys are stored in this project.
2. For each agent, Python starts the CLI in the background. It hands the CLI the instructions and
   the input data and waits for the answer. Each call may take up to 15 minutes; `--timeout`
   changes this.
3. The CLI returns the JSON form, and Python checks it as described in section 2.

**Interchangeable suppliers.** All three providers get the same instructions, the same input and
the same answer form, and their replies pass the same checks. Switching provider takes one word
on the command line. They are close substitutes, not perfect ones: speed and writing quality
differ.

**Locked down.** Python switches off each CLI's own tools: no reading files and no running
commands. Web search is switched on only for Agent A.

**Small differences.** Claude and Codex enforce the answer form themselves. Gemini receives the
form inside the prompt instead. Python checks all three in the same way afterwards. Gemini has no
effort setting.

### Model and effort

Set the defaults in `run_settings.yaml`:

```yaml
provider: claude      # claude, codex or gemini
claude:
  model: opus         # opus, sonnet, ...
  effort: medium      # low, medium, high, xhigh, max
```

**Effort** is how long the model thinks before answering. It is a cost–benefit trade-off. Higher
effort can mean better judgment, but it is slower and uses more of your plan's limit. `medium` is
a sensible default.

### Usage per job

A normal job makes **3 AI calls** (J, A, B) and sends about 40,000 characters for about 10,000
back. With `--review` it makes 4 calls. A repair or shortening attempt adds one more call each
time. Every job's console line and report show the exact count.

If a provider refuses a request, for example because a usage limit was reached or you are signed
out, the whole batch stops, since the remaining jobs would fail too. Rerun later: finished jobs
are skipped.

Every prompt and reply is saved, so you can see exactly what the AI received and said. For
example, `temp/build/job_N/agent_j/attempt_1/` holds:

- `prompt.txt`: what was sent
- `response.txt`: what came back
- `metrics.json`: time taken, characters, model used

---

## 4. How to run

### One-time setup (Windows)

1. **Python 3.12**: install it from [python.org](https://www.python.org/downloads/) and tick
   *Add python.exe to PATH*.
2. **MiKTeX**: install it from [miktex.org](https://miktex.org/download). It provides `pdflatex`,
   which builds the PDFs. Allow it to install missing packages automatically.
3. **Node.js**: install it from [nodejs.org](https://nodejs.org/). It is needed to install the AI
   CLIs.
4. **At least one AI CLI.** Install it, then start it once and sign in:

   ```powershell
   npm install -g @anthropic-ai/claude-code    # then run: claude
   npm install -g @openai/codex                # then run: codex login
   npm install -g @google/gemini-cli           # then run: gemini
   ```

5. **This project**:

   ```powershell
   cd $HOME\Desktop\Work
   git clone https://github.com/ygtcskn/job-application-workspace.git job_py
   cd job_py
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

6. **Job ads**: create the `Job_ad` folder next to `job_py` and put the ads in it, named
   `job_description_1.pdf`, `job_description_2.pdf`, and so on.

### Everyday use

Run these commands from the `job_py` folder in PowerShell:

```powershell
.venv\Scripts\python -m src.run_job
```

That runs every job in `Job_ad` that is not finished yet. Add options to change what it does:

| Add this | What it does |
|----------|--------------|
| `12` | Run job 12 only (redo it if it is already finished) |
| `5-8 12` | Run jobs 5, 6, 7, 8 and 12 |
| `--review` | Also run Agent C and write `hiring_review.xlsx` |
| `--force` | Redo every job, including finished ones |
| `--language de` or `--language en` | Use this language, whatever the ad's language |
| `--provider codex` | Use Codex (or `gemini`) for this run |
| `--model sonnet --effort high` | Use another model or effort for this run |
| `--timeout 1200` | Allow each AI call up to 20 minutes |

Examples:

```powershell
.venv\Scripts\python -m src.run_job 12 --review
.venv\Scripts\python -m src.run_job 5-8 --provider gemini --language en
```

About skipping: without job numbers, finished jobs are skipped. A job finished *without* a review
counts as finished for normal runs. A `--review` run redoes it (J, A, B and C) so it gets a review.

Press **Ctrl+C** to stop. The current job is logged as "stopped" in `batch_summary.csv`.

### If something goes wrong

| Message | What to do |
|---------|------------|
| `Job ad not found` | Check the file is named `job_description_N.pdf` and sits in `../Job_ad`. |
| `No extractable text` | The PDF is a scanned image. Save the ad again as a text PDF. |
| `The provider rejected the request` | Usage limit reached or signed out. Sign in again or wait, then rerun. |
| `Invalid agent output after one repair` | The agent broke a rule twice. The reason is in `temp/build/job_N/<agent>/attempt_2/`. Rerun the job. |
| `LaTeX: ...` | A PDF did not build. Open the `.log` file named in the message. |

---

## Project map

```
agents/              instructions for each agent, plus the answer forms (JSON schemas)
input/               CV and cover letter templates: cv, cv_de, cl, cl_de
src/
  run_job.py         the main program: runs the steps for each job
  agent_workflow.py  prepares each agent's input, checks its reply, saves it
  cv_model.py        converts the CV between LaTeX and plain text; skills rules
  analysis.py        match scores and the tailoring report
  validation.py      cover letter and review checks
  hiring_review.py   writes Agent C's Excel review
  language.py        English/German detection
  providers/         one connector per AI CLI: claude, codex, gemini
run_settings.yaml    default AI, model and effort
requirements.txt     Python packages to install
```
