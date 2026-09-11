# Contributing

Keep changes focused on the Odoo harness and its evidence. Start with an issue for a new capability or boundary change; small documentation fixes can go straight to a pull request.

Before opening a pull request:

1. Describe the user-visible change and affected boundary.
2. Run checks relevant to the files changed. For the desktop, use `npm run typecheck`, `npm run self-check`, and `node scripts/renderer-check.mjs` from `desktop/` when applicable.
3. Do not include credentials, cookies, private Odoo data, model request bodies, or local `.runtime` outputs.
4. Keep benchmark answers and verifier internals out of agent-facing prompts and product claims.

Changes to pinned sources, licenses, ERP-Bench scope, or native/MCP boundaries need evidence in `sources.lock.json` or `experiments/`.

For ordinary development, open a focused pull request and include the relevant checks. The optional Codex review is configured in the ChatGPT web project; see [`docs/github-automation.md`](docs/github-automation.md). Do not treat that review as a replacement for maintainer review or business evidence.
