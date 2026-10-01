import json
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from src import run_job
from src.agent_workflow import CV_FILES, CV_READ_ONLY
from src.providers.base import Settings
from test_pipeline_validation import valid_analysis, valid_review


class ReviewModeTests(unittest.TestCase):
    def run_fixture(self, root, review=True, failure=False, ad_text='Job description\nWe are hiring an analyst for our team.'):
        template = root / 'input/cl/src'
        template.mkdir(parents=True, exist_ok=True)
        for folder in ('cl_de', 'cv_de'):
            (root / 'input' / folder).mkdir(exist_ok=True)
        for field in ('company', 'towhom', 'position', 'part1', 'part2', 'part3'):
            (template / f'{field}.tex').write_text('Template', encoding='utf-8')
        (root / 'input/cv/src').mkdir(parents=True, exist_ok=True)
        for path in CV_FILES + CV_READ_ONLY:
            (root / 'input' / path).write_text('Wrote SQL queries.', encoding='utf-8')
        ads = root / 'ads'
        ads.mkdir(exist_ok=True)
        (ads / 'job_description_1.pdf').write_bytes(b'fixture ad')
        agents = []
        self.notes, self.salutation = {}, None

        def agent(settings, build, ad, name, instructions, notes, *args):
            agents.append(name)
            self.notes[name] = notes
            if name == 'agent_j':
                return json.dumps(valid_analysis())
            if name == 'agent_a':
                self.salutation = (build / 'cl/src/towhom.tex').read_text(encoding='utf-8')
                self.assertFalse((build / 'cl_de').exists() or (build / 'cv_de').exists())
                (build / 'cl/src/company.tex').write_text('Example', encoding='utf-8')
            if name == 'agent_c':
                if failure:
                    raise ValueError('Invalid review')
                (build / name).mkdir()
                reply = json.dumps(valid_review())
                (build / name / 'review.json').write_text(reply, encoding='utf-8')
                return reply
            return 'Complete'

        def compile_pdf(folder, tex):
            folder.mkdir(exist_ok=True)
            pdf = folder / Path(tex).with_suffix('.pdf')
            pdf.write_bytes(b'fixture compiled pdf')
            return pdf

        with ExitStack() as stack:
            stack.enter_context(patch.object(run_job, 'ROOT', root))
            stack.enter_context(patch.object(run_job, 'JOB_ADS', ads))
            stack.enter_context(patch.object(run_job, 'pdf_text', return_value=ad_text))
            stack.enter_context(patch.object(run_job, 'run_agent', side_effect=agent))
            stack.enter_context(patch.object(run_job, 'validate_identity'))
            stack.enter_context(patch.object(run_job, 'page_count', return_value=1))
            stack.enter_context(patch.object(run_job, 'build_pdf', side_effect=compile_pdf))
            result = run_job.run_job(1, Settings('claude', 'unused', None, None, 10), review=review)
            self.completion = json.loads((run_job.output_dir(1) / 'completion.json').read_text(encoding='utf-8'))
            self.assertTrue(run_job.is_finished(1, require_review=False))
            self.assertEqual(run_job.is_finished(1), review)
            shutil.rmtree(run_job.build_dir(1))
            self.assertTrue(run_job.is_finished(1, require_review=False))
            self.assertEqual(run_job.is_finished(1), review)
            output = run_job.output_dir(1)
            (output / 'yigit_coskun_cv.pdf').write_bytes(b'changed after completion')
            self.assertFalse(run_job.is_finished(1, require_review=False))
        return agents, result

    def test_review_runs_all_three_and_writes_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents, result = self.run_fixture(root)
            self.assertEqual(agents, ['agent_j', 'agent_a', 'agent_b', 'agent_c'])
            self.assertTrue((root / 'output/job_1/hiring_review.xlsx').is_file())
            self.assertTrue(result['coverage'])

    def test_default_publishes_pdfs_without_review_and_records_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents, result = self.run_fixture(root, review=False)
            self.assertEqual(agents, ['agent_j', 'agent_a', 'agent_b'])
            self.assertFalse((root / 'temp/build/job_1/hiring_review.xlsx').exists())
            self.assertFalse((root / 'temp/build/job_1/agent_c').exists())
            self.assertEqual(result['coverage'], '')
            self.assertIn('job match 75%; CV showed 52% before tailoring; AI ', result['detail'])
            # The CV's job title is the cover letter's role, copied without an AI call.
            self.assertIn('**Job title**\n- Template', (root / 'output/job_1/tailoring_report.md').read_text(encoding='utf-8'))
            report = (root / 'output/job_1/tailoring_report.md').read_text(encoding='utf-8')
            self.assertIn('## E. Final verification', report)
            self.assertIn('Complete', report)  # Agent B's own report of its changes

    def test_german_ad_gets_german_templates_and_language_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'input/cl_de/src').mkdir(parents=True)
            (root / 'input/cl_de/src/towhom.tex').write_text('Sehr geehrte Damen und Herren,', encoding='utf-8')
            agents, _ = self.run_fixture(root, ad_text='Job description\nWir suchen dich für unser Team und bieten dir flexible Arbeitszeiten.')
            self.assertEqual(self.salutation, 'Sehr geehrte Damen und Herren,')
            self.assertEqual(self.completion['language'], 'de')
            for name in agents:
                self.assertIn('Application language: German', ' '.join(self.notes[name]))

    def test_english_ad_keeps_english_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'input/cl_de/src').mkdir(parents=True)
            (root / 'input/cl_de/src/towhom.tex').write_text('Sehr geehrte Damen und Herren,', encoding='utf-8')
            self.run_fixture(root)
            self.assertEqual(self.salutation, 'Template')
            self.assertEqual(self.completion['language'], 'en')
            self.assertIn('Application language: English', ' '.join(self.notes['agent_a']))

    def test_unchanged_german_letter_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'input/cl_de/src').mkdir(parents=True)
            (root / 'input/cl_de/src/company.tex').write_text('Example', encoding='utf-8')
            with self.assertRaisesRegex(run_job.JobFailure, 'unchanged from the template'):
                self.run_fixture(root, ad_text='Job description\nWir suchen Sie für unser Team und die Analyse der Daten.')

    def test_failed_review_never_marks_job_complete_or_publishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'Invalid review'):
                self.run_fixture(root, failure=True)
            self.assertFalse((root / 'temp/build/job_1/completion.json').exists())
            self.assertFalse((root / 'output/job_1').exists())


if __name__ == '__main__':
    unittest.main()
