# Agentic AI and token optimization tutorial

Read this first. Full background is imported below.

@docs/project-context.md

## How to work in this folder
- This is a learning project. Explain choices briefly so the reasoning can go into the tutorial.
- Before proposing anything about Claude models, pricing, caching, batching, or subagent behavior, check the current Anthropic docs rather than relying on memory.
- Keep every experiment reproducible: fixed task, fixed prompts, logged token counts and cost per run.
- Save generated reusable assets under `.claude/agents/` and `.claude/skills/` so they can be reused in other projects.

## Python
Conda is not on PATH, so `conda activate` does not work in tool shells. Call the project
env's interpreter by absolute path instead:

`C:\Users\giacomov\AppData\Local\anaconda3\envs\agentflow\python.exe` (Python 3.12.14)

Install packages with `<that path> -m pip install ...`. Do not use the `python3` on PATH —
it is the Windows Store stub and will fail.

Installed: `anthropic` 1.8.0 (the 1.x SDK), `pandas`, `pypdf`, `python-dotenv`.

**Credentials are not set up yet.** `ANTHROPIC_API_KEY` is unset and the `ant` CLI is not
installed, so no run can call the API until one of those is fixed.
