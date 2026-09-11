# Security

Please do not open a public issue for credentials, private Odoo data, authentication material, or a reproducible security vulnerability. Use the repository's [private vulnerability report form](https://github.com/Sanssssssssssssssss/pi-odoo-harness-lab/security/advisories/new) when it is enabled; otherwise contact the maintainers privately with the affected path, version or commit, reproduction steps, and impact.

Remove secrets from logs and reports before sharing them. The workbench stores session and trace data locally; treat those files as business data. Do not commit `.env`, profile directories, request captures, or `.runtime` artifacts.

The native workbench is a local research application. It is not a hardened public service and should not be exposed on an unauthenticated network endpoint.
