"""Agent J's job analysis: its checks, the match scores and the per-job tailoring report."""
import re

# Importance weights and evidence scores of the tailoring guideline; Contextual items do not count.
IMPORTANCE = {"Must-have": 3, "Important": 2, "Preferred": 1, "Contextual": 0}
MATCH = {"STRONG": 1.0, "MODERATE": 0.7, "WEAK": 0.3, "MISSING": 0.0}


def plain(text):
    """LaTeX source as searchable text: whole-line comments dropped, escapes such as \\& undone."""
    lines = (line for line in text.splitlines() if not line.lstrip().startswith('%'))
    return re.sub(r'\\([&%$#_])', r'\1', '\n'.join(lines))


def contains(text, term):
    """Whole-term, case-insensitive: 'R' is not found inside 'Research'."""
    term = term.strip()
    return bool(term) and re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text, re.I) is not None


def match_scores(requirements):
    """(actual job match, match the CV shows), each weighted by requirement importance."""
    total = sum(IMPORTANCE[r['importance']] for r in requirements)
    if not total:
        return 0.0, 0.0
    return tuple(sum(IMPORTANCE[r['importance']] * MATCH[r[key]] for r in requirements) / total
                 for key in ('actual_match', 'cv_match'))


def validate_analysis(analysis, cv_text):
    # Checked here, not with minItems in the schema: OpenAI's strict structured outputs reject that keyword.
    if not analysis['requirements']:
        raise ValueError('requirements is empty; list what the ad asks for')
    overstated = [r['requirement'] for r in analysis['requirements'] if MATCH[r['cv_match']] > MATCH[r['actual_match']]]
    if overstated:
        raise ValueError(f'cv_match cannot be higher than actual_match: {overstated}')
    present = [term for term in analysis['must_not_add'] if contains(plain(cv_text), term)]
    if present:
        raise ValueError(f'must_not_add lists terms the CV already contains: {present}')


def unsupported_terms(text, analysis):
    """must_not_add terms that appear in tailored CV text."""
    return [term for term in analysis['must_not_add'] if contains(plain(text), term)]


def bullets(items):
    return '\n'.join(f'- {item}' for item in items) or '- None'


def report(analysis, *, job_title, comparison, words, note, before, after, pages, usage):
    """One job's tailoring report. Everything but Agent J's analysis and Agent B's note is computed here.

    comparison: before/after of every changed line; words: (new words from the ad, new words from neither);
    before, after: the CV source text; usage: (AI calls, characters sent, characters received).
    """
    rules = analysis['application_rules']
    requirements = analysis['requirements']
    actual, shown = match_scores(requirements)
    rows = '\n'.join(f"| {r['requirement']} | {r['importance']} | {r['type']} | {r['actual_match']} | {r['cv_match']} | {r['evidence']} |"
                     .replace('\n', ' ') for r in requirements)
    key = [r for r in requirements if IMPORTANCE[r['importance']] >= 2]
    plan = analysis['edit_plan']
    banned = analysis['must_not_add']
    supported = [k for k in analysis['keywords'] if not any(contains(k, t) or contains(t, k) for t in banned)]
    had = [k for k in supported if contains(plain(before), k)]
    has = [k for k in supported if contains(plain(after), k)]
    added = unsupported_terms(after, analysis)
    from_ad, from_neither = words
    calls, sent, received = usage
    return '\n'.join([
        '# Tailoring report',
        '## A. Application rules in the ad',
        f"- Required documents: {', '.join(rules['required_documents']) or 'none stated'}",
        f"- Location or relocation requirement: {rules['location_requirement'] or 'none stated'}",
        f"- Other rules: {'; '.join(rules['other_restrictions']) or 'none stated'}",
        '\n## B. Match analysis',
        f'- Actual job match: {actual:.0%}',
        f'- Match the CV showed before tailoring: {shown:.0%}',
        '- These scores guide what deserves CV space. They are not an employer’s ATS score.',
        '\n| Requirement | Importance | Type | Actual match | CV before | Evidence |\n|---|---|---|---|---|---|\n' + rows,
        '\n### Major strengths\n' + bullets(f"{r['requirement']}: {r['evidence']}" for r in key if r['actual_match'] == 'STRONG'),
        '\n### Major gaps\n' + bullets(r['requirement'] for r in key if r['actual_match'] in ('MISSING', 'WEAK')),
        '\n### Most important keywords\n' + bullets(analysis['keywords']),
        '\n## C. Edit plan',
        '### Reorder\n' + bullets(plan['reorder']),
        '\n### Rewrite\n' + bullets(plan['rewrite']),
        '\n### Emphasize\n' + bullets(plan['emphasize']),
        '\n### Remove or shorten\n' + bullets(plan['remove']),
        '\n### Must not be added\n' + bullets(banned),
        '\n## D. Before and after',
        'Compared by the pipeline from the files themselves, not reported by the agent.\n',
        f'**Job title**\n- {job_title}\n',
        comparison,
        f"\nWords new to the CV that the ad uses: {', '.join(from_ad) or 'none'}",
        f"Words new to the CV that are in neither the old CV nor the ad (check these): {', '.join(from_neither) or 'none'}",
        f'\nAgent B: {note.strip()}' if note.strip() else '',
        '\n## E. Final verification',
        '- Employers, job titles, dates, places, degrees and project titles are unchanged (enforced before saving).',
        '- The numbers in every bullet are unchanged; no figure outside the CV entered the summary (enforced).',
        f"- Unsupported keywords in the tailored CV: {', '.join(added) if added else 'none'} ({len(banned)} checked). "
        'They are allowed on the Programming and Tools lines only; be ready to speak about any listed here.',
        f'- Supported keywords present in the CV: {len(had)} of {len(supported)} before, {len(has)} of {len(supported)} after.',
        f"- Supported keywords still not in the CV in the ad's wording: {', '.join(k for k in supported if k not in has) or 'none'}.",
        f'- The CV builds to {pages} page(s).',
        f'\nAI use for this job: {calls} calls, {sent:,} characters sent, {received:,} received.',
    ]) + '\n'
