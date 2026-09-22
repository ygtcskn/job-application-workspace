import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
from src import run_job
from src.providers import codex_cli
from src.providers.base import ProviderFailure, Settings
from src.validation import company_matches, validate_identity, validate_review
from src.agent_workflow import editor_schema, request_structured


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


class PipelineValidationTests(unittest.TestCase):
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
            with fitz.open() as doc:
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
            with fitz.open() as doc:
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
