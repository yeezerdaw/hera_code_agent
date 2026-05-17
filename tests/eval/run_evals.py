#!/usr/bin/env python3
"""Headless eval harness for Hera agent regressions."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = ROOT / "tests" / "eval" / "tasks"
WORKSPACES_DIR = ROOT / "tests" / "eval" / "workspaces"
RESULTS_DIR = ROOT / "tests" / "eval" / "results"
BASELINE_PATH = RESULTS_DIR / "baseline.json"
AGENT_PATH = ROOT / "llm_code_agent.py"


ASSERTIONS: dict[str, dict[str, Any]] = {
    "01": {
        "file_exists": ["fibonacci.py"],
        "file_contains": {"fibonacci.py": ["def fib", "ValueError"]},
    },
    "02": {
        "file_exists": ["buggy_module.py"],
        "python_snippet": "import buggy_module; buggy_module.run()",
        "expect_exit": 0,
    },
    "03": {
        "any_file_exists": ["test_utils.py", "tests/test_utils.py"],
        "any_file_contains": {
            "test_utils.py": ["utils"],
            "tests/test_utils.py": ["utils"],
        },
    },
    "04": {
        "file_exists": ["auth.py"],
        "file_contains": {"app.py": ["auth"], "auth.py": ["def"]},
    },
    "05": {
        "file_contains": {"src/models.py": ["email"], "src/views.py": ["Customer"]},
    },
    "06": {
        "file_contains": {"app/main.py": ["/health"]},
    },
    "07": {
        "file_not_contains": {"app.py": ["raise ValueError"]},
    },
    "08": {
        "file_exists": ["config.py"],
        "file_contains": {"config.py": ["@dataclass", "class Config", "__post_init__"]},
    },
    "09": {
        "file_contains": {"legacy_code.py": ["->", ": "]},
    },
    "10": {
        "file_contains": {
            "src/models.py": ["class Customer"],
            "src/services.py": ["Customer"],
            "src/views.py": ["Customer"],
        },
        "file_not_contains": {
            "src/models.py": ["class User"],
            "src/services.py": ["User"],
            "src/views.py": ["User"],
        },
    },
}


def discover_tasks(tasks_dir: Path = TASKS_DIR) -> list[dict[str, Any]]:
    """Discover eval tasks and map each to its optional seeded workspace."""
    tasks: list[dict[str, Any]] = []
    for task_file in sorted(tasks_dir.glob("*.md")):
        task_id = task_file.stem[:2]
        workspace_src = WORKSPACES_DIR / task_file.stem
        tasks.append({
            "id": task_id,
            "name": task_file.stem,
            "task_file": task_file,
            "workspace_src": workspace_src,
            "has_workspace": workspace_src.exists(),
        })
    return tasks


class SilentDisplay:
    """No-op UI for unattended eval runs."""

    def banner(self, model: str):
        return None

    def thinking_start(self, label: str = "Thinking"):
        return None

    def thinking_stop(self):
        return None

    def assistant(self, text: str):
        return None

    def tool_call(self, name: str, args: dict):
        return None

    def tool_result(self, result: str, tool_name: str = "", tool_args: dict | None = None,
                    is_error: bool = False, healed: bool = False):
        return None

    def phase(self, name: str, turn: int, max_turns: int):
        return None

    def observer(self, text: str):
        return None

    def info(self, text: str):
        return None

    def warning(self, text: str):
        return None

    def error(self, text: str):
        return None

    def success(self, text: str):
        return None

    def scratchpad(self, text: str):
        return None

    def watcher_event(self, path: str, kind: str):
        return None

    def prompt(self):
        return None


def load_agent_module(path: Path):
    spec = importlib.util.spec_from_file_location("hera_agent", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module spec from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                events.append(obj)
        except json.JSONDecodeError:
            continue
    return events


def count_turns(events: list[dict[str, Any]]) -> int:
    turns = {e.get("turn") for e in events if e.get("type") == "turn_start"}
    return len([t for t in turns if isinstance(t, int)])


def count_tools(events: list[dict[str, Any]]) -> int:
    return sum(1 for e in events if e.get("type") == "tool_call")


def count_errors(events: list[dict[str, Any]]) -> int:
    return sum(
        1
        for e in events
        if e.get("type") == "tool_result" and e.get("success") is False
    )


def count_heals(events: list[dict[str, Any]]) -> int:
    return sum(
        1
        for e in events
        if e.get("type") == "tool_result" and bool(e.get("healed"))
    )


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _run_python_snippet(workspace: Path, snippet: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=str(workspace),
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def check_task(
    workspace: Path,
    task_num: str,
    messages: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> tuple[bool, str]:
    done_path = workspace / "DONE.md"
    if not done_path.exists():
        return False, "DONE.md missing"

    last_turns = [
        int(e.get("turn"))
        for e in events
        if e.get("type") == "turn_end" and isinstance(e.get("turn"), int)
    ]
    if last_turns:
        final_turn = max(last_turns)
        final_errors = [
            e for e in events
            if e.get("type") == "tool_result"
            and e.get("turn") == final_turn
            and e.get("success") is False
        ]
        if final_errors:
            return False, "final turn contains tool errors"

    if messages:
        last_content = str(messages[-1].get("content", ""))
        if last_content.startswith("ERROR:"):
            return False, "final assistant response is error"

    rules = ASSERTIONS.get(task_num, {})

    for rel in rules.get("file_exists", []):
        if not (workspace / rel).exists():
            return False, f"missing file: {rel}"

    any_exists = rules.get("any_file_exists")
    if any_exists:
        if not any((workspace / rel).exists() for rel in any_exists):
            return False, f"missing any expected file: {any_exists}"

    for rel, needles in rules.get("file_contains", {}).items():
        target = workspace / rel
        if not target.exists():
            return False, f"missing file for content check: {rel}"
        content = _read(target)
        for needle in needles:
            if needle not in content:
                return False, f"{rel} missing expected text: {needle}"

    for rel, needles in rules.get("file_not_contains", {}).items():
        target = workspace / rel
        if not target.exists():
            return False, f"missing file for exclusion check: {rel}"
        content = _read(target)
        for needle in needles:
            if needle in content:
                return False, f"{rel} still contains forbidden text: {needle}"

    any_contains = rules.get("any_file_contains", {})
    if any_contains:
        matched = False
        for rel, needles in any_contains.items():
            target = workspace / rel
            if not target.exists():
                continue
            content = _read(target)
            if all(needle in content for needle in needles):
                matched = True
                break
        if not matched:
            return False, "none of candidate files contained expected text"

    snippet = rules.get("python_snippet")
    if snippet:
        rc, _out, err = _run_python_snippet(workspace, snippet)
        expect = int(rules.get("expect_exit", 0))
        if rc != expect:
            return False, f"python snippet exit={rc} expected={expect}: {err.strip()}"

    return True, ""


def print_report(results: list[dict[str, Any]], baseline_summary: dict[str, Any] | None = None):
    headers = ["Task", "Pass", "Turns", "Tools", "Errors", "Heals", "Sec", "Reason"]
    rows: list[list[str]] = []
    for r in results:
        rows.append([
            r["task"],
            "Y" if r["passed"] else "N",
            str(r["turns"]),
            str(r["tool_calls"]),
            str(r["errors"]),
            str(r["heals"]),
            f"{r['duration']:.2f}",
            r["fail_reason"] or "-",
        ])

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row: list[str]) -> str:
        return " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    print(fmt(headers))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(fmt(row))

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    if baseline_summary:
        print(
            f"\nPassed {passed}/{total} "
            f"(baseline: {baseline_summary.get('passed', 0)}/{baseline_summary.get('total', total)})"
        )
    else:
        print(f"\nPassed {passed}/{total}")


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    errors = sum(int(r["errors"]) for r in results)
    turns = [int(r["turns"]) for r in results]
    avg_turns = (sum(turns) / len(turns)) if turns else 0.0
    return {
        "total": total,
        "passed": passed,
        "pass_rate": (passed / total) if total else 0.0,
        "errors": errors,
        "avg_turns": avg_turns,
    }


def run() -> int:
    parser = argparse.ArgumentParser(description="Run Hera eval harness")
    parser.add_argument("--model", default="gemma4:e2b")
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--api-base", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--max-turns", type=int, default=10)
    parser.add_argument("--auto-test", action="store_true", default=True)
    parser.add_argument(
        "--auto-approve-warn",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Auto-approve warn-level commands during eval runs.",
    )
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    agent = load_agent_module(AGENT_PATH)
    agent.ui = SilentDisplay()

    api_key = (
        args.api_key
        or os.environ.get("HERA_API_KEY", "")
        or os.environ.get("GROQ_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
        or os.environ.get("TOGETHER_API_KEY", "")
        or os.environ.get("MISTRAL_API_KEY", "")
    )
    agent._backend = agent.make_backend(args.backend, args.api_base, api_key)
    agent.prompt_user_approval = (lambda _msg: bool(args.auto_approve_warn))

    tasks = discover_tasks(TASKS_DIR)
    if not tasks:
        print(f"No tasks found in {TASKS_DIR}")
        return 1

    results: list[dict[str, Any]] = []

    for task in tasks:
        task_num = str(task["id"])
        task_name = str(task["name"])
        task_file = Path(task["task_file"])
        workspace_src = Path(task["workspace_src"])
        task_content = task_file.read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory(prefix=f"hera_eval_{task_num}_") as tmpdir:
            tmp = Path(tmpdir)

            if workspace_src.exists():
                shutil.copytree(workspace_src, tmp, dirs_exist_ok=True)

            shutil.copy(task_file, tmp / "TASK.md")

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": agent.SYSTEM_PROMPT}
            ]
            config = agent.AgentConfig(
                approval=False,
                max_turns=args.max_turns,
                auto_test=args.auto_test,
                watch=False,
            )

            start = time.monotonic()
            messages = agent.run_agent(
                args.model,
                task_content,
                messages,
                str(tmp),
                config,
                watcher_queue=None,
                task_mode=True,
            )
            duration = time.monotonic() - start

            journal = parse_journal(tmp / agent.JOURNAL_FILENAME)
            passed, fail_reason = check_task(tmp, task_num, messages, journal)

            results.append({
                "task": task_num,
                "task_name": task_name,
                "passed": passed,
                "duration": duration,
                "turns": count_turns(journal),
                "tool_calls": count_tools(journal),
                "errors": count_errors(journal),
                "heals": count_heals(journal),
                "fail_reason": fail_reason,
            })

    summary = summarize(results)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": args.model,
        "backend": args.backend,
        "summary": summary,
        "results": results,
    }

    if args.update_baseline or not BASELINE_PATH.exists():
        BASELINE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print_report(results)
        print(f"\nBaseline written to {BASELINE_PATH}")
        return 0

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    base_summary = baseline.get("summary", {})

    print_report(results, baseline_summary=base_summary)

    failed_gates: list[str] = []
    if summary["passed"] < int(base_summary.get("passed", 0)):
        failed_gates.append(
            f"pass count regressed: {summary['passed']} < {base_summary.get('passed', 0)}"
        )

    base_errors = float(base_summary.get("errors", 0))
    if base_errors <= 0:
        base_errors = 1.0
    if float(summary["errors"]) > base_errors * 1.5:
        failed_gates.append(
            f"error count regressed: {summary['errors']} > {base_errors * 1.5:.1f}"
        )

    if failed_gates:
        print("\nEval gates failed:")
        for msg in failed_gates:
            print(f"- {msg}")
        return 2

    print("\nEval gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
