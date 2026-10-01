"""Opt-in live CLI checks using synthetic data only; not part of unittest discovery.

Run from the project root: python tests/smoke_providers.py [claude codex gemini].
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml
from src.agent_workflow import editor_schema, request_structured
from src.providers.base import Settings, find_executable
from src.run_job import ADAPTERS, chosen


def main():
    config = yaml.safe_load((ROOT / 'run_settings.yaml').read_text(encoding='utf-8'))
    output = ROOT / 'temp' / ('provider_smoke_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    providers = sys.argv[1:] or list(ADAPTERS)

    def check(provider):
        try:
            defaults = config[provider]
            settings = Settings(provider, find_executable(provider, provider), chosen(defaults.get('model')),
                                chosen(defaults.get('effort')), 180)
            for allow_web in (False, True):
                settings.allow_web = allow_web
                stage = output / provider / ('web_allowed' if allow_web else 'no_tools')
                def validate(result):
                    if result['files']['example.tex'] != 'Example Company':
                        raise ValueError('Synthetic content was changed')
                request_structured(ADAPTERS[provider], settings,
                                   'Synthetic integration test. Do not use any tools. Return status complete, '
                                   'files with example.tex containing exactly Example Company, and report OK.',
                                   editor_schema(['example.tex']), stage, validate)
            return {'provider': provider, 'status': 'passed'}
        except Exception as error:
            return {'provider': provider, 'status': 'failed', 'error': str(error)}

    output.mkdir(parents=True)
    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        results = list(pool.map(check, providers))
    (output / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results, indent=2))
    print('Logs:', output)
    return int(any(r['status'] != 'passed' for r in results))


if __name__ == '__main__':
    sys.exit(main())
