# Hera Project Context (AI Agent Guide)

## Overview
Hera (v2.4) is a local, terminal-based AI coding assistant. It uses a **Planner-Executor-Observer** state machine to read/write files and execute sandboxed shell commands autonomously. 

## Technical Stack & Architecture
- **Language**: Python 3.10+. Main script is `llm_code_agent.py`.
- **LLM Backends**: Local (Ollama - default: `gemma4:e2b`) or remote OpenAI-compatible APIs (Groq, Together, Mistral, LM Studio).
- **Core Dependencies**:
  - `rich` (optional): For CLI UI, syntax highlighting, and progress spinners.
  - `chromadb` (optional): For semantic memory retention (`nomic-embed-text` default).
  - `watchdog` (optional): For watching file system changes in the workspace.
- **Tools System**: Includes native python function tools sent to the LLM (e.g., `read_file`, `write_file`, `replace_in_file`, `edit_file` with diff formatting, `list_files`, `run_command`, `remember`, `recall`, `scratchpad`).

## Working Paradigms
- **Self-Healing**: The agent automatically attempts fuzzy matching on filenames and retries transient terminal command errors with exponential backoff. Reduces "hallucination loops".
- **Memory**: The agent persists user preferences either in a highly semantic vector database space (`chromadb`) or a flat `memory.json` fallback.
- **Safety & Isolation**: Employs an approval gate (`--approval`) for destructive commands. `resolve()` blocks directory traversal. Passing `shell=False` prevents shell injection.
- **Atomic File Operations**: Defensive programming using `NamedTemporaryFile` and `os.replace` guarantees the file system is never in a corrupted half-written state if interrupted.
- **Graceful Degradation**: Zero-bloat custom adapter. Optional dependencies (`rich`, `chromadb`, `watchdog`) fallback safely to ANSI, flat files, and polling.

## AI Agent Protocol & Etiquette
To maintain the repository's integrity, all AI agents (including GitHub Copilot and Antigravity) MUST adhere to the following disciplines during any working session:

### 1. Mandatory Progress Tracking
- **Log All Changes**: You MUST append an entry to [[progress_updates]] immediately after completing any code change, feature addition, or workspace cleanup.
- **Identify Yourself**: Always explicitly prefix your entries with your agent designation (e.g., `- **Antigravity**: Implemented ...`).

### 2. Walkthrough Documentation
- **Save Walkthroughs**: After completing a task or an implementation plan, create a detailed Markdown walkthrough summarizing the changes made, tests performed, and rationale.
- **Storage Location**: Save these files in the `walkthrough_antigravity` folder. Ensure the filename includes a descriptive name and the date for easy tracking (e.g., `bug_fixes_2026_05_17.md`).
- **Date & Time**: Always mention the current date and time at the top of the walkthrough file.

### 3. Continuous Issue Discovery
- **Proactive Issue Logging**: When reviewing code, making edits, or planning features, if you identify tech debt, unhandled edge cases, or potential bugs, you MUST catalog them in [[issues]]. Do not silently ignore poor code logic.
- **Update Epic Documents**: When working on large features (like the new browser tools), ensure you refer to and update feature-specific markdown files (e.g., [[browser_integration_frd]]).

### 4. Code Editing Discipline
- **Read Before Write**: Always read the existing file structures and surrounding context before executing replacement or writing tools. This ensures semantic correctness and prevents formatting/indentation breakages.
- **Maintain Architectural Integrity**: Stick to the project's zero-bloat, defensive programming paradigms. Do not introduce heavy dependencies or break the procedural state-machine architecture unless strictly instructed.

## Project Management References
When determining your next task or updating the workspace state, actively link between these core documents:
- **Progress & Changelog**: [[progress_updates]]
- **Bugs & Enhancements Tracker**: [[issues]]
- **Feature Requirements**: [[browser_integration_frd]]
- **Task Summaries**: `walkthrough_antigravity/` folder
