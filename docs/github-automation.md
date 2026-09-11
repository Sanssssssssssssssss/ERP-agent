# GitHub automation

The repository's deterministic checks live in [`.github/workflows/checks.yml`](../.github/workflows/checks.yml). They run on pushes, pull requests, and manual dispatch with read-only repository permissions.

Code review is managed through the ChatGPT web Codex project and GitHub's pull-request integration. It is enabled from [Codex Code Review settings](https://chatgpt.com/codex/settings/code-review), with the GitHub connection described in the [official integration guide](https://learn.chatgpt.com/docs/third-party/github).

The web project reviews pull requests after the repository and account settings are enabled. GitHub email notifications are controlled by GitHub notification settings; this repository does not send mail through SMTP or an API workflow.

A direct push without a pull request has the deterministic checks path only. A pull request push can also enter the configured web Codex review path. A review with no findings may be represented by a thumbs-up rather than a long report.

The connected ChatGPT Codex project has been observed performing a real native-Codex review of PR #1 at commit `58c4b79`. Automatic review on every PR push and GitHub email delivery have not been verified here; account-page access was unavailable through the current tools. Configure [GitHub notification settings](https://github.com/settings/notifications) for Watching/Participating and pull-request-review email preferences, then follow the [Codex Code Review settings](https://chatgpt.com/codex/settings/code-review) page. This repository does not claim control over those account-level email scopes.
