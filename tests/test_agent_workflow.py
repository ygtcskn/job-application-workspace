import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pymupdf

from src.agent_workflow import CL_FILES, CV_FILES, execute_agent, parse_reply, request_structured, validate_edits
from src.providers.base import ProviderFailure, Settings
from src.cv_model import apply_edits, before_after, compact_cv, detex, new_words
from src.providers import claude_cli, codex_cli, gemini_cli
from test_pipeline_validation import valid_review


ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def test_identical_editor_inputs_and_local_writes_for_all_providers(self):
        prompts = []
        for provider in ('claude', 'codex', 'gemini'):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as tmp:
                build = Path(tmp)
                ad = build/'ad.pdf'
                with pymupdf.open() as doc:
                    doc.new_page().insert_text((72,72), 'Data Analyst\nCompany: Example')
                    doc.save(ad)
                original = dict(zip(CL_FILES, ['Old Company', 'Dear Hiring Team,', 'Data Analyst', 'In {finance}.', 'Work in {analytics}.', 'Use [data].']))
                for path, value in original.items():
                    p = build/path; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(value)
                revised = dict(original, **{'cl/src/company.tex': 'Example'})
                def invoke(settings, prompt, schema_path, stage, cwd):
                    prompts.append(prompt)
                    self.assertTrue(settings.allow_web)
                    self.assertEqual(cwd, stage)
                    self.assertIn('Data Analyst', prompt)
                    self.assertIn('Old Company', prompt)
                    self.assertEqual((build/'cl/src/company.tex').read_text(), 'Old Company')
                    return json.dumps({'status': 'complete', 'files': revised, 'report': 'Updated company'})
                adapter = SimpleNamespace(invoke=invoke)
                execute_agent(adapter, Settings(provider, 'unused', None, None, 10), build, ad, 'agent_a', ROOT/'agents/agent_a_writer.md', [])
                self.assertEqual((build/'cl/src/company.tex').read_text(), 'Example')
                self.assertTrue((build/'job_description.txt').exists())
                self.assertTrue((build/'agent_a/attempt_1/metrics.json').exists())
        self.assertEqual(prompts[0], prompts[1])
        self.assertEqual(prompts[1], prompts[2])

    def test_review_has_compiled_pdf_text_and_no_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            build=Path(tmp)
            for rel, content in [('ad.pdf','Job duties'), ('cv/resume.pdf','Candidate evidence'), ('cl/main.pdf','Motivation evidence')]:
                path=build/rel; path.parent.mkdir(parents=True,exist_ok=True)
                with pymupdf.open() as doc:
                    doc.new_page().insert_text((72,72),content); doc.save(path)
            def invoke(settings,prompt,*args):
                self.assertFalse(settings.allow_web)
                for content in ('Job duties','Candidate evidence','Motivation evidence'):
                    self.assertIn(content,prompt)
                return json.dumps(valid_review())
            execute_agent(SimpleNamespace(invoke=invoke),Settings('gemini','unused',None,None,10),build,build/'ad.pdf','agent_c',ROOT/'agents/agent_c_hiring_reviewer.md',[],ROOT/'agents/agent_c_schema.json')
            self.assertTrue((build/'agent_c/review.json').exists())

    def test_identical_cv_inputs_and_rules_for_all_providers(self):
        prompts = []
        for provider in ('claude', 'codex', 'gemini'):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as tmp:
                build = Path(tmp)
                (build / 'job_description.txt').write_text('Data analyst: Python and statistics', encoding='utf-8')
                originals = dict(zip(CV_FILES, [
                    'Economics graduate who works with Python and SQL.',
                    r'Heading\resumeItem{Analysed 20 records in Python.}End',
                    r'Project\resumeItem{Built 2 models.}End',
                    r'\textbf{Concepts}{: Data Analysis, Statistics, Machine Learning, Deep Learning, Data Visualization, Data Modeling, ETL/Data Pipelines, Model Evaluation}',
                ]))
                for path, value in originals.items():
                    target = build / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(value, encoding='utf-8')
                for name, evidence in (('education', 'Statistics coursework'), ('certificates', 'SQL certificate'), ('languages', 'German: B2')):
                    (build / f'cv/src/{name}.tex').write_text(evidence, encoding='utf-8')
                def invoke(settings, prompt, *args):
                    self.assertFalse(settings.allow_web)
                    for evidence in ('Statistics coursework', 'SQL certificate', 'German: B2'):
                        self.assertIn(evidence, prompt)
                    prompts.append(prompt)
                    # Agent B sees plain text, not LaTeX, and returns only what changes.
                    self.assertNotIn('resumeItem', prompt.split('INPUT DATA:')[1])
                    self.assertIn('Analysed 20 records in Python.', prompt)
                    return json.dumps({'status': 'complete', 'summary': '', 'bullets': [{'id': 'E1.1', 'text': 'Analysed 20 records in Python & R.'}],
                                       'order': [], 'skills': [], 'omitted': ['Snowflake'], 'note': 'Data analyst profile.'})
                execute_agent(SimpleNamespace(invoke=invoke), Settings(provider, 'unused', None, None, 10),
                              build, Path('ad.pdf'), 'agent_b', ROOT / 'agents/agent_b_optimizer.md', [])
                # Python restores the markup and escapes the ampersand.
                self.assertEqual((build / 'cv/src/experience.tex').read_text(encoding='utf-8'),
                                 r'Heading\resumeItem{Analysed 20 records in Python \& R.}End')
                self.assertIn('Snowflake', (build / 'agent_b/report.md').read_text(encoding='utf-8'))
        self.assertEqual(prompts[0], prompts[1])
        self.assertEqual(prompts[1], prompts[2])

    def test_identity_mismatch_gets_repair_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            (build / 'job_description.txt').write_text('Data Analyst\nCompany: Example\nJob description\nAnalyse data.', encoding='utf-8')
            originals = dict(zip(CL_FILES, ['Old Company', 'Dear Hiring Team,', 'Data Analyst', 'In {finance}.', 'Work in {analytics}.', 'Use [data].']))
            for path, value in originals.items():
                target = build / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value, encoding='utf-8')
            replies = [json.dumps({'status': 'complete', 'files': dict(originals, **{'cl/src/company.tex': company}), 'report': 'Updated'}) for company in ('Wrong', 'Example')]
            adapter = SimpleNamespace(invoke=Mock(side_effect=replies))
            execute_agent(adapter, Settings('claude', 'unused', None, None, 10), build, Path('ad.pdf'),
                          'agent_a', ROOT / 'agents/agent_a_writer.md', [])
            self.assertEqual(adapter.invoke.call_count, 2)
            self.assertIn('company does not match', adapter.invoke.call_args.args[1])
            self.assertEqual((build / 'cl/src/company.tex').read_text(), 'Example')

    def test_provider_failure_is_logged_without_format_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = SimpleNamespace(invoke=Mock(side_effect=ProviderFailure('Service unavailable')))
            stage = Path(tmp) / 'stage'
            with self.assertRaises(ProviderFailure):
                request_structured(adapter, Settings('gemini', 'unused', None, None, 10), 'prompt', {}, stage, lambda x: None)
            self.assertEqual(adapter.invoke.call_count, 1)
            metrics = json.loads((stage / 'attempt_1/metrics.json').read_text())
            self.assertEqual(metrics['status'], 'failed')
            self.assertEqual(metrics['error'], 'Service unavailable')

    def test_gemini_uses_shared_parser_and_restricted_policy(self):
        for allow_web in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                stage = Path(tmp)
                settings = Settings('gemini', 'unused', None, None, 10, allow_web=allow_web)
                reply = 'Prose before {"answer": 1}'
                with patch.object(gemini_cli, 'run_process', return_value=(json.dumps({'response': reply}), '')):
                    actual = gemini_cli.invoke(settings, 'prompt', stage / 'schema.json', stage, stage)
                self.assertEqual(actual, reply)
                with self.assertRaises(ValueError):
                    parse_reply(actual, {'type': 'object'})
                policy = (stage / 'tools.toml').read_text()
                self.assertIn('toolName = "*"\ndecision = "deny"', policy)
                self.assertEqual('google_web_search' in policy, allow_web)
                self.assertEqual('web_fetch' in policy, allow_web)

    def test_shared_schema_repair_is_bounded(self):
        schema={'type':'object','required':['answer'],'additionalProperties':False,'properties':{'answer':{'type':'integer'}}}
        for provider in ('claude','codex','gemini'):
            with tempfile.TemporaryDirectory() as tmp:
                adapter=SimpleNamespace(invoke=Mock(side_effect=['{"answer":"bad"}','{"answer":1}']))
                result=request_structured(adapter,Settings(provider,'unused',None,None,10),'same prompt',schema,Path(tmp)/'stage',lambda result:None)
                self.assertEqual(result,{'answer':1})
                self.assertEqual(adapter.invoke.call_count,2)
            with tempfile.TemporaryDirectory() as tmp:
                adapter=SimpleNamespace(invoke=Mock(return_value='not json'))
                with self.assertRaises(ValueError):
                    request_structured(adapter,Settings(provider,'unused',None,None,10),'prompt',schema,Path(tmp)/'stage',lambda result:None)
                self.assertEqual(adapter.invoke.call_count,2)

    def test_protected_content_and_numbers_rejected(self):
        before={'cv/src/experience.tex':r'Heading\resumeItem{Analysed 44,000 records.}End'}
        validate_edits(before,{'cv/src/experience.tex':r'Heading\resumeItem{Prepared 44,000 records.}End'})
        for value in (r'Wrong\resumeItem{Analysed 44,000 records.}End',r'Heading\resumeItem{Analysed 50,000 records.}End'):
            with self.assertRaises(ValueError):
                validate_edits(before,{'cv/src/experience.tex':value})
        with self.assertRaises(ValueError):
            validate_edits({'cl/src/part1.tex':'In {finance}.'},{'cl/src/part1.tex':'Changed {finance}.'})
        with self.assertRaises(ValueError):
            validate_edits(before,{'../../outside.tex':'bad'})

    def test_bullets_may_be_reordered_within_an_entry_only(self):
        entry=lambda *bullets: 'Head'+''.join(r'\resumeItem{'+b+'}' for b in bullets)+r'\resumeItemListEnd '
        before={'cv/src/experience.tex':entry('Analyzed 44,000 records.','Wrote SQL queries.')+entry('Built 2 models.','Prepared reports.')}
        edit=lambda text: validate_edits(before,{'cv/src/experience.tex':text})
        edit(entry('Wrote SQL queries.','Analyzed 44,000 records.')+entry('Prepared reports.','Built 2 models.'))
        # A figure cannot move to another job, and no bullet may be added or dropped.
        for value in (entry('Built 2 models.','Wrote SQL queries.')+entry('Analyzed 44,000 records.','Prepared reports.'),
                      entry('Analyzed 44,000 records.')+entry('Built 2 models.','Prepared reports.')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                edit(value)

    def test_skills_keep_every_tool_and_may_add_the_ads_tools(self):
        skills=lambda tools,concepts: (r'\textbf{Programming}{: '+tools+'} \\'+'\n'+r'\textbf{Concepts}{: '+concepts+'}')
        core='Data Analysis, Statistics, Machine Learning, Deep Learning, Data Visualization, Data Modeling, ETL/Data Pipelines'
        before={'cv/src/skills.tex':skills('Python (pandas, NumPy), R, SQL, Stata',core+', Cloud Computing')}
        ad='We use SQL, Snowflake, Looker, dbt, Airflow and Spark.'
        edit=lambda tools,concepts,banned=(): validate_edits(before,{'cv/src/skills.tex':skills(tools,concepts)},banned=banned,ad_text=ad)
        # A tool the ad names may join the line, even one the analysis found no CV evidence for.
        edit('SQL, Snowflake, Python (pandas, NumPy), R, Stata',core,banned=['Snowflake'])
        edit('SQL, Python (pandas, NumPy), R, Stata','Statistics, Data Analysis, '+core.split(', ',2)[2]+', Reporting')
        for tools,concepts in (('SQL, Python (pandas, NumPy), R','x'),                     # a tool removed
                               ('SQL, Python (pandas, NumPy), R, Stata, SPSS',core),       # a tool the ad does not name
                               ('SQL, Python (pandas, NumPy), R, Stata, Snowflake, Looker, dbt, Airflow, Spark',core),  # more than four added
                               ('SQL, Python (pandas), R, Stata',core),                    # an entry reworded
                               ('Python (pandas, NumPy), R, SQL, Stata',core.replace('Statistics, ','')),  # a core concept dropped
                               ('Python (pandas, NumPy), R, SQL, Stata',core+', Reporting, Reporting')):
            with self.subTest(tools=tools,concepts=concepts), self.assertRaises(ValueError):
                edit(tools,concepts)
        # The analysis found no evidence for A/B testing, so it cannot be added as a concept.
        with self.assertRaisesRegex(ValueError,'no evidence'):
            edit('Python (pandas, NumPy), R, SQL, Stata',core+', A/B Testing',banned=['A/B testing'])

    def test_unsupported_terms_are_blocked(self):
        before={'cv/src/summary_text.tex':'Economics graduate who works with Python and SQL.'}
        edit=lambda summary: validate_edits(before,{'cv/src/summary_text.tex':summary},banned=['Snowflake'])
        edit('Economics graduate who works with SQL and Python.')
        with self.assertRaisesRegex(ValueError,'no evidence'):
            edit('Economics graduate who works with SQL and Snowflake.')

    CV={'cv/src/summary_text.tex':'Economics graduate.\n',
        'cv/src/experience.tex':(r'\resumeSubheading{Working Student | Research}{Oct 2024 -- Sep 2026}{IAB}{Nürnberg}'
                                 r'\resumeItem{Analyzed 44,000 records in R \& Stata.}\resumeItem{Wrote SQL queries.}\resumeItemListEnd '
                                 r'\resumeSubheading{Intern}{Jun 2021 -- May 2022}{Bank}{Istanbul}'
                                 r'\resumeItem{Prepared reports.}\resumeItemListEnd'),
        'cv/src/skills.tex':r'\textbf{Programming}{: Python (pandas, NumPy), R, SQL} \\ \textbf{Concepts}{: Statistics, Reporting}'}
    NO_CHANGE={'status':'complete','summary':'','bullets':[],'order':[],'skills':[],'omitted':[],'note':''}

    def test_agents_get_the_cv_as_plain_data_with_bullet_ids(self):
        cv=compact_cv(self.CV)
        self.assertEqual(cv['summary'],'Economics graduate.')
        self.assertEqual(cv['experience'][0]['heading'],'Working Student | Research — Oct 2024 – Sep 2026 — IAB — Nürnberg')
        self.assertEqual(cv['experience'][0]['bullets'],[{'id':'E1.1','text':'Analyzed 44,000 records in R & Stata.'},{'id':'E1.2','text':'Wrote SQL queries.'}])
        self.assertEqual(cv['experience'][1]['bullets'][0]['id'],'E2.1')
        self.assertEqual(cv['skills'][0],{'label':'Programming','items':['Python (pandas, NumPy)','R','SQL']})
        self.assertNotIn('\\',json.dumps(cv,ensure_ascii=False))
        self.assertEqual(detex(r'\textbf{German}{: C1} \\ 50\,\% of A \& B'),'German: C1\n50 % of A & B')

    def test_only_the_changes_are_applied_and_markup_is_restored(self):
        self.assertEqual(apply_edits(self.CV,self.NO_CHANGE),self.CV)
        reply=dict(self.NO_CHANGE,summary='Economist & analyst.',bullets=[{'id':'E1.2','text':'Wrote SQL queries for 5% of cases.'}],
                   order=[{'entry':'E1','ids':['E1.2','E1.1']}],skills=[{'label':'Programming','items':['SQL','Python (pandas, NumPy)','R']}])
        files=apply_edits(self.CV,reply)
        self.assertEqual(files['cv/src/summary_text.tex'],'Economist \\& analyst.\n')
        # The reworded bullet moved first; the untouched one kept its LaTeX; the other entry is as it was.
        self.assertIn(r'\resumeItem{Wrote SQL queries for 5\% of cases.}\resumeItem{Analyzed 44,000 records in R \& Stata.}\resumeItemListEnd',files['cv/src/experience.tex'])
        self.assertIn(r'\resumeItem{Prepared reports.}',files['cv/src/experience.tex'])
        self.assertIn(r'\textbf{Programming}{: SQL, Python (pandas, NumPy), R}',files['cv/src/skills.tex'])
        self.assertIn(r'\textbf{Concepts}{: Statistics, Reporting}',files['cv/src/skills.tex'])
        for bad in (dict(bullets=[{'id':'E9.1','text':'New bullet.'}]),                       # no such bullet
                    dict(order=[{'entry':'E1','ids':['E1.1']}]),                               # a bullet dropped
                    dict(order=[{'entry':'E1','ids':['E1.1','E2.1']}]),                        # a bullet from another job
                    dict(bullets=[{'id':'E1.1','text':r'Analyzed \textbf{44,000} records.'}]),  # LaTeX
                    dict(bullets=[{'id':'E1.1','text':'x'},{'id':'E1.1','text':'y'}]),
                    dict(skills=[{'label':'Databases','items':['Snowflake']}])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                apply_edits(self.CV,dict(self.NO_CHANGE,**bad))

    def test_pipeline_compares_before_and_after_itself(self):
        self.assertEqual(before_after(self.CV,self.CV),'Nothing changed.')
        final=apply_edits(self.CV,dict(self.NO_CHANGE,summary='Economics graduate and data analyst.',
                                      bullets=[{'id':'E1.2','text':'Wrote SQL queries to extract data.'}],order=[{'entry':'E1','ids':['E1.2','E1.1']}]))
        text=before_after(self.CV,final)
        self.assertIn('**Working Student | Research — Oct 2024 – Sep 2026 — IAB — Nürnberg**',text)
        self.assertIn('- New bullet order, by old position: 2, 1',text)
        self.assertIn('- Before: Wrote SQL queries.\n  After:  Wrote SQL queries to extract data.',text)
        self.assertIn('- Before: Economics graduate.\n  After:  Economics graduate and data analyst.',text)
        self.assertNotIn('Prepared reports',text)   # unchanged entries are not listed
        # New words are split into those the ad uses and those from nowhere.
        self.assertEqual(new_words(self.CV,final,'We need a data analyst.'),(['analyst','data'],['and','extract']))

    def test_summary_is_one_paragraph_with_cv_figures_only(self):
        summary='Economics graduate with two years of research experience in Python and SQL.\n'
        before={'cv/src/summary_text.tex':summary,'cv/src/experience.tex':r'H\resumeItem{Analyzed 44,000 records.}E'}
        edit=lambda text: validate_edits(before,dict(before,**{'cv/src/summary_text.tex':text}))
        edit('Economics graduate who analyzed 44,000 records in Python and SQL.')
        # A figure from a read-only file (language level B2) is evidence too.
        with self.assertRaisesRegex(ValueError,'number'):
            edit('Economics graduate with German at B2 level, Python and SQL.')
        validate_edits(before,dict(before,**{'cv/src/summary_text.tex':'Economics graduate with German at B2 level, Python and SQL.'}),['German: B2'])
        for value in ('Economics graduate.\n\nWorks with Python.',summary*2,'Economics graduate with 5 years of experience.'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                edit(value)

    def test_invalid_edits_are_never_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage=Path(tmp)/'stage'
            target=Path(tmp)/'file.tex';target.write_text('original')
            adapter=SimpleNamespace(invoke=Mock(return_value='{"files":{"../../outside.tex":"bad"}}'))
            schema={'type':'object','properties':{},'additionalProperties':False}
            with self.assertRaises(ValueError):
                request_structured(adapter,Settings('codex','unused',None,None,10),'prompt',schema,stage,lambda x:None)
            self.assertEqual(target.read_text(),'original')

    def test_claude_and_codex_tools_are_restricted(self):
        s=Settings('claude','unused',None,None,10)
        args=claude_cli.command(s)
        self.assertEqual(args[args.index('--tools')+1],'')
        s.allow_web=True
        args=claude_cli.command(s)
        self.assertEqual(args[args.index('--tools')+1],'WebSearch,WebFetch')
        args=codex_cli.command(s,None,Path('out'),Path('.'))
        self.assertIn('features.shell_tool=false',args)
        self.assertIn('read-only',args)


if __name__=='__main__':
    unittest.main()
