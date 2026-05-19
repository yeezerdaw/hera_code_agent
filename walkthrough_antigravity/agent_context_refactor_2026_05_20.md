# Walkthrough: AgentContext Backend Refactor
**Date:** May 20, 2026

## Overview
This walkthrough summarizes the refactoring operation that eliminated the standalone global `_backend` variable in the Hera Project to reduce tech debt and enhance modularity/testability. 

## Refactoring Steps
1. **Introduced `AgentContext` Dataclass:** Declared an encapsulation data structure named `AgentContext` within `llm_code_agent.py` to organize top-level settings and states such as the backend, model, work directory, watcher queue, and test framework configurations explicitly.
2. **Unified Proxy Replacement:** Replaced the global `_backend` instance with `_ctx` globally, effectively shifting the architecture to a proxy pattern similar to the recent updates targeting the internal terminal `UIProxy` system.
3. **Updating Wrappers:** Functions utilizing the LLM context (`chat_llm`, `list_available_models`) as well as the script initialization code mapping to argument/environmental factors (`HERA_BACKEND`) were seamlessly shifted out of their single global references to leverage properties housed natively in `_ctx`.

## Conclusion
This effectively resolves a marked objective under the `Refactoring & Tech Debt` section from `issues.md`, directly increasing the application's overall testability.