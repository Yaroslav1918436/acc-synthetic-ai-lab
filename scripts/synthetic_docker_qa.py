"""SYNTHETIC ONLY: test bounded public LLM expressions in isolated Docker.

The model's text is never evaluated or executed on the host. The only code
execution is inside a read-only network-disabled container with no secrets.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from scripts.synthetic_model_trial import MODEL_SHA256, check_environment

# Official Docker Hub image, exact immutable multi-architecture index digest.
# No floating tag fallback is permitted.
IMAGE = "python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3"
SCENARIOS = (
    ("add", "def add(a,b):\n    return a-b\n",
     "from src.logic import add\nimport unittest\n"
     "class TestAdd(unittest.TestCase):\n"
     "  def test_positive(self): self.assertEqual(add(2,3),5)\n"
     "  def test_negative(self): self.assertEqual(add(-5,2),-3)\n"),
    ("is_even", "def is_even(n):\n    return n%2 != 0\n",
     "from src.logic import is_even\nimport unittest\n"
     "class TestParity(unittest.TestCase):\n"
     "  def test_even(self): self.assertIs(is_even(2),True)\n"
     "  def test_odd(self): self.assertIs(is_even(3),False)\n"
     "  def test_zero(self): self.assertIs(is_even(0),True)\n"),
    ("clamp", "def clamp(n,lo,hi):\n    return min(lo,max(hi,n))\n",
     "from src.logic import clamp\nimport unittest\n"
     "class TestClamp(unittest.TestCase):\n"
     "  def test_inside(self): self.assertEqual(clamp(4,0,10),4)\n"
     "  def test_below(self): self.assertEqual(clamp(-5,0,10),0)\n"
     "  def test_above(self): self.assertEqual(clamp(15,0,10),10)\n"),
)
ALLOWED = (ast.Expression, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Mod,
           ast.Div, ast.FloorDiv, ast.Name, ast.Load, ast.Constant,
           ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.Gt, ast.LtE,
           ast.GtE, ast.Call, ast.UnaryOp, ast.UAdd, ast.USub)
NAMES = {"a", "b", "n", "lo", "hi", "min", "max"}


class SyntheticBlocked(Exception):
    pass


def bounded_expression(item: dict) -> str:
    if not isinstance(item, dict) or item.get("accepted_expression") is not True:
        raise SyntheticBlocked("unaccepted_expression")
    raw = item.get("raw_preview")
    if not isinstance(raw, str) or not 1 <= len(raw) <= 200:
        raise SyntheticBlocked("invalid_response")
    if item.get("raw_length") != len(raw):
        raise SyntheticBlocked("truncated_response")
    raw = raw.strip()
    if raw.startswith("return "):
        raw = raw[len("return "):].strip()
    if any(c in raw for c in ("\n", "\x00", "`", "#", ";")):
        raise SyntheticBlocked("unapproved_format")
    try:
        tree = ast.parse(raw, mode="eval")
    except (ValueError, SyntaxError) as exc:
        raise SyntheticBlocked("expression_syntax") from exc
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED):
            raise SyntheticBlocked("unapproved_ast")
        if isinstance(node, ast.Name) and node.id not in NAMES:
            raise SyntheticBlocked("unapproved_name")
        if isinstance(node, ast.Constant):
            if type(node.value) is not int or abs(node.value) > 1000:
                raise SyntheticBlocked("unapproved_constant")
        if isinstance(node, ast.Call):
            if (not isinstance(node.func, ast.Name) or node.func.id not in ("min", "max")
                or node.keywords or len(node.args) > 3):
                raise SyntheticBlocked("unapproved_call")
    return ast.unparse(tree.body)


def verified_model_result(path: Path) -> list[str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 15000:
        raise SyntheticBlocked("model_evidence_missing")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if (raw.get("synthetic_only") is not True or raw.get("no_private_code") is not True
        or raw.get("no_paid_api") is not True
        or raw.get("model_sha256") != MODEL_SHA256
        or raw.get("model_inference") != "PERFORMED"
        or raw.get("status") != "COMPLETED_SYNTHETIC_MODEL_TRIAL"
        or raw.get("synthetic_trials") != len(SCENARIOS)
        or raw.get("exact_expression_passes") != len(SCENARIOS)):
        raise SyntheticBlocked("model_evidence_incomplete_or_wrong")
    results = raw.get("results")
    if not isinstance(results, list) or len(results) != len(SCENARIOS):
        raise SyntheticBlocked("wrong_result_count")
    if any(x.get("exact_expected") is not True for x in results):
        raise SyntheticBlocked("model_quality_not_accepted")
    return [bounded_expression(x) for x in results]


def safe_fixture(root: Path, name: str, source: str, tests: str) -> Path:
    fixture = root / name
    fixture.mkdir(mode=0o755)
    (fixture / "src").mkdir(mode=0o755)
    (fixture / "tests").mkdir(mode=0o755)
    (fixture / "src" / "__init__.py").write_text("", encoding="utf-8")
    (fixture / "src" / "logic.py").write_text(source, encoding="utf-8")
    (fixture / "tests" / "test_logic.py").write_text(tests, encoding="utf-8")
    for path in fixture.rglob("*"):
        if path.is_file():
            path.chmod(0o644)
    return fixture


def docker_command(fixture: Path) -> list[str]:
    return [
        "docker", "run", "--rm", "--pull=never", "--network=none",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--pids-limit=64", "--memory=512m", "--cpus=1.0",
        "--user=65534:65534", "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=32m",
        "--mount", f"type=bind,src={fixture.resolve()},dst=/work,readonly",
        "--workdir=/work", "--env=PYTHONDONTWRITEBYTECODE=1",
        IMAGE, "python", "-B", "-m", "unittest", "discover", "-s", "tests", "-q",
    ]


def no_secrets_environment() -> dict:
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent"}


def run_docker(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True,
                          env=no_secrets_environment(), timeout=timeout, check=False)


def evaluate(out: Path, model_json: Path) -> int:
    record = {"schema_version": 1, "kind": "acc_public_synthetic_docker_qa",
              "model_sha256": MODEL_SHA256, "image": IMAGE,
              "status": "BLOCKED", "docker_isolation": "NOT_VERIFIED",
              "synthetic_cases": [], "private_code_used": False}
    try:
        check_environment(Path.cwd())
        expressions = verified_model_result(model_json)
        if not shutil.which("docker"):
            raise SyntheticBlocked("docker_missing")
        pull = run_docker(["docker", "pull", IMAGE], timeout=120)
        if pull.returncode != 0:
            raise SyntheticBlocked("pinned_image_unavailable")
        with tempfile.TemporaryDirectory(prefix="acc-public-docker-") as tmp:
            root = Path(tmp)
            root.chmod(0o755)
            for idx, expression in enumerate(expressions):
                name, buggy, tests = SCENARIOS[idx]
                original_sha = hashlib.sha256(buggy.encode()).hexdigest()
                baseline = safe_fixture(root, name + "-red", buggy, tests)
                prefix = buggy.split("    return ")[0]
                corrected = prefix + "    return " + expression + "\n"
                patched = safe_fixture(root, name + "-green", corrected, tests)
                # Original must fail and proposed correction must pass IN DOCKER.
                red = run_docker(docker_command(baseline), timeout=45)
                green = run_docker(docker_command(patched), timeout=45)
                if red.returncode == 0 or green.returncode != 0:
                    raise SyntheticBlocked("red_green_regression_failed_" + name)
                if hashlib.sha256((baseline / "src" / "logic.py").read_bytes()).hexdigest() != original_sha:
                    raise SyntheticBlocked("original_synthetic_source_modified")
                record["synthetic_cases"].append({
                    "name": name, "red_failed": True, "green_passed": True,
                    "original_unchanged": True,
                    "candidate_sha256": hashlib.sha256(corrected.encode()).hexdigest()})
        record.update(status="SYNTHETIC_DOCKER_TESTED",
                      docker_isolation="DOCKER_NO_NETWORK_READ_ONLY",
                      passed=len(record["synthetic_cases"]))
        outcome = 0
    except (SyntheticBlocked, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        record.update(status="BLOCKED",
                      reason=str(exc) if isinstance(exc, SyntheticBlocked) else type(exc).__name__)
        outcome = 1
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("ACC Docker synthetic:", record["status"], record.get("reason", ""))
    return outcome


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    return evaluate(args.output, args.input)


if __name__ == "__main__":
    raise SystemExit(main())
