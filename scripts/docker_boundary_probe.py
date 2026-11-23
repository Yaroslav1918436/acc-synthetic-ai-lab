"""One-shot public synthetic Docker boundary smoke, without model download.

This is a test of isolation settings, not a penetration test or production RC83 QA.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from scripts.synthetic_docker_qa import IMAGE, safe_fixture, docker_command, run_docker

REPO='Yaroslav1918436/acc-synthetic-ai-lab'
REF='refs/heads/acc-synthetic-docker-boundary-20261123'
TRIGGER='ACC_SYNTHETIC_DOCKER_BOUNDARY_ONCE_20261123\n'
SECURITY_TESTS = '''import os
from pathlib import Path
import re
import socket
import unittest

class TestDockerBoundaries(unittest.TestCase):
    def test_unprivileged_user(self):
        self.assertEqual(os.geteuid(),65534)

    def test_no_external_network(self):
        sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        sock.settimeout(1.0)
        try:
            try: result=sock.connect_ex(("1.1.1.1",443))
            except OSError: result=-1
            self.assertNotEqual(result,0)
        finally: sock.close()

    def test_rootfs_and_source_mount_readonly(self):
        self.assertNotEqual(os.statvfs("/").f_flag & os.ST_RDONLY,0)
        self.assertNotEqual(os.statvfs("/work").f_flag & os.ST_RDONLY,0)

    def test_no_linux_capabilities(self):
        status=Path("/proc/self/status").read_text()
        caps=re.search(r"^CapEff:\\s*([0-9a-fA-F]+)$",status,re.M)
        self.assertIsNotNone(caps)
        self.assertEqual(int(caps.group(1),16),0)

    def test_no_new_privileges(self):
        status=Path("/proc/self/status").read_text()
        value=re.search(r"^NoNewPrivs:\\s*([01])$",status,re.M)
        self.assertIsNotNone(value)
        self.assertEqual(value.group(1),"1")

    def test_no_host_authentication_variables(self):
        for key in ("GITHUB_TOKEN","GH_TOKEN","HF_TOKEN","OPENAI_API_KEY",
                    "CLOUDFLARE_API_TOKEN","TG_BOT_TOKEN","GITHUB_ACTIONS"):
            self.assertNotIn(key,os.environ)
'''

class Blocked(Exception): pass


def context_gate(root: Path) -> None:
    if os.environ.get('GITHUB_REPOSITORY')!=REPO:
        raise Blocked('wrong_repo')
    if os.environ.get('GITHUB_REF')!=REF:
        raise Blocked('wrong_branch')
    if os.environ.get('ACC_PUBLIC_REPO')!='true':
        raise Blocked('private_repo_disallowed')
    if (root/'.acc-public-synthetic-only').read_text()!='ACC_PUBLIC_SYNTHETIC_ONLY_V1\n':
        raise Blocked('missing_synthetic_marker')
    if (root/'.acc-docker-boundary-trigger').read_text()!=TRIGGER:
        raise Blocked('wrong_one_shot_trigger')
    if any((root/x).exists() for x in ('AGENTS.md','.env','private','rc83')):
        raise Blocked('private_tree_marker_present')


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    result={'kind':'acc_public_docker_boundary_smoke','model_inference':'NOT_RUN',
            'image':IMAGE,'status':'BLOCKED','suite_size':7,'private_code_used':False}
    code=1
    try:
        context_gate(Path.cwd())
        if not shutil.which('docker'):
            raise Blocked('docker_missing')
        pull=run_docker(['docker','pull',IMAGE],timeout=100)
        if pull.returncode!=0:
            raise Blocked('digest_pinned_image_unavailable')
        with tempfile.TemporaryDirectory(prefix='acc-boundary-') as tmp:
            root=Path(tmp);root.chmod(0o755)
            smoke='import unittest\nclass TestSmoke(unittest.TestCase):\n  def test_ok(self): self.assertEqual(1+1,2)\n'
            fixture=safe_fixture(root,'pure-synthetic','def add(a,b):\n    return a+b\n',smoke)
            security=fixture/'tests'/'test_security.py'
            security.write_text(SECURITY_TESTS,encoding='utf-8')
            security.chmod(0o644)
            r=run_docker(docker_command(fixture),timeout=55)
            log=(r.stdout+'\n'+r.stderr)[-4000:]
        if r.returncode or 'Ran 7 tests' not in log:
            raise Blocked('container_boundaries_failed')
        result.update(status='SYNTHETIC_BOUNDARIES_PASS',suite_passed=7,
                      network_disabled_in_container=True,
                      readonly_mounts_in_container=True,
                      capabilities_zero_in_container=True,
                      no_new_privileges_in_container=True,
                      secrets_not_exposed_in_container=True)
        code=0
    except (Blocked,subprocess.TimeoutExpired,ValueError,OSError) as exc:
        result.update(reason=str(exc) if isinstance(exc,Blocked) else type(exc).__name__)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('ACC synthetic Docker boundary:',result['status'],result.get('reason',''))
    return code

if __name__=='__main__': raise SystemExit(main())
