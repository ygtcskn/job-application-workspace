"""Agent J's job analysis: its checks, the match scores and the per-job tailoring report."""
import re

# Importance weights and evidence scores of the tailoring guideline; Contextual items do not count.
IMPORTANCE = {"Must-have": 3, "Important": 2, "Preferred": 1, "Contextual": 0}
MATCH = {"STRONG": 1.0, "MODERATE": 0.7, "WEAK": 0.3, "MISSING": 0.0}
# Where Agent J may place one of the ad's terms, besides a bullet id; NOWHERE marks a term the CV cannot carry.
PLACES, NOWHERE = ('summary', 'concepts'), 'none'
# C++ must never become a candidate claim, including alternate spellings.
CPP = re.compile(r'(?<!\w)(?:c\s*[+＋]\s*[+＋]|(?:c\s*plus\s*plus|cplusplus|cpp)(?!\w))', re.I)


def plain(text):
    """LaTeX source as searchable text: whole-line comments dropped, escapes such as \\& undone."""
    lines = (line for line in text.splitlines() if not line.lstrip().startswith('%'))
    return re.sub(r'\\([&%$#_])', r'\1', '\n'.join(lines))


def contains(text, term):
    """Whole-term, case-insensitive: 'R' is not found inside 'Research'."""
    term = term.strip()
    return bool(term) and re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text, re.I) is not None


def mentions(text, term):
    """Like contains, but each word may carry an ending: 'stakeholder' is found in 'stakeholders',
    'Datenbanksystem' in 'Datenbanksystemen', and 'data-quality' matches 'data quality'."""
    words = re.findall(r'\w+', term)
    pattern = r'[\W_]+'.join(re.escape(word) + (r'\w{0,3}' if len(word) > 3 else '') for word in words)
    return bool(words) and re.search(r'(?<!\w)' + pattern + r'(?!\w)', text, re.I) is not None


def planned_terms(analysis):
    """The ad's terms Agent J placed somewhere on the CV."""
    return [k['term'] for k in analysis['keywords'] if k['use_in'] != NOWHERE]


def match_scores(requirements):
    """(actual job match, match the CV shows), each weighted by requirement importance."""
    total = sum(IMPORTANCE[r['importance']] for r in requirements)
    if not total:
        return 0.0, 0.0
    return tuple(sum(IMPORTANCE[r['importance']] * MATCH[r[key]] for r in requirements) / total
                 for key in ('actual_match', 'cv_match'))


def validate_analysis(analysis, cv_text, bullet_ids=()):
    # Checked here, not with minItems in the schema: OpenAI's strict structured outputs reject that keyword.
    if not analysis['requirements']:
        raise ValueError('requirements is empty; list what the ad asks for')
    overstated = [r['requirement'] for r in analysis['requirements'] if MATCH[r['cv_match']] > MATCH[r['actual_match']]]
    if overstated:
        raise ValueError(f'cv_match cannot be higher than actual_match: {overstated}')
    present = [term for term in analysis['must_not_add'] if contains(plain(cv_text), term)]
    if present:
        raise ValueError(f'must_not_add lists terms the CV already contains: {present}')
    places = {*bullet_ids, *PLACES, NOWHERE}
    misplaced = [k for k in analysis['keywords'] if k['use_in'] not in places]
    if misplaced:
        raise ValueError(f"keywords: use_in must be a bullet id, 'summary', 'concepts' or 'none': {misplaced}")
    blocked = [term for term in planned_terms(analysis) if any(contains(term, banned) for banned in analysis['must_not_add'])]
    if blocked:
        raise ValueError(f'keywords placed on the CV contain must_not_add terms; set their use_in to none: {blocked}')
    languages = [r['requirement'] for r in analysis['requirements'] if r['type'] == 'Programming Language']
    unsupported = [term for term in languages if not contains(plain(cv_text), term)]
    overstated_languages = [r['requirement'] for r in analysis['requirements']
                           if r['type'] == 'Programming Language' and r['requirement'] in unsupported
                           and (r['actual_match'] != 'MISSING' or r['cv_match'] != 'MISSING')]
    if overstated_languages:
        raise ValueError(f'Programming languages without CV evidence must be MISSING: {overstated_languages}. '
                         "A Programming Language requirement must be just the language name as the CV spells it (e.g. 'Python'), "
                         'one per language; put libraries or context in evidence')
    blocked_languages = [term for term in planned_terms(analysis)
                         if CPP.search(term) or any(contains(term, language) for language in unsupported)]
    if blocked_languages:
        raise ValueError(f'Unsupported programming keywords must have use_in none: {blocked_languages}')
    invalid_tools = [tool['name'] for tool in analysis['tools'] if tool['line'] != 'tools'
                     or CPP.search(tool['name']) or any(contains(tool['name'], language) for language in languages)]
    if invalid_tools:
        raise ValueError(f'tools must contain software only, never programming languages or C++: {invalid_tools}')


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
    planned = planned_terms(analysis)
    had = [term for term in planned if mentions(plain(before), term)]
    has = [term for term in planned if mentions(plain(after), term)]
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
        "\n### The ad's terms and where the CV can carry them\n"
        + bullets(f"{k['term']} → {'not supported by the CV' if k['use_in'] == NOWHERE else k['use_in']}" for k in analysis['keywords']),
        '\n### Tools from the ad added to the skills lines\n' + bullets(f"{t['name']} ({t['line']})" for t in analysis['tools']),
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
        f"- The ad's wording: {len(has)} of the {len(planned)} terms the CV can carry are now on it ({len(had)} before).",
        f"- Terms Agent B left out as overstating: {', '.join(term for term in planned if term not in has) or 'none'}.",
        f"- Terms without CV evidence on the tailored CV: {', '.join(added) if added else 'none'} ({len(banned)} checked). "
        'Ad-only software is allowed on the Tools line only; programming entries stay as verified in the template.',
        f'- The CV builds to {pages} page(s).',
        f'\nAI use for this job: {calls} calls, {sent:,} characters sent, {received:,} received.',
    ]) + '\n'
