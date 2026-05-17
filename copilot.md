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

## Project Management References
When you (the AI) need to update tracking files or find current priorities, consult:
- **Progress**: [[progress_updates]]. *Note: Always specify which AI agent is handling the updates (e.g., prefix with "**GitHub Copilot**: ")*.
- **Bugs/Enhancements**: [[issues]]
