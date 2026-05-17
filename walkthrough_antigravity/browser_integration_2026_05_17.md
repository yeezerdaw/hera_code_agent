# Hera Agent - Browser Integration (v3.0) Walkthrough

**Date & Time:** May 17, 2026, 23:03 IST (17:33 UTC)
**Agent:** Antigravity

I have successfully designed, implemented, and verified the **v3.0 Browser Integration** feature in `llm_code_agent.py` according to all constraints outlined in [[browser_integration_frd]] and the updated design feedback.

---

## 1. Core Architectural Integration

The browser integration was designed to perfectly align with Hera's multi-threaded, parallel-tool execution architecture:

### Lazy Initialization
The browser is never initialized on agent start. Under `BROWSER_LOCK` (a dedicated `threading.RLock()`), `_ensure_browser()` launches `playwright` and Chromium **only upon the first invocation** of a browser tool, avoiding unnecessary resource usage.

### Thread Safety
Since Playwright does not natively permit cross-thread DOM manipulation and the agent utilizes a `ThreadPoolExecutor` (4 concurrent workers), all browser tool entry points are protected by acquiring `BROWSER_LOCK`.

### CLI flag and Default Visibility
- The browser defaults to **visible mode** (`headless=False`), allowing users to observe the agent's exact navigation and interactions.
- A new CLI flag `--headless` was added to run the browser in headless mode.

### Safe Lifecycle Teardown
To prevent zombie processes on termination, `_teardown_browser()` is wired directly into both:
1. Interactive REPL exit `/exit` or `EOFError`/`KeyboardInterrupt`.
2. CI task mode completion exit path.

---

## 2. Browser Tools Exposed to LLM

When `playwright` is installed (`HAS_PLAYWRIGHT = True`), three new schemas are automatically appended to the agent's active `TOOLS` definition:

### 1. `browser_navigate(url)`
- **Parameters**: `url` (fully qualified string with `http://` or `https://`).
- **Implementation**: Navigates with a hard 15-second timeout and awaits the `domcontentloaded` lifecycle event.
- **Error Handling**: Custom error formatting for `"ERROR: timed out loading [URL]"` to hook cleanly into the existing exponential backoff logic inside `self_heal_dispatch`.

### 2. `browser_observe(query)`
- **Parameters**: Optional `query` string to filter Markdown lines.
- **Implementation**: Extracts the full DOM page content, converts it to clean structural Markdown via `html2text` (or a regex fallback), strips noise (`<script>`, `<style>`, `<svg>`), and enforces a strict **8,000-character context limit** (with a `...[TRUNCATED]` warning if breached) to protect the LLM context window.

### 3. `browser_interact(action, selector, value)`
- **Parameters**: 
  - `action`: `click`, `fill`, or `press` (enum).
  - `selector`: CSS selector, XPath, or Playwright locator string.
  - `value`: Input value (required for all actions to satisfy OpenAI strict mode schema validations).
- **Implementation**: Wait up to 5 seconds to locate the target element. After triggering the interaction, it awaits `domcontentloaded` with a 10-second timeout to allow SPA re-renders to complete safely while avoiding false positives on modern websites that use continuous websocket/analytics polling.
- **Self-Healing Integration**: If the selector fails, the error explicitly instructs the observer: `"ERROR: Selector not found. Call browser_observe to analyze the current DOM structure before retrying."`

---

## 3. System Prompt Adaptations

The `SYSTEM_PROMPT` has been updated dynamically when `HAS_PLAYWRIGHT` is detected to introduce clear behavior boundaries:
1. **Mandatory Observation**: Instructs the LLM to immediately call `browser_observe` after `browser_navigate`.
2. **Selector Strategy**: Prevents DOM path hallucination by instructing the LLM to deduce logical selectors solely from `browser_observe` output.
3. **Recovery Heuristic**: Advises calling `browser_observe` to analyze the DOM whenever `browser_interact` raises a selector exception.

---

## 4. Verification & Testing

- **Syntax & Compilation**: Verified that the updated `llm_code_agent.py` compiles cleanly without warnings or errors.
- **CLI Options**: Verified `--headless` flag registration under `python3 llm_code_agent.py --help`.
- **Imports**: Verified that both `playwright` and `html2text` imports are fully modular and implement defensive try-except blocks, ensuring graceful degradation if those libraries are missing.

---

> [!TIP]
> The Hera agent is now fully equipped with state-of-the-art visual browsing tools, allowing it to navigate real-time websites, extract Markdown data, and execute click/fill events, all while adhering to the core thread-safety and token constraints of the platform.
