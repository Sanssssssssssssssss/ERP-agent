# Third-party notices

The root MIT license applies to original integration and harness code where no more specific notice is present. It does not replace the license of an included or referenced component.

| Component | Location or source | License | Note |
| --- | --- | --- | --- |
| pi-agent-python | `agent/`, pinned in `sources.lock.json` | MIT | Source and license are retained in `agent/`. |
| Odoo helper source | `mcp/` and `odoo_runtime/_odoo_core/` | MIT | The retained source notice is in `mcp/LICENSE`; extracted helpers are listed in `sources.lock.json`. |
| ERP-Bench | `bench/`, pinned in `sources.lock.json` | CC0-1.0 | The benchmark's license is independent of the root MIT license. |
| Odoo 19 | External runtime used by the experiments | See the Odoo distribution and deployment terms | Odoo is an external service/runtime and is not bundled by this repository. |

The complete pinned commits, trees, modifications, and bundle hashes are recorded in [`sources.lock.json`](sources.lock.json). Keep the corresponding notices when redistributing a component.
