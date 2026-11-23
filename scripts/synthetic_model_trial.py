"""Public synthetic-only local inference. Never execute AI-generated code."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
import sys
from pathlib import Path

PUBLIC_REPO = "Yaroslav1918436/acc-synthetic-ai-lab"
PUBLIC_REF = "refs/heads/acc-synthetic-one-shot-20261123"
MODEL_REPO = "Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF"
MODEL_REVISION = "ebb2015"
MODEL_FILE = "qwen2.5-coder-0.5b-instruct-q4_k_m.gguf"
MODEL_SHA256 = "1d9614638d18024d0fbb36575a15f1302a3adf044df10345688ec4f6e1c4ff32"
TRIGGER = "ACC_SYNTHETIC_MODEL_TRIAL_ONCE_20261123\n"
CHALLENGES = [
    ("Fix only the return expression: def add(a,b): return a-b", "a + b"),
    ("Fix only the return expression: def is_even(n): return n%2 != 0", "n % 2 == 0"),
    ("Fix only the return expression: def clamp(n,lo,hi): return min(lo,max(hi,n))", "max(lo, min(hi, n))"),
]

class PilotBlocked(Exception):
    pass

def check_environment(root: Path) -> None:
    if os.environ.get("GITHUB_EVENT_NAME") not in ("push", "workflow_dispatch"):
        raise PilotBlocked("unsupported_event")
    if os.environ.get("GITHUB_REF") != PUBLIC_REF:
        raise PilotBlocked("wrong_branch")
    if os.environ.get("GITHUB_REPOSITORY") != PUBLIC_REPO:
        raise PilotBlocked("wrong_repo")
    if os.environ.get("ACC_PUBLIC_REPO") != "true":
        raise PilotBlocked("not_public")
    marker = root / ".acc-public-synthetic-only"
    trigger = root / ".acc-trial-trigger"
    if (not marker.is_file() or marker.is_symlink()
        or marker.read_text() != "ACC_PUBLIC_SYNTHETIC_ONLY_V1\n"):
        raise PilotBlocked("synthetic_marker_missing")
    if (not trigger.is_file() or trigger.is_symlink()
        or trigger.read_text() != TRIGGER):
        raise PilotBlocked("one_shot_trigger_missing")
    if any((root / forbidden).exists() for forbidden in
           ("AGENTS.md", ".env", "private", "rc83")):
        raise PilotBlocked("private_data_marker_present")

def verify_model(path: Path) -> dict:
    if not path.is_file(): raise PilotBlocked("model_not_file")
    size = path.stat().st_size
    if not 300_000_000 <= size <= 800_000_000:
        raise PilotBlocked("model_size_mismatch")
    with path.open("rb") as src:
        if src.read(4) != b"GGUF":
            raise PilotBlocked("model_format_mismatch")
        src.seek(0)  # Hash the entire GGUF, including the 4-byte magic header.
        digest = hashlib.sha256()
        while data := src.read(1024 * 1024):
            digest.update(data)
    if digest.hexdigest() != MODEL_SHA256:
        raise PilotBlocked("model_sha256_mismatch")
    return {"size_bytes": size, "sha256": digest.hexdigest()}

def safe_expression(raw: str) -> str | None:
    if not isinstance(raw, str) or not 0 < len(raw.strip()) <= 200:
        return None
    if "```" in raw or "\x00" in raw or "\n" in raw:
        return None
    raw = raw.strip()
    if raw.startswith("return "): raw = raw[7:].strip()
    try: tree = ast.parse(raw, mode="eval")
    except (SyntaxError, ValueError): return None
    allow = (ast.Expression, ast.BinOp, ast.Add, ast.Sub, ast.Mult,
             ast.Mod, ast.Div, ast.FloorDiv, ast.Name, ast.Load,
             ast.Constant, ast.Compare, ast.Eq, ast.NotEq, ast.Lt,
             ast.Gt, ast.LtE, ast.GtE, ast.Call, ast.UnaryOp,
             ast.UAdd, ast.USub)
    for node in ast.walk(tree):
        if not isinstance(node, allow):
            return None
        if isinstance(node, ast.Constant):
            if type(node.value) is not int or abs(node.value) > 1000:
                return None
        if isinstance(node, ast.Name):
            if node.id not in {"a", "b", "n", "lo", "hi", "min", "max"}:
                return None
        if isinstance(node, ast.Call):
            if (not isinstance(node.func, ast.Name)
                or node.func.id not in {"min", "max"}
                or node.keywords or len(node.args) > 3):
                return None
    return ast.unparse(tree.body)

def run(output: Path) -> int:
    result = {"schema_version": 1, "synthetic_only": True,
              "model_inference": "NOT_RUN", "quality": "NOT_VERIFIED",
              "no_private_code": True, "no_paid_api": True}
    status = 1
    try:
        check_environment(Path.cwd())
        from huggingface_hub import hf_hub_download
        from llama_cpp import Llama
        model_path = Path(hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_FILE,
                                          revision=MODEL_REVISION, token=False))
        model_info = verify_model(model_path)
        result["model_sha256"] = model_info["sha256"]
        result["model_size_bytes"] = model_info["size_bytes"]
        llm = Llama(model_path=str(model_path), n_ctx=2048, n_threads=2,
                   n_gpu_layers=0, verbose=False, seed=0, chat_format="chatml")
        results = []
        for prompt, expected in CHALLENGES:
            response = llm.create_chat_completion(
                messages=[{"role": "system",
                           "content": "Return ONLY a single Python expression. No markdown or explanations."},
                          {"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=90, stop=["\n\n"], seed=0)
            raw = response["choices"][0]["message"]["content"]
            expr = safe_expression(raw)
            results.append({"accepted_expression": expr is not None,
                            "exact_expected": expr == safe_expression(expected)})
        result.update(status="COMPLETED_SYNTHETIC_MODEL_TRIAL",
                      model_inference="PERFORMED",
                      synthetic_trials=len(results),
                      exact_expression_passes=sum(x["exact_expected"] for x in results),
                      results=results, quality="SYNTHETIC_ONLY_NOT_RC83")
        status = 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, PilotBlocked) else type(exc).__name__
        result.update(status="BLOCKED", reason=reason[:100])
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("ACC trial:", result["status"], result.get("reason", ""))
    return status

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return run(parser.parse_args().output)

if __name__ == "__main__":
    sys.exit(main())
