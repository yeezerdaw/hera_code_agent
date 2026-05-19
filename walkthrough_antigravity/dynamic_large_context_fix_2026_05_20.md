# Walkthrough: Dynamic Large Context Limit Fix
**Date:** May 20, 2026

## Overview
This walkthrough summarizes the resolution to a hidden context limit bottleneck affecting the newly introduced "Context-Aware Code Discovery & AST Extraction" mechanisms inside `llm_code_agent.py`.

## Problem 
While `read_symbol`, `search_codebase`, and `read_file` correctly pulled critical code data context to the LLM agent via python string processing, the response strings returned to the LLM's history array were strictly capped universally by the `CONTEXT_CHAR_LIMIT` constant located inside the "Append tool results" logic block in `run_agent`.

This truncated critical codebase context at a strict 2,000-character ceiling, which completely negated the benefits of extracting AST structures and multi-file code searches, triggering hallucinations due to missing implementation bodies.

## Resolution
1. **Dynamic Constants:** Configured a new constant `LARGE_CONTEXT_LIMIT` set to 8000 characters and tied it to a targeted whitelist set `LARGE_CONTEXT_TOOLS = {"read_symbol", "search_codebase", "read_file"}`.
2. **Context Extension Check:** Reworked tool processing inside `run_agent` to conditionally throttle lengths depending exactly on the target invocation function invoked, granting specialized AST and discovery actions significant context window real estate unconditionally. 
3. **Optimized Truncation Directions:** Modified the fallback truncation error message attached whenever bounds *are* finally triggered to direct the LLM system natively: `...[TRUNCATED] Use read_file with start_line/end_line to see more`.

## Impact
This final polish efficiently bridges targeted AST search with the native context limit engine preventing model blinding on large components directly resolving context pipeline bottlenecks sustainably.