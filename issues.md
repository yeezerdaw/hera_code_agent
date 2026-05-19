# Hera Project: Issues & Tasks Tracking

Use this file to log active bugs, necessary enhancements, and developer tasks. 

## Active Issues & Bugs
- [x] **LLM JSON Output Hallucination**: Model occasionally outputs raw JSON strings that look like function calls, causing empty `tool_calls` lists and the agent terminating prematurely. Implemented regex heuristic check to nudge the model as an observer.
- [x] **search_codebase Journal Poisoning**: Agent journal logs (`.agent_journal.jsonl`) were cluttering search results. Appended `.jsonl` to `WATCH_IGNORE_SUFFIXES` and added specific file-targeting advice to `search_codebase` tool description.
- [x] **Missing Tool Arguments KeyError Crash**: The agent occasionally crashes when the LLM calls a tool without required arguments (e.g., calling `edit_file` without `path`).
- [x] **LLM Playwright Selector Hallucinations**: The LLM fails to correctly use the `browser_interact` tool due to a lack of understanding of Playwright text locators. It uses overly broad CSS selectors (e.g., `a`) causing Playwright strict mode errors, or hallucinates that it cannot interact with the live page at all.
  - *Fix Required*: Update the `SYSTEM_PROMPT` in `llm_code_agent.py` to include a "Selector Cheat-Sheet" that explicitly teaches the LLM to use specific, strict text matchers (e.g., `text="Next"` or `a:has-text("Ask")`) and confirms it has permissions to interact dynamically.
- [x] **UnboundLocalError in journal_append**: `finally` block attempts to close `fd` unassigned if `os.open()` fails. Switch to standard `with open(..., "a") as f:`
- [x] **Race Condition in Parallel Tool Execution**: `ThreadPoolExecutor` might silently overwrite files if multiple file write/edit tools target the same file in one turn. Needs per-file locking or sequential execution for mutating tools.
- [x] **Context Window Blowout**: Current `maybe_summarise` relies on turn count. Should be refactored to trigger based on a rolling token/character counter to avoid API 400/500s.
- [x] **ChromaDB Hash Determinism**: Python `hash()` is non-deterministic, duplicate entries stored.
- [x] **Atomic Write Temp File Leak**: Temp files left on disk if `os.replace` fails in tool functions.

### Refactoring & Tech Debt
- [x] **Global State (`ui`)**: Refactored global `ui` using a `UIProxy` to fix thread-safety and testing issues.
- [x] **Global State (`_backend`)**: Refactor global `_backend` into an `AgentContext` dataclass for better testability.
- [ ] **Diff Parsing Robustness**: Switch from `<<<<<<< SEARCH` regex to Unified Diff Format or line-number-based patches.
- [ ] **Type Hinting**: Apply `typing.TypedDict` for OpenAI message formats.
- [ ] **Blocking Network Calls**: `time.sleep()` in OpenAI 429 handler blocks the main thread. Note as tech debt if moving to `asyncio`.
- [ ] **Parallel Tool Execution Warning**: `messages` appending is single-threaded but `self_heal_dispatch` worker threads run concurrently. (Low Severity).
- [ ] **Sandbox Weakness**: `tool_run_command` allows `HOME` in `SAFE_ENV_ALLOWLIST`, leaking `~/.pth` and `usercustomize.py` to the subprocess Python.
- [x] **`_split_into_sections` Bug**: Fails to chunk correctly if there is only one top-level definition, silently merging all content.
- [x] **Self-Review Hallucinations**: Improved system prompts for `/selfreview` in `run_selfreview()` to tighten constraints, explicitly ignore non-actionable elements (like missing docstrings), and format actionable findings strictly.
- [ ] **Network Sandbox Bypass**: `BLOCKED_COMMAND_PREFIXES` blocks `curl`/`wget` but not python network scripts (e.g. `python -m http.server`).
- [ ] **Self-Review Truncation**: `run_selfreview` truncates combined review to 12000 chars, dropping some chunk reviews.
- [ ] **ChromaDB Timeouts**: `col.upsert`/`col.query` have no timeout and can hang the agent if ChromaDB blocks.
- [ ] **Journaling Failures Swallowed**: `journal_append` fails silently. Consider a one-time warning via `ui`.

### v3.0 Upgrades
- [x] **Context-Aware Code Discovery & AST Extraction**: Implemented `read_symbol` and `search_codebase` tools, and upgraded `read_file` with pagination to resolve the "blind reader" bottleneck causing context window exhaustion.
- [x] **Browser Integration**: Implement Playwright-based autonomous browser usage as defined in [[browser_integration_frd]]. Features `browser_navigate`, `browser_observe`, and `browser_interact` tools protected by `BROWSER_LOCK`.
- [ ] **Containerized Sandboxing**: Mount `workdir` into a lightweight Docker container for `run_command` security.
- [ ] **Model Context Protocol (MCP)**: Support dynamic tool discovery from local MCP servers instead of hardcoding tools.
- [ ] **Automated AST Validation**: Run `ast.parse()` on edited Python files in-memory before committing to disk. Short-circuit execution if `SyntaxError`.
- [ ] **Checkpoint / Rollback System**: Auto-initialize a hidden `.git` repo in `workdir`, auto-commit before every turn, and expose a `/undo` command.

## Planned Enhancements
- TBD

## Resolved Issues
- **May 20, 2026**: Resolved context truncation bottleneck for code discovery tools by implementing a dynamic `LARGE_CONTEXT_LIMIT` (8,000 chars) for `read_symbol`, `search_codebase`, and `read_file`, preventing premature cutoff of critical codebase context.
- **May 17, 2026**: Implemented v3.0 Browser Integration featuring `browser_navigate`, `browser_observe`, and `browser_interact` tools. Powered by Playwright (Sync API) and `html2text` with `BROWSER_LOCK` thread-safety, lazy-initialization, context-friendly DOM-to-Markdown conversion (with 8000-char truncation), --headless CLI flag support, and automatic lifecycle teardown.
- **May 17, 2026**: Resolved critical code review issues: Fixed thread-unsafe `FILE_LOCKS`, replaced fragile global `ui` with `UIProxy`, fixed summarization trigger logic, secured temp files against leaking on `os.replace` failure, and migrated ChromaDB to deterministic `hashlib` IDs.
- **May 17, 2026**: Fixed UnboundLocalError in `journal_append` by switching to standard file context managers.
- **May 17, 2026**: Fixed race condition in parallel tool execution using per-file locking.
- **May 17, 2026**: Fixed context window blowout by updating `maybe_summarise` to trigger based on a character limit instead of just turn count.
- **May 17, 2026**: Added warnings for brittle `tool_edit_file` fallback when editing whitespace-sensitive files like Python or YAML.
- **May 17, 2026**: Resolved git conflict issues and purged unwanted `.DS_Store` and bytecode caches from the GitHub repository during the v2.4 sync.
