import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf

from src.agent_workflow import CL_FILES, CV_FILES, execute_agent, request_structured, validate_edits
from src.providers.base import Settings
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
