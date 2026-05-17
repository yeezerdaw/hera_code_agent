#!/usr/bin/env python3
"""
llm_code_agent.py — Hera local agent (v2.4)
LLM    : Ollama  (default model: gemma4:e2b)
Sandbox: Local filesystem + subprocess (sandboxed working directory)

Features (v2.4):
  1. Planner-Executor-Observer state machine
  2. Parallel tool execution via ThreadPoolExecutor
  3. Self-healing tool calls with exponential backoff
  4. Turn limiter (--max-turns, default 15)
  5. Approval mode (--approval) for destructive commands
  6. Rich CLI with spinners, syntax highlighting, tool-call trees
  7. Conversation summarisation every N turns
  8. Semantic memory with embeddings (chromadb) — top-N relevant recall
  9. /selfreview slash command
 10. Auto-test hook (--auto-test)
 11. Scratchpad tool — private LLM reasoning, never injected into history
 12. TASK.md / DONE.md — CI-composable task file workflow
 13. Live file watcher (--watch) — external edit detection

Setup:
    pip install requests rich chromadb
    ollama pull gemma4:e2b
    ollama pull nomic-embed-text   # embedding model

Usage:
    python llm_code_agent.py
    python llm_code_agent.py --model gemma4:e2b --approval --auto-test
    python llm_code_agent.py --model qwen2.5:7b --max-turns 20
    python llm_code_agent.py --workdir /tmp/workspace --watch
    python llm_code_agent.py --workdir /tmp/workspace --no-rich
"""

import argparse
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# ── OPTIONAL DEPENDENCY: rich ────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.text import Text
    from rich.markdown import Markdown
    from rich import box as rich_box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ── OPTIONAL DEPENDENCY: chromadb ────────────────────────────────────────────
try:
    import chromadb
    from chromadb.utils import embedding_functions
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False
    logging.getLogger(__name__).debug(
        "chromadb not installed — semantic memory unavailable. "
        "Run: pip install chromadb"
    )

# ── OPTIONAL DEPENDENCY: watchdog ────────────────────────────────────────────
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    logging.getLogger(__name__).debug(
        "watchdog not installed — file watcher will use polling fallback. "
        "Run: pip install watchdog"
    )

# ── CONSTANTS ────────────────────────────────────────────────────────────────
OLLAMA_BASE = "http://localhost:11434"
MEMORY_FILE = "memory.json"
MEMORY_DIR = os.path.join(os.path.expanduser("~"), ".config", "llm_code_agent")
MEMORY_PATH = os.path.join(MEMORY_DIR, MEMORY_FILE)
CHROMADB_DIR = os.path.join(MEMORY_DIR, "chromadb")
EMBED_MODEL = "nomic-embed-text"
MEMORY_COLLECTION = "agent_memories"
MEMORY_TOP_N = 8          # max memories injected per turn
MAX_TURNS_DEFAULT = 15
DEFAULT_SUMMARISE_EVERY = 5
DEFAULT_SUMMARY_KEEP_MESSAGES = 6
CONTEXT_CHAR_LIMIT = 2000
DISPLAY_CHAR_LIMIT = 500
SELF_HEAL_MAX_SEARCH_DEPTH = 5
SCRATCHPAD_FILENAME = ".agent_scratchpad.md"
JOURNAL_FILENAME = ".agent_journal.jsonl"
TASK_FILENAME = "TASK.md"
DONE_FILENAME = "DONE.md"
WATCH_DEBOUNCE_SECONDS = 1.5

# Diff editor constants
DIFF_BLOCK_PATTERN = re.compile(
    r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE",
    re.DOTALL
)
DIFF_FUZZY_NORMALIZE = re.compile(r'[ \t]+')

# Files that the watcher will never report (noisy / internal)
WATCH_IGNORE_NAMES = {
    SCRATCHPAD_FILENAME, DONE_FILENAME, "__pycache__",
    ".git", ".venv", "memory.json", JOURNAL_FILENAME,
}
WATCH_IGNORE_SUFFIXES = {".pyc", ".pyo", ".swp", ".swo", ".tmp"}

# Command prefixes
SAFE_COMMAND_PREFIXES = {
    "python", "python3", "pip", "pip3", "pytest", "git", "ls", "cat",
    "head", "tail", "grep", "find", "pwd", "echo", "sed", "awk", "wc",
    "mkdir", "touch", "cp", "which", "uname", "date", "env",
}
WARN_COMMAND_PREFIXES = {
    "rm", "rmdir", "mv", "sudo", "chmod", "chown", "mkfs", "dd",
    "shutdown", "reboot", "kill", "pkill",
}
BLOCKED_COMMAND_PREFIXES = {
    "curl", "wget", "nc", "netcat", "eval", "exec",
}
SAFE_ENV_ALLOWLIST = {
    # Intentionally restrictive — excludes PYTHONPATH to avoid leaking host
    # environment into subprocesses.  Add PYTHONPATH here if needed.
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR",
    "USER", "SHELL",
}

MEMORY_LOCK = threading.RLock()
JOURNAL_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# §0  DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AgentConfig:
    approval: bool
    max_turns: int
    auto_test: bool
    watch: bool = False
    summarise_every: int = DEFAULT_SUMMARISE_EVERY
    summary_keep_messages: int = DEFAULT_SUMMARY_KEEP_MESSAGES


@dataclass
class WatcherEvent:
    """A file-change event queued by the background watcher."""
    path: str
    kind: str   # "modified" | "created" | "deleted"
    # monotonic clock for debounce arithmetic; wall time stored separately
    ts: float = field(default_factory=time.monotonic)
    wall_ts: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


class OllamaClientError(RuntimeError):
    """Raised when requests to the local Ollama server fail."""


# ══════════════════════════════════════════════════════════════════════════════
# §0b  COMMAND CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class CommandStatus(str, Enum):
    """
    Result of classify_command().
    Inherits from str so existing comparisons (level == "block") still work
    during the transition, but new code should use CommandStatus.BLOCK etc.
    """
    SAFE  = "safe"
    WARN  = "warn"
    BLOCK = "block"


def classify_command(command: str) -> tuple[CommandStatus, str, list[str]]:
    """Classify command intent as safe, warn (requires approval), or block."""
    try:
        argv = shlex.split(command)
    except ValueError as e:
        return CommandStatus.BLOCK, f"Failed to parse command: {e}", []

    if not argv:
        return CommandStatus.BLOCK, "Empty command.", []

    exe = argv[0].lower()

    if exe in BLOCKED_COMMAND_PREFIXES:
        return CommandStatus.BLOCK, f"Executable '{exe}' is blocked by policy.", []
    if exe in {"python", "python3"} and "-c" in argv:
        return CommandStatus.BLOCK, "Inline Python execution (-c) is blocked by policy.", []
    if exe in {"bash", "sh"} and "-c" in argv:
        return CommandStatus.BLOCK, "Inline shell execution (-c) is blocked by policy.", []
    if exe in WARN_COMMAND_PREFIXES:
        return CommandStatus.WARN, f"Executable '{exe}' is potentially destructive.", argv
    if exe not in SAFE_COMMAND_PREFIXES:
        return CommandStatus.BLOCK, f"Unsupported executable: {exe}", []
    if exe == "env" and any(
        "=" in token and not token.startswith("=") for token in argv[1:]
    ):
        return CommandStatus.BLOCK, "env variable assignment is blocked by policy.", []

    return CommandStatus.SAFE, "", argv


def resolve_executable_argv(argv: list[str]) -> list[str]:
    """Resolve executable to full path; handle mixed-case names gracefully."""
    if not argv or "/" in argv[0]:
        return argv
    resolved = shutil.which(argv[0]) or shutil.which(argv[0].lower())
    if resolved:
        out = list(argv)
        out[0] = resolved
        return out
    return argv  # subprocess will raise FileNotFoundError — caught upstream


LEXER_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".html": "html", ".css": "css", ".json": "json", ".md": "markdown",
    ".sh": "bash", ".yml": "yaml", ".yaml": "yaml", ".rs": "rust",
    ".go": "go", ".java": "java", ".c": "c", ".cpp": "cpp",
    ".rb": "ruby", ".sql": "sql", ".xml": "xml", ".toml": "toml",
    ".txt": "text", ".cfg": "ini", ".ini": "ini", ".env": "bash",
}


# ══════════════════════════════════════════════════════════════════════════════
# §1  DISPLAY LAYER — Rich CLI with graceful ANSI fallback
# ══════════════════════════════════════════════════════════════════════════════

class Display:
    _RESET   = "\033[0m"
    _BOLD    = "\033[1m"
    _DIM     = "\033[2m"
    _CYAN    = "\033[96m"
    _GREEN   = "\033[92m"
    _YELLOW  = "\033[93m"
    _RED     = "\033[91m"
    _BLUE    = "\033[94m"
    _MAGENTA = "\033[95m"

    def __init__(self, use_rich: bool = False):
        self.use_rich = use_rich
        self._status = None
        if use_rich:
            self.console = Console()

    def _ansi(self, text, *codes):
        return f"{''.join(codes)}{text}{self._RESET}"

    @staticmethod
    def _guess_lexer(path: str) -> str:
        _, ext = os.path.splitext(path)
        return LEXER_MAP.get(ext.lower(), "text")

    def banner(self, model: str):
        if self.use_rich:
            title = Text.assemble(
                ("Hera ", "bold cyan"),
                ("v2.4", "bold white"),
                ("  ·  ", "dim"),
                (f"{model}  (local)", "dim"),
            )
            self.console.print()
            self.console.print(Panel(
                title, box=rich_box.DOUBLE, border_style="cyan", padding=(0, 2),
            ))
            cmds = Text.assemble(
                ("Commands: ", "bold"),
                ("/exit ", "yellow"), ("/clear ", "yellow"),
                ("/model <n> ", "yellow"), ("/workdir ", "yellow"),
                ("/selfreview ", "yellow"), ("/memory ", "yellow"),
                ("/scratch", "yellow"),
            )
            self.console.print(cmds)
            self.console.print()
        else:
            inner = f"  Hera v2.4  ·  {model}  (local)"
            w = max(50, len(inner) + 2)
            pad = w - len(inner)
            bar = "═" * w
            print(f"\n{self._ansi('╔' + bar + '╗', self._CYAN)}")
            print(
                f"{self._ansi('║', self._CYAN)}  "
                f"{self._ansi('Hera v2.4', self._BOLD, self._CYAN)}  ·  "
                f"{self._ansi(f'{model}  (local)', self._DIM)}"
                f"{' ' * pad}{self._ansi('║', self._CYAN)}"
            )
            print(f"{self._ansi('╚' + bar + '╝', self._CYAN)}")
            cmds1 = ["/exit", "/clear", "/model <n>", "/workdir"]
            cmds2 = ["/selfreview", "/memory", "/scratch"]
            print(
                f"{self._ansi('Commands:', self._BOLD)}  "
                + "  ".join(self._ansi(c, self._YELLOW) for c in cmds1)
            )
            print(
                " " * 11
                + "  ".join(self._ansi(c, self._YELLOW) for c in cmds2)
            )
            print()

    def thinking_start(self, label: str = "Thinking"):
        if self.use_rich:
            self._status = self.console.status(
                f"[dim]{label}…[/dim]", spinner="dots"
            )
            self._status.start()
        else:
            print(self._ansi(f"  {label}…", self._DIM), end="\r", flush=True)

    def thinking_stop(self):
        if self.use_rich:
            if self._status:
                self._status.stop()
                self._status = None
        else:
            print("                              ", end="\r")

    def assistant(self, text: str):
        if self.use_rich:
            self.console.print()
            self.console.print(Text("Assistant", style="bold cyan"), end=": ")
            try:
                self.console.print(Markdown(text))
            except Exception:
                self.console.print(text)
            self.console.print()
        else:
            print(f"\n{self._ansi('Assistant', self._CYAN, self._BOLD)}: {text}\n")

    def tool_call(self, name: str, args: dict):
        visible_args = {k: v for k, v in args.items() if not str(k).startswith("_")}
        preview = json.dumps(visible_args, ensure_ascii=False)
        if len(preview) > 120:
            preview = preview[:117] + "…"
        if self.use_rich:
            self.console.print(
                f"  [yellow]⚙[/yellow]  [bold]{name}[/bold]([dim]{preview}[/dim])"
            )
        else:
            print(
                f"  {self._ansi('⚙', self._YELLOW)}  "
                f"{self._ansi(name, self._BOLD)}"
                f"({self._ansi(preview, self._DIM)})"
            )

    def tool_result(self, result: str, tool_name: str = "",
                    tool_args: dict = None, is_error: bool = False,
                    healed: bool = False):
        if is_error:
            prefix, style = "✗", "red"
        elif healed:
            prefix, style = "⟲", "yellow"
        else:
            prefix, style = "→", "green"

        truncated = len(result) > DISPLAY_CHAR_LIMIT
        display = result[:DISPLAY_CHAR_LIMIT] + ("\n…(truncated)" if truncated else "")

        if (self.use_rich and tool_name == "read_file"
                and tool_args and not result.startswith("ERROR:")):
            path = tool_args.get("path", "")
            lexer = self._guess_lexer(path)
            self.console.print(f"  [{style}]{prefix}[/{style}] ", end="")
            try:
                self.console.print(
                    Syntax(display, lexer, theme="monokai",
                           line_numbers=True, word_wrap=True)
                )
            except Exception:
                self.console.print(display)
            self.console.print()
        elif self.use_rich:
            self.console.print(f"  [{style}]{prefix}[/{style}] {display}\n")
        else:
            ansi_map = {"red": self._RED, "yellow": self._YELLOW, "green": self._GREEN}
            print(f"  {self._ansi(prefix, ansi_map[style])} {display}\n")

    def phase(self, name: str, turn: int, max_turns: int):
        msg = f"─── {name} (turn {turn}/{max_turns}) ───"
        if self.use_rich:
            self.console.print(f"  [dim]{msg}[/dim]")
        else:
            print(self._ansi(f"  {msg}", self._DIM))

    def observer(self, text: str):
        if self.use_rich:
            self.console.print(f"  [magenta]👁 {text}[/magenta]")
        else:
            print(self._ansi(f"  👁 {text}", self._MAGENTA))

    def info(self, text: str):
        if self.use_rich:
            self.console.print(f"  [blue]ℹ[/blue] {text}")
        else:
            print(self._ansi(f"  ℹ {text}", self._BLUE))

    def warning(self, text: str):
        if self.use_rich:
            self.console.print(f"  [yellow]⚠ {text}[/yellow]")
        else:
            print(self._ansi(f"  ⚠ {text}", self._YELLOW))

    def error(self, text: str):
        if self.use_rich:
            self.console.print(f"  [red]✗ {text}[/red]")
        else:
            print(self._ansi(f"  ✗ {text}", self._RED))

    def success(self, text: str):
        if self.use_rich:
            self.console.print(f"  [green]✓ {text}[/green]")
        else:
            print(self._ansi(f"  ✓ {text}", self._GREEN))

    def scratchpad(self, text: str):
        """Display a scratchpad write notification (dim, non-intrusive)."""
        preview = text[:80].replace("\n", " ") + ("…" if len(text) > 80 else "")
        if self.use_rich:
            self.console.print(f"  [dim]✎ scratchpad: {preview}[/dim]")
        else:
            print(self._ansi(f"  ✎ scratchpad: {preview}", self._DIM))

    def watcher_event(self, path: str, kind: str):
        msg = f"[Watcher] {kind}: {path}"
        if self.use_rich:
            self.console.print(f"  [cyan]👁 {msg}[/cyan]")
        else:
            print(self._ansi(f"  👁 {msg}", self._CYAN))

    def prompt(self) -> str | None:
        try:
            if self.use_rich:
                return self.console.input("[bold blue]You[/bold blue] › ").strip()
            else:
                return input(f"{self._ansi('You', self._BLUE, self._BOLD)} › ").strip()
        except (EOFError, KeyboardInterrupt):
            return None


# Global display — initialised in main()
ui: Display = Display(use_rich=False)


# ══════════════════════════════════════════════════════════════════════════════
# §2  TOOL DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file from the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "Relative path from the working directory."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write (or overwrite) a file. Parent directories are created automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to write to."},
                    "content": {"type": "string", "description": "Full content to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Replace a specific string in a file. Use for targeted edits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_content": {"type": "string", "description": "Exact string to replace."},
                    "new_content": {"type": "string", "description": "Replacement string."},
                },
                "required": ["path", "old_content", "new_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories at a given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "Directory to list. Defaults to '.'."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command in the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer",
                                "description": "Timeout in seconds (default 60)."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Save a fact or user preference to semantic memory. "
                "Persists across sessions. Example keys: "
                "'preferred_test_framework', 'coding_style'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Short descriptive key."},
                    "value": {"type": "string", "description": "The value to remember."},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Retrieve facts from memory. Pass a query for semantic search, or empty string to list all.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "Natural language query (empty = list all)."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scratchpad",
            "description": (
                "Write private reasoning or planning notes. "
                "Content is saved locally but NEVER included in the conversation "
                "or shown to the user. Use this for extended thinking, outlining "
                "a plan, or working through a problem before responding."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string",
                                "description": "Your private reasoning or plan."},
                    "mode": {
                        "type": "string",
                        "enum": ["append", "overwrite"],
                        "description": "Whether to append or overwrite. Default: append.",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Apply a unified diff-style edit to a file. "
                "More robust than replace_in_file for multi-line changes. "
                "Format uses SEARCH/REPLACE markers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to file to edit."
                    },
                    "diff_block": {
                        "type": "string",
                        "description": (
                            "Diff block with format:\n"
                            "<<<<<<< SEARCH\n"
                            "exact content to find\n"
                            "=======\n"
                            "replacement content\n"
                            ">>>>>>> REPLACE\n"
                            "Whitespace is normalized for matching if exact fails."
                        )
                    }
                },
                "required": ["path", "diff_block"]
            },
        },
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# §3  LOCAL TOOL IMPLEMENTATIONS
# ══════════════════════════════════════════════════════════════════════════════

def resolve(path: str, workdir: str) -> str | None:
    """Resolve a path relative to workdir, blocking directory traversal."""
    base = os.path.realpath(workdir)
    full = os.path.realpath(os.path.join(workdir, path))
    if full != base and not full.startswith(base + os.sep):
        return None
    return full


def tool_read_file(path: str, workdir: str) -> str:
    full = resolve(path, workdir)
    if full is None:
        return "ERROR: Path traversal outside working directory is not allowed."
    try:
        with open(full, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"ERROR: File not found: {path}"
    except Exception as e:
        return f"ERROR: {e}"


def tool_write_file(path: str, content: str, workdir: str) -> str:
    full = resolve(path, workdir)
    if full is None:
        return "ERROR: Path traversal outside working directory is not allowed."
    try:
        d = os.path.dirname(full)
        if d:
            os.makedirs(d, exist_ok=True)
        # Atomic write via temp-file rename
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=os.path.dirname(full) or workdir,
            delete=False, suffix=".tmp"
        ) as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, full)
        return f"Written {len(content)} bytes → {full}"
    except Exception as e:
        return f"ERROR: {e}"


def tool_replace_in_file(path: str, old_content: str,
                         new_content: str, workdir: str) -> str:
    full = resolve(path, workdir)
    if full is None:
        return "ERROR: Path traversal outside working directory is not allowed."
    try:
        with open(full, "r", encoding="utf-8") as f:
            content = f.read()
        if old_content not in content:
            return (
                "ERROR: old_content not found in the file. "
                "Make sure you matched the existing string exactly."
            )
        updated = content.replace(old_content, new_content, 1)
        # Atomic write
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=os.path.dirname(full) or workdir,
            delete=False, suffix=".tmp"
        ) as tmp:
            tmp.write(updated)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, full)
        return f"Successfully replaced content in {full}"
    except Exception as e:
        return f"ERROR: {e}"


def tool_edit_file(path: str, diff_block: str, workdir: str) -> str:
    """
    Apply a unified diff block to a file.
    
    Format:
    <<<<<<< SEARCH
    exact or approximate content to find
    =======
    replacement content
    >>>>>>> REPLACE
    
    Whitespace is normalized for fuzzy matching if exact match fails.
    """
    full = resolve(path, workdir)
    if full is None:
        return "ERROR: Path traversal outside working directory is not allowed."
    
    try:
        with open(full, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return f"ERROR: File not found: {path}"
    except Exception as e:
        return f"ERROR: {e}"
    
    # Parse diff block
    match = DIFF_BLOCK_PATTERN.search(diff_block)
    if not match:
        return (
            "ERROR: Invalid diff format. Use exactly:\n"
            "<<<<<<< SEARCH\n"
            "content to find\n"
            "=======\n"
            "replacement content\n"
            ">>>>>>> REPLACE"
        )
    
    search_text = match.group(1)
    replace_text = match.group(2)
    
    search_lines = search_text.split('\n')
    content_lines = content.split('\n')
    
    # Attempt 1: Exact match
    if search_text in content:
        updated = content.replace(search_text, replace_text, 1)
        return _atomic_write_and_confirm(full, updated, workdir, path)
    
    # Attempt 2: Fuzzy match (normalize whitespace)
    def normalize(s: str) -> str:
        # Collapse multiple whitespace, strip leading/trailing per line
        lines = s.split('\n')
        normalized_lines = []
        for line in lines:
            # Preserve empty lines for structure, normalize spacing
            if line.strip():
                normalized_lines.append(DIFF_FUZZY_NORMALIZE.sub(' ', line).strip())
            else:
                normalized_lines.append('')
        return '\n'.join(normalized_lines)
    
    norm_search = normalize(search_text)
    norm_content = normalize(content)
    
    if norm_search in norm_content:
        # Find the actual substring in original that matches normalized
        # Strategy: line-by-line sliding window
        for i in range(len(content_lines) - len(search_lines) + 1):
            window = content_lines[i:i+len(search_lines)]
            if normalize('\n'.join(window)) == norm_search:
                # Found match, reconstruct with replacement
                new_lines = (
                    content_lines[:i] + 
                    replace_text.split('\n') + 
                    content_lines[i+len(search_lines):]
                )
                updated = '\n'.join(new_lines)
                return _atomic_write_and_confirm(full, updated, workdir, path)
    
    # Attempt 3: Line-stripped match (ignore all indentation)
    stripped_search = '\n'.join(line.strip() for line in search_text.split('\n'))
    for i in range(len(content_lines) - len(search_lines) + 1):
        window = content_lines[i:i+len(search_lines)]
        stripped_window = '\n'.join(line.strip() for line in window)
        if stripped_window == stripped_search:
            new_lines = (
                content_lines[:i] + 
                replace_text.split('\n') + 
                content_lines[i+len(search_lines):]
            )
            updated = '\n'.join(new_lines)
            return _atomic_write_and_confirm(full, updated, workdir, path)
    
    return (
        f"ERROR: SEARCH block not found in {path}.\n"
        f"Tried: exact match, fuzzy whitespace match, and stripped match.\n"
        f"Hint: Read the file first to see exact content."
    )


def _atomic_write_and_confirm(full_path: str, content: str, 
                               workdir: str, display_path: str) -> str:
    """Helper: atomic write with fsync."""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", 
            dir=os.path.dirname(full_path) or workdir,
            delete=False, suffix=".tmp"
        ) as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        
        os.replace(tmp_path, full_path)
        return f"Successfully edited {display_path} ({len(content)} bytes)"
    except Exception as e:
        return f"ERROR: Write failed: {e}"


def tool_list_files(path: str, workdir: str) -> str:
    target = resolve(path or ".", workdir)
    if target is None:
        return "ERROR: Path traversal outside working directory is not allowed."
    try:
        entries = os.listdir(target)
        if not entries:
            return "(empty directory)"
        lines = []
        for name in sorted(entries):
            full = os.path.join(target, name)
            size = os.path.getsize(full) if os.path.isfile(full) else "-"
            kind = "DIR" if os.path.isdir(full) else "FILE"
            lines.append(f"  {kind:<5}  {str(size):>8}  {name}")
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


def tool_run_command(command: str, timeout: int, workdir: str,
                     approved: bool = False,
                     preclassified: tuple[str, str, list[str]] | None = None) -> str:
    level, reason, argv = preclassified or classify_command(command)
    if level == CommandStatus.BLOCK:
        return f"ERROR: Command rejected by security policy ({reason})"
    if level == CommandStatus.WARN and not approved:
        return (
            "ERROR: Command rejected by security policy "
            "(warn-level command requires explicit approval token)."
        )

    logger.info("run_command: %s", command)
    argv = resolve_executable_argv(argv)

    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_ALLOWLIST}
    venv_bin = os.path.join(workdir, ".venv", "bin")
    if os.path.isdir(venv_bin):
        env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}".strip(":")
        env["VIRTUAL_ENV"] = os.path.join(workdir, ".venv")

    try:
        result = subprocess.run(
            argv, shell=False, capture_output=True,
            text=True, timeout=timeout, cwd=workdir, env=env,
        )
        parts = []
        if result.stdout and result.stdout.strip():
            parts.append(f"STDOUT:\n{result.stdout.strip()}")
        if result.stderr and result.stderr.strip():
            parts.append(f"STDERR:\n{result.stderr.strip()}")
        parts.append(f"Exit code: {result.returncode}")
        return "\n".join(parts) if parts else "(no output)"
    except subprocess.TimeoutExpired:
        return f"ERROR: Command timed out after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# §11  SCRATCHPAD TOOL
# ══════════════════════════════════════════════════════════════════════════════

def tool_scratchpad(content: str, mode: str, workdir: str) -> str:
    """
    Write private reasoning to a local file.  This result is intercepted
    by dispatch_tool and NEVER appended to the conversation history.
    """
    path = os.path.join(workdir, SCRATCHPAD_FILENAME)
    try:
        if mode == "overwrite":
            text = content
        else:
            existing = ""
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    existing = f.read()
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            text = existing + f"\n\n<!-- {ts} -->\n{content}"

        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=workdir, delete=False, suffix=".tmp"
        ) as tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, path)
        ui.scratchpad(content)
        # Return a structured result; the suppress flag is set by dispatch_tool.
        return f"Scratchpad updated ({len(content)} chars)."
    except Exception as e:
        return f"ERROR: scratchpad write failed: {e}"


def journal_append(workdir: str, event: dict) -> None:
    """Append one trajectory event as JSONL to the journal file."""
    payload = dict(event)
    payload["ts"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    path = os.path.join(workdir, JOURNAL_FILENAME)

    try:
        with JOURNAL_LOCK:
            fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
    except Exception as e:
        logger.debug("journal append failed: %s", e)


def read_scratchpad(workdir: str) -> str:
    """Read the current scratchpad contents (for /scratch command)."""
    path = os.path.join(workdir, SCRATCHPAD_FILENAME)
    if not os.path.exists(path):
        return "(scratchpad is empty)"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"ERROR: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# §8  SEMANTIC MEMORY (chromadb)
# ══════════════════════════════════════════════════════════════════════════════

def _get_embed_fn():
    """Return a ChromaDB embedding function backed by Ollama."""
    if not HAS_CHROMADB:
        return None
    try:
        return embedding_functions.OllamaEmbeddingFunction(
            url=f"{OLLAMA_BASE}/api/embeddings",
            model_name=EMBED_MODEL,
        )
    except Exception:
        return None


def _get_chroma_collection():
    """Open or create the persistent ChromaDB collection."""
    if not HAS_CHROMADB:
        return None
    os.makedirs(CHROMADB_DIR, exist_ok=True)
    try:
        client = chromadb.PersistentClient(path=CHROMADB_DIR)
        ef = _get_embed_fn()
        kwargs = {"name": MEMORY_COLLECTION}
        if ef:
            kwargs["embedding_function"] = ef
        return client.get_or_create_collection(**kwargs)
    except Exception as e:
        logger.warning("ChromaDB init failed: %s", e)
        return None


# ── Fallback flat-file memory (used when chromadb unavailable) ──────────────

def _load_flat_memory() -> dict:
    if os.path.exists(MEMORY_PATH):
        try:
            with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_flat_memory_unlocked(memory: dict):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=MEMORY_DIR,
            delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(memory, tmp, indent=2, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
            temp_path = tmp.name
        os.replace(temp_path, MEMORY_PATH)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


# ── Public memory API ────────────────────────────────────────────────────────

def tool_remember(key: str, value: str) -> str:
    """
    Store a memory.  Uses ChromaDB for semantic search when available;
    falls back to flat JSON otherwise.
    """
    doc = f"{key}: {value}"

    with MEMORY_LOCK:
        col = _get_chroma_collection()
        if col is not None:
            try:
                # Upsert by deterministic ID derived from key
                mem_id = f"mem_{abs(hash(key)) % (10**9)}"
                col.upsert(
                    ids=[mem_id],
                    documents=[doc],
                    metadatas=[{"key": key, "value": value,
                                "ts": datetime.now(timezone.utc).isoformat()}],
                )
                return f"Remembered (semantic): {key} = {value}"
            except Exception as e:
                logger.warning("ChromaDB upsert failed, falling back: %s", e)

        # Flat fallback
        memory = _load_flat_memory()
        memory[key] = value
        _save_flat_memory_unlocked(memory)
        return f"Remembered: {key} = {value}"


def tool_recall(query: str, top_n: int = MEMORY_TOP_N) -> str:
    """
    Retrieve memories.  Uses semantic similarity when ChromaDB is available.
    Empty query returns all stored memories.
    """
    with MEMORY_LOCK:
        col = _get_chroma_collection()
        if col is not None:
            try:
                count = col.count()
                if count == 0:
                    return "No memories stored yet."
                if not query:
                    results = col.get()
                    docs = results.get("documents") or []
                    metas = results.get("metadatas") or []
                    lines = []
                    for doc, meta in zip(docs, metas):
                        ts = meta.get("ts", "")
                        lines.append(f"• {doc}" + (f"  [{ts[:10]}]" if ts else ""))
                    return "\n".join(lines) if lines else "No memories stored yet."
                else:
                    results = col.query(
                        query_texts=[query],
                        n_results=min(top_n, count),
                    )
                    docs = (results.get("documents") or [[]])[0]
                    distances = (results.get("distances") or [[]])[0]
                    if not docs:
                        return "No relevant memories found."
                    lines = []
                    for doc, dist in zip(docs, distances):
                        similarity = round(1.0 - dist, 3)
                        lines.append(f"• {doc}  [similarity={similarity}]")
                    return "\n".join(lines)
            except Exception as e:
                logger.warning("ChromaDB query failed, falling back: %s", e)

        # Flat fallback
        memory = _load_flat_memory()
        if not memory:
            return "No memories stored yet."
        if not query:
            return json.dumps(memory, indent=2, ensure_ascii=False)
        # Simple substring match as degraded search
        hits = {k: v for k, v in memory.items()
                if query.lower() in k.lower() or query.lower() in str(v).lower()}
        return json.dumps(hits, indent=2) if hits else "No relevant memories found."


def retrieve_relevant_memories(query: str, top_n: int = MEMORY_TOP_N) -> str | None:
    """
    Called at turn start to inject the most relevant memories into context.
    Returns a formatted string or None if no memories exist.
    """
    if not query:
        return None
    result = tool_recall(query, top_n=top_n)
    if result in ("No memories stored yet.", "No relevant memories found."):
        return None
    return result


# ── Legacy migration ─────────────────────────────────────────────────────────

def _legacy_memory_path(workdir: str | None) -> str | None:
    if not workdir:
        return None
    return os.path.join(workdir, MEMORY_FILE)


def migrate_legacy_memory_from_workdir(workdir: str) -> bool:
    """
    One-time migration from legacy workdir memory.json → config path.
    Returns True if a legacy stale file still exists after the attempt
    so the caller can show a one-time UI hint.
    """
    legacy = _legacy_memory_path(workdir)
    if not legacy or not os.path.exists(legacy):
        return False
    if os.path.exists(MEMORY_PATH):
        # Both exist — log at DEBUG only; UI shows a single hint on startup.
        logger.debug(
            "Legacy memory at %s skipped (new path already exists at %s).",
            legacy, MEMORY_PATH,
        )
        return True   # stale file still present
    try:
        with open(legacy, "r", encoding="utf-8") as f:
            memory = json.load(f)
        with MEMORY_LOCK:
            _save_flat_memory_unlocked(memory)
        logger.info("Migrated legacy memory from %s", legacy)
    except (json.JSONDecodeError, IOError):
        pass
    return False   # migrated cleanly


# ══════════════════════════════════════════════════════════════════════════════
# §12  TASK.md / DONE.md
# ══════════════════════════════════════════════════════════════════════════════

def load_task_file(workdir: str) -> str | None:
    """
    If TASK.md exists in workdir, read and return its contents.
    Returns None if the file doesn't exist.
    """
    path = os.path.join(workdir, TASK_FILENAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return content if content else None
    except Exception as e:
        logger.warning("Could not read TASK.md: %s", e)
        return None


def write_done_file(workdir: str, summary: str):
    """
    Write DONE.md to workdir summarising the completed task.
    """
    path = os.path.join(workdir, DONE_FILENAME)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    content = f"# Task Completed\n\n**Timestamp**: {ts}\n\n## Summary\n\n{summary}\n"
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=workdir, delete=False, suffix=".tmp"
        ) as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, path)
        ui.success(f"DONE.md written → {path}")
    except Exception as e:
        logger.warning("Could not write DONE.md: %s", e)


def _extract_done_summary(messages: list) -> str:
    """
    Pull the last assistant text message to use as the DONE.md summary.
    Falls back to a generic message if none found.
    """
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = (msg.get("content") or "").strip()
            if content:
                # Trim to a reasonable length
                return content[:2000] + ("…" if len(content) > 2000 else "")
    return "Task completed (no summary available)."


# ══════════════════════════════════════════════════════════════════════════════
# §13  LIVE FILE WATCHER
# ══════════════════════════════════════════════════════════════════════════════

class _PollingWatcher(threading.Thread):
    """
    Fallback watcher using os.stat polling when watchdog is unavailable.
    Tracks mtime of all files in workdir and reports changes.
    """

    def __init__(self, workdir: str, event_queue: queue.Queue,
                 interval: float = 1.0):
        super().__init__(daemon=True, name="PollingWatcher")
        self.workdir = workdir
        self.event_queue = event_queue
        self.interval = interval
        self._stop_event = threading.Event()
        self._snapshot: dict[str, float] = {}

    def _scan(self) -> dict[str, float]:
        mtimes: dict[str, float] = {}
        for root, dirs, files in os.walk(self.workdir):
            # Prune ignored dirs
            dirs[:] = [d for d in dirs if d not in WATCH_IGNORE_NAMES]
            for fname in files:
                if fname in WATCH_IGNORE_NAMES:
                    continue
                if any(fname.endswith(s) for s in WATCH_IGNORE_SUFFIXES):
                    continue
                full = os.path.join(root, fname)
                try:
                    mtimes[full] = os.stat(full).st_mtime
                except OSError:
                    pass
        return mtimes

    def run(self):
        self._snapshot = self._scan()
        while not self._stop_event.is_set():
            time.sleep(self.interval)
            current = self._scan()
            old_keys = set(self._snapshot)
            new_keys = set(current)

            for path in new_keys - old_keys:
                self.event_queue.put(WatcherEvent(path=path, kind="created"))
            for path in old_keys - new_keys:
                self.event_queue.put(WatcherEvent(path=path, kind="deleted"))
            for path in old_keys & new_keys:
                if current[path] != self._snapshot[path]:
                    self.event_queue.put(WatcherEvent(path=path, kind="modified"))

            self._snapshot = current

    def stop(self):
        self._stop_event.set()


if HAS_WATCHDOG:
    class _WatchdogHandler(FileSystemEventHandler):
        def __init__(self, workdir: str, event_queue: queue.Queue):
            self.workdir = workdir
            self.event_queue = event_queue

        def _should_ignore(self, path: str) -> bool:
            name = os.path.basename(path)
            if name in WATCH_IGNORE_NAMES:
                return True
            if any(name.endswith(s) for s in WATCH_IGNORE_SUFFIXES):
                return True
            # Ignore .git and .venv subtrees
            rel = os.path.relpath(path, self.workdir)
            parts = rel.split(os.sep)
            return any(p in WATCH_IGNORE_NAMES for p in parts)

        def on_modified(self, event):
            if not event.is_directory and not self._should_ignore(event.src_path):
                self.event_queue.put(
                    WatcherEvent(path=event.src_path, kind="modified")
                )

        def on_created(self, event):
            if not event.is_directory and not self._should_ignore(event.src_path):
                self.event_queue.put(
                    WatcherEvent(path=event.src_path, kind="created")
                )

        def on_deleted(self, event):
            if not event.is_directory and not self._should_ignore(event.src_path):
                self.event_queue.put(
                    WatcherEvent(path=event.src_path, kind="deleted")
                )


def start_watcher(workdir: str) -> tuple[queue.Queue, object]:
    """
    Start the file watcher for workdir.
    Returns (event_queue, watcher_handle).
    Call watcher_handle.stop() to shut down.
    """
    q: queue.Queue = queue.Queue()

    if HAS_WATCHDOG:
        handler = _WatchdogHandler(workdir, q)
        observer = Observer()
        observer.schedule(handler, workdir, recursive=True)
        observer.start()
        ui.info("File watcher started (watchdog)")
        return q, observer
    else:
        watcher = _PollingWatcher(workdir, q)
        watcher.start()
        ui.info("File watcher started (polling fallback — pip install watchdog for better performance)")
        return q, watcher


def stop_watcher(handle):
    """Stop a watcher returned by start_watcher()."""
    try:
        handle.stop()
        if HAS_WATCHDOG and hasattr(handle, "join"):
            handle.join(timeout=2)
    except Exception:
        pass


def drain_watcher_events(
    event_queue: queue.Queue,
    messages: list,
    debounce: float = WATCH_DEBOUNCE_SECONDS,
) -> int:
    """
    Drain all queued watcher events and inject a system message for each
    unique changed path.  Returns the number of events injected.
    Applies simple debouncing: events within `debounce` seconds of each
    other are grouped into one message.
    """
    now = time.monotonic()
    events: dict[str, WatcherEvent] = {}

    while True:
        try:
            ev: WatcherEvent = event_queue.get_nowait()
            # Only keep events older than the debounce window
            if now - ev.ts >= debounce:
                events[ev.path] = ev
        except queue.Empty:
            break

    if not events:
        return 0

    for path, ev in events.items():
        rel = os.path.relpath(path)
        msg = f"[Watcher] File {ev.kind}: {rel}"
        messages.append({"role": "system", "content": msg})
        ui.watcher_event(rel, ev.kind)

    return len(events)


# ══════════════════════════════════════════════════════════════════════════════
# §3b  TOOL DISPATCHER
# ══════════════════════════════════════════════════════════════════════════════

def dispatch_tool(name: str, args: dict, workdir: str) -> tuple[str, bool]:
    """
    Returns (result_string, suppress).
    suppress=True means the result should never be appended to conversation
    history (used for the scratchpad tool).
    """
    if name == "read_file":
        return tool_read_file(args["path"], workdir), False
    elif name == "write_file":
        return tool_write_file(args["path"], args["content"], workdir), False
    elif name == "replace_in_file":
        return tool_replace_in_file(
            args["path"], args["old_content"], args["new_content"], workdir
        ), False
    elif name == "edit_file":
        return tool_edit_file(args["path"], args["diff_block"], workdir), False
    elif name == "list_files":
        return tool_list_files(args.get("path", "."), workdir), False
    elif name == "run_command":
        preclassified = None
        if all(k in args for k in ("_cmd_level", "_cmd_reason", "_cmd_argv")):
            preclassified = (args["_cmd_level"], args["_cmd_reason"], args["_cmd_argv"])
        return tool_run_command(
            args["command"],
            args.get("timeout", 60),
            workdir,
            approved=bool(args.get("_approved", False)),
            preclassified=preclassified,
        ), False
    elif name == "remember":
        return tool_remember(args["key"], args["value"]), False
    elif name == "recall":
        return tool_recall(args.get("query", "")), False
    elif name == "scratchpad":
        # suppress=True: result is never injected into conversation history
        return tool_scratchpad(
            args["content"], args.get("mode", "append"), workdir
        ), True
    return f"ERROR: Unknown tool '{name}'", False


# ══════════════════════════════════════════════════════════════════════════════
# §3c  SELF-HEALING DISPATCH
# ══════════════════════════════════════════════════════════════════════════════

def self_heal_dispatch(name: str, args: dict, workdir: str,
                       max_retries: int = 3) -> tuple[str, bool, bool]:
    """
    Returns (result_string, was_healed, suppress).
    suppress=True means the result should not be appended to conversation history.
    """
    result, suppress = dispatch_tool(name, args, workdir)

    if not result.startswith("ERROR:"):
        return result, False, suppress

    # Heal: File not found → fuzzy search
    if "File not found" in result and name in ("read_file", "replace_in_file", "edit_file"):
        basename = os.path.basename(args.get("path", ""))
        if basename:
            candidates = []
            base_depth = workdir.rstrip(os.sep).count(os.sep)
            for root, _dirs, files in os.walk(workdir):
                depth = root.rstrip(os.sep).count(os.sep) - base_depth
                if depth >= SELF_HEAL_MAX_SEARCH_DEPTH:
                    _dirs[:] = []
                for filename in files:
                    if basename.lower() in filename.lower():
                        rel = os.path.relpath(os.path.join(root, filename), workdir)
                        candidates.append(rel)
                        if len(candidates) >= 5:
                            break
                if len(candidates) >= 5:
                    break
            if candidates:
                return (
                    "ERROR: File not found (auto-redirect disabled for safety). "
                    "Closest matches: " + ", ".join(candidates)
                ), False, False

    # Heal: Transient run_command errors → exponential backoff
    if name == "run_command" and any(
        kw in result.lower()
        for kw in ("timed out", "connection", "temporary")
    ):
        for attempt in range(1, max_retries + 1):
            delay = 2 ** (attempt - 1)
            time.sleep(delay)
            result, suppress = dispatch_tool(name, args, workdir)
            if not result.startswith("ERROR:"):
                return (
                    f"[Self-healed: succeeded on retry {attempt} "
                    f"after {delay}s]\n{result}"
                ), True, suppress

    return result, False, suppress


# ══════════════════════════════════════════════════════════════════════════════
# §5  APPROVAL MODE
# ══════════════════════════════════════════════════════════════════════════════

def check_approval(name: str, args: dict, workdir: str,
                   approval_enabled: bool) -> tuple[bool, str]:
    """
    warn-level run_command ops always require approval.
    --approval additionally gates file overwrites.
    """
    if name == "run_command":
        if all(k in args for k in ("_cmd_level", "_cmd_reason")):
            level, reason = args["_cmd_level"], args["_cmd_reason"]
        else:
            level, reason, _ = classify_command(args.get("command", ""))
        if level == CommandStatus.WARN:
            return True, (
                "This command is potentially destructive and requires approval:\n"
                f"    {args.get('command', '')}\n"
                f"Reason: {reason}"
            )

    if not approval_enabled:
        return False, ""

    if name == "write_file":
        full = resolve(args.get("path", ""), workdir)
        if full and os.path.exists(full):
            return True, f"File already exists and will be overwritten:\n    {full}"

    return False, ""


def prompt_user_approval(message: str) -> bool:
    ui.warning(message)
    try:
        answer = input("  Proceed? (y/N) › ").strip().lower()
        return answer == "y"
    except (EOFError, KeyboardInterrupt):
        return False


# ══════════════════════════════════════════════════════════════════════════════
# §6  LLM BACKEND — Ollama + OpenAI-compatible (Groq, Together, Mistral, etc.)
# ══════════════════════════════════════════════════════════════════════════════

class LLMBackend:
    """
    Abstract base for LLM backends.  Concrete subclasses implement:
      chat(model, messages, use_tools) -> dict   (OpenAI-style message dict)
      list_models()                    -> list[str]
    """

    def chat(self, model: str, messages: list,
             use_tools: bool = True) -> dict:
        raise NotImplementedError

    def list_models(self) -> list[str]:
        return []

    def display_name(self) -> str:
        raise NotImplementedError


class OllamaBackend(LLMBackend):
    """
    Talks to a local Ollama instance via its native /api/chat endpoint.
    Tool calls are returned as Ollama-format dicts (already OpenAI-shaped).
    """

    def __init__(self, base_url: str = OLLAMA_BASE):
        self.base_url = base_url.rstrip("/")

    def display_name(self) -> str:
        return f"ollama ({self.base_url})"

    def chat(self, model: str, messages: list,
             use_tools: bool = True) -> dict:
        import requests

        payload = {"model": model, "messages": messages, "stream": False}
        if use_tools:
            payload["tools"] = TOOLS

        try:
            r = requests.post(
                f"{self.base_url}/api/chat", json=payload, timeout=300
            )
            r.raise_for_status()
            return r.json()["message"]
        except requests.exceptions.ConnectionError:
            raise OllamaClientError(
                f"Cannot reach Ollama at {self.base_url}. Run: ollama serve"
            )
        except requests.exceptions.HTTPError as e:
            if use_tools:
                try:
                    body = e.response.json().get("error", "")
                except Exception:
                    body = e.response.text if e.response is not None else ""
                if "does not support tools" in str(body).lower():
                    ui.warning(
                        f"Model {model} does not support Ollama tools; retrying without tools."
                    )
                    return self.chat(model, messages, use_tools=False)
            raise OllamaClientError(f"Ollama error: {e}") from e
        except Exception as e:
            raise OllamaClientError(f"Ollama error: {e}") from e

    def list_models(self) -> list[str]:
        import requests
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []


class OpenAICompatibleBackend(LLMBackend):
    """
    Talks to any OpenAI-compatible /v1/chat/completions endpoint.

    Tested with:
      - Groq        (https://api.groq.com/openai/v1)
      - OpenAI      (https://api.openai.com/v1)
      - Together AI (https://api.together.xyz/v1)
      - Mistral     (https://api.mistral.ai/v1)
      - LM Studio   (http://localhost:1234/v1)
      - Anything else that speaks the OpenAI chat completions spec

    Tool calls: translates OpenAI-format tool_calls back into the Ollama-style
    dict that the rest of the agent expects, so no changes are needed downstream.
    """

    def __init__(self, base_url: str, api_key: str = "",
                 provider_name: str = "openai-compat"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.provider_name = provider_name

    def display_name(self) -> str:
        return f"{self.provider_name} ({self.base_url})"

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    @staticmethod
    def _make_strict_schema(schema: dict) -> dict:
        """
        Recursively annotate a JSON schema for OpenAI strict mode:
        every object node needs additionalProperties=false.
        """
        if not isinstance(schema, dict):
            return schema

        schema = dict(schema)
        if schema.get("type") == "object":
            schema["additionalProperties"] = False
            if "properties" in schema and isinstance(schema["properties"], dict):
                schema["properties"] = {
                    k: OpenAICompatibleBackend._make_strict_schema(v)
                    for k, v in schema["properties"].items()
                }
        if "items" in schema:
            schema["items"] = OpenAICompatibleBackend._make_strict_schema(schema["items"])
        return schema

    def _openai_tools(self) -> list:
        """Convert our tool schema to OpenAI function-calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "parameters": self._make_strict_schema(t["function"]["parameters"]),
                    "strict": True,
                },
            }
            for t in TOOLS
        ]

    def _normalise_response(self, oai_message: dict) -> dict:
        """
        Translate an OpenAI-format message dict into the Ollama-style dict
        the rest of the agent consumes:
          {
            "role": "assistant",
            "content": "...",
            "tool_calls": [
              {"function": {"name": "...", "arguments": {...}}}
            ]
          }
        """
        role = oai_message.get("role", "assistant")
        content = oai_message.get("content") or ""

        raw_tool_calls = oai_message.get("tool_calls") or []
        tool_calls = []
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            # OpenAI returns arguments as a JSON string; parse it
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = raw_args
            tool_calls.append({"function": {"name": name, "arguments": args}})

        result = {"role": role, "content": content}
        if tool_calls:
            result["tool_calls"] = tool_calls
        return result

    def chat(self, model: str, messages: list,
             use_tools: bool = True, _max_retries: int = 4) -> dict:
        import requests

        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if use_tools:
            payload["tools"] = self._openai_tools()
            payload["tool_choice"] = "auto"

        for attempt in range(1, _max_retries + 1):
            try:
                r = requests.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                    timeout=300,
                )

                # Handle 429 rate-limit with backoff before raising
                if r.status_code == 429:
                    retry_after = self._parse_retry_after(r)
                    if attempt < _max_retries:
                        wait = retry_after or min(2 ** attempt, 60)
                        logger.warning(
                            "%s rate-limited (attempt %d/%d). "
                            "Waiting %.1fs…",
                            self.provider_name, attempt, _max_retries, wait,
                        )
                        ui.warning(
                            f"{self.provider_name} rate limit hit "
                            f"(attempt {attempt}/{_max_retries}). "
                            f"Waiting {wait:.0f}s…"
                        )
                        time.sleep(wait)
                        continue
                    # Last attempt — let raise_for_status surface the error
                r.raise_for_status()
                data = r.json()
                oai_message = data["choices"][0]["message"]
                return self._normalise_response(oai_message)

            except requests.exceptions.ConnectionError:
                raise OllamaClientError(
                    f"Cannot reach {self.provider_name} at {self.base_url}."
                )
            except requests.exceptions.HTTPError as e:
                body = ""
                try:
                    body = e.response.json().get("error", {}).get("message", "")
                except Exception:
                    pass
                raise OllamaClientError(
                    f"{self.provider_name} API error {e.response.status_code}"
                    + (f": {body}" if body else "")
                ) from e
            except OllamaClientError:
                raise
            except Exception as e:
                raise OllamaClientError(
                    f"{self.provider_name} error: {e}"
                ) from e

        raise OllamaClientError(f"{self.provider_name}: max retries exceeded.")

    @staticmethod
    def _parse_retry_after(response) -> float | None:
        """
        Parse wait time from a 429 response.
        Checks Retry-After header first, then the error message body
        (Groq embeds 'try again in X.XXs' in the error text).
        """
        import re as _re
        # Standard Retry-After header (seconds or HTTP date — we only handle seconds)
        ra = response.headers.get("Retry-After") or response.headers.get("retry-after")
        if ra:
            try:
                return float(ra) + 0.5   # small buffer
            except ValueError:
                pass
        # Groq-style: "Please try again in 6.06s."
        try:
            body = response.json().get("error", {}).get("message", "")
            m = _re.search(r"try again in ([0-9]+(?:\.[0-9]+)?)s", body, _re.I)
            if m:
                return float(m.group(1)) + 0.5
        except Exception:
            pass
        return None

    def list_models(self) -> list[str]:
        import requests
        try:
            r = requests.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=5,
            )
            r.raise_for_status()
            data = r.json()
            # OpenAI format: {"data": [{"id": "model-name"}, ...]}
            return [m["id"] for m in data.get("data", [])]
        except Exception:
            return []


def make_backend(backend_type: str, api_base: str,
                 api_key: str) -> LLMBackend:
    """
    Factory.  backend_type is one of: ollama, openai, groq, together,
    mistral, lmstudio, or "auto" (infer from api_base URL).
    """
    bt = backend_type.lower().strip()

    if bt == "ollama":
        return OllamaBackend(base_url=api_base or OLLAMA_BASE)

    # Known provider defaults
    PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
        "groq":     ("https://api.groq.com/openai/v1", "Groq"),
        "openai":   ("https://api.openai.com/v1",      "OpenAI"),
        "together": ("https://api.together.xyz/v1",    "Together AI"),
        "mistral":  ("https://api.mistral.ai/v1",      "Mistral"),
        "lmstudio": ("http://localhost:1234/v1",        "LM Studio"),
    }

    if bt in PROVIDER_DEFAULTS:
        default_url, name = PROVIDER_DEFAULTS[bt]
        return OpenAICompatibleBackend(
            base_url=api_base or default_url,
            api_key=api_key,
            provider_name=name,
        )

    # Auto-detect from URL
    if bt == "auto" or not bt:
        if not api_base or "localhost:11434" in api_base:
            return OllamaBackend(base_url=api_base or OLLAMA_BASE)
        return OpenAICompatibleBackend(
            base_url=api_base,
            api_key=api_key,
            provider_name=_infer_provider_name(api_base),
        )

    # Fallback: treat unknown backend type as a generic OpenAI-compat endpoint
    return OpenAICompatibleBackend(
        base_url=api_base,
        api_key=api_key,
        provider_name=bt,
    )


def _infer_provider_name(url: str) -> str:
    """Best-effort friendly name from a URL."""
    for fragment, name in [
        ("groq.com",    "Groq"),
        ("openai.com",  "OpenAI"),
        ("together",    "Together AI"),
        ("mistral.ai",  "Mistral"),
        ("localhost",   "local"),
        ("127.0.0.1",   "local"),
    ]:
        if fragment in url:
            return name
    return "openai-compat"


# ── Module-level backend instance (set in main(), used everywhere) ───────────
_backend: LLMBackend = OllamaBackend()


def chat_llm(model: str, messages: list, use_tools: bool = True) -> dict:
    """Single call-site wrapper used throughout the agent."""
    return _backend.chat(model, messages, use_tools)


def list_available_models() -> list[str]:
    return _backend.list_models()


# ══════════════════════════════════════════════════════════════════════════════
# §7  SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
You are an expert coding assistant running locally on the user's machine.
You have a sandboxed working directory where you can read/write files and run
shell commands.

Available tools:
  read_file       — Read a file's contents.
  write_file      — Write or overwrite a file (parent dirs auto-created).
  replace_in_file — Surgically replace a specific string in a file.
  list_files      — List files and directories at a given path.
  run_command     — Execute shell commands (Python 3, pip, git, unix tools).
  remember        — Save a fact/preference to semantic memory (persists across sessions).
  recall          — Retrieve semantically relevant memories by natural language query.
  scratchpad      — Write PRIVATE reasoning/planning. Never shown to user or in history.

Rules:
1. MANDATORY: Before calling two or more tools in sequence, you MUST first call
    `scratchpad` with your full step-by-step plan: what each tool will do, what
    success looks like, and what you will do if a step fails. Do not skip this.
2. Always use tools — never just describe what to do.
3. For file edits: use `edit_file` with diff block for multi-line changes, 
   `replace_in_file` for single-line substitutions, `write_file` only for 
   new files. Always read the file before editing if unsure of contents.
4. Read command output and fix errors automatically — do not give up easily.
5. Stay within the working directory.
6. Never ask the user to run commands manually.
7. Use remember to store user preferences you discover.
8. When writing Python files, consider whether a test file is appropriate.
9. Use recall with a relevant query before starting a task to surface useful context.
"""


# ══════════════════════════════════════════════════════════════════════════════
# §7b  CONVERSATION SUMMARISATION
# ══════════════════════════════════════════════════════════════════════════════

def maybe_summarise(model: str, messages: list, turn: int,
                    summarise_every: int,
                    keep_recent_messages: int,
                    display: Display) -> list:
    if turn % summarise_every != 0 or turn == 0:
        return messages
    if len(messages) < keep_recent_messages + 4:
        return messages

    to_summarise = []
    for m in messages[1:-keep_recent_messages]:
        role = m.get("role", "?")
        content = (m.get("content") or "")[:300]
        to_summarise.append(f"[{role}]: {content}")

    summary_request = [
        {
            "role": "system",
            "content": (
                "Summarise the following conversation in 3-5 sentences. "
                "Focus on: what the user asked, what files were created or "
                "modified, what worked, and what failed. Be concise."
            ),
        },
        {"role": "user", "content": "\n".join(to_summarise)[:3000]},
    ]

    try:
        display.info("Summarising conversation history…")
        response = chat_llm(model, summary_request, use_tools=False)
        summary = (response.get("content") or "").strip()
        if summary:
            new_messages = [
                messages[0],
                {"role": "system",
                 "content": f"[Conversation summary (turns 1-{turn})]: {summary}"},
                *messages[-keep_recent_messages:],
            ]
            display.success(
                f"Compressed {len(messages)} messages → {len(new_messages)}"
            )
            return new_messages
    except OllamaClientError as e:
        display.warning(f"Summarisation failed (non-fatal): {e}")

    return messages


# ══════════════════════════════════════════════════════════════════════════════
# §10  AUTO-TEST HOOK
# ══════════════════════════════════════════════════════════════════════════════

def maybe_suggest_test(tool_name: str, tool_args: dict,
                       auto_test: bool,
                       preferred_test_framework: str = "pytest") -> str | None:
    if not auto_test or tool_name not in ("write_file", "edit_file"):
        return None
    path = tool_args.get("path", "")
    if not path.endswith(".py"):
        return None
    basename = os.path.basename(path)
    if basename.startswith("test_") or basename.endswith("_test.py"):
        return None
    test_name = f"test_{basename}"
    framework = str(preferred_test_framework or "pytest").strip().lower()
    if framework == "unittest":
        run_cmd = f"python -m unittest -v {os.path.splitext(test_name)[0]}"
    else:
        run_cmd = f"python -m pytest {test_name} -v"
    return (
        f"[Auto-test] You wrote {path}. Write a unit test file "
        f"({test_name}) and run it with: {run_cmd}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# §MAIN  STATE MACHINE — Planner → Executor → Observer
# ══════════════════════════════════════════════════════════════════════════════

def run_agent(model: str, user_input: str, messages: list,
              workdir: str, config: AgentConfig,
              watcher_queue: queue.Queue | None = None,
              task_mode: bool = False) -> list:
    """
    Main agentic loop.

    task_mode=True: running from TASK.md.  On completion, write DONE.md.
    watcher_queue: if provided, drain watcher events at the start of each turn.
    """
    messages.append({"role": "user", "content": user_input})

    def _args_preview(args: dict) -> dict:
        return {k: v for k, v in args.items() if not str(k).startswith("_")}

    for turn in range(1, config.max_turns + 1):
        journal_append(workdir, {
            "type": "turn_start",
            "turn": turn,
            "user_input_preview": user_input[:100],
        })

        # ── Drain watcher events ─────────────────────────────────────────
        if watcher_queue is not None:
            n = drain_watcher_events(watcher_queue, messages)
            if n:
                ui.info(f"Injected {n} watcher event(s) into context.")

        # ── Load per-turn memory snapshot ────────────────────────────────
        memory_snapshot_raw = tool_recall(user_input, top_n=MEMORY_TOP_N)
        relevant_memories = (
            memory_snapshot_raw
            if memory_snapshot_raw not in (
                "No memories stored yet.", "No relevant memories found."
            ) else None
        )

        # Inject / update relevant memories
        if relevant_memories:
            mem_msg = {
                "role": "system",
                "content": (
                    f"[Relevant memories for this task]:\n{relevant_memories}"
                ),
            }
            replaced = False
            for i, m in enumerate(messages):
                if (m.get("role") == "system"
                        and m.get("content", "").startswith(
                            "[Relevant memories for this task]"
                        )):
                    messages[i] = mem_msg
                    replaced = True
                    break
            if not replaced:
                messages.insert(1, mem_msg)

        # ── preferred_test_framework ─────────────────────────────────────
        tf_result = tool_recall("preferred_test_framework", top_n=1)
        preferred_test_framework = "pytest"
        if "pytest" in tf_result.lower():
            preferred_test_framework = "pytest"
        elif "unittest" in tf_result.lower():
            preferred_test_framework = "unittest"

        # ── PHASE 1: PLAN ────────────────────────────────────────────────
        ui.phase("PLAN", turn, config.max_turns)
        ui.thinking_start(f"Planning (turn {turn})")
        try:
            response = chat_llm(model, messages)
        except OllamaClientError as e:
            ui.thinking_stop()
            ui.error(str(e))
            messages.append({"role": "assistant", "content": str(e)})
            break
        ui.thinking_stop()

        tool_calls = response.get("tool_calls") or []
        text_content = (response.get("content") or "").strip()

        if text_content:
            ui.assistant(text_content)

        if not tool_calls:
            messages.append({"role": "assistant", "content": text_content})
            if task_mode:
                summary = _extract_done_summary(messages)
                write_done_file(workdir, summary)
            break

        messages.append(response)

        # ── PHASE 2: EXECUTE ─────────────────────────────────────────────
        ui.phase("EXECUTE", turn, config.max_turns)

        # 2a. Parse + pre-classify
        tasks = []
        for tc in tool_calls:
            fn = tc["function"]["name"]
            args = tc["function"].get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError as e:
                    tasks.append((fn, {}, f"ERROR: Malformed JSON for {fn}: {e}."))
                    continue
            if fn == "run_command":
                level, reason, argv = classify_command(args.get("command", ""))
                args["_cmd_level"] = level
                args["_cmd_reason"] = reason
                args["_cmd_argv"] = argv
            tasks.append((fn, args, None))

        # 2b. Approval gate
        for i, (fn, args, err) in enumerate(tasks):
            if err is not None:
                continue
            needs, msg = check_approval(fn, args, workdir, config.approval)
            if needs:
                if not prompt_user_approval(msg):
                    tasks[i] = (fn, args, "BLOCKED: User denied execution.")
                elif fn == "run_command":
                    args["_approved"] = True

        # 2c. Split
        to_execute = [(fn, args) for fn, args, err in tasks if err is None]
        blocked = [(fn, err) for fn, _, err in tasks if err is not None]
        all_results: list[tuple[str, str, bool]] = []
        test_nudges: list[str] = []

        for fn, err in blocked:
            ui.tool_call(fn, {})
            ui.tool_result(err, is_error=True)
            all_results.append((fn, err, False))

        # 2d. Execute
        if len(to_execute) > 1:
            ui.info(f"⚡ Dispatching {len(to_execute)} tools in parallel")
            with ThreadPoolExecutor(max_workers=4) as pool:
                future_map = {}
                for idx, (fn, args) in enumerate(to_execute):
                    ts_start = time.monotonic()
                    journal_append(workdir, {
                        "type": "tool_call",
                        "turn": turn,
                        "name": fn,
                        "args_preview": _args_preview(args),
                        "ts_start": ts_start,
                    })
                    ui.tool_call(fn, args)
                    fut = pool.submit(self_heal_dispatch, fn, args, workdir)
                    future_map[fut] = (idx, fn, args, ts_start)

                ordered_results: dict[int, tuple[str, str, bool]] = {}

                for fut in as_completed(future_map):
                    idx, fn, args, ts_start = future_map[fut]
                    try:
                        result, healed, suppress = fut.result()
                    except Exception as exc:
                        result, healed, suppress = f"ERROR: Thread exception: {exc}", False, False

                    journal_append(workdir, {
                        "type": "tool_result",
                        "turn": turn,
                        "name": fn,
                        "success": not (result.startswith("ERROR:") or result.startswith("BLOCKED:")),
                        "healed": healed,
                        "suppressed": suppress,
                        "latency_ms": int((time.monotonic() - ts_start) * 1000),
                    })

                    if suppress:
                        continue   # scratchpad: display only, never in history
                    ui.tool_result(result, tool_name=fn, tool_args=args, healed=healed)
                    ordered_results[idx] = (fn, result, healed)
                    nudge = maybe_suggest_test(
                        fn, args, config.auto_test, preferred_test_framework
                    )
                    if nudge:
                        test_nudges.append(nudge)

                for idx in sorted(ordered_results):
                    all_results.append(ordered_results[idx])
        else:
            for fn, args in to_execute:
                ts_start = time.monotonic()
                journal_append(workdir, {
                    "type": "tool_call",
                    "turn": turn,
                    "name": fn,
                    "args_preview": _args_preview(args),
                    "ts_start": ts_start,
                })
                ui.tool_call(fn, args)
                result, healed, suppress = self_heal_dispatch(fn, args, workdir)

                journal_append(workdir, {
                    "type": "tool_result",
                    "turn": turn,
                    "name": fn,
                    "success": not (result.startswith("ERROR:") or result.startswith("BLOCKED:")),
                    "healed": healed,
                    "suppressed": suppress,
                    "latency_ms": int((time.monotonic() - ts_start) * 1000),
                })

                if suppress:
                    continue   # scratchpad: display only, never in history
                ui.tool_result(result, tool_name=fn, tool_args=args, healed=healed)
                all_results.append((fn, result, healed))
                nudge = maybe_suggest_test(
                    fn, args, config.auto_test, preferred_test_framework
                )
                if nudge:
                    test_nudges.append(nudge)

        # Scratchpad-only turns produce suppressed results; inject an ack so the
        # model can continue from planning to execution on the next turn.
        if not all_results and not blocked and to_execute:
            messages.append({
                "role": "tool",
                "content": "Scratchpad written. Proceed with execution.",
            })

        # 2e. Append tool results (scratchpad results already filtered out)
        for _fn, result, _healed in all_results:
            ctx = (
                result if len(result) <= CONTEXT_CHAR_LIMIT
                else result[:CONTEXT_CHAR_LIMIT]
                + "\n...[TRUNCATED] Use head/grep to see more"
            )
            messages.append({"role": "tool", "content": ctx})

        # ── PHASE 3: OBSERVE ─────────────────────────────────────────────
        ui.phase("OBSERVE", turn, config.max_turns)

        errors = [
            r for _, r, _ in all_results
            if r.startswith("ERROR:") or r.startswith("BLOCKED:")
        ]
        heals = sum(1 for _, _, h in all_results if h)
        observer_parts = []
        if errors:
            observer_parts.append(
                f"{len(errors)} tool(s) failed: "
                + "; ".join(e[:80] for e in errors[:3])
            )
        if heals:
            observer_parts.append(f"{heals} error(s) were self-healed.")
        if test_nudges:
            observer_parts.extend(test_nudges)

        observer_msg_len = 0
        if observer_parts:
            obs = "[Observer] " + " | ".join(observer_parts) + " — Re-plan."
            observer_msg_len = len(obs)
            messages.append({"role": "system", "content": obs})
            ui.observer(obs)
        else:
            ui.success("All tools executed successfully.")

        messages = maybe_summarise(
            model, messages, turn,
            config.summarise_every, config.summary_keep_messages, ui,
        )

        journal_append(workdir, {
            "type": "turn_end",
            "turn": turn,
            "had_errors": bool(errors),
            "tool_count": len(to_execute),
            "observer_msg_len": observer_msg_len,
        })

    else:
        # Turn limit reached
        ui.warning(f"Turn limit ({config.max_turns}) reached. Requesting final answer…")
        messages.append({
            "role": "system",
            "content": (
                "TURN LIMIT REACHED. Provide your best answer now based on "
                "what you have accomplished. Do NOT call any more tools."
            ),
        })
        ui.thinking_start("Final answer")
        try:
            response = chat_llm(model, messages, use_tools=False)
        except OllamaClientError as e:
            ui.thinking_stop()
            ui.error(str(e))
            messages.append({"role": "assistant", "content": str(e)})
            return messages
        ui.thinking_stop()
        text = (response.get("content") or "").strip()
        if text:
            ui.assistant(text)
        messages.append({"role": "assistant", "content": text})
        if task_mode:
            write_done_file(workdir, _extract_done_summary(messages))

    return messages


# ══════════════════════════════════════════════════════════════════════════════
# §9  /selfreview
# ══════════════════════════════════════════════════════════════════════════════

def _split_into_sections(source: str, chars_per_chunk: int = 6000) -> list[str]:
    """
    Split source into reviewable chunks aligned to top-level definitions.
    Each chunk is at most `chars_per_chunk` characters.  We prefer to break
    at 'def ' / 'class ' boundaries at column 0 so the model sees complete
    functions rather than arbitrary mid-function cuts.
    """
    import re
    # Find all top-level def/class start positions
    boundaries = [m.start() for m in re.finditer(r'^(?:def |class )', source, re.M)]
    boundaries.append(len(source))  # sentinel

    chunks: list[str] = []
    current_start = 0

    for boundary in boundaries[1:]:
        # Would adding up to this boundary exceed our limit?
        if boundary - current_start >= chars_per_chunk:
            # Flush what we have so far
            chunk = source[current_start:boundary].strip()
            if chunk:
                chunks.append(chunk)
            current_start = boundary

    # Flush the final remainder
    tail = source[current_start:].strip()
    if tail:
        chunks.append(tail)

    return chunks or [source]


def run_selfreview(model: str):
    """
    Review the agent's own source code section by section, then synthesise
    a final consolidated report.  This avoids the head+tail truncation that
    caused earlier versions to only review the argument-parsing boilerplate.
    """
    ui.info("Running self-review of agent code…")
    try:
        with open(__file__, "r", encoding="utf-8") as f:
            source = f.read()
    except Exception:
        ui.error("Could not read own source code.")
        return

    lines = source.count("\n")
    ui.info(f"Source: {lines} lines, {len(source):,} chars")

    review_system = (
        "You are a senior Python code reviewer. Analyse the following "
        "code for bugs, anti-patterns, security issues, and improvement "
        "suggestions. Be specific. Cite approximate line numbers where "
        "possible. Format as a bulleted list grouped by severity: "
        "Critical / Warning / Info. If a section has no issues, say so briefly."
    )

    chunks = _split_into_sections(source, chars_per_chunk=6000)
    ui.info(f"Reviewing in {len(chunks)} chunk(s)…")

    chunk_reviews: list[str] = []

    # Estimate tokens per chunk to decide inter-chunk pacing.
    # Groq free tier: 12k TPM for llama-3.3-70b.  ~4 chars ≈ 1 token.
    # We pause between chunks when using an OpenAI-compat backend.
    is_rate_limited_backend = isinstance(_backend, OpenAICompatibleBackend)
    avg_chunk_tokens = sum(len(c) for c in chunks) // max(len(chunks), 1) // 4

    for i, chunk in enumerate(chunks, 1):
        # Pace requests to stay under TPM on rate-limited backends
        if is_rate_limited_backend and i > 1:
            pause = max(3.0, avg_chunk_tokens / 180)   # ~180 tok/s safety margin
            ui.info(f"Pacing: waiting {pause:.0f}s before chunk {i} (TPM guard)…")
            time.sleep(pause)

        # Extract a label from the first def/class in this chunk
        first_line = chunk.split("\n")[0][:80]
        ui.thinking_start(f"Reviewing chunk {i}/{len(chunks)}: {first_line}")
        try:
            resp = chat_llm(model, [
                {"role": "system", "content": review_system},
                {"role": "user",
                 "content": f"# Chunk {i} of {len(chunks)}\n\n{chunk}"},
            ], use_tools=False)
            ui.thinking_stop()
            text = (resp.get("content") or "").strip()
            if text:
                chunk_reviews.append(f"## Chunk {i}: {first_line}\n\n{text}")
        except OllamaClientError as e:
            ui.thinking_stop()
            ui.error(f"Chunk {i} review failed: {e}")
            continue

    if not chunk_reviews:
        ui.warning("No review output produced.")
        return

    if len(chunk_reviews) == 1:
        # Single chunk — just display directly
        ui.assistant(chunk_reviews[0])
        return

    # Synthesise all chunk reviews into a consolidated report
    ui.thinking_start("Synthesising final report…")
    combined = "\n\n---\n\n".join(chunk_reviews)
    try:
        synth_resp = chat_llm(model, [
            {
                "role": "system",
                "content": (
                    "You are a senior Python code reviewer. You have reviewed a "
                    "large codebase in chunks. Below are the per-chunk reviews. "
                    "Produce a single consolidated report: de-duplicate findings, "
                    "promote the most critical issues to the top, and group by "
                    "severity (Critical / Warning / Info). Be concise."
                ),
            },
            {"role": "user", "content": combined[:12000]},
        ], use_tools=False)
        ui.thinking_stop()
        final = (synth_resp.get("content") or "").strip()
        if final:
            ui.assistant(final)
        else:
            # Fallback: just print the raw chunk reviews
            for review in chunk_reviews:
                ui.assistant(review)
    except OllamaClientError as e:
        ui.thinking_stop()
        ui.error(f"Synthesis failed: {e}")
        for review in chunk_reviews:
            ui.assistant(review)


# ══════════════════════════════════════════════════════════════════════════════
# §12  MAIN / REPL
# ══════════════════════════════════════════════════════════════════════════════

def main():
    global ui

    if sys.version_info < (3, 10):
        print("This script requires Python 3.10+.")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Hera v2.4 — Claude Code-style local assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python llm_code_agent.py --model qwen2.5:7b\n"
            "  python llm_code_agent.py --backend groq --model llama-3.3-70b-versatile\n"
            "  python llm_code_agent.py --backend openai --model gpt-4o\n"
            "  python llm_code_agent.py --api-base http://localhost:1234/v1 --model local-model\n"
            "  python llm_code_agent.py --workdir ./proj --watch\n"
            "  GROQ_API_KEY=gsk_... python llm_code_agent.py --backend groq\n"
        ),
    )
    parser.add_argument("--model", default="gemma4:e2b",
        help="Model name (default: gemma4:e2b)")
    parser.add_argument(
        "--backend", default="auto",
        help=(
            "LLM backend: ollama (default), groq, openai, together, "
            "mistral, lmstudio, or auto. "
            "Set HERA_BACKEND env var as alternative."
        ),
    )
    parser.add_argument(
        "--api-base", default="", dest="api_base",
        help=(
            "Override the API base URL. "
            "E.g. https://api.groq.com/openai/v1  "
            "Set HERA_API_BASE env var as alternative."
        ),
    )
    parser.add_argument(
        "--api-key", default="", dest="api_key",
        help=(
            "API key for OpenAI-compatible backends. "
            "Set HERA_API_KEY or GROQ_API_KEY or OPENAI_API_KEY env var "
            "as alternative (checked in that order)."
        ),
    )
    parser.add_argument(
        "--workdir",
        default=os.path.join(os.getcwd(), "agent_workspace"),
        help="Working directory (default: ./agent_workspace)",
    )
    parser.add_argument(
        "--approval", action="store_true",
        help=(
            "Enable extra approval prompts (e.g. file overwrite). "
            "Destructive shell commands always require approval."
        ),
    )
    parser.add_argument(
        "--max-turns", type=int, default=MAX_TURNS_DEFAULT, dest="max_turns"
    )
    parser.add_argument(
        "--summarise-every", type=int, default=DEFAULT_SUMMARISE_EVERY,
        dest="summarise_every",
    )
    parser.add_argument(
        "--summary-keep", type=int, default=DEFAULT_SUMMARY_KEEP_MESSAGES,
        dest="summary_keep_messages",
    )
    parser.add_argument("--auto-test", action="store_true", dest="auto_test")
    parser.add_argument(
        "--watch", action="store_true",
        help="Watch workdir for external file changes and notify the agent.",
    )
    parser.add_argument("--no-rich", action="store_true", dest="no_rich")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--eval", action="store_true",
        help="Run the eval harness (tests/eval/run_evals.py) and exit.",
    )
    parser.add_argument(
        "--eval-update-baseline", action="store_true", dest="eval_update_baseline",
        help="When used with --eval, rewrite tests/eval/results/baseline.json.",
    )
    args = parser.parse_args()

    log_level = logging.WARNING
    if args.debug:
        log_level = logging.DEBUG
    elif args.verbose:
        log_level = logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = AgentConfig(
        approval=args.approval,
        max_turns=args.max_turns,
        auto_test=args.auto_test,
        watch=args.watch,
        summarise_every=max(2, args.summarise_every),
        summary_keep_messages=max(2, args.summary_keep_messages),
    )

    use_rich = HAS_RICH and not args.no_rich
    ui = Display(use_rich=use_rich)

    if args.eval:
        eval_script = Path(__file__).resolve().parent / "tests" / "eval" / "run_evals.py"
        if not eval_script.exists():
            print(f"Eval harness not found: {eval_script}")
            sys.exit(1)

        cmd = [
            sys.executable,
            str(eval_script),
            "--model", args.model,
            "--backend", args.backend,
            "--max-turns", str(args.max_turns),
        ]
        if args.api_base:
            cmd.extend(["--api-base", args.api_base])
        if args.api_key:
            cmd.extend(["--api-key", args.api_key])
        if args.auto_test:
            cmd.append("--auto-test")
        if args.eval_update_baseline:
            cmd.append("--update-baseline")

        sys.exit(subprocess.run(cmd).returncode)

    # ── Resolve backend credentials (CLI > env vars) ─────────────────────
    api_key = (
        args.api_key
        or os.environ.get("HERA_API_KEY", "")
        or os.environ.get("GROQ_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
        or os.environ.get("TOGETHER_API_KEY", "")
        or os.environ.get("MISTRAL_API_KEY", "")
    )
    api_base = args.api_base or os.environ.get("HERA_API_BASE", "")
    backend_type = args.backend or os.environ.get("HERA_BACKEND", "auto")

    global _backend
    _backend = make_backend(backend_type, api_base, api_key)

    model = args.model
    workdir = os.path.realpath(args.workdir)
    os.makedirs(workdir, exist_ok=True)
    legacy_stale = migrate_legacy_memory_from_workdir(workdir)

    # Venv
    venv_path = os.path.join(workdir, ".venv")
    if not os.path.exists(venv_path):
        ui.info(f"Creating virtual environment in {workdir}…")
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", venv_path],
                check=True, capture_output=True,
            )
            ui.success("Virtual environment created.")
        except subprocess.CalledProcessError as e:
            err = (
                e.stderr.decode("utf-8", errors="ignore").strip()
                if e.stderr else "Unknown error"
            )
            ui.warning(f"Failed to create .venv: {err}")

    ui.banner(model)

    available = list_available_models()
    if available:
        ui.info(f"Available models: {', '.join(available[:8])}{'…' if len(available) > 8 else ''}")
    else:
        ui.warning("Could not list models from backend.")

    ui.info(f"Backend: {_backend.display_name()}")
    ui.info(f"Model  : {model}")
    ui.info(f"Workdir: {workdir}")

    # Memory backend
    if HAS_CHROMADB:
        ui.info(f"Memory : semantic (chromadb + {EMBED_MODEL})")
    else:
        ui.info("Memory : flat JSON fallback (pip install chromadb for semantic search)")
    if legacy_stale:
        ui.warning(
            f"Old memory.json found in workdir.  It has been superseded by "
            f"{MEMORY_PATH}\n"
            f"  Delete the old file to stop this hint: "
            f"rm '{_legacy_memory_path(workdir)}'"
        )

    if config.approval:
        ui.info("Approval mode: ON")
    if config.auto_test:
        ui.info("Auto-test: ON")
    if config.watch:
        ui.info("File watcher: ON")
    ui.info(f"Max turns: {config.max_turns}")
    ui.info(
        f"Summarisation: every {config.summarise_every} turns, "
        f"keep last {config.summary_keep_messages} messages"
    )
    print()

    # File watcher
    watcher_queue: queue.Queue | None = None
    watcher_handle = None
    if config.watch:
        watcher_queue, watcher_handle = start_watcher(workdir)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # ── TASK.md auto-load ────────────────────────────────────────────────
    task_content = load_task_file(workdir)
    if task_content:
        ui.info(f"TASK.md found — running in task mode.")
        ui.info(f"Task: {task_content[:120]}{'…' if len(task_content) > 120 else ''}")
        print()
        messages = run_agent(
            model, task_content, messages, workdir, config,
            watcher_queue=watcher_queue,
            task_mode=True,
        )
        # In task mode, exit after completion (CI-friendly)
        if watcher_handle:
            stop_watcher(watcher_handle)
        return

    # ── Interactive REPL ─────────────────────────────────────────────────
    while True:
        user_input = ui.prompt()
        if user_input is None:
            ui.info("Bye!")
            break

        if not user_input:
            continue

        if user_input == "/exit":
            ui.info("Bye!")
            break

        if user_input == "/clear":
            messages = [messages[0]]
            ui.success("Session cleared.")
            continue

        if user_input == "/workdir":
            ui.info(f"Working directory: {workdir}")
            continue

        if user_input == "/memory":
            result = tool_recall("")
            ui.info("Stored memories:\n" + result)
            continue

        if user_input == "/scratch":
            ui.info("Scratchpad contents:\n" + read_scratchpad(workdir))
            continue

        if user_input.startswith("/model"):
            parts = user_input.split()
            if len(parts) == 2:
                model = parts[1]
                ui.success(f"Switched to: {model}")
            else:
                ui.info(f"Current model: {model}  |  /model <name>")
            continue

        if user_input == "/selfreview":
            run_selfreview(model)
            continue

        messages = run_agent(
            model, user_input, messages, workdir, config,
            watcher_queue=watcher_queue,
        )

    if watcher_handle:
        stop_watcher(watcher_handle)


if __name__ == "__main__":
    main()