import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import docker_boundary_probe as b

class BoundaryGateTests(unittest.TestCase):
    def test_inside_container_synthetic_suite_has_six_real_boundary_assertions(self):
        ast.parse(b.SECURITY_TESTS)
        self.assertEqual(b.SECURITY_TESTS.count('    def test_'),6)
        for important in ('os.geteuid()','connect_ex', 'os.statvfs',
                          'CapEff','NoNewPrivs','GITHUB_TOKEN'):
            self.assertIn(important,b.SECURITY_TESTS)

    def test_exact_ref_and_public_repo_preflight(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'.acc-public-synthetic-only').write_text('ACC_PUBLIC_SYNTHETIC_ONLY_V1\n')
            (root/'.acc-docker-boundary-trigger').write_text(b.TRIGGER)
            with patch.dict('os.environ',{'GITHUB_REPOSITORY':b.REPO,
                'GITHUB_REF':b.REF,'ACC_PUBLIC_REPO':'true'}):
                b.context_gate(root)
            with patch.dict('os.environ',{'GITHUB_REPOSITORY':'someone/private',
                'GITHUB_REF':b.REF,'ACC_PUBLIC_REPO':'true'}):
                with self.assertRaises(b.Blocked): b.context_gate(root)

    def test_docker_command_no_privileged_or_host_network(self):
        from scripts.synthetic_docker_qa import docker_command
        with tempfile.TemporaryDirectory() as td:
            args=docker_command(Path(td))
        self.assertIn('--network=none',args)
        self.assertIn('--read-only',args)
        self.assertIn('--cap-drop=ALL',args)
        self.assertNotIn('--privileged',args)
        self.assertIn('--user=65534:65534',args)
        self.assertNotIn('--network=host',args)

if __name__=='__main__': unittest.main()
