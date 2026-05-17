# Hera — Group Presentation Script (3 Speakers)

This script provides a structured, 5-10 minute presentation flow formatted specifically for a trio. It perfectly divides the workload so each speaker highlights a different advanced engineering subsystem of your Agent.

---

## 🕒 Speaker 1: Introduction & Sandboxed Execution (2.5 mins)

**Action:** Have your terminal open and `llm_code_agent.py` code open in your IDE.

**Talking Points (Speaker 1):**
> "Professor, today our team is demonstrating an autonomous, local-only LLM Coding Agent inspired by professional developer tools like Claude Code. 
> 
> Our objective was to build an agent that doesn't just generate text, but can actively run code, test it, and fix its own errors. It operates completely locally using Ollama for total data privacy, and it features a fully custom **Planner-Executor-Observer** state machine rather than a simple chatbot loop."

**Action:** Run the agent with the approval flag: `python llm_code_agent.py --model gemma4:e2b --approval`

**Talking Points (Speaker 1):**
> "When the agent boots up, it automatically creates a sterile Python Virtual Environment (venv) inside its working directory to ensure it doesn't pollute the host machine's global packages. Let me demonstrate."

**Action:** Type to the agent: `Create a python script that prints 'Hello World', run it, and then try to delete the root directory using rm -rf /`

**Talking Points (Speaker 1):**
> "Notice how the agent decides to use the `write_file` tool to create the file, and then the `run_command` tool to execute it. 
> 
> However, because we have **Approval Mode** enabled, our custom regex-based permissions gateway catches the destructive `rm` command before the system blindly executes it. It cleanly separates intelligence from execution privileges."

**Reference Code to Show:**
```python
def check_approval(name: str, args: dict, workdir: str,
                   approval_enabled: bool) -> tuple[bool, str]:
    if not approval_enabled:
        return False, ""

    if name == "run_command":
        cmd = args.get("command", "")
        # Blocks destructive inputs like rm, mkfs, kill, etc.
        if DANGEROUS_PATTERNS.search(cmd):
            return True, f"This command may modify the system:\n    {cmd}"
```
*(Speaker 1 declines the `y/N` prompt during the demo).*

---

## 🕒 Speaker 2: Autonomous Self-Healing (2.5 mins)

**Talking Points (Speaker 2):**
> "Building off that foundational tool execution, I'd like to demonstrate how our agent recovers from its own mistakes. One of the most complex engineering challenges with LLMs is hallucinatory tool usage—like misspelling a filename. 
> 
> In traditional scripts, this hard-crashes the program. In our architecture, the agent fixes it automatically."

**Action:** Type to the agent: `Read the file called hello_worldd.py` *(Intentionally misspell the filename by adding an extra 'd')*

**Talking Points (Speaker 2):**
> "As you can see, when the underlying tool throws a 'File not found' error, our **Self-Healing Tool Dispatch Layer** intercepts it. 
> 
> Under the hood, it dynamically spins up a Unix `find` subprocess to fuzzy-match similar files in the workspace, replacing the hallucinatory input with the actual file path. The AI actively self-corrects without requiring human intervention to type out the fix."

**Reference Code to Show:**
```python
def self_heal_dispatch(name: str, args: dict, workdir: str, max_retries: int = 3):
    result = dispatch_tool(name, args, workdir)

    # ── Heal: File not found → fuzzy unix search ─────────────────────
    if "File not found" in result and name in ("read_file", "replace_in_file"):
        basename = os.path.basename(args.get("path", ""))
        search_result = tool_run_command(
            f"find . -name '*{basename}*' -type f 2>/dev/null | head -5", 
            10, workdir
        )
        candidates = parse_find_output(search_result)
        
        if candidates:
            # Auto-repair the hallucinated path and retry dynamically
            args_copy = dict(args)
            args_copy["path"] = candidates[0]
            retry_result = dispatch_tool(name, args_copy, workdir)
            return f"[Self-healed: used '{candidates[0]}' instead]\n{retry_result}", True
```

---

## 🕒 Speaker 3: Multi-threading, Memory Condensation, & Conclusion (3 mins)

**Talking Points (Speaker 3):**
> "For the final part of our presentation, I want to briefly showcase two significant optimizations we engineered in the core State Machine to dramatically boost speed and reliability."

**1. Concurrent Dispatching:**
> "To minimize latency, if our Planner module determines that multiple tool calls are independent (like evaluating 3 different files at once), we bypass sequential Python loops entirely. Instead, we map those tools to Python's `ThreadPoolExecutor`. The agent dispatches them concurrently across multiple threads, heavily reducing our time-to-action."

**Reference Code to Show:**
```python
if len(to_execute) > 1:
    ui.info(f"⚡ Dispatching {len(to_execute)} tools in parallel")
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_map = {}
        for fn, args in to_execute:
            # Map tools to async futures wrapper
            fut = pool.submit(self_heal_dispatch, fn, args, workdir)
            future_map[fut] = (fn, args)
```

**2. Auto-Condensation & Context Expiration:**
> "Secondly, to prevent the standard 'context window exhaustion' problem that breaks standard chatbots on long sessions, we wrote an aggressive garbage collector. Every 5 conversation turns, the agent spawns a background LLM process. It asks the background model to ingest the massive history and compress it into a precise 5-sentence summary, permanently purging the massive tool output bloat while retaining vital workflow facts."

**Conclusion (Speaker 3):**
> "Ultimately, this localized LLM script demonstrates a deep integration of system-level MLOps: rigid schema parsing, parallel threaded workflow, secure shell sandboxing, and robust self-healing error recovery.
> 
> Looking ahead, we plan to implement a permanent vector database for RAG retrieval. Professor, that concludes our demonstration—are there any implementation questions we can answer?"
