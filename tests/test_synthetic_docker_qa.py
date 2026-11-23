import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import synthetic_docker_qa as d


class SyntheticDockerGateTests(unittest.TestCase):
    def test_publisher_pinned_image_and_lockdown_flags(self):
        with tempfile.TemporaryDirectory() as td:
            args=d.docker_command(Path(td))
        self.assertIn('--network=none',args)
        self.assertIn('--read-only',args)
        self.assertIn('--cap-drop=ALL',args)
        self.assertIn('--security-opt=no-new-privileges',args)
        self.assertIn('--user=65534:65534',args)
        self.assertIn('--pull=never',args)
        self.assertIn(d.IMAGE,args)
        self.assertIn('sha256:',d.IMAGE)
        self.assertNotIn('--privileged',args)
        self.assertEqual(args[-10:],['--env=PYTHONDONTWRITEBYTECODE=1',d.IMAGE,'python','-B','-m','unittest','discover','-s','tests','-q'])

    def test_generated_code_never_becomes_host_shell(self):
        bad=['__import__("os").system("id")','open("/etc/passwd")',
             'a.__class__','[x for x in range(3)]',
             'a+b; __import__("os")','a+b # comment', 'a+b\nprint(1)']
        for raw in bad:
            with self.subTest(raw=raw),self.assertRaises(d.SyntheticBlocked):
                d.bounded_expression({'raw_preview':raw,'raw_length':len(raw),
                                      'accepted_expression':True})

    def test_real_qwen15b_expressions_from_public_evidence(self):
        for raw,expected in [('return a+b','a + b'),('return n % 2 == 0','n % 2 == 0'),
                             ('return max(lo, min(hi, n))','max(lo, min(hi, n))')]:
            self.assertEqual(d.bounded_expression({'raw_preview':raw,'raw_length':len(raw),
                                                    'accepted_expression':True}),expected)

    def test_reject_incomplete_or_wrong_model_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'result.json'
            path.write_text(json.dumps({'model_sha256':'wrong','model_inference':'PERFORMED'}))
            with self.assertRaisesRegex(d.SyntheticBlocked,'model_evidence_incomplete_or_wrong'):
                d.verified_model_result(path)

    def test_no_docker_blocks_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'result.json'
            with patch.object(d,'check_environment'),\
                 patch.object(d,'verified_model_result',return_value=['a+b']*3),\
                 patch.object(d.shutil,'which',return_value=None),\
                 patch.object(d,'run_docker',side_effect=AssertionError('must not run')):
                self.assertEqual(d.evaluate(out,Path(tmp)/'input.json'),1)
            obj=json.loads(out.read_text())
            self.assertEqual(obj['status'],'BLOCKED')
            self.assertEqual(obj['reason'],'docker_missing')
            self.assertEqual(obj['docker_isolation'],'NOT_VERIFIED')

    def test_fixture_has_no_real_repository_or_client_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            f=d.safe_fixture(root,'add-green','def add(a,b):\n    return a+b\n',
                             'from src.logic import add\nimport unittest\n')
            self.assertTrue((f/'src'/'logic.py').is_file())
            self.assertFalse((f/'.git').exists())
            self.assertFalse((f/'AGENTS.md').exists())
            self.assertEqual((f/'src'/'logic.py').stat().st_mode & 0o222,0o200)  # writable owner before bind; read-only in container


if __name__=='__main__': unittest.main()
