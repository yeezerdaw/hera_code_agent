# Hera v2.4 (Local AI Coding Assistant) – User Manual

Hera is an advanced, autonomous AI coding assistant running locally on your machine. Through a secure, sandboxed working environment, Hera uses a state-of-the-art **Planner-Executor-Observer** loop to read codebase context, write and modify code, execute shell commands, and iteratively solve complex programming tasks.

---

## 🚀 Key Features
1. **Autonomous Execution Engine:** Automatically plans, executes, and observes outcomes of multiple tools in parallel (via `ThreadPoolExecutor`).
2. **"Self-Healing" Tool Calls:** If an operation fails, Hera automatically attempts to fix it (e.g., exponential backoffs for shell command timeouts, or fuzzy searching if an exact file isn't found).
3. **Semantic Memory Workspace:** Using `chromadb`, it logs facts, preferences, and long-term context to recall naturally across different sessions.
4. **Rich Terminal CLI:** Features intuitive progress spinners, syntax-highlighted diffs, and transparent tool-call previews via the `rich` library.
5. **Approval Mode:** Allows strict user-consent gating for potentially destructive shell commands or file overwrite operations.
6. **Task Driven Workflow:** Place a `TASK.md` inside your workspace, and Hera will autonomously execute it. Once complete, it writes a `DONE.md` summary.
7. **Live File Watcher:** Automatically detects external edits you make in the workspace without you having to explicitly re-prompt the agent.

---

## 🛠️ Setup & Installation

Hera runs on a standard Python 3.10+ execution environment with optimal performance when optional dependencies are included.

```bash
# Provide core dependencies for enhanced UI, filesystem watching, and memory
pip install requests rich chromadb watchdog

# Provide LLM engines (Default relies on local Ollama)
ollama pull gemma4:e2b
ollama pull nomic-embed-text   # Embedding model for vector memory
```

---

## 💻 CLI Usage

You can start Hera by running the centralized script.

**Basic Start (Default local Ollama + Gemma model):**
```bash
python llm_code_agent.py
```

**Custom LLM Backends (OpenAI, Groq, Mistral, etc.):**
Provide API keys via environment variables like `GROQ_API_KEY`, `OPENAI_API_KEY`, or `HERA_API_KEY`.
```bash
python llm_code_agent.py --backend groq --model llama-3.3-70b-versatile
```

### Important CLI Flags
* `--model <name>`: Switch the LLM model used.
* `--workdir <path>`: Specifies the isolated workspace environment.
* `--approval`: **Recommended constraint mode**. Hera will ask `Proceed? (y/N)` before executing risky bash routines or clobbering files.
* `--max-turns <N>`: Set the max number of automatic reasoning loops (default: 15).
* `--watch`: Tracks external file changes inside the working directory in parallel.
* `--auto-test`: Forces the agent to proactively suggest and run unit tests for files it creates.

---

## 💬 Interactive REPL Commands

Once inside the Hera interface, besides conversing colloquially, use these slash expressions to command the engine directly:

* `/clear` - Flush the current conversation history.
* `/exit` - Close the agent session cleanly.
* `/workdir` - Check the current absolute sandbox path.
* `/memory` - Prints all persistent workspace facts memorized by the AI.
* `/scratch` - View the hidden "Scratchpad" where Hera does its private reasoning.
* `/model <name>` - Hot-swap the underlying LLM mid-conversation.
* `/selfreview` - Proactively invokes Hera to perform an autonomous chunked source-code review on its own codebase!

---

## ⚙️ How "Working Paradigms" Actually Function

**1. The Sandbox:** 
Hera is directory-locked to `--workdir` (defaults to `./agent_workspace`). Path traversal hacks (e.g., trying to modify root server contexts `../../etc`) are actively blocked by the tool-calling engine for safety. By default, it runs operations explicitly in its own python `.venv` created dynamically at runtime.

**2. The Memory Space:** 
If you state something like "Always use pytest", the agent automatically leverages the `remember` tool to dump this into `.config/llm_code_agent`. When it starts fresh sessions, the engine pulls semantic top-N matches transparently. 

**3. Tool Sets available to Hera:**
Behind the scenes, Hera invokes `read_file`, `write_file`, `edit_file`, `replace_in_file`, `list_files`, `run_command`, `remember`, `recall`, and `scratchpad`. Hera uses its `scratchpad` aggressively to chain commands together and trace logic out-of-band!
