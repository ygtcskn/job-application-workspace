# Job application pipeline

Run from this folder in PowerShell using the project virtual environment.

```powershell
# Default: Agent J (job analysis), A (cover letter), B (CV), then compile both PDFs
.venv/Scripts/python -m src.run_job

# Also run Agent C and write its review workbook
.venv/Scripts/python -m src.run_job --review

# Choose a provider for this run; all three use the same workflow
.venv/Scripts/python -m src.run_job --provider claude
.venv/Scripts/python -m src.run_job --provider codex
.venv/Scripts/python -m src.run_job --provider gemini

# Explicit job numbers/ranges redo those jobs
.venv/Scripts/python -m src.run_job 327 330 333

# Force the document language instead of following each ad
.venv/Scripts/python -m src.run_job 312 --language en
```

## How a job runs

1. **Agent J** reads the ad and the CV before anything is written. It notes what the ad asks
   applicants to submit, lists the requirements with the CV evidence for each, scores the actual
   job match and the match the CV currently shows, names the terms the CV cannot support, and
   writes an edit plan.
2. **Agent A** fills the cover letter fields. Python copies the role it writes into the CV's
   job title (`cv/src/job_title.tex`), so the CV and the letter name the same role.
3. **Agent B** tailors the CV from Agent J's analysis: summary, bullet wording and order within
   each job or project, and the order and Concepts of the skills lines.
4. Both PDFs are built and checked for length.
5. `tailoring_report.md` is written next to the PDFs: the ad's application rules, match analysis,
   edit plan, a before-and-after comparison of every changed line, and the final verification.
6. With `--review`, **Agent C** reviews the finished documents as a hiring manager.

The match scores weight requirements Must-have 3, Important 2, Preferred 1 and score evidence
STRONG 1, MODERATE 0.7, WEAK 0.3, MISSING 0. They guide what deserves CV space; they are not
an employer's ATS score.

`run_settings.yaml` selects the default provider, model and reasoning effort.
`--provider`, `--model` and `--effort` override those settings for one run. Gemini
CLI has no effort control; use `--effort default` if overriding an old setting.
Without job numbers, finished jobs are skipped; `--force` reruns all jobs.

Jobs completed without a review are skipped on later default runs.
A later `--review` run reruns them through A, B and C so they receive a review.
Jobs already completed with a valid review are skipped in either mode.
The old `--skip-review` flag is still accepted and does nothing.
Successful new runs record PDF hashes and review status in `output/job_X/completion.json`.
Completion records and review copies survive removal of `temp/`. Existing outputs
through job 353 were explicitly accepted during the requested cleanup; their old
review artifacts were already unavailable.

## Languages

German ads get a German CV and cover letter; all other ads get English ones.
Python decides by counting German and English function words in the ad body
(below LinkedIn's English header fields), then prints the result for each job
and records it in `completion.json`. `--language en` or `--language de` overrides
the detection for a run.

`input/cl_de/` and `input/cv_de/` hold only the German files: the letter body,
its preamble (German hyphenation and date), the fill-in parts, the CV content
and the CV's hyphenation setting (`cv_de/src/babel.tex`).
They replace their counterparts in `cl/` and `cv/` in the job's build folder; shared files
such as the contact headings and CV layout come from `input/`. Edit a shared file
once, and edit wording in both languages. Every agent is told the language;
agent reports and the hiring review stay in English.

Jobs finished before language support have English documents. Name German jobs
explicitly to redo them in German.

## Shared provider behaviour

Claude's cover-letter and CV tailoring rules are the baseline for all providers.
They use the same instructions, input text, permitted fields, JSON contracts,
local validation, and one repair attempt for invalid responses. Python extracts
the job-ad text once and supplies it to every agent. Agents return proposed
content; Python validates and saves it. They do not need to discover PDF tools,
read local documents or edit files themselves.

Only Agent A can search/fetch the web, for a missing company postal address.
Agents J, B and C need no tools. Provider adapters restrict tools through each
CLI's available controls. Their underlying models, CLI features, service latency,
and generated wording still differ; identical elapsed times are not guaranteed.
Claude and Codex also support native JSON-schema constraints; Gemini receives
the same schema in its prompt. All replies pass the same local schema checks.

Python checks protected text, numeric facts, company/title identity and PDF page
limits, and rejects any CV edit that introduces a term Agent J found no evidence for,
moves a bullet to another job, or changes the tools on the skills lines. Page
limits: one page for the cover letter, two for the CV. If the CV exceeds two
pages, Agent B gets one shortening attempt.

## What Python does and what the agents do

AI calls are kept for judgment; everything mechanical is Python, so no tokens are spent on it.

- The agents never see or write LaTeX. Python sends Agents J and B the CV as plain text with
  an id on every bullet (`E1.2`), and puts the markup and escaping back afterwards.
- Agent B returns only what changes: reworded bullets by id, a new bullet order per entry,
  changed skills lines and the summary. Unchanged text is never sent back.
- Agent B does not receive the ad again: Agent J's analysis carries what the ad asks for.
- No agent describes its edits. Python compares the files before and after and writes that
  comparison, the new words, the strengths and gaps, the match scores and the keyword counts.
- The CV's job title is copied from the cover letter's role.
- Agent B may add up to four tools the ad names to the Programming line and to the Tools
  line; your own entries always stay. Python checks that each added tool is in the ad.
  Everywhere else in the CV, terms without CV evidence are still rejected.
- Providers that enforce the reply schema themselves (Claude, Codex) do not get the schema
  repeated in the prompt.

Each job's console line and report end with the AI calls made and the characters sent and
received, from the `metrics.json` of every attempt.
Agent C assesses text extracted from the compiled PDFs; it does not visually
inspect page layout. Disabling C keeps the tailoring and compilation checks.

PDFs are published to `output/job_X/` after all selected stages succeed.
Build files and logs are in `temp/build/job_X/`. Completed review workbooks and
review JSON are also preserved alongside the PDFs in `output/job_X/`.
Each agent attempt records its prompt, reply, raw provider logs, timing and
validation result. Batch history is appended to `output/batch_summary.csv`.

## Verification

```powershell
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m unittest discover -s tests -v

# Optional live checks: consumes provider usage, sends only synthetic text
.venv/Scripts/python tests/smoke_providers.py
```

The pipeline also requires `pdflatex` on PATH and the selected provider's CLI
installed and authenticated. The live checks exercise both web-enabled and
tool-free request configurations without using candidate documents.
