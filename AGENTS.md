# AGENTS.MD: Directives for DeepSeek Agent Execution

## 1. Core Operating Principles
1. **Do Not Guess**: If a task description or requirement is ambiguous, request clarification or refer to `PROJECT_PROFILE.md`.
2. **Scope Isolation**: Modify ONLY the files explicitly assigned to the current task (`TASK-XXX`).
3. **Zero Regression**: Any modification causing existing tests to fail must be immediately reverted or resolved.
4. **Financial Integrity**: Any changes touching the financial engine must maintain 100% mathematical precision.
5. **READ-ONLY AUDIT**: Before making any changes, perform a complete analysis of the existing repository. DO NOT modify any files until after the audit is complete.

## 2. 10-Step Execution Workflow
For every assigned task, execute the following workflow sequentially:

| Step | Action | Command / Tool |
| :--- | :--- | :--- |
| **1. Analysis** | Read and isolate target files only | `Read Scope` |
| **2. Inspection** | Run current test suite to verify base health | `pytest tests/` |
| **3. Planning** | Draft a 3-5 point execution plan before coding | `Plan Review` |
| **4. Implementation** | Apply precise code edits exclusively to scoped files | `Code Edit` |
| **5. Syntax Check** | Verify there are no syntax or import errors | `python -m py_compile` |
| **6. Unit Test** | Execute isolated unit test for the updated module | `pytest tests/test_target.py` |
| **7. Full Test Suite**| Run the complete financial and workflow test suite | `pytest tests/test_financial_rules_rc2.py` |
| **8. Refactoring** | Resolve any side-effects or failed assertions | `Refactor/Fix` |
| **9. Compliance Check**| Ensure UI/UX and financial engine rules are fully respected | `Profile Compliance Check` |
| **10. Reporting** | Provide a summary report and proposed Git commits | `Git Status Report` |

## 3. Definition of Done (DoD)
A task is considered complete ONLY when all the following criteria are met:
* [ ] The new code executes without runtime exceptions.
* [ ] All Pytest test cases pass with a 100% success rate (`0 failures`).
* [ ] No files outside the designated task scope were altered.
* [ ] No third-party dependencies/libraries were added without explicit permission.
* [ ] Full compatibility with Excel and PDF export templates in `templates/` is preserved.

## 4. Error Handling Protocol
If a test failure occurs during execution:
1. Read the `pytest` output carefully to pinpoint the exact failing line and assertion.
2. Isolate the root cause: Logic error, assertion error, or database schema mismatch.
3. Fix the issue within your target codebase. Do not alter reference test files unless business rules have explicitly changed.