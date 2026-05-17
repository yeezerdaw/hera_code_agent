# Hera Project: Issues & Tasks Tracking

Use this file to log active bugs, necessary enhancements, and developer tasks. 

## Active Issues & Bugs
- [ ] **UnboundLocalError in journal_append**: `finally` block attempts to close `fd` unassigned if `os.open()` fails. Switch to standard `with open(..., "a") as f:`
- [ ] **Race Condition in Parallel Tool Execution**: `ThreadPoolExecutor` might silently overwrite files if multiple file write/edit tools target the same file in one turn. Needs per-file locking or sequential execution for mutating tools.
- [ ] **Context Window Blowout**: Current `maybe_summarise` relies on turn count. Should be refactored to trigger based on a rolling token/character counter to avoid API 400/500s.
### Refactoring & Tech Debt
- [ ] **Global State**: Refactor global `ui` and `_backend` into an `AgentContext` dataclass for better testability.
- [ ] **Diff Parsing Robustness**: Switch from `<<<<<<< SEARCH` regex to Unified Diff Format or line-number-based patches.
- [ ] **Type Hinting**: Apply `typing.TypedDict` for OpenAI message formats.
- [ ] **Blocking Network Calls**: `time.sleep()` in OpenAI 429 handler blocks the main thread. Note as tech debt if moving to `asyncio`.

### v3.0 Upgrades
- [ ] **Browser Integration**: Implement Playwright-based autonomous browser usage as defined in [[browser_integration_frd]]. Features `browser_navigate`, `browser_observe`, and `browser_interact` tools protected by `BROWSER_LOCK`.
- [ ] **Containerized Sandboxing**: Mount `workdir` into a lightweight Docker container for `run_command` security.
- [ ] **Model Context Protocol (MCP)**: Support dynamic tool discovery from local MCP servers instead of hardcoding tools.
- [ ] **Automated AST Validation**: Run `ast.parse()` on edited Python files in-memory before committing to disk. Short-circuit execution if `SyntaxError`.
- [ ] **Checkpoint / Rollback System**: Auto-initialize a hidden `.git` repo in `workdir`, auto-commit before every turn, and expose a `/undo` command. **Brittle `tool_edit_file` Fallback**: Whitespace normalization strips structure on fuzzy matches. If applied to Python/YAML, it introduces indentation errors. Warn LLM via observer if fuzzy match is used on sensitive files.

## Planned Enhancements
- TBD

## Resolved Issues
- **May 17, 2026**: Resolved git conflict issues and purged unwanted `.DS_Store` and bytecode caches from the GitHub repository during the v2.4 sync.
