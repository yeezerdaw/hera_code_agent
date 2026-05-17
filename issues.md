# Hera Project: Issues & Tasks Tracking

Use this file to log active bugs, necessary enhancements, and developer tasks. 

## Active Issues & Bugs
- [x] **UnboundLocalError in journal_append**: `finally` block attempts to close `fd` unassigned if `os.open()` fails. Switch to standard `with open(..., "a") as f:`
- [x] **Race Condition in Parallel Tool Execution**: `ThreadPoolExecutor` might silently overwrite files if multiple file write/edit tools target the same file in one turn. Needs per-file locking or sequential execution for mutating tools.
- [x] **Context Window Blowout**: Current `maybe_summarise` relies on turn count. Should be refactored to trigger based on a rolling token/character counter to avoid API 400/500s.
- [x] **ChromaDB Hash Determinism**: Python `hash()` is non-deterministic, duplicate entries stored.
- [x] **Atomic Write Temp File Leak**: Temp files left on disk if `os.replace` fails in tool functions.

### Refactoring & Tech Debt
- [x] **Global State (`ui`)**: Refactored global `ui` using a `UIProxy` to fix thread-safety and testing issues.
- [ ] **Global State (`_backend`)**: Refactor global `_backend` into an `AgentContext` dataclass for better testability.
- [ ] **Diff Parsing Robustness**: Switch from `<<<<<<< SEARCH` regex to Unified Diff Format or line-number-based patches.
- [ ] **Type Hinting**: Apply `typing.TypedDict` for OpenAI message formats.
- [ ] **Blocking Network Calls**: `time.sleep()` in OpenAI 429 handler blocks the main thread. Note as tech debt if moving to `asyncio`.
- [ ] **Parallel Tool Execution Warning**: `messages` appending is single-threaded but `self_heal_dispatch` worker threads run concurrently. (Low Severity).
- [ ] **Sandbox Weakness**: `tool_run_command` allows `HOME` in `SAFE_ENV_ALLOWLIST`, leaking `~/.pth` and `usercustomize.py` to the subprocess Python.
- [ ] **`_split_into_sections` Bug**: Fails to chunk correctly if there is only one top-level definition, silently merging all content.
- [ ] **Network Sandbox Bypass**: `BLOCKED_COMMAND_PREFIXES` blocks `curl`/`wget` but not python network scripts (e.g. `python -m http.server`).
- [ ] **Self-Review Truncation**: `run_selfreview` truncates combined review to 12000 chars, dropping some chunk reviews.
- [ ] **ChromaDB Timeouts**: `col.upsert`/`col.query` have no timeout and can hang the agent if ChromaDB blocks.
- [ ] **Journaling Failures Swallowed**: `journal_append` fails silently. Consider a one-time warning via `ui`.

### v3.0 Upgrades
- [x] **Browser Integration**: Implement Playwright-based autonomous browser usage as defined in [[browser_integration_frd]]. Features `browser_navigate`, `browser_observe`, and `browser_interact` tools protected by `BROWSER_LOCK`.
- [ ] **Containerized Sandboxing**: Mount `workdir` into a lightweight Docker container for `run_command` security.
- [ ] **Model Context Protocol (MCP)**: Support dynamic tool discovery from local MCP servers instead of hardcoding tools.
- [ ] **Automated AST Validation**: Run `ast.parse()` on edited Python files in-memory before committing to disk. Short-circuit execution if `SyntaxError`.
- [ ] **Checkpoint / Rollback System**: Auto-initialize a hidden `.git` repo in `workdir`, auto-commit before every turn, and expose a `/undo` command.

## Planned Enhancements
- TBD

## Resolved Issues
- **May 17, 2026**: Implemented v3.0 Browser Integration featuring `browser_navigate`, `browser_observe`, and `browser_interact` tools. Powered by Playwright (Sync API) and `html2text` with `BROWSER_LOCK` thread-safety, lazy-initialization, context-friendly DOM-to-Markdown conversion (with 8000-char truncation), --headless CLI flag support, and automatic lifecycle teardown.
- **May 17, 2026**: Resolved critical code review issues: Fixed thread-unsafe `FILE_LOCKS`, replaced fragile global `ui` with `UIProxy`, fixed summarization trigger logic, secured temp files against leaking on `os.replace` failure, and migrated ChromaDB to deterministic `hashlib` IDs.
- **May 17, 2026**: Fixed UnboundLocalError in `journal_append` by switching to standard file context managers.
- **May 17, 2026**: Fixed race condition in parallel tool execution using per-file locking.
- **May 17, 2026**: Fixed context window blowout by updating `maybe_summarise` to trigger based on a character limit instead of just turn count.
- **May 17, 2026**: Added warnings for brittle `tool_edit_file` fallback when editing whitespace-sensitive files like Python or YAML.
- **May 17, 2026**: Resolved git conflict issues and purged unwanted `.DS_Store` and bytecode caches from the GitHub repository during the v2.4 sync.
