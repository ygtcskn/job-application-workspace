"""The CV as compact plain data for the agents, and Agent B's edits applied back to the LaTeX files.

Agents never see or write LaTeX: Python strips the markup before a request and restores it afterwards,
so no tokens are spent on markup, on unchanged text or on describing edits the pipeline can compare itself.
"""
import difflib
import re
from pathlib import Path

from src.validation import escape_latex_text

# One Skills line: \textbf{Label}{: item, item (with, commas), item}
SKILL_LINE = re.compile(r'(\\textbf\{([^{}]*)\}\{:\s*)([^{}]*)(\})')
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
        labels = {label for _, label, _, _ in SKILL_LINE.findall(original[SKILLS])}
        if set(lines) - labels:
            raise ValueError(f'skills: unknown labels {sorted(set(lines) - labels)}; the CV has {sorted(labels)}')
        files[SKILLS] = SKILL_LINE.sub(lambda m: m[1] + ', '.join(clean(item, f'{m[2]} item') for item in lines[m[2]]) + m[4]
                                       if m[2] in lines else m[0], original[SKILLS])
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
            for (_, label, old, _), (_, _, new, _) in zip(SKILL_LINE.findall(original[path]), SKILL_LINE.findall(final[path])):
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
