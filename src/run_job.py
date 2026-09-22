"""Run the pipeline for one job or many: Agents A and B tailor, LaTeX builds, Agent C reviews.

Templates in input/ are never edited. Each job is copied to temp/build/job_X, where the
agents edit, LaTeX compiles and Agent C's hiring_review.xlsx is written. The final PDFs
go to output/job_X only once the whole job has succeeded.

Provider, model and effort come from run_settings.yaml; flags override them for one run.

Usage:
  python -m src.run_job              every job in ../Job_ad without finished output
  python -m src.run_job 1 3 5-8      these jobs, even if already finished
  python -m src.run_job --force      every job in ../Job_ad, redoing finished ones
"""
import argparse
import csv
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
import yaml
from src.hiring_review import weighted_coverage, write_review
from src.process import run_process
from src.progress import Progress, clock
from src.validation import validate_identity, validate_review
from src.agent_workflow import execute_agent
from src.providers import claude_cli, codex_cli, gemini_cli
from src.providers.base import ProviderFailure, Settings, find_executable

ROOT = Path(__file__).resolve().parent.parent
JOB_ADS = ROOT.parent / "Job_ad"
AGENTS_DIR = ROOT / "agents"
ADAPTERS = {"claude": claude_cli, "codex": codex_cli, "gemini": gemini_cli}
# (stage folder, instructions, files the agent edits, console label), run in this order before the PDFs are built.
EDITORS = (("agent_a", "agent_a_writer.md", "Cover letter files: `cl/src/`", "Agent A: filling cover letter fields"),
           ("agent_b", "agent_b_optimizer.md", "CV files: `cv/src/`", "Agent B: tailoring CV bullets and skills"))
LATEX_TIMEOUT = 180
SHORTEN_CV = ("The CV you tailored builds to more than one page. Shorten it to one page: drop the least "
              "important concepts you added first, then shorten the longest bullets you reworded.")
AD_NAME = re.compile(r"job_description_(\d+)\.pdf")
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
        run_process(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex], folder, LATEX_TIMEOUT)
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


def run_job(job: int, settings: Settings) -> dict:
    """Run every step for one job; raises on failure."""
    ad = JOB_ADS / f"job_description_{job}.pdf"
    if not ad.is_file():
        raise JobFailure(f"Job ad not found: {ad}")
    build = build_dir(job)
    progress = Progress(total_steps=7)

    with progress.step("Copying templates and job ad"):
        # Fresh copy of the templates; a rerun replaces the previous build of this job.
        if build.exists():
            shutil.rmtree(build)
        shutil.copytree(ROOT / "input", build)
        shutil.copy2(ad, build / ad.name)

    for name, instructions, files, label in EDITORS:
        with progress.step(label):
            run_agent(settings, build, ad, name, instructions, [files])
            if name == 'agent_a':
                tidy_address(build / "cl" / "src" / "company.tex")
                validate_identity(build, build / ad.name)
                tailored = ('company', 'towhom', 'position', 'part1', 'part2', 'part3')
                if all((build / f'cl/src/{field}.tex').read_bytes() ==
                       (ROOT / f'input/cl/src/{field}.tex').read_bytes() for field in tailored):
                    raise JobFailure('Cover letter is unchanged from the template; refusing to publish')

    with progress.step("Building CV PDF"):
        cv_pdf = build_pdf(build / "cv", "resume.tex")
    if page_count(cv_pdf) > 1:
        # One retry: Agent B trims its own changes, then the CV is rebuilt.
        progress.total_steps += 2
        with progress.step("Agent B: shortening CV to one page"):
            run_agent(settings, build, ad, "agent_b_retry", "agent_b_optimizer.md", ["CV files: `cv/src/`", SHORTEN_CV])
        with progress.step("Rebuilding CV PDF"):
            cv_pdf = build_pdf(build / "cv", "resume.tex")
        if page_count(cv_pdf) > 1:
            raise JobFailure(f"CV is still {page_count(cv_pdf)} pages after Agent B's retry")
    with progress.step("Building cover letter PDF"):
        cl_pdf = build_pdf(build / "cl", "main.tex")
        if page_count(cl_pdf) > 1:
            raise JobFailure('Cover letter exceeds one page; refusing to publish')

    with progress.step("Agent C: reviewing CV and cover letter"):
        review = json.loads(run_agent(settings, build, ad, "agent_c", "agent_c_hiring_reviewer.md",
                                      ["CV: `cv/resume.pdf`", "Cover letter: `cl/main.pdf`"], AGENTS_DIR / "agent_c_schema.json"))
        validate_review(review)
    with progress.step("Writing hiring_review.xlsx"):
        write_review(review, build / "hiring_review.xlsx")

    output = output_dir(job)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cv_pdf, output / "yigit_coskun_cv.pdf")
    shutil.copy2(cl_pdf, output / "yigit_coskun_cl.pdf")
    return {"coverage": f"{weighted_coverage(review['requirements']):.0%}",
            "interview": review["interview_recommendation"]["decision"]}


def is_finished(job: int) -> bool:
    output = output_dir(job)
    if not all(p.is_file() for p in (output / "yigit_coskun_cv.pdf", output / "yigit_coskun_cl.pdf",
                                   build_dir(job) / "hiring_review.xlsx")):
        return False
    try:
        validate_review(json.loads((build_dir(job) / 'agent_c/review.json').read_text(encoding='utf-8')))
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
    """Append one line per job to temp/batch_summary.csv, a history across batches."""
    path = ROOT / "temp" / "batch_summary.csv"
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
        skipped = [] if args.force else [job for job in available if is_finished(job)]
        jobs = [job for job in available if job not in skipped]
    print(f"Provider: {args.provider}, model: {model or 'default'}, effort: {effort or 'default'}")
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
            row.update(run_job(job, settings), status="done")
            print(f"Job {job} done in {clock(time.monotonic() - started)}: coverage {row['coverage']}, interview {row['interview']}")
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
        outcome = f"coverage {r['coverage']:>4}  {r['interview']}" if r["status"] == "done" else r["detail"]
        print(f"  job {r['job']:<5} {r['status']:<8} {r['duration']:>8}  {outcome}")
    print(f"\nPDFs:             {ROOT / 'output' / 'job_X'}\n"
          f"Reviews and logs: {ROOT / 'temp' / 'build' / 'job_X'}\n"
          f"History:          {ROOT / 'temp' / 'batch_summary.csv'}")
    if finished < len(jobs):
        sys.exit(1)


if __name__ == "__main__":
    main()
