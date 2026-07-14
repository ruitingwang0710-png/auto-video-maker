# AGENTS\.md

# Codex Project Instructions



This repository contains a desktop automatic video generation application\.



Before making any code changes, read the following files in order:



1. docs/PRODUCT\_SPEC\.md

2. docs/ARCHITECTURE\.md

3. docs/DEVELOPMENT\_RULES\.md

4. docs/ACCEPTANCE\_TESTS\.md

5. TASK\.md

6. README\.md

    

## Scope rules



- Only implement the work explicitly defined in TASK\.md\.

- Do not implement future phases unless TASK\.md requests them\.

- Do not modify unrelated files\.

- Do not replace working modules without a clear reason\.

- Do not add user accounts, cloud databases, payment systems, or unrelated features\.

- Core application logic must remain separate from GUI code\.

- Do not put secrets or API keys in source code\.

    

## Before coding



Before changing files:



1. Inspect the existing repository\.

2. Summarise the current project state\.

3. Explain your understanding of TASK\.md\.

4. List the files you intend to create or modify\.

5. Identify conflicts, missing information, or technical risks\.

6. Provide a short implementation plan\.

    

If the documents contradict each other, stop and report the contradiction instead of guessing\.



## During implementation



- Use Python 3\.12\.

- Add type annotations to public functions\.

- Use pathlib\.Path for file paths\.

- Follow the architecture defined in ARCHITECTURE\.md\.

- Follow all rules in DEVELOPMENT\_RULES\.md\.

- Keep changes small and focused\.

- Add or update tests for important behaviour\.

- Never use shell=True for FFmpeg commands\.

    

## Completion requirements



After implementation:



1. Run the relevant tests\.

2. Report the exact commands that were executed\.

3. Report the real test results\.

4. List all created and modified files\.

5. Explain how to run the completed functionality\.

6. Mention any remaining limitations\.

7. Do not claim completion if tests fail\.

8. Do not begin the next task automatically\.

