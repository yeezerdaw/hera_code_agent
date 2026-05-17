# Feature Requirements Document: Browser Integration

## 1. Architectural Constraints & State Management
The browser integration MUST conform to the agent’s existing multi-threaded, parallel-tool execution architecture.
- **Engine Requirement:** The system SHALL use `playwright` (Synchronous API) to manage the browser session.
- **Lazy Initialization:** The browser engine MUST NOT initialize on agent startup. It SHALL initialize only upon the first invocation of a browser-related tool.
- **Thread Safety (Critical):** Because the agent uses `ThreadPoolExecutor` (`max_workers=4`) in Phase 2, and Playwright does not natively support cross-thread DOM manipulation, all browser tool executions MUST be wrapped in a dedicated `threading.RLock()` (e.g., `BROWSER_LOCK`).
- **Visibility:** The browser SHALL launch in non-headless mode (`headless=False`) by default so the user can observe the agent's actions, with an option to toggle via a CLI flag (e.g., `--headless`).
- **Lifecycle Teardown:** The browser context and Playwright instance MUST be explicitly closed when the agent loop terminates or intercepts an `EOFError`/`/exit` command to prevent zombie processes.

## 2. Context Management & Parsing
Raw HTML from modern web pages exceeds LLM context limits (e.g., >100k tokens). The system MUST process DOM output before returning it to the agent.
- **HTML to Markdown:** The system MUST strip `<style>`, `<script>`, and `<svg>` tags. It SHALL convert the remaining visible DOM into structural Markdown (e.g., using the `html2text` or `markdownify` libraries).
- **Actionable Selectors:** The text extraction layer MUST preserve actionable elements. Links and buttons SHOULD retain discernible labels (e.g., `[Login Button]`, `[Link: Pricing]`) so the LLM can infer valid CSS selectors or XPath queries.
- **Hard Truncation:** All browser read operations MUST enforce a strict character limit on the returned string (e.g., 8,000 characters). If the document exceeds this, it MUST append a `...[TRUNCATED: Use specific queries to read further]` notice.

## 3. Required LLM Tool Specifications (JSON Schemas)
The integration requires exposing exactly three unambiguous tools to the LLM.

### Tool 1: `browser_navigate`
- **Purpose:** Instructs the browser to load a specific URL.
- **Parameters:**
  - `url` *(string, required)*: The fully qualified URL (must include `http://` or `https://`).
- **Execution Rules:**
  - MUST implement a hard timeout (e.g., 15 seconds).
  - MUST wait for the `domcontentloaded` lifecycle event before returning.
- **Return Signature:** Returns a string. On success: `"Navigated to [Title] at [URL]"`. On failure: `"ERROR: [Exception details]"`.

### Tool 2: `browser_observe`
- **Purpose:** Returns the current visual state of the page to the LLM.
- **Parameters:** None required. *(Optional: query string to filter output to specific sections using text matching).*
- **Execution Rules:**
  - MUST capture the current DOM and apply the "Context Management" Markdown conversion defined in Section 2.
- **Return Signature:** Returns a string formatted as: `"Page: [Title]\nURL: [URL]\n\n[Markdown Content]"`.

### Tool 3: `browser_interact`
- **Purpose:** Triggers DOM events to interact with web applications.
- **Parameters:**
  - `action` *(string, required)*: MUST be an enum of `["click", "fill", "press"]`.
  - `selector` *(string, required)*: A standard CSS selector, XPath, or Playwright text locator (e.g., `button:has-text('Submit')`).
  - `value` *(string, optional)*: Required only if action is `fill` (the text to type) or `press` (the keyboard key, e.g., `Enter`).
- **Execution Rules:**
  - MUST apply a short timeout (e.g., 5 seconds) to locate the element. If not found, immediately return a failure string.
  - After the interaction, the tool MUST await a network idle state (max 3 seconds) to allow SPA (Single Page Application) re-renders to complete before returning control to the agent.
- **Return Signature:** Returns a string. On success: `"Successfully performed [action] on [selector]"`. On failure: `"ERROR: Element [selector] not found or not interactable."`.

## 4. Error Handling & Self-Healing Integration
The browser tools MUST integrate cleanly with the agent's existing `self_heal_dispatch` logic.
- **Timeout Handling:** If a page fails to load within the timeout limit, the tool MUST return a string starting with `"ERROR: timed out"`. This explicitly triggers the existing exponential backoff logic in `self_heal_dispatch` (Line 846).
- **Selector Failures:** If `browser_interact` fails due to a bad selector, the error message MUST explicitly instruct the observer: `"ERROR: Selector not found. Call browser_observe to analyze the current DOM structure before retrying."`

## 5. System Prompt Modifications
The `SYSTEM_PROMPT` MUST be updated with explicit rules governing browser behavior to prevent infinite loops:
- **Mandatory Observation:** The LLM MUST be instructed to call `browser_observe` immediately after `browser_navigate` to understand the page state.
- **Selector Strategy:** The LLM MUST be instructed to rely on the output of `browser_observe` to deduce logical CSS selectors rather than hallucinating absolute DOM paths.
