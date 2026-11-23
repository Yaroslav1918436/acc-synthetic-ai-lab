"""Safety tests; no network, no model, no private source."""
import importlib.util
import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("trial", BASE/"scripts"/"synthetic_model_trial.py")
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)

class SyntheticOnlyTests(unittest.TestCase):
    def test_only_public_repository(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "GITHUB_EVENT_NAME":"push", "GITHUB_REF":trial.PUBLIC_REF,
            "GITHUB_REPOSITORY":"wrong/repo", "ACC_PUBLIC_REPO":"true"}):
            with self.assertRaisesRegex(trial.PilotBlocked, "wrong_repo"):
                trial.check_environment(Path(tmp))

    def test_only_public_branch(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "GITHUB_EVENT_NAME":"push", "GITHUB_REF":"refs/heads/main",
            "GITHUB_REPOSITORY":trial.PUBLIC_REPO, "ACC_PUBLIC_REPO":"true"}):
            with self.assertRaisesRegex(trial.PilotBlocked, "wrong_branch"):
                trial.check_environment(Path(tmp))

    def test_block_when_repository_is_private(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "GITHUB_EVENT_NAME":"push", "GITHUB_REF":trial.PUBLIC_REF,
            "GITHUB_REPOSITORY":trial.PUBLIC_REPO, "ACC_PUBLIC_REPO":"false"}):
            with self.assertRaisesRegex(trial.PilotBlocked, "not_public"):
                trial.check_environment(Path(tmp))

    def test_one_shot_trigger_is_mandatory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/".acc-public-synthetic-only").write_text("ACC_PUBLIC_SYNTHETIC_ONLY_V1\n")
            with patch.dict(os.environ, {"GITHUB_EVENT_NAME":"push",
                "GITHUB_REF":trial.PUBLIC_REF,"GITHUB_REPOSITORY":trial.PUBLIC_REPO,
                "ACC_PUBLIC_REPO":"true"}):
                with self.assertRaisesRegex(trial.PilotBlocked,"one_shot_trigger_missing"):
                    trial.check_environment(root)
                (root/".acc-trial-trigger").write_text("invalid\n")
                with self.assertRaisesRegex(trial.PilotBlocked,"one_shot_trigger_missing"):
                    trial.check_environment(root)
                (root/".acc-trial-trigger").write_text(trial.TRIGGER)
                trial.check_environment(root)

    def test_reject_forbidden_private_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/".acc-public-synthetic-only").write_text("ACC_PUBLIC_SYNTHETIC_ONLY_V1\n")
            (root/".acc-trial-trigger").write_text(trial.TRIGGER)
            (root/"AGENTS.md").write_text("private rules")
            with patch.dict(os.environ, {"GITHUB_EVENT_NAME":"push",
                "GITHUB_REF":trial.PUBLIC_REF,"GITHUB_REPOSITORY":trial.PUBLIC_REPO,
                "ACC_PUBLIC_REPO":"true"}):
                with self.assertRaisesRegex(trial.PilotBlocked,"private_data_marker_present"):
                    trial.check_environment(root)

    def test_diagnostic_preview_is_bounded_and_ascii_only(self):
        self.assertEqual(trial.synthetic_response_preview("a+b\n\x00"), "a+b  ")
        self.assertEqual(trial.synthetic_response_preview("x"*300), "x"*240)
        self.assertEqual(trial.synthetic_response_preview(None), "")
        self.assertEqual(trial.synthetic_response_preview("q\u2603"), "q ")

    def test_no_code_execution_or_imports(self):
        self.assertEqual(trial.safe_expression("a+b"),"a + b")
        self.assertEqual(trial.safe_expression("  a+b\n"),"a + b")
        self.assertEqual(trial.safe_expression("max(lo,min(hi,n))"),"max(lo, min(hi, n))")
        for bad in ['__import__("os")','os.system("id")','[x for x in range(3)]',
                    'import os','a+b\nprint(1)','lambda x:x','a[0]','a.__class__',
                    'print(1)','open("/etc/passwd")','```python\na+b\n```']:
            with self.subTest(bad=bad):
                self.assertIsNone(trial.safe_expression(bad))

    def test_verify_model_hash_includes_gguf_magic_header(self):
        # Sparse synthetic file exercises the real 300 MB size gate without
        # downloading or publishing any model weights.
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "fake-synthetic.gguf"
            with model.open("wb") as stream:
                stream.write(b"GGUF")
                stream.truncate(900_000_000)
            with model.open("rb") as stream:
                full_digest = hashlib.file_digest(stream, "sha256").hexdigest()
            with patch.object(trial, "MODEL_SHA256", full_digest):
                checked = trial.verify_model(model)
            self.assertEqual(checked["sha256"], full_digest)
            self.assertEqual(checked["size_bytes"], 900_000_000)

    def test_reject_wrong_weight_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/"model.gguf"
            f.write_bytes(b"GGUF"+b"x"*300)
            with self.assertRaisesRegex(trial.PilotBlocked, "model_size_mismatch"):
                trial.verify_model(f)

    def test_reject_non_model_magic(self):
        with tempfile.TemporaryDirectory() as tmp, patch("pathlib.Path.stat") as dummy:
            # Don't override stat globally: the public constant must remain a strict SHA.
            self.assertEqual(len(trial.MODEL_SHA256),64)
            self.assertEqual(trial.MODEL_REVISION,"2ab9f8f")

    def test_is_blocked_before_any_import_without_context(self):
        with tempfile.TemporaryDirectory() as tmp, patch("pathlib.Path.cwd",return_value=Path(tmp)):
            dest=Path(tmp)/"evidence.json"
            self.assertEqual(trial.run(dest),1)
            data=json.loads(dest.read_text())
            self.assertEqual(data["status"],"BLOCKED")
            self.assertEqual(data["model_inference"],"NOT_RUN")

    def test_workflow_no_secrets_and_no_private_runner(self):
        yaml=(BASE/".github/workflows/synthetic-llm-trial.yml").read_text()
        self.assertIn("runs-on: ubuntu-latest",yaml)
        self.assertIn("timeout-minutes: 20",yaml)
        self.assertIn("contents: read",yaml)
        self.assertNotIn("${{ secrets.",yaml)
        self.assertNotIn("windows-latest",yaml)
        self.assertNotIn("self-hosted",yaml)
        self.assertIn("acc-synthetic-qwen15b-20261123",yaml)
        self.assertIn("acc-trial-trigger",yaml)

if __name__ == "__main__":
    unittest.main()
