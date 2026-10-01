import unittest
from pathlib import Path

from src.agent_workflow import bullet_parts, validate_edits
from src.language import detect_language

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / 'input'


def german(path):
    """'cv/src/x.tex' -> input/cv_de/src/x.tex, the German replacement for input/cv/src/x.tex."""
    folder, rest = path.split('/', 1)
    return INPUT / f'{folder}_de' / rest


class LanguageDetectionTests(unittest.TestCase):
    def test_german_body_under_english_linkedin_header(self):
        ad = ('Data Analyst\nCompany: Example\nLocation: Berlin, Germany\nApplicant info: Be among the first 25 applicants\n'
              'Job description\nAbout The Company\nDie Example GmbH ist ein führender Anbieter. '
              'Wir suchen dich für unser Team und bieten dir flexible Arbeitszeiten.')
        self.assertEqual(detect_language(ad), 'de')

    def test_english_ad(self):
        ad = 'Data Analyst\nCompany: Example\nJob description\nWe are looking for an analyst to join our team and work with the data.'
        self.assertEqual(detect_language(ad), 'en')

    def test_english_platform_wrapper_around_german_ad(self):
        ad = ('Job description\nYou are interested in the position? On Instaffo, you can apply for this and other jobs with ease.\n'
              'Wir legen mehr Wert auf Lernbereitschaft und Eigenverantwortung als auf einen perfekten Lebenslauf. '
              'Zeig uns, dass du etwas bewegen willst, und bring erste Erfahrung mit Daten mit. '
              'Du arbeitest eng mit dem Team zusammen und übernimmst Verantwortung für die Daten-Pipelines.')
        self.assertEqual(detect_language(ad), 'de')


class GermanTemplateTests(unittest.TestCase):
    def test_overlay_only_replaces_existing_files(self):
        # A misspelt name would leave the English file in a German application.
        for folder in ('cl', 'cv'):
            for path in (INPUT / f'{folder}_de').rglob('*'):
                if path.is_file():
                    self.assertTrue((INPUT / folder / path.relative_to(INPUT / f'{folder}_de')).is_file(), path)

    def test_german_cv_has_the_english_bullet_structure(self):
        for name in ('experience', 'projects'):
            english_bullets = bullet_parts((INPUT / f'cv/src/{name}.tex').read_text(encoding='utf-8'))[1]
            german_bullets = bullet_parts(german(f'cv/src/{name}.tex').read_text(encoding='utf-8'))[1]
            self.assertEqual(len(english_bullets), len(german_bullets), name)

    def test_unchanged_templates_pass_edit_validation(self):
        # Agents may return a template unchanged, so every template must satisfy the checks.
        paths = [f'{folder}/src/{name}.tex' for folder, names in
                 (('cl', ('part1', 'part2', 'part3')), ('cv', ('summary_text', 'experience', 'projects', 'skills'))) for name in names]
        for language, locate in (('English', lambda path: INPUT / path), ('German', german)):
            with self.subTest(language=language):
                files = {path: locate(path).read_text(encoding='utf-8') for path in paths}
                validate_edits(files, dict(files))



class GermanEditValidationTests(unittest.TestCase):
    def test_numbers_may_move_but_not_change(self):
        before = {'cv/src/experience.tex': r'H\resumeItem{Analyse von 44.000+ Befragten mit Erwerbsbiografien bis 1975.}E'}
        validate_edits(before, {'cv/src/experience.tex': r'H\resumeItem{Analyse seit 1975 erfasster Erwerbsbiografien von 44.000+ Befragten.}E'})
        for changed in ('44.000+ Befragten bis 1957.', '44,000+ Befragten bis 1975.', '44.000+ Befragten.'):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'numeric'):
                validate_edits(before, {'cv/src/experience.tex': rf'H\resumeItem{{Analyse von {changed}}}E'})
        percent = {'cv/src/projects.tex': r'H\resumeItem{Senkung des RMSE um 8,16\,\%.}E'}
        validate_edits(percent, {'cv/src/projects.tex': r'H\resumeItem{RMSE um 8,16\,\% gesenkt.}E'})

    def test_german_concepts_line(self):
        line = (r'\textbf{Kompetenzen}{: Datenanalyse, Statistik, Machine Learning, Deep Learning, '
                r'Datenvisualisierung, Datenmodellierung, ETL/Datenpipelines, Zeitreihenprognose}')
        before = {'cv/src/skills.tex': line}
        validate_edits(before, {'cv/src/skills.tex': line.replace('Zeitreihenprognose', 'Power BI, Reporting')})
        with self.assertRaises(ValueError):
            validate_edits(before, {'cv/src/skills.tex': line.replace('Statistik', 'Statistics')})

    def test_straight_quotes_rejected(self):
        # Under German babel "A prints as Ä and "u as ü.
        before = {'cl/src/part2.tex': 'An der Position reizt mich {Datenanalyse}.'}
        with self.assertRaisesRegex(ValueError, 'typographic'):
            validate_edits(before, {'cl/src/part2.tex': 'An der Position reizt mich {"Analytics" und Datenanalyse}.'})
        validate_edits(before, {'cl/src/part2.tex': 'An der Position reizt mich {„Analytics“ und Datenanalyse}.'})

    def test_unescaped_latex_characters_rejected(self):
        # Each of these stops pdflatex, after the agent's repair attempt would have been possible.
        before = {'cl/src/part2.tex': '% Only edit parts with brackets\nInterest in {analytics}.'}
        for fill in ('Data & AI', '50% of reports', 'data_quality', 'team #1', 'costs in $'):
            with self.subTest(fill=fill), self.assertRaisesRegex(ValueError, 'escape'):
                validate_edits(before, {'cl/src/part2.tex': before['cl/src/part2.tex'].replace('analytics', fill)})
        escaped = r'Data \& AI, 50\% of reports, data\_quality, team \#1, costs in \$'
        validate_edits(before, {'cl/src/part2.tex': before['cl/src/part2.tex'].replace('analytics', escaped)})


if __name__ == '__main__':
    unittest.main()
