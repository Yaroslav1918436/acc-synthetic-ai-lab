"""Offline exact-workflow tests. No GitHub Actions dispatch or network requests."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'acc-public-synthetic-v075.yml'
DOCUMENT = yaml.load(WORKFLOW.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
TASK = 'lab-artifact-001'
SOURCE_SHA = '1ef077c75ce964432b4031dc96ce20ffcf523c51447254e7a1d4ac3a6274da21'
OUTPUT_SHA = '5ec53e7bccffb2017b87937d28e921d07d6957c5b1d6e5bcde061bfb35c35226'
COMMIT = '71a19a394f99fb37b1af2bc589faf5dc1b5ed6af'
SCRIPT = DOCUMENT['jobs']['synthetic']['steps'][0]['run']


def execute(tempdir: Path, **overrides) -> subprocess.CompletedProcess:
    env = {
        'PATH': os.environ['PATH'],
        'RUNNER_TEMP': str(tempdir),
        'ACC_TASK_ID': TASK,
        'ACC_INPUT_SHA256': SOURCE_SHA,
        'GITHUB_REPOSITORY': 'Yaroslav1918436/acc-synthetic-ai-lab',
        'GITHUB_RUN_ID': '123456789',
        'GITHUB_SHA': COMMIT,
        'GITHUB_STEP_SUMMARY': str(tempdir / 'summary.txt'),
    }
    env.update(overrides)
    return subprocess.run(['bash', '-e', '-o', 'pipefail'], input=SCRIPT, text=True,
                          capture_output=True, env=env, timeout=15, shell=False)


class SyntheticPublicWorkflowContract(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='acc-v075-')
        self.addCleanup(self.folder.cleanup)
        self.directory = Path(self.folder.name)

    def test_manual_dispatch_only_no_push_or_schedule(self):
        self.assertEqual(set(DOCUMENT['on']), {'workflow_dispatch'})
        self.assertEqual(set(DOCUMENT['on']['workflow_dispatch']['inputs']), {'task_id','source_sha256'})

    def test_standard_public_runner_and_bounded_time(self):
        self.assertEqual(DOCUMENT['jobs']['synthetic']['runs-on'], 'ubuntu-latest')
        self.assertEqual(DOCUMENT['jobs']['synthetic']['timeout-minutes'], '5')
        self.assertEqual(DOCUMENT['permissions'], {'contents':'read'})

    def test_workflow_must_be_this_exact_public_repo(self):
        self.assertIn("github.repository == 'Yaroslav1918436/acc-synthetic-ai-lab'",DOCUMENT['jobs']['synthetic']['if'])

    def test_no_download_actions_external_build_tools_or_private_checkout(self):
        steps = DOCUMENT['jobs']['synthetic']['steps']
        self.assertEqual(len(steps), 1)
        self.assertNotIn('uses', steps[0])
        for suspicious in ('actions/checkout','curl ','wget ','pip install','npm install',
                           'GH_TOKEN','PRIVATE_KEY','fetch(','subprocess','socket.','requests.'):
            self.assertNotIn(suspicious, SCRIPT)

    def test_allowed_synthetic_case_passes_with_full_receipt(self):
        result = execute(self.directory)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt_line = next(x for x in result.stdout.splitlines() if x.startswith('ACC_PUBLIC_SYNTHETIC_RECEIPT='))
        record = json.loads(receipt_line.partition('=')[2])
        self.assertEqual(record['task_id'], TASK)
        self.assertEqual(record['source_sha256'], SOURCE_SHA)
        self.assertEqual(record['candidate_sha256'], OUTPUT_SHA)
        self.assertEqual(record['run_id'], '123456789')
        self.assertEqual(record['run_commit'], COMMIT)
        self.assertEqual(record['result'], 'SYNTHETIC_NEEDS_REVIEW')
        self.assertFalse(record['private_code_used'])
        self.assertFalse(record['private_rc83_executed'])
        self.assertFalse(record['sandbox_namespace_proven'])
        self.assertFalse(record['ai_model_run'])
        receipt_hash = hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':')).encode()).hexdigest().upper()
        self.assertIn('ACC_PUBLIC_SYNTHETIC_RECEIPT_SHA256='+receipt_hash,result.stdout)
        self.assertIn('Receipt SHA-256: '+receipt_hash,(self.directory/'summary.txt').read_text())
        self.assertEqual([p.name for p in self.directory.iterdir()], ['summary.txt'])

    def assert_rejected(self, **env):
        result = execute(self.directory, **env)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('ACC_PUBLIC_SYNTHETIC_RECEIPT=',result.stdout)
        self.assertNotIn('ACC_PUBLIC_SYNTHETIC_NEEDS_REVIEW',result.stdout)

    def test_private_task_cannot_run(self):
        self.assert_rejected(ACC_TASK_ID='rc83-issue-257')

    def test_other_public_task_cannot_run(self):
        self.assert_rejected(ACC_TASK_ID='lab-external-002')

    def test_wrong_input_sha_rejected(self):
        self.assert_rejected(ACC_INPUT_SHA256='f'*64)

    def test_private_repo_name_rejected(self):
        self.assert_rejected(GITHUB_REPOSITORY='synthetic-example/private-code-repo')

    def test_wrong_public_repo_rejected(self):
        self.assert_rejected(GITHUB_REPOSITORY='someone/other')

    def test_invalid_run_id_rejected(self):
        self.assert_rejected(GITHUB_RUN_ID='not-an-id')

    def test_invalid_run_commit_rejected(self):
        self.assert_rejected(GITHUB_SHA='not-a-sha')

    def test_injection_like_string_treated_as_data(self):
        self.assert_rejected(ACC_TASK_ID="$(touch /tmp/acc-should-never-exist)")
        self.assertFalse(Path('/tmp/acc-should-never-exist').exists())

    def test_no_runner_temp_fails_closed(self):
        self.assert_rejected(RUNNER_TEMP='/missing/acc-public-runner-dir')

    def test_preexisting_summary_gets_append_not_overwritten(self):
        summary = self.directory/'summary.txt'
        summary.write_text('PREVIOUS CONTENT\n')
        result = execute(self.directory)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(summary.read_text().startswith('PREVIOUS CONTENT\n'))

    def test_synthetic_candidate_is_exact_fixed_bytes(self):
        original = b'export function syntheticAnswer() { return 1; }\n'
        candidate = original.replace(b'return 1;',b'return 2;')
        self.assertEqual(hashlib.sha256(original).hexdigest(), SOURCE_SHA)
        self.assertEqual(hashlib.sha256(candidate).hexdigest(), OUTPUT_SHA)

    def test_no_extra_workflows_or_default_action_triggers(self):
        workflows=list((ROOT/'.github'/'workflows').glob('*.yml'))
        self.assertEqual(workflows,[WORKFLOW])
        self.assertIn('cancel-in-progress: false',WORKFLOW.read_text())


if __name__=='__main__': unittest.main()
