# Provider and pipeline audit — 21 September 2026

Scope: active source files, agent prompts, templates, validation, process management,
tests, batch history, retained stage logs and generated artifacts. Archived ZIP
inventories and their README were inspected as historical context, not treated as
the active implementation. No new provider benchmark was run for this audit.

## Findings from measured runs

The retained history contains 16 successful Claude runs averaging 2m 52s, versus
three successful Codex runs averaging 11m 37s. Excludes failed requests and the two
invalid Codex runs that published unchanged templates. Jobs and run conditions
differ, so the roughly fourfold difference is not an isolated model benchmark.
There are no Gemini runs in the current batch history.

Claude stage logs for the latest retained jobs 310–321 average approximately
47 seconds for the cover letter, 41 seconds for the CV, and 81 seconds for the
review. Its reviewer is therefore also the largest stage. Codex reviewer JSON is
only moderately longer on average: approximately 29,360 characters versus 25,084
for Claude. Review length alone does not explain the runtime gap.

Codex repeatedly tried unavailable PDF commands and Python modules, searched for
executables, and sometimes wrote custom PDF decoders. Recorded nonzero shell exits
for jobs 322/323/324 were 14/23/20. These include failed discovery attempts; they
are not all fatal request errors. Job 323's reviewer produced a single tool output
of 1,048,606 characters and another of 324,813 characters while decoding PDF streams.
Some retained logs also contain lines that cannot be parsed individually as JSON.
Counts based on parsed events should not be treated as complete trace telemetry.

## What differs by provider

All three receive the same role instructions and relative file paths. The host
does not supply extracted PDF text. Each editor and reviewer starts a separate
process, so discoveries made by one agent are not supplied to the next. The host
runs A, B, compilation and C sequentially for every provider.

| Area | Claude | Codex | Gemini |
| --- | --- | --- | --- |
| Installed version checked | 2.1.272 | 0.153.4 | 0.59.0 |
| Configured model | opus | gpt-5.6-sol | provider default |
| PDF access | Built-in Read supports PDF | Retained runs use shell extraction and custom decoders | Built-in read_file supports PDF; no measured runs here |
| Tools/permissions selected by adapter | Default tools, safe-mode, bypassPermissions | workspace-write, explicit Windows elevated sandbox, approval never | yolo and skip-trust; effective sandbox also depends on CLI settings/environment |
| Customization isolation | safe-mode | ignore-user-config; apps, memories and multi-agent disabled | No corresponding explicit customization isolation in adapter |
| Reasoning setting | --effort | model_reasoning_effort | No effort argument passed |
| Reviewer structure | --json-schema | --output-schema | Schema appended to prompt only |
| Logs | Final JSON envelope and usage | JSONL events plus final-message file | Final response/stats envelope |
| Nonzero-exit log capture | Missing | Present | Present |

The permission choices are not equivalent execution environments. A working
directory alone is not an enforced per-role file allowlist. The prompts tell each
agent what it may read/edit, but the host does not enforce those exact boundaries.

Claude logs disclose both claude-opus-5 and claude-haiku-4-5-20251001 usage. They do
not retain a detailed tool trace, so the helper model's precise contribution and
the exact PDF-reading route in each run cannot be proved from these logs. Do not
interpret modelUsage as evidence that the pipeline explicitly delegates work.

Official capability references:
- [Claude Read and other tools](https://code.claude.com/docs/en/tools-reference#read-tool-behavior)
- [Gemini file-system tools](https://geminicli.com/docs/tools/file-system/)
- [Windows sandbox configuration](https://learn.chatgpt.com/docs/windows/windows-sandbox)

## Remaining implementation issues

1. **PDF extraction is left to every agent.** `run_job.run_agent` sends paths only.
   This is the strongest directly observed source of Codex overhead. Extract each
   ad once with the project's installed PyMuPDF, and provide the reviewer with
   extracted compiled-document text. Supply page images only when layout matters.

2. **Preflight exists but is unused.** `providers.base.probe` checks executable
   capabilities, auth and effort, but `run_job.main` constructs Settings directly.
   Gemini can therefore receive `--effort medium` at the runner level, display it
   as selected, and silently omit it from the provider command. The unused
   resolve_settings function expects an older configuration shape.

3. **Gemini's documented format repair does not exist in the active runner.**
   Its adapter extracts a JSON candidate. The runner calls json.loads and a
   shallow validator; there is no full local JSON Schema validation or repair
   loop. Claude and Codex use CLI schema flags, but should still receive the same
   complete host-side validation as Gemini.

4. **Validation is incomplete.** validate_review checks selected arrays and
   criterion counts, not required item fields, rating ranges or unique criteria.
   The completion marker is self-reported. CV fact preservation, unchanged
   protected text, bullet counts and the Concepts limit are prompt rules rather
   than runtime checks. Company matching only operates on imported Company
   headers, and role matching remains a normalized substring check.

5. **Claude still loses logs on nonzero process exit.** Its invoke writes stdout
   and stderr after run_process returns. A failure bypasses those writes. The
   runner then treats an ordinary ProcessFailure differently from ProviderFailure,
   potentially trying every remaining job after a shared provider failure.

6. **Build/publication state is fragile.** Reruns remove the existing build while
   old output PDFs remain until success. Publication copies the CV and letter
   separately, without an atomic completion manifest. is_finished checks file
   presence and review structure, not hashes linking the ad, source and outputs.
   There is no run lock preventing concurrent invocations from sharing a build.

7. **Layout verification is uneven.** Only the CV has a one-page gate and a
   shortening retry. Cover letters have no page-count gate, and neither has an
   automated overflow/visual-layout gate. Current inspected CVs and letters for
   jobs 310–324 are all one page, but that does not guarantee future results.

8. **Metrics are not normalized.** The history stores the requested model alias,
   not reliable per-stage resolved models, usage, tool retries or timings. Claude
   resolves a model only when modelUsage contains exactly one entry; retained
   runs commonly have two. Gemini captures a resolved model but the batch history
   does not use it. Progress timers show elapsed time while buffered subprocess
   output prevents live explanation of what is taking time.

9. **Documentation and dependencies need cleanup.** The versions README describes
   an obsolete active architecture with budgets, locks and completion sentinels.
   Gemini comments promise absent repair behavior. Codex's module comment names
   an older CLI version. No active dependency manifest was found alongside the
   current source; the environment already needed correction between pypdf and
   the installed PyMuPDF.

## Artifact checks and fixes already applied

Current jobs 322–324 have the correct companies and roles, changed CVs and valid
nonempty reviews. Across jobs 310–324, compared LaTeX files outside the permitted
editing set were unchanged. CV structure surrounding bullets and cover-letter
text outside fill regions were preserved, ignoring trailing newlines. These are
structural checks, not a guarantee of semantic accuracy or address verification.

The earlier fixes restored Codex's Windows file access, persisted its failure
logs, rejected missing completion markers and empty reviews, and made invalid
old reviews count as unfinished.

The strict company-header comparison was a false-positive bug: Elmos, comrce and
CHECK24 ads contain longer hiring-entity names in their bodies. It now accepts a
brand-prefixed expansion only when that entire name appears in the ad text on
any page. Unsupported prefixes and unrelated employers still fail. The writer
instructions now describe this rule. All seven local regression tests passed
after that fix, including later-page evidence and wrong-company counterexamples.

## Recommended order

1. Host-side PDF extraction and explicit inputs for all providers.
2. Full shared schemas and content-preservation checks, with bounded repair.
3. Consistent failed-process logs, startup checks, and per-stage metrics.
4. Atomic publication, input/output hashes, and a run lock.
5. A small same-job benchmark with identical inputs and quality checks before
   changing model/effort settings. After that, consider running A and B concurrently
   in separate controlled directories; C must wait for both documents.

These are audit recommendations. Apart from the documented company-matching fix,
the broader refactor has not been implemented in this audit.
