# Hera Agent - Code Review Fixes

**Date & Time:** May 17, 2026, 22:43 IST (17:13 UTC)
**Agent:** Antigravity

I have successfully resolved the 5 top-priority actionable fixes from the comprehensive senior-level code review and updated all tracking files according to project protocols.

## What was Changed

### 1. Fixed Global `ui` Fragility
- **Problem**: The global `ui` object was initialized as `Display(use_rich=False)` and reassigned in `main()`, which could cause imports to capture a stale uninitialized reference or result in an `AttributeError`.
- **Change**: Created a robust `UIProxy` singleton class. The global `ui` is now an instance of this proxy. In `main()`, we call `ui.set_instance(...)` so that all references to `ui` immediately delegate to the updated `Display` object without changing the identity of the global variable. It safely initializes an internal fallback instance so it works correctly even before `set_instance` is called.

### 2. Fixed `FILE_LOCKS` Thread Race Condition
- **Problem**: `collections.defaultdict(threading.Lock)` evaluates `__missing__` concurrently, creating a race condition where multiple threads could generate different locks for the same file path.
- **Change**: Replaced it with a `SafeFileLocks` class that wraps dictionary access in a global `threading.Lock()` to ensure that `threading.Lock()` instantiation per path is completely serialized and atomic.

### 3. Migrated ChromaDB Memory Hashes
- **Problem**: Python's `hash()` function is non-deterministic across processes (since Python 3.3). This meant that the same key resulted in different memory IDs across runs, causing silent data duplication in ChromaDB.
- **Change**: Imported `hashlib` and swapped to a deterministic hash: `hashlib.md5(key.encode()).hexdigest()[:16]`.
- **Note**: A comment was added to the code explaining that old memories stored with the old `hash()` method will become orphaned (one-time migration edge case).

### 4. Corrected Summarization Triggers
- **Problem**: The context window summarization logic `if (turn % summarise_every != 0 and total_chars < 30000)` short-circuited incorrectly, potentially ignoring a massive `total_chars` payload if the turn counter hadn't fired yet.
- **Change**: Refactored the condition so that summarization is triggered explicitly if the turn threshold is met **OR** the character limit is breached.

### 5. Plugged `.tmp` File Leaks
- **Problem**: If the atomic `os.replace` operation failed during `tool_write_file`, `tool_replace_in_file`, or `_atomic_write_and_confirm`, the created temporary file was permanently abandoned on the disk.
- **Change**: Added standard Python `try...finally` blocks to explicitly call `os.remove(tmp_path)` if the file still exists after the routine completes or raises an exception.

### 6. Tracked Remaining Issues
- Extracted the other informational and warning-level feedback items (like network sandbox bypassing, Single-threaded appends with worker threads, etc.) and appended them to the "Refactoring & Tech Debt" backlog in `issues.md`.
- Appended a new entry under today's date in `progress_updates.md`.

> [!TIP]
> The AI Agent is now structurally much safer for multi-threaded testing and has eliminated severe hidden race conditions and leaks!
