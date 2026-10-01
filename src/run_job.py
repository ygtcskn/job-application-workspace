"""Run the pipeline for one job or many: Agent J analyses the ad, Agents A and B tailor, LaTeX builds; Agent C reviews on request.

Templates in input/ are never edited. Each job is copied to temp/build/job_X, where the
proposed edits are saved, LaTeX compiles and, with --review, Agent C's hiring_review.xlsx is written.
The final PDFs and tailoring_report.md go to output/job_X only once all selected stages have succeeded.

Provider, model and effort come from run_settings.yaml; flags override them for one run.

Usage:
  python -m src.run_job              every job in ../Job_ad without finished output
  python -m src.run_job 1 3 5-8      these jobs, even if already finished
  python -m src.run_job --force      every job in ../Job_ad, redoing finished ones
  python -m src.run_job --review     also run Agent C and write hiring_review.xlsx
  python -m src.run_job --language en  English documents, even for German ads
"""
import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
import yaml
from src.hiring_review import weighted_coverage, write_review
from src.process import run_process, ProcessFailure
from src.progress import Progress, clock
from src.validation import validate_identity, validate_review
from src.agent_workflow import CL_FILES, CV_FILES, CV_READ_ONLY, execute_agent, pdf_text
from src.analysis import match_scores, report as tailoring_report
from src.cv_model import before_after, new_words
from src.language import LANGUAGES, detect_language
from src.providers import claude_cli, codex_cli, gemini_cli
from src.providers.base import ProviderFailure, Settings, find_executable

ROOT = Path(__file__).resolve().parent.parent
JOB_ADS = ROOT.parent / "Job_ad"
AGENTS_DIR = ROOT / "agents"
ADAPTERS = {"claude": claude_cli, "codex": codex_cli, "gemini": gemini_cli}
# (stage folder, instructions, files the agent edits, console label), run in this order before the PDFs are built.
EDITORS = (("agent_a", "agent_a_writer.md", "Cover letter files: `cl/src/`", "Agent A: filling cover letter fields"),
           ("agent_b", "agent_b_optimizer.md", "CV files: `cv/src/`", "Agent B: tailoring CV summary, bullets and skills"))
LATEX_TIMEOUT = 180
GERMAN_FOLDERS = ("cl", "cv")  # input/cl_de and input/cv_de replace files in input/cl and input/cv
CV_PAGES = 2  # resume.tex breaks the page before Projects; a section running over adds a third page
SHORTEN_CV = ("The CV you tailored builds to more than two pages: a section runs over its page. "
              "Drop the least important concepts you added first, then shorten the summary and the longest "
              "bullets you reworded.")
# Every agent gets this run note; the templates copied for the job are already in this language.
LANGUAGE_NOTE = ("Application language: {0}. The CV and cover letter are in {0}; any document text you write "
                 "must be in {0}. Write your report or review in English.")
AD_NAME = re.compile(r"job_description_(\d+)\.pdf")
REPORT_FILE = "tailoring_report.md"
SUMMARY_COLUMNS = ["finished_at", "job", "status", "duration", "provider", "model", "effort", "coverage", "interview", "detail"]


class JobFailure(RuntimeError):
    """A job-level problem; the batch continues with the next job."""



def build_dir(job: int) -> Path:
    return ROOT / "temp" / "build" / f"job_{job}"


def output_dir(job: int) -> Path:
    return ROOT / "output" / f"job_{job}"


def build_pdf(folder: Path, tex: str) -> Path:
    # Two passes so hyperref metadata and references settle.
    for _ in range(2):
        try:
            run_process(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex], folder, LATEX_TIMEOUT)
        except ProcessFailure as error:
            log = folder / Path(tex).with_suffix('.log')
            content = log.read_text(encoding='utf-8', errors='replace') if log.exists() else error.stdout
            details = re.findall(r'^! (.+)', content, re.M)
            if details:
                raise JobFailure(f'LaTeX: {details[0]} (see {log})') from error
            raise
    return folder / Path(tex).with_suffix(".pdf")


def tidy_address(company_tex: Path):
    r"""Rewrite company.tex as one address part per line, each but the last ending in \\.

    Agents sometimes end lines with a single backslash, which LaTeX prints as a space,
    running the whole address onto one line.
    """
    lines = [line.strip().rstrip("\\").strip() for line in company_tex.read_text(encoding="utf-8").splitlines()]
    company_tex.write_text("\\\\\n".join(line for line in lines if line) + "\n", encoding="utf-8")


def page_count(pdf: Path) -> int:
    log = pdf.with_suffix(".log").read_text(encoding="utf-8", errors="replace")
    match = re.search(r"Output written on .*?\((\d+) pages?", log, re.S)
    if not match:
        raise RuntimeError(f"No page count in {pdf.with_suffix('.log')}")
    return int(match[1])


def run_agent(settings, build: Path, ad: Path, name: str, instructions: str, files: list[str], schema: Path | None = None) -> str:
    return execute_agent(ADAPTERS[settings.provider], settings, build, ad, name,
                         AGENTS_DIR / instructions, files, schema)


def run_job(job: int, settings: Settings, *, review: bool = False, language: str = "auto") -> dict:
    """Run every step for one job; raises on failure."""
    ad = JOB_ADS / f"job_description_{job}.pdf"
    if not ad.is_file():
        raise JobFailure(f"Job ad not found: {ad}")
    build = build_dir(job)
    progress = Progress(total_steps=8 if review else 6)

    with progress.step("Copying templates and job ad"):
        # Fresh copy of the templates; a rerun replaces the previous build of this job.
        if build.exists():
            shutil.rmtree(build)
        shutil.copytree(ROOT / "input", build, ignore=shutil.ignore_patterns(*(f"{folder}_de" for folder in GERMAN_FOLDERS)))
        shutil.copy2(ad, build / ad.name)
        ad_text = pdf_text(ad)
        detected = language == "auto"
        if detected:
            language = detect_language(ad_text)
        if language == "de":
            # German ads get German documents. input/cl_de and input/cv_de hold only the files
            # that differ; shared files (contact headings, CV layout) stay from input/cl and input/cv.
            for folder in GERMAN_FOLDERS:
                shutil.copytree(ROOT / "input" / f"{folder}_de", build / folder, dirs_exist_ok=True)
        template = {path: (build / path).read_text(encoding="utf-8").strip() for path in CL_FILES}
    print(f"    Language: {LANGUAGES[language]}" + (" (detected from the ad)" if detected else ""))
    note = LANGUAGE_NOTE.format(LANGUAGES[language])
    cv_files = lambda: {path: (build / path).read_text(encoding="utf-8") for path in CV_FILES + CV_READ_ONLY}
    cv_before = cv_files()
    output = output_dir(job)

    with progress.step("Agent J: analysing the job ad against the CV"):
        analysis = json.loads(run_agent(settings, build, ad, "agent_j", "agent_j_analyst.md", [note],
                                        AGENTS_DIR / "agent_j_schema.json"))

    changes = ""
    for name, instructions, files, label in EDITORS:
        with progress.step(label):
            reply = run_agent(settings, build, ad, name, instructions, [files, note])
            if name == 'agent_b':
                changes = reply
            if name == 'agent_a':
                tidy_address(build / "cl" / "src" / "company.tex")
                validate_identity(build, build / ad.name)
                # Stripped: tidy_address always rewrites company.tex with a final newline.
                if all((build / path).read_text(encoding="utf-8").strip() == content for path, content in template.items()):
                    raise JobFailure('Cover letter is unchanged from the template; refusing to publish')
                # The CV prints the same role under the name; no AI call is spent on it.
                shutil.copyfile(build / "cl/src/position.tex", build / "cv/src/job_title.tex")

    with progress.step("Building CV PDF"):
        cv_pdf = build_pdf(build / "cv", "resume.tex")
    if page_count(cv_pdf) > CV_PAGES:
        # One retry: Agent B trims its own changes, then the CV is rebuilt.
        progress.total_steps += 2
        with progress.step("Agent B: shortening CV to two pages"):
            changes += "\n\nAfter shortening to two pages:\n" + run_agent(
                settings, build, ad, "agent_b_retry", "agent_b_optimizer.md", ["CV files: `cv/src/`", SHORTEN_CV, note])
        with progress.step("Rebuilding CV PDF"):
            cv_pdf = build_pdf(build / "cv", "resume.tex")
        if page_count(cv_pdf) > CV_PAGES:
            raise JobFailure(f"CV is still {page_count(cv_pdf)} pages after Agent B's retry")
    with progress.step("Building cover letter PDF"):
        cl_pdf = build_pdf(build / "cl", "main.tex")
        if page_count(cl_pdf) > 1:
            raise JobFailure('Cover letter exceeds one page; refusing to publish')

    actual, shown = match_scores(analysis["requirements"])
    cv_after = cv_files()
    usage = ai_usage(build)
    job_title = (build / "cv/src/job_title.tex").read_text(encoding="utf-8").strip()
    (build / REPORT_FILE).write_text(tailoring_report(
        analysis, job_title=job_title, comparison=before_after(cv_before, cv_after),
        words=new_words(cv_before, cv_after, ad_text),
        # The job title is on the CV too, so a keyword such as "Data Analyst" counts as present.
        note=changes, before="\n".join(cv_before.values()), after="\n".join([job_title, *cv_after.values()]),
        pages=page_count(cv_pdf), usage=usage), encoding="utf-8")
    result = {"coverage": "", "interview": "",
              "detail": f"job match {actual:.0%}; CV showed {shown:.0%} before tailoring; "
                        f"AI {usage[0]} calls, {usage[1] / 1000:.0f}k characters in, {usage[2] / 1000:.0f}k out"}
    if review:
        with progress.step("Agent C: reviewing CV and cover letter"):
            verdict = json.loads(run_agent(settings, build, ad, "agent_c", "agent_c_hiring_reviewer.md",
                                           ["CV: `cv/resume.pdf`", "Cover letter: `cl/main.pdf`", note], AGENTS_DIR / "agent_c_schema.json"))
            validate_review(verdict)
        with progress.step("Writing hiring_review.xlsx"):
            write_review(verdict, build / "hiring_review.xlsx")
        result = {"coverage": f"{weighted_coverage(verdict['requirements']):.0%}",
                  "interview": verdict["interview_recommendation"]["decision"], "detail": result["detail"]}

    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cv_pdf, output / "yigit_coskun_cv.pdf")
    shutil.copy2(cl_pdf, output / "yigit_coskun_cl.pdf")
    shutil.copy2(build / REPORT_FILE, output / REPORT_FILE)
    if review:
        shutil.copy2(build / "hiring_review.xlsx", output / "hiring_review.xlsx")
        shutil.copy2(build / "agent_c/review.json", output / "review.json")
    completion = {"job": job, "reviewed": review, "language": language,
                  "pdfs": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in (output / "yigit_coskun_cv.pdf", output / "yigit_coskun_cl.pdf")}}
    (build / "completion.json").write_text(json.dumps(completion, indent=2), encoding="utf-8")
    (output / "completion.json").write_text(json.dumps(completion, indent=2), encoding="utf-8")
    return result


def ai_usage(build: Path) -> tuple[int, int, int]:
    """(calls, characters sent, characters received) over every agent attempt of this job so far."""
    metrics = [json.loads(path.read_text(encoding="utf-8")) for path in build.glob("agent_*/attempt_*/metrics.json")]
    return (len(metrics), sum(m["prompt_characters"] for m in metrics),
            sum(m.get("response_characters", 0) for m in metrics))


def is_finished(job: int, *, require_review: bool = True) -> bool:
    output = output_dir(job)
    pdfs = (output / "yigit_coskun_cv.pdf", output / "yigit_coskun_cl.pdf")
    if not all(p.is_file() for p in pdfs):
        return False
    try:
        durable = (output / "completion.json").exists()
        marker = (output if durable else build_dir(job)) / "completion.json"
        if marker.exists():
            completion = json.loads(marker.read_text(encoding="utf-8"))
            if completion["job"] != job or completion["pdfs"] != {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in pdfs}:
                return False
            if not require_review:
                return True
            # Explicitly accepted legacy outputs can survive removal of old logs.
            if completion.get("accepted_existing_output") is True:
                return True
            if completion["reviewed"] is not True:
                return False
        evidence = output if durable else build_dir(job)
        if not (evidence / "hiring_review.xlsx").is_file():
            return False
        validate_review(json.loads((evidence / ('review.json' if durable else 'agent_c/review.json')).read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return True


def ads_in_folder() -> list[int]:
    return sorted(int(m[1]) for p in JOB_ADS.glob("job_description_*.pdf") if (m := AD_NAME.fullmatch(p.name)))


def parse_jobs(values: list[str]) -> list[int]:
    jobs = []
    for value in values:
        start, _, end = value.partition("-")
        if not start.isdigit() or (end and not end.isdigit()):
            raise SystemExit(f"Not a job number or range: {value!r} (use e.g. 7 or 5-8)")
        jobs += range(int(start), int(end or start) + 1)
    return list(dict.fromkeys(jobs))


def log_result(row: dict):
    """Keep batch history with permanent outputs, outside disposable temp files."""
    path = ROOT / "output" / "batch_summary.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        if new:
            writer.writeheader()
        writer.writerow(row)


def chosen(value) -> str | None:
    """'default' or an empty setting leaves the choice to the provider's CLI."""
    return None if value in (None, "", "default") else str(value)


def outcome(row: dict) -> str:
    """One console line: the match note, plus Agent C's verdict when it ran."""
    verdict = f"; coverage {row['coverage']}, interview {row['interview']}" if row["coverage"] else ""
    return row["detail"] + verdict


def first_line(error: BaseException) -> str:
    text = str(error).strip().splitlines()
    return (text[0] if text else type(error).__name__)[:200]


def main():
    # Agent reports can contain characters (e.g. arrows) a Windows console encoding cannot show.
    sys.stdout.reconfigure(errors="replace")
    defaults = yaml.safe_load((ROOT / "run_settings.yaml").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog="\n".join(__doc__.splitlines()[9:]),
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jobs", nargs="*", help="job numbers or ranges, e.g. 1 3 5-8; none means every unfinished job")
    parser.add_argument("--force", action="store_true", help="with no job numbers: also redo finished jobs")
    parser.add_argument("--review", action="store_true", help="also run Agent C and write hiring_review.xlsx (default: Agents A and B and the PDFs only)")
    # Accepted from older commands; skipping the review is now the default.
    parser.add_argument("--skip-review", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--language", choices=("auto", *LANGUAGES), default="auto",
                        help="language of the CV and cover letter; auto (default) follows each job ad")
    parser.add_argument("--provider", choices=ADAPTERS, default=defaults["provider"])
    parser.add_argument("--model", help="overrides run_settings.yaml, e.g. opus or sonnet")
    parser.add_argument("--effort", help="overrides run_settings.yaml, e.g. low, medium or high")
    parser.add_argument("--timeout", type=int, default=900, help="agent timeout in seconds")
    args = parser.parse_args()
    provider_defaults = defaults.get(args.provider) or {}
    model = chosen(args.model or provider_defaults.get("model"))
    effort = chosen(args.effort or provider_defaults.get("effort"))
    if args.provider == 'gemini' and effort:
        parser.error('Gemini CLI has no effort setting; omit --effort or use --effort default')

    if args.jobs:
        jobs, skipped = parse_jobs(args.jobs), []
    else:
        available = ads_in_folder()
        skipped = [] if args.force else [job for job in available if is_finished(job, require_review=args.review)]
        jobs = [job for job in available if job not in skipped]
    print(f"Provider: {args.provider}, model: {model or 'default'}, effort: {effort or 'default'}")
    print("Agents: J, A, B, C" if args.review else "Agents: J, A, B (add --review for Agent C)")
    print(f"Language: {'from each job ad' if args.language == 'auto' else LANGUAGES[args.language]}")
    if skipped:
        print(f"Already finished, skipped: {', '.join(map(str, skipped))} (use --force or name them to redo)")
    if not jobs:
        print(f"Nothing to run: no unfinished job_description_X.pdf in {JOB_ADS}")
        return
    print(f"Jobs to run ({len(jobs)}): {', '.join(map(str, jobs))}")

    settings = Settings(args.provider, find_executable(args.provider, args.provider), model, effort, args.timeout)
    batch_started = time.monotonic()
    results = []
    for index, job in enumerate(jobs, 1):
        print(f"\n=== Job {job} ({index} of {len(jobs)}) === batch elapsed {clock(time.monotonic() - batch_started)}")
        started = time.monotonic()
        row = {"job": job, "provider": args.provider, "model": model or "default", "effort": effort or "default",
               "coverage": "", "interview": "", "detail": ""}
        stop_reason = None
        try:
            row.update(run_job(job, settings, review=args.review, language=args.language), status="done")
            print(f"Job {job} done in {clock(time.monotonic() - started)}: {outcome(row)}")
        except KeyboardInterrupt:
            row.update(status="stopped", detail="stopped by user")
            stop_reason = "Stopped by user."
        except ProviderFailure as error:
            # Usage limits and sign-in problems affect every remaining job too.
            row.update(status="failed", detail=first_line(error))
            stop_reason = f"The provider rejected the request ({row['detail']}). Rerun later to continue with unfinished jobs."
        except Exception as error:
            row.update(status="failed", detail=first_line(error))
            print(f"Job {job} FAILED: {row['detail']}\n  Logs: {build_dir(job)}")
        row.update(finished_at=datetime.now().isoformat(timespec="seconds"), duration=clock(time.monotonic() - started))
        log_result(row)
        results.append(row)
        if stop_reason:
            print(f"\n{stop_reason}")
            break
        done_so_far = time.monotonic() - batch_started
        remaining = len(jobs) - index
        if remaining:
            print(f"Batch: {index} of {len(jobs)} processed, elapsed {clock(done_so_far)}, "
                  f"about {clock(done_so_far / index * remaining)} left")

    finished = sum(r["status"] == "done" for r in results)
    print(f"\nBatch finished in {clock(time.monotonic() - batch_started)}: {finished} done, "
          f"{len(results) - finished} failed or stopped, {len(jobs) - len(results)} not started")
    for r in results:
        print(f"  job {r['job']:<5} {r['status']:<8} {r['duration']:>8}  {outcome(r)}")
    print(f"\nPDFs and reports: {ROOT / 'output' / 'job_X'}\n"
          f"Reviews and logs: {ROOT / 'temp' / 'build' / 'job_X'}\n"
          f"History:          {ROOT / 'output' / 'batch_summary.csv'}")
    if finished < len(jobs):
        sys.exit(1)


if __name__ == "__main__":
    main()
