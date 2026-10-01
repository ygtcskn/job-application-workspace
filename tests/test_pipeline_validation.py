import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
from src import run_job
from src.providers import codex_cli
from src.providers.base import ProviderFailure, Settings
from src.process import ProcessFailure
from src.validation import company_matches, validate_identity, validate_review, prepare_cover_fields, escape_latex_text
from src.agent_workflow import editor_schema, parse_reply, request_structured
from src.analysis import contains, match_scores, plain, report as tailoring_report, validate_analysis


def valid_review():
    schema = json.loads((run_job.AGENTS_DIR / 'agent_c_schema.json').read_text(encoding='utf-8'))
    def sample(node):
        if 'enum' in node:
            return node['enum'][0]
        if node['type'] == 'object':
            return {key: sample(value) for key, value in node['properties'].items()}
        if node['type'] == 'array':
            return [sample(node['items'])]
        return 'Evidence'
    review = sample(schema)
    for key in ('cv_assessment', 'cover_letter_assessment'):
        criteria = schema['properties'][key]['items']['properties']['criterion']['enum']
        review[key] = [{'criterion': value, 'rating': 3, 'justification': 'Evidence'} for value in criteria]
    return review


def valid_analysis():
    return {'application_rules': {'required_documents': ['CV'], 'location_requirement': '', 'other_restrictions': []},
            'requirements': [
                {'requirement': 'SQL', 'importance': 'Must-have', 'type': 'Programming Language',
                 'evidence': 'Ziraat Bank: SQL queries', 'actual_match': 'STRONG', 'cv_match': 'MODERATE'},
                {'requirement': 'Snowflake', 'importance': 'Preferred', 'type': 'Tool',
                 'evidence': 'None', 'actual_match': 'MISSING', 'cv_match': 'MISSING'}],
            'keywords': ['SQL', 'Snowflake'],
            'must_not_add': ['Snowflake'],
            'edit_plan': {'reorder': ['Move the SQL bullet first'], 'rewrite': [], 'emphasize': ['SQL'], 'remove': []}}


class AnalysisTests(unittest.TestCase):
    def test_schema_accepts_the_sample_and_scores_follow_the_guideline(self):
        schema = json.loads((run_job.AGENTS_DIR / 'agent_j_schema.json').read_text(encoding='utf-8'))
        analysis = parse_reply(json.dumps(valid_analysis()), schema)
        validate_analysis(analysis, 'Wrote SQL queries.')
        # Must-have weighs 3 and Preferred 1; STRONG scores 1, MODERATE 0.7, MISSING 0.
        actual, shown = match_scores(analysis['requirements'])
        self.assertAlmostEqual(actual, 0.75)
        self.assertAlmostEqual(shown, 0.525)

    def test_inconsistent_analysis_is_sent_back(self):
        with self.assertRaisesRegex(ValueError, 'already contains'):
            validate_analysis(valid_analysis(), 'Loaded data into Snowflake.')
        overstated = valid_analysis()
        overstated['requirements'][1]['cv_match'] = 'WEAK'
        with self.assertRaisesRegex(ValueError, 'cv_match'):
            validate_analysis(overstated, 'Wrote SQL queries.')
        with self.assertRaisesRegex(ValueError, 'requirements is empty'):
            validate_analysis(dict(valid_analysis(), requirements=[]), 'Wrote SQL queries.')

    def test_whole_term_matching(self):
        self.assertTrue(contains('Analysis in R and Stata', 'R'))
        self.assertFalse(contains('Research at the IAB', 'R'))
        self.assertTrue(contains(plain(r'Data \& Analytics with A/B testing'), 'a/b Testing'))

    def test_report_has_the_five_parts_and_counts_keywords(self):
        text = tailoring_report(valid_analysis(), job_title='Data Analyst', comparison='- Before: Queried data.\n  After:  Wrote SQL queries.',
                                words=(['sql'], ['wrote']), note='Analyst profile.', before='Queried data.', after='Wrote SQL queries.',
                                pages=2, usage=(3, 21500, 3900))
        for heading in ('## A. Application rules in the ad', '## B. Match analysis', '## C. Edit plan', '## D. Before and after', '## E. Final verification'):
            self.assertIn(heading, text)
        self.assertIn('Actual job match: 75%', text)
        self.assertIn('0 of 1 before, 1 of 1 after', text)
        self.assertIn('Unsupported keywords in the tailored CV: none', text)
        # Strengths and gaps come from the requirement table, not from extra agent output.
        self.assertIn('### Major strengths\n- SQL: Ziraat Bank: SQL queries', text)
        self.assertIn('in neither the old CV nor the ad (check these): wrote', text)
        self.assertIn('AI use for this job: 3 calls, 21,500 characters sent, 3,900 received.', text)


class PipelineValidationTests(unittest.TestCase):
    def test_latex_failure_reports_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'main.log').write_text('Package loading\n! Misplaced alignment tab character &.\nMore output', encoding='utf-8')
            with patch.object(run_job, 'run_process', side_effect=ProcessFailure('babel.sty')):
                with self.assertRaisesRegex(run_job.JobFailure, 'Misplaced alignment tab'):
                    run_job.build_pdf(folder, 'main.tex')

    def test_regional_company_brand(self):
        self.assertTrue(company_matches('Lidl\\\\', 'Lidl in Germany', 'Mehrwert für Lidl schaffen.'))
        self.assertTrue(company_matches('Example', 'Example in Austria', 'Welcome to Example.'))
        self.assertFalse(company_matches('Lidl', 'Lidl Services GmbH', 'We work with Lidl.'))
        self.assertFalse(company_matches('Wrong', 'Lidl in Germany', 'Wrong is a client.'))

    def test_plain_fields_escaped_once_and_listing_role_retained(self):
        files = {'cl/src/company.tex': 'A & B\\\\\nBerlin', 'cl/src/towhom.tex': 'Dear Hiring Team,',
                 'cl/src/position.tex': 'Data Analyst (Call Channel & AI Insights)'}
        prepare_cover_fields(files, 'Data Analyst\nCompany: A & B\nJob description\nJob Title: Data Analyst (Call Channel & AI Insights)')
        self.assertEqual(files['cl/src/position.tex'], 'Data Analyst')
        self.assertEqual(files['cl/src/company.tex'], 'A \\& B\\\\\nBerlin')
        self.assertEqual(escape_latex_text(r'Data \& AI'), r'Data \& AI')
        self.assertEqual(escape_latex_text('Data & AI'), r'Data \& AI')
        self.assertEqual(escape_latex_text('50% #1 $2 A_B'), r'50\% \#1 \$2 A\_B')

    def test_publisher_header_requires_explicit_employer_evidence(self):
        self.assertTrue(company_matches('KPMG', 'Jobster', 'Begleite KPMG bei den Herausforderungen.'))
        self.assertTrue(company_matches('KPMG', 'Jobster', 'Als Teil des weltweiten KPMG-Netzwerks bieten wir Dir Karriereperspektiven.'))
        self.assertFalse(company_matches('KPMG', 'Jobster', 'Unsere Kunden sind KPMG und andere Unternehmen.'))
        self.assertTrue(company_matches('KPMG', 'Other Publisher', 'Begleite KPMG bei den Herausforderungen.'))
        self.assertFalse(company_matches('KPMG Other GmbH', 'Jobster', 'Begleite KPMG bei den Herausforderungen.'))

    def test_body_employer_overrides_any_publisher_or_parent_group(self):
        cases = [
            ('DB InfraGO AG', 'Deutsche Bahn', 'Job description\nDie DB InfraGO AG ist die Infrastrukturgesellschaft der Deutschen Bahn.'),
            ('Vereinigte Hagelversicherung VVaG', 'TaskVerse', 'Job description\nAbout The Company\nDie Vereinigte Hagelversicherung VVaG ist Europas Spezialist.'),
            ('Example Services GmbH', 'Unseen Publisher', 'About The Company\nExample Services GmbH is the hiring company.'),
            ('DB InfraGO AG', 'Deutsche Bahn', 'Zum nächsten Zeitpunkt suchen wir dich als Analyst für die DB InfraGO AG am Standort München.'),
        ]
        for actual, header, body in cases:
            with self.subTest(company=actual):
                self.assertTrue(company_matches(actual + '\\\\', header, body))
        self.assertFalse(company_matches('Other GmbH', 'TaskVerse', 'About The Company\nVereinigte Hagelversicherung VVaG is our employer. Other GmbH is a client.'))
        self.assertFalse(company_matches('DB InfraGO', 'Deutsche Bahn', 'Die DB InfraGO AG ist die Infrastrukturgesellschaft.'))

    def test_wrapped_title_and_publisher_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'cl/src'
            source.mkdir(parents=True)
            ad = folder / 'ad.pdf'
            with pymupdf.open() as doc:
                doc.new_page().insert_text((72,72), 'Volkswirt als (Junior) Consultant Global\nTransfer Pricing - Jobbird.com\n\nCompany: Jobster\nAls Teil des weltweiten KPMG-Netzwerks')
                doc.save(ad)
            (source / 'company.tex').write_text('KPMG\\\\\nLeipzig', encoding='utf-8')
            (source / 'position.tex').write_text('Consultant Global Transfer Pricing', encoding='utf-8')
            validate_identity(folder, ad)

    def test_brand_expansions_require_full_name_evidence(self):
        pairs = [('Elmos', 'Elmos Semiconductor Business Services GmbH'),
                 ('comrce', 'comrce GmbH'),
                 ('CHECK24', 'CHECK24 Vergleichsportal Mietwagen GmbH')]
        for brand, legal in pairs:
            with self.subTest(brand=brand):
                self.assertTrue(company_matches(legal + '\\\\', brand, 'Contact: ' + legal + '.'))
                self.assertTrue(company_matches(brand, brand, ''))
                self.assertFalse(company_matches(legal, brand, 'Only ' + brand + ' is named.'))
        self.assertFalse(company_matches('TeamBank AG', 'inca', 'TeamBank AG is a competitor.'))
        self.assertFalse(company_matches('CHECK24 Other GmbH', 'CHECK24', 'CHECK24 Vergleichsportal Mietwagen GmbH'))
        self.assertFalse(company_matches('Elmosh GmbH', 'Elmos', 'Elmosh GmbH'))
        self.assertFalse(company_matches('Elmos GmbH', 'Elmos', 'Elmos GmbHolding'))

    def test_legal_name_on_later_pdf_page_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'cl/src'
            source.mkdir(parents=True)
            ad = folder / 'ad.pdf'
            with pymupdf.open() as doc:
                doc.new_page().insert_text((72, 72), 'Data Analyst\nCompany: Example')
                doc.new_page().insert_text((72, 72), 'Contact\nExample Services GmbH')
                doc.save(ad)
            (source/'company.tex').write_text('Example Services GmbH\\\\\nBerlin', encoding='utf-8')
            (source/'position.tex').write_text('Data Analyst', encoding='utf-8')
            validate_identity(folder, ad)

    def test_windows_sandbox_is_explicit(self):
        with patch.object(codex_cli.os, 'name', 'nt'):
            args = codex_cli.command(Settings('codex', 'codex', None, None, 10), None, Path('out'), Path('.'))
        self.assertIn('windows.sandbox="elevated"', args)
        self.assertIn('read-only', args)
        self.assertIn('features.shell_tool=false', args)

    def test_blocked_editor_stops_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            settings = Settings('codex', 'codex', None, None, 10)
            schema = editor_schema(['file.tex'])
            with patch.object(codex_cli, 'invoke', return_value=json.dumps({'status': 'blocked', 'files': {'file.tex': ''}, 'report': 'Could not read input'})):
                with self.assertRaises(ProviderFailure):
                    request_structured(codex_cli, settings, 'test', schema, folder/'agent_a', lambda result: None)
            self.assertTrue((folder/'agent_a/attempt_1/response.txt').exists())

    def test_empty_review_rejected_and_valid_review_accepted(self):
        review = {key: [] for key in ('job_analysis', 'requirements', 'cv_assessment', 'cover_letter_assessment', 'ats_keywords')}
        with self.assertRaises(ValueError):
            validate_review(review)
        validate_review(valid_review())
        review = valid_review()
        review['cv_assessment'][0]['rating'] = 9
        with self.assertRaises(ValueError):
            validate_review(review)

    def test_company_and_role_must_match_ad(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder/'cl/src'
            source.mkdir(parents=True)
            ad = folder/'ad.pdf'
            with pymupdf.open() as doc:
                doc.new_page().insert_text((72,72), 'Junior Credit Analyst\nCompany: Example Finance\nLocation: Berlin')
                doc.save(ad)
            (source/'company.tex').write_text('Wrong Company', encoding='utf-8')
            (source/'position.tex').write_text('Credit Analyst', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'company'):
                validate_identity(folder, ad)
            (source/'company.tex').write_text('Example Finance\\\\\nBerlin', encoding='utf-8')
            validate_identity(folder, ad)
            (source/'position.tex').write_text('Credit Risk Management', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'role'):
                validate_identity(folder, ad)

    def test_invalid_old_output_is_not_finished(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root/'output/job_1'
            build = root/'temp/build/job_1'
            output.mkdir(parents=True)
            (build/'agent_c').mkdir(parents=True)
            for path in (output/'yigit_coskun_cv.pdf', output/'yigit_coskun_cl.pdf', build/'hiring_review.xlsx'):
                path.touch()
            (build/'agent_c/review.json').write_text(json.dumps({'requirements': []}))
            with patch.object(run_job, 'ROOT', root):
                self.assertFalse(run_job.is_finished(1))


if __name__ == '__main__':
    unittest.main()
