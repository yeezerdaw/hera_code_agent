# Walkthrough: Playwright Selector Fix
**Date:** May 20, 2026

## Overview
This walkthrough summarizes the resolution to the "LLM Playwright Selector Hallucinations" bug that was impacting the v3.0 Browser Integration in the Hera Project.

## Description of the Problem
The local AI agent was frequently hallucinating or using overly broad Playwright CSS selectors like `a`, `button`, or `input` when calling the `browser_interact` tool. Because Playwright operates with strict resolution mode by default, finding multiple targets for these vague selectors would trigger terminal execution errors. It also occasionally guessed locators instead of interacting with the dynamically mapped DOM elements.

## Resolution
1. Modified `llm_code_agent.py` to target the dynamic `SYSTEM_PROMPT` `HAS_PLAYWRIGHT` appending block.
2. Swapped the generic "Browser Rules" section for a revised "Browser Rules (Selector Cheat-Sheet)".
3. The new rules strictly forbid the use of generic tags and mandate using Playwright unique locators:
   - Evaluated string text locators (`text="Exact String"`)
   - Semantic selectors with nested text rules (`button:has-text("Submit")`)
   - ARIA labels (`[aria-label="Search"]`)
4. Re-enforced that the AI *must* call the `browser_observe` tool to read the live HTML DOM before any re-attempt of an interaction tool failure.

## Next Steps
Monitor future integration tests to ensure that the agent ceases triggering strict-mode violations when interacting with multi-node webpages like Hacker News or GitHub.