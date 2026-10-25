"""The CV as compact plain data for the agents, and Agent B's edits applied back to the LaTeX files.

Agents never see or write LaTeX: Python strips the markup before a request and restores it afterwards,
so no tokens are spent on markup, on unchanged text or on describing edits the pipeline can compare itself.
"""
import difflib
import re
from pathlib import Path

from src.analysis import CPP, contains, mentions
from src.validation import escape_latex_text

# One Skills line: \textbf{Label}{: item, item (with, commas), item}
SKILL_LINE = re.compile(r'(\\textbf\{([^{}]*)\}\{:\s*)([^{}]*)(\})')
CONCEPT_LABELS = ('Concepts', 'Kompetenzen')
PROGRAMMING_LABELS = ('Programming', 'Programmierung')
REQUIRED_CONCEPTS = ('LSTM', 'XGBoost')
# Include the row's line break when comparing Skills with or without a Concepts row.
CONCEPT_ROW = re.compile(r'[ \t]*\\textbf\{(Concepts|Kompetenzen)\}\{:\s*([^{}]*)\}[ \t]*(?:\\\\[ \t]*)?(?:\r?\n)?')
# \resumeSubheading{title}{dates}{organisation}{place}
ENTRY = re.compile(r'\\resumeSubheading\s*\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}')
# Agent text is plain; these characters would break or change the LaTeX it is placed in.
NOT_PLAIN = re.compile(r'[{}\\^~]')
# Files whose bullets are addressed by id: E1.2 is the second bullet of the first experience entry.
PREFIX = {'cv/src/experience.tex': 'E', 'cv/src/projects.tex': 'P'}
SUMMARY, SKILLS = 'cv/src/summary_text.tex', 'cv/src/skills.tex'


def bullet_parts(text):
    """Separate bullet bodies from protected LaTeX, preserving count and order."""
    pattern = re.compile(r'\\resumeItem\s*\{')
    bodies, structure, cursor = [], [], 0
    for match in pattern.finditer(text):
        depth, end = 1, match.end()
        while end < len(text) and depth:
            if text[end-1] != '\\':
                depth += (text[end] == '{') - (text[end] == '}')
            end += 1
        if depth:
            raise ValueError('Unbalanced CV bullet braces')
        bodies.append(text[match.end():end-1])
        structure.append(text[cursor:match.end()] + '<BULLET>}')
        cursor = end
    return ''.join(structure) + text[cursor:], bodies


def entry_bullets(structure, bullets):
    """Bullets grouped by CV entry; each \\resumeItemListEnd closes one entry's list."""
    groups, start = [], 0
    for segment in structure.split('\\resumeItemListEnd'):
        count = segment.count('<BULLET>')
        groups.append(bullets[start:start + count])
        start += count
    return groups


def skill_items(text):
    """Comma-separated skills; the commas inside 'Python (pandas, NumPy)' do not split."""
    return [item.strip() for item in re.split(r',\s*(?![^()]*\))', text) if item.strip()]


# Tools from the ad that may join one skills line, so the line stays readable and on its page.
MAX_ADDED_TOOLS = 4


def add_tools(skills_tex, tools, ad_text):
    """Append the ad's software tools to Tools; never extend Programming/Programmierung.

    tools: [{'name', 'line'}], most important first. A tool is added only if the ad names it and
    the CV does not already list it. Returns (new skills.tex, names added).
    """
    named = lambda name: re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', ad_text, re.I)
    added = []

    def extend(match):
        if match[2] != 'Tools':
            return match[0]
        items = skill_items(match[3])
        listed = ' '.join(items).casefold()
        new = [tool['name'].strip() for tool in tools if tool['line'] == 'tools'
               and tool['name'].strip() and tool['name'].strip().casefold() not in listed
               and named(tool['name'].strip()) and not NOT_PLAIN.search(tool['name'])
               and not CPP.search(tool['name'])]
        new = list(dict.fromkeys(new))[:MAX_ADDED_TOOLS]
        added.extend(new)
        items += [escape_latex_text(name) for name in new]
        return match[1] + ', '.join(items) + match[4]

    return SKILL_LINE.sub(extend, skills_tex), added


def validate_programming_skills(original, edited, analysis=None):
    """Keep the template's programming entries; reject C++ and unsupported language claims.

    Run before saving agent edits and again before compiling, against the original template.
    The analysis's language requirements are checked even if must_not_add omitted them.
    """
    def programming(files):
        return {label: sorted(skill_items(items)) for _, label, items, _ in
                SKILL_LINE.findall(files.get(SKILLS, '')) if label in PROGRAMMING_LABELS}

    if programming(original) != programming(edited):
        raise ValueError('Programming skills may only be reordered; keep the verified template entries')
    text = '\n'.join(detex(value) for value in edited.values())
    if CPP.search(text):
        raise ValueError('C++ is prohibited in candidate CV claims; remove it')
    if analysis is not None:
        before = '\n'.join(detex(value) for value in original.values())
        unsupported = [r['requirement'] for r in analysis['requirements']
                       if r['type'] == 'Programming Language' and not contains(before, r['requirement'])]
        added = [term for term in unsupported if contains(text, term)]
        if added:
            raise ValueError(f'The original CV has no evidence for programming languages {added}; remove them')


def detex(text):
    """LaTeX source as the plain text a reader sees, one line per source line."""
    text = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('%'))
    text = re.sub(r'\\(?:begin|end)\{[^{}]*\}(?:\[[^\]]*\])?', ' ', text)
    text = text.replace('\\\\', '\n').replace('\\,', ' ').replace('--', '–')
    text = re.sub(r'\\([&%$#_])', r'\1', text)
    text = re.sub(r'\\[A-Za-z]+\*?', ' ', text)
    text = re.sub(r'\}\{:', ':', text)   # \textbf{Label}{: items} reads "Label: items"
    text = re.sub(r'[{}]', ' ', text)
    lines = (' '.join(line.split()) for line in text.splitlines())
    return '\n'.join(line for line in lines if line)


def insert_concepts_row(skills, label):
    """Restore a missing Concepts row above the read-only Languages line."""
    marker = r'\input{src/languages}'
    anchor = skills.index(marker)
    indent = re.search(r'[ \t]*$', skills[:anchor])[0]
    row = rf'\textbf{{{label}}}{{: }} \\' + '\n' + indent
    return skills.replace(marker, row + marker, 1)


def deduplicate_concepts(files, evidence=()):
    """Remove repeated optional Concepts; always keep LSTM and XGBoost.

    Other skills lines, wording, read-only evidence and language levels are preserved.
    Match complete terms with the same case/inflection/hyphen handling as ad keywords.
    """
    if SKILLS not in files:
        return dict(files)
    skills = files[SKILLS]
    if not CONCEPT_ROW.search(skills) and r'\input{src/languages}' in skills:
        label = 'Kompetenzen' if any(line[1] == 'Programmierung' for line in SKILL_LINE.findall(skills)) else 'Concepts'
        skills = insert_concepts_row(skills, label)
    elsewhere = '\n'.join(detex(CONCEPT_ROW.sub('', text) if path == SKILLS else text)
                          for path, text in files.items())
    elsewhere += '\n' + '\n'.join(detex(text) for text in evidence)

    def prune(match):
        def already_named(item):
            phrase = detex(item)
            # Slash-separated aliases such as ETL/Data Pipelines also repeat a named Data Pipelines keyword.
            aliases = [part.strip() for part in phrase.split('/')]
            terms = [phrase, *aliases] if len(aliases) > 1 and all(len(part) >= 3 for part in aliases) else [phrase]
            return any(mentions(elsewhere, term) for term in terms)

        required = {item.casefold(): item for item in REQUIRED_CONCEPTS}
        remaining = []
        for item in skill_items(match[2]):
            canonical = required.get(detex(item).casefold())
            if canonical:
                if canonical not in remaining:
                    remaining.append(canonical)
            elif not already_named(item):
                remaining.append(item)
        remaining.extend(item for item in REQUIRED_CONCEPTS if item not in remaining)
        return SKILL_LINE.sub(lambda line: line[1] + ', '.join(remaining) + line[4], match[0], count=1)

    return {**files, SKILLS: CONCEPT_ROW.sub(prune, skills)}


def cv_entries(path, text):
    """[(entry id, heading, [(bullet id, LaTeX body)])] for experience.tex or projects.tex."""
    structure, bodies = bullet_parts(text)
    groups = [group for group in entry_bullets(structure, bodies) if group]
    headings = [' — '.join(detex(part) for part in match if part.strip()) for match in ENTRY.findall(text)]
    entries = []
    for number, group in enumerate(groups, 1):
        entry = f'{PREFIX[path]}{number}'
        heading = headings[number - 1] if len(headings) == len(groups) else entry
        entries.append((entry, heading, [(f'{entry}.{position}', body) for position, body in enumerate(group, 1)]))
    return entries


def compact_cv(files):
    """{path: LaTeX} as plain data: entries and bullets by id, skills as lists, the rest as text."""
    cv = {}
    for path, text in files.items():
        name = Path(path).stem.removesuffix('_text')
        if path in PREFIX:
            cv[name] = [{'entry': entry, 'heading': heading, 'bullets': [{'id': bullet, 'text': detex(body)} for bullet, body in bullets]}
                        for entry, heading, bullets in cv_entries(path, text)]
        elif path == SKILLS:
            cv[name] = [{'label': label, 'items': skill_items(detex(items))} for _, label, items, _ in SKILL_LINE.findall(text)]
        else:
            cv[name] = detex(text)
    return cv


def clean(text, what):
    """One line of an agent's plain text, escaped for LaTeX."""
    text = text.strip()
    if not text or '\n' in text or NOT_PLAIN.search(text):
        raise ValueError(f'{what}: write one line of plain text, without braces, backslashes, ^ or ~')
    return escape_latex_text(text)


def apply_edits(original, reply):
    """Agent B's reply (only what changes) applied to the CV files. Returns {path: new LaTeX}."""
    files = dict(original)
    if reply['summary'].strip():
        files[SUMMARY] = clean(reply['summary'], 'summary') + '\n'
    edits = {}
    for bullet in reply['bullets']:
        if bullet['id'] in edits:
            raise ValueError(f"bullets: {bullet['id']} appears twice")
        edits[bullet['id']] = clean(bullet['text'], f"bullet {bullet['id']}")
    order = {item['entry']: item['ids'] for item in reply['order']}
    for path in PREFIX:
        if path not in original:
            continue
        structure, _ = bullet_parts(original[path])
        for entry, _, bullets in cv_entries(path, original[path]):
            bodies = dict(bullets)
            ids = order.pop(entry, list(bodies))
            if sorted(ids) != sorted(bodies):
                raise ValueError(f'order: {entry} must list exactly its own bullets {list(bodies)}')
            for bullet in ids:
                structure = structure.replace('<BULLET>', edits.pop(bullet, bodies[bullet]), 1)
        files[path] = structure
    if edits or order:
        raise ValueError(f'Unknown bullet or entry ids: {sorted(edits) + sorted(order)}')
    lines = {line['label']: line['items'] for line in reply['skills']}
    if lines:
        skills = original[SKILLS]
        labels = {label for _, label, _, _ in SKILL_LINE.findall(skills)}
        concept_label = 'Kompetenzen' if 'Programmierung' in labels else 'Concepts'
        if concept_label in lines and concept_label not in labels:
            # A shortening pass may move a term out of the summary after its Concepts row was removed.
            if not lines[concept_label]:
                lines.pop(concept_label)
            elif r'\input{src/languages}' in skills:
                skills = insert_concepts_row(skills, concept_label)
                labels.add(concept_label)
        if set(lines) - labels:
            raise ValueError(f'skills: unknown labels {sorted(set(lines) - labels)}; the CV has {sorted(labels)}')
        files[SKILLS] = SKILL_LINE.sub(lambda m: m[1] + ', '.join(clean(item, f'{m[2]} item') for item in lines[m[2]]) + m[4]
                                       if m[2] in lines else m[0], skills)
    return files


def before_after(original, final):
    """Markdown list of every difference between two versions of the CV files, found by the pipeline itself."""
    lines = []
    for path in original:
        if path in PREFIX:
            for (_, heading, old), (_, _, new) in zip(cv_entries(path, original[path]), cv_entries(path, final[path])):
                old, new = [detex(body) for _, body in old], [detex(body) for _, body in new]
                # Pair each bullet with the old one it most resembles, so reordering and rewording are told apart.
                remaining, pairs = list(range(len(old))), []
                for text in new:
                    best = max(remaining, key=lambda index: difflib.SequenceMatcher(None, old[index], text).ratio())
                    remaining.remove(best)
                    pairs.append(best)
                changed = [(old[source], text) for source, text in zip(pairs, new) if old[source] != text]
                if changed or pairs != sorted(pairs):
                    lines.append(f'\n**{heading}**')
                    if pairs != sorted(pairs):
                        lines.append('- New bullet order, by old position: ' + ', '.join(str(source + 1) for source in pairs))
                    for before, after in changed:
                        lines += [f'- Before: {before}', f'  After:  {after}']
        elif path == SKILLS:
            old_lines = {label: items for _, label, items, _ in SKILL_LINE.findall(original[path])}
            new_lines = {label: items for _, label, items, _ in SKILL_LINE.findall(final[path])}
            for label in dict.fromkeys([*old_lines, *new_lines]):
                old, new = old_lines.get(label, ''), new_lines.get(label, '')
                if detex(old) != detex(new):
                    lines += [f'\n**Skills, {label}**', f'- Before: {detex(old)}', f'  After:  {detex(new)}']
        elif detex(original[path]) != detex(final[path]):
            lines += [f'\n**{Path(path).stem.removesuffix("_text").replace("_", " ").capitalize()}**',
                      f'- Before: {detex(original[path])}', f'  After:  {detex(final[path])}']
    return '\n'.join(lines).strip() or 'Nothing changed.'


def new_words(original, final, ad_text):
    """Words the tailored CV uses that the original CV did not: (also in the ad, in neither)."""
    words = lambda text: set(re.findall(r'[^\W\d_]{3,}', text.casefold()))
    before = words(' '.join(detex(text) for text in original.values()))
    after = words(' '.join(detex(text) for text in final.values()))
    ad = words(ad_text)
    added = after - before
    return sorted(added & ad), sorted(added - ad)
