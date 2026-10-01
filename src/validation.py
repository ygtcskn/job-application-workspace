"""Checks that must pass before generated applications are published."""
import re
import json
from pathlib import Path
import pymupdf
from jsonschema import Draft202012Validator


def normalise(text):
    text = re.sub(r"\\([&%$#_])", r"\1", text)
    return ' '.join(re.findall(r'\w+', text.casefold()))


def company_matches(actual, advertised, ad_text):
    """Accept the header brand, an evidenced expansion, or the body's employer.

    Parent companies and publishers need not match the hiring legal entity.
    A different name needs employer context, not just a client/competitor mention.
    """
    actual, advertised = normalise(actual), normalise(advertised)
    if not actual or not advertised:
        return False
    if actual == advertised:
        return True
    description = re.split(r'^\s*Job description\s*$', ad_text, maxsplit=1,
                           flags=re.M | re.I)[-1]
    body = normalise(description)
    # Regional social-media company pages often append a country to the brand.
    regional_brand = re.sub(r' in (?:germany|deutschland|austria|österreich|switzerland|schweiz|the uk|uk|united kingdom|france|italy|spain|the netherlands|netherlands)$', '', advertised)
    if actual == regional_brand and f' {actual} ' in f' {body} ':
        return True
    employer = re.escape(actual)
    patterns = (
        rf'\bbegleite {employer} bei\b',
        rf'\bals teil des weltweiten {employer} netzwerks\b',
        rf'\b(?:arbeitgeber|employer) {employer}(?:\b|$)',
        # Company introduction at the start of the description or its section.
        rf'^(?:(?:about the company|über uns|das unternehmen|unternehmensprofil) )?'
        rf'(?:(?:die|der|das|the) )?{employer} (?:ist|sind|is|are)\b',
        rf'\b(?:about the company|über uns|unternehmensprofil) '
        rf'(?:(?:die|der|das|the) )?{employer} (?:ist|sind|is|are)\b',
        # Explicit recruiting language, including subsidiaries of group listings.
        rf'\bsuchen wir (?:dich|sie)\b[^.!?]{{0,300}}\bfür (?:die |den |das )?{employer}\b',
        rf'\bjoin (?:the team at |us at )?{employer}\b',
    )
    third_party = re.search(
        rf'\b{employer} (?:ist|is) (?:(?:a|an|our|ein|eine|unser|unsere) )?'
        r'(?:competitor|client|customer|wettbewerber|konkurrent|kunde)\b', body)
    if not third_party and any(re.search(pattern, body) for pattern in patterns):
        return True
    brand = actual.startswith(advertised + ' ')
    return brand and f' {actual} ' in f' {normalise(ad_text)} '


def escape_latex_text(value):
    """Escape plain-text fields once; preserve existing escapes and line breaks."""
    return re.sub(r'(\\*)([&%$#_])',
                  lambda m: m[1] + ('' if len(m[1]) % 2 else '\\') + m[2], value)


def prepare_cover_fields(files, text):
    """Keep listing titles when the writer adds body-only specialisations."""
    company = re.search(r'^Company:\s*(.+)', text, re.M)
    if company:
        title = ' '.join(text[:company.start()].split())
        position = files['cl/src/position.tex'].strip()
        if title and normalise(position).startswith(normalise(title) + ' '):
            files['cl/src/position.tex'] = title
    for name in ('company', 'towhom', 'position'):
        path = f'cl/src/{name}.tex'
        files[path] = escape_latex_text(files[path])


def validate_identity_fields(files, text):
    company = re.search(r'^Company:\s*(.+)', text, re.M)
    # Imported LinkedIn ads have a title and Company header. Other ad formats
    # still receive the editor completion and hiring-review checks.
    if not company:
        return
    actual = files['cl/src/company.tex'].splitlines()[0]
    if not company_matches(actual, company[1], text):
        raise ValueError(f'Cover letter company does not match job ad: {actual!r} vs {company[1]!r}')
    # PDF extraction can wrap a long title across several physical lines.
    title = text[:company.start()].strip()
    position = files['cl/src/position.tex'].strip()
    # The writer deliberately removes seniority, gender tags and location.
    core = normalise(position)
    if not core or core not in normalise(title):
        raise ValueError(f'Cover letter role does not match job ad: {position!r} vs {title!r}')


def validate_identity(build, ad):
    with pymupdf.open(ad) as document:
        text = '\n'.join(page.get_text() for page in document)
    files = {f'cl/src/{name}.tex': (build / f'cl/src/{name}.tex').read_text(encoding='utf-8')
             for name in ('company', 'position')}
    validate_identity_fields(files, text)


def validate_review(review):
    schema = json.loads((Path(__file__).resolve().parent.parent / 'agents/agent_c_schema.json').read_text(encoding='utf-8'))
    errors = list(Draft202012Validator(schema).iter_errors(review))
    if errors:
        raise ValueError(f'Hiring review does not match schema: {errors[0].message}')
    for field in ('job_analysis', 'requirements', 'cv_assessment', 'cover_letter_assessment', 'ats_keywords'):
        if not isinstance(review.get(field), list) or not review[field]:
            raise ValueError(f'Hiring review incomplete: {field} is empty or missing')
    if len(review['cv_assessment']) != 8 or len(review['cover_letter_assessment']) != 6:
        raise ValueError('Hiring review incomplete: expected all 8 CV and 6 cover-letter criteria')
    for key in ('cv_assessment', 'cover_letter_assessment'):
        if len({entry['criterion'] for entry in review[key]}) != len(review[key]):
            raise ValueError(f'Hiring review repeats criteria in {key}')
