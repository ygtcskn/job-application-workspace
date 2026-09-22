"""Checks that must pass before generated applications are published."""
import re
import json
from pathlib import Path
import fitz
from jsonschema import Draft202012Validator


def normalise(text):
    text = re.sub(r"\\([&%$#_])", r"\1", text)
    return ' '.join(re.findall(r'\w+', text.casefold()))


def company_matches(actual, advertised, ad_text):
    """Accept the advertised brand or an evidenced expansion of that brand.

    Do not accept arbitrary prefix matches: the entire expanded legal name
    must also occur in the advertisement, on any page.
    """
    actual, advertised = normalise(actual), normalise(advertised)
    if not actual or not advertised:
        return False
    if actual == advertised:
        return True
    brand = actual.startswith(advertised + ' ')
    return brand and f' {actual} ' in f' {normalise(ad_text)} '


def validate_identity(build, ad):
    with fitz.open(ad) as document:
        text = '\n'.join(page.get_text() for page in document)
    company = re.search(r'^Company:\s*(.+)', text, re.M)
    # Imported LinkedIn ads have a title and Company header. Other ad formats
    # still receive the editor completion and hiring-review checks.
    if not company:
        return
    actual = (build / 'cl/src/company.tex').read_text(encoding='utf-8').splitlines()[0]
    if not company_matches(actual, company[1], text):
        raise ValueError(f'Cover letter company does not match job ad: {actual!r} vs {company[1]!r}')
    title = text.strip().splitlines()[0]
    position = (build / 'cl/src/position.tex').read_text(encoding='utf-8').strip()
    # The writer deliberately removes seniority, gender tags and location.
    core = normalise(position)
    if not core or core not in normalise(title):
        raise ValueError(f'Cover letter role does not match job ad: {position!r} vs {title!r}')


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
