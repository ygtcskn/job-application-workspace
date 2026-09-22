# Preserved versions

The ZIP files in this directory are code-and-configuration snapshots taken
before later pipeline changes. They contain the application pipeline, LinkedIn
importer, tests, prompts, reference documents, templates, supporting assets,
and entry-point scripts.

Mutable job-source ledgers and URL queues, generated artifacts, build files,
`.venv`, IDE files, and the independent `job_mar_ana` project are intentionally
excluded. Extract the archive into a separate directory if you need this
baseline; do not unpack it over the active project.

Version 1.1.0 locks the S-DS summary and approved 18-bullet inventory in the CV
template, leaving Skills as the only adaptive CV region. The active project is
version 1.2.0, which keeps that document contract while reducing model payloads
and adding runtime safety, provenance, and batch controls.

## Active version 1.2.0

Version 1.2.0 is preserved below and remains the active working release. Its version is sourced from
`src/job_application_pipeline/version.py`, exposed by the package and project
metadata, printed by `run.ps1 -Version` and
`python -m job_application_pipeline --version`, and written into manifests,
validation reports, completion sentinels, usage metrics, and versioned source
hashes.

The v1.2 generation contract sends a compact prompt containing stable candidate
facts and one job advertisement, and accepts only a strict nine-field JSON
draft. Skills are selected from the approved inventory and every CV/cover/audit
file is rendered locally. The S-DS summary and 18 unique approved bullets remain
byte-protected; only the four-line Skills region adapts.

Operational changes include read-only planning and token estimation, batch
input-token budgets, single-job and pending-job limits, bounded structured
repairs, per-job error continuation, provider and LaTeX process-tree timeouts,
optional address-search disablement, exclusive run locking, captured-input
mutation detection, normalised `usage.json` metrics, and checksum-verified
`.complete.json` resume sentinels.

When preserving a future v1.2 snapshot, follow the same exclusion policy and
record its SHA-256 below before advancing the active version.

- `job-application-pipeline-v1.0.0.zip` — SHA-256
  `AFC9F21E84A37F479000BF1F2D36819AE83E04AE2A9C59C2328A3F2C8C4AB305`
- `job-application-pipeline-v1.1.0.zip` — SHA-256
  `6CA6EAA43567E26E60FE6A8238DD43F63614322B3AFBD8DA3D5A1DD08981AA25`

## Version 1.2.0 baseline — 2026-09-09

Preserved the current job_py working files, including uncommitted edits, before
further changes. The desc importer and its launcher have moved to ../job/_desc
and are not included. Includes source, tests, configuration, templates, reference
documents, project notes, and the root CV PDF. Environments, IDE settings, caches,
generated runtime artifacts, Git metadata, and earlier archives are excluded.
This is a preservation baseline, not a newly tested release.

- job-application-pipeline-v1.2.0.zip — SHA-256
  2A1C6A7A1C86F4265E8EB30B38C7740B1D1DC89CF1BD57E0ABE136541C9CC36C
- Verified all 49 archived files against source SHA-256 hashes.

## Version 2.0.0 snapshot — 2026-09-18

Preserved the full job_py working tree (agent-based pipeline: src/, agents/,
config/, docs/, input/, tests/, run.ps1, notes), including uncommitted edits.
`.venv`, `.idea`, caches, `output/`, `tmp/`, and earlier archives are excluded.

- job-application-pipeline-v2.0.0.zip — SHA-256
  C253C72EAEBF76F803F36385D1B3D31868825370A9E27EB0B74013AEF503ECDC
- Verified all 60 archived files against source SHA-256 hashes.
