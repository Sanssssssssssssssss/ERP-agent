# Odoo desktop workbench V1

> Historical plan, superseded where noted by [DEMO_READINESS_REVIEW.md](DEMO_READINESS_REVIEW.md) (2026-09-09). In particular, treating every message without business_id as a sale_invoice proposal was an incorrect product contract. Ordinary conversation and explicit business proposals must be distinct; the current desktop is not Demo-ready.

Baseline: `b4e6461`, tag `desktop-workbench-baseline-b4e6461`.
Implementation branch: `feature/odoo-desktop-workbench`.

## Product contract

Windows desktop app with packaged local UI and Python native Harness host.
Session-first: left session list, center conversation, right business tabs.
One supported business type, `sale_invoice`, with multiple instances per session.
Business tabs contain Overview, Documents, Approvals, Verification, Trace.
Trace drills down from run to model round to tool invocation and action receipt.
Keep the prior light gray/white/teal design, Chinese business labels, readable tables,
keyboard focus and resizable panes. Do not copy the old backend, credentials,
Compiler, environments or generated data from erp-openai.

Business workspace creation is a local intent confirmation, separate from every
ERP action approval. Viewing a tab never changes an executing run's business ID.
Only one active run per host in V1; other sessions/workspaces remain viewable.
No model calls when browsing, reading a trace, refreshing UI or managing tabs.
Persist sessions, businesses, messages, run states and exact approval decisions.
Never infer business success from final model prose. Unknown state stays unknown.

## Ownership

- Root: contract, integration review, adversarial acceptance, real desktop QA,
  isolated Odoo acceptance, documentation, commits and pushes.
- Luna frontend: desktop renderer and its component tests.
- Luna host: workbench Python service/worker, native runner integration, host tests.
- Luna desktop: Electron main/preload, package/build resources and shell tests.

## Desktop/host protocol v1

Python host starts as `python -m workbench.host --data-dir PATH`.
Use newline-delimited UTF-8 JSON over stdin/stdout. stdout is protocol-only;
diagnostic messages go to stderr without credentials. No public network server.
Requests: `{id: string, method: string, params: object}`.
Responses: `{id: string, result: ...}` or `{id: string, error: {code, message}}`.
Events: `{event: string, data: object}`. State-changing events include session_id,
business_id/run_id when applicable, and a monotonic persisted sequence.

Renderer bridge: `window.workbench.call(method, params?)`,
`window.workbench.subscribe(listener)` returning an unsubscribe function,
`window.workbench.windowControl('minimize'|'maximize'|'close')`.
Electron owns method allowlists, privileged settings and window/file controls.
Business logic and approval authorization remain in the Python host.

Public methods:

- `list_sessions` -> SessionSummary[]
- `create_session {title?}` -> SessionSummary
- `rename_session {session_id,title}` -> SessionSummary
- `archive_session {session_id}` -> {ok:true}
- `get_session {session_id}` -> {session,messages,businesses}
- `send_message {session_id,text,business_id?}` -> {ok:true}
  Without business_id: propose a sale_invoice workspace; never mutate Odoo.
  With business_id: continue that business, rejecting any busy run.
- `confirm_business {session_id,proposal_id,confirmed}` -> Business|null
- `get_business {session_id,business_id}` -> BusinessDetail
- `start_run {session_id,business_id}` -> Run
- `decide_approval {session_id,business_id,run_id,action_id,decision:'approve'|'reject'}` -> {ok:true}
- `cancel_run {session_id,business_id,run_id}` -> {ok:true}
- `get_trace {session_id,business_id,run_id}` -> {run,rounds,tools,events}
- `refresh_business {session_id,business_id}` -> BusinessDetail (Odoo reads only)
- `get_settings` / `save_settings` (Electron-owned, public values only on read)
- `health` -> {host_ready,odoo_status,model_configured,environment}

Shapes (snake_case on the wire):

- SessionSummary: {id,title,created_at,updated_at,archived,status}
- Message: {id,role:'user'|'assistant'|'system',text,created_at,business_id?,
  proposal?:{id,type:'sale_invoice',title,goal,status:'pending'|'confirmed'|'rejected'}}
- Business: {id,session_id,type:'sale_invoice',title,goal,status,created_at,updated_at,active_run_id?}
- Run: {id,business_id,session_id,status,started_at,ended_at?,error?,usage?,tool_count?,model_rounds?,elapsed_seconds?}
- BusinessDetail: {business,runs,approvals,documents,checks,observed_at?,stale,summary?}
- Approval: {action_id,run_id,business_id,status,title,model,operation,record_ids,
  values,prestate,expires_at,source?,result?,verification?}
- Document: {id,model,name,state,fields,observed_at?,source}
- Check: {name,label,status:'passed'|'failed'|'unknown',detail?,source?}
- Usage: {input:number|null,cache_read:number|null,output:number|null,reasoning:number|null,total:number|null}
- Round: {index,status,text?,usage?,elapsed_seconds?,tool_ids:string[]}
- Tool: {id,name,round?,status,arguments,result?,elapsed_seconds?,action_id?}

All timestamps are ISO 8601 UTC strings; expires_at from the native ledger may be
Unix seconds. Task status: idle/running/awaiting_approval/cancel_requested/
cancelled/interrupted/completed/failed/needs_reconciliation. A completed run
must preserve verification status separately. Missing usage is null, not zero.
`changed` events ask the UI to reload the affected session/business, `message_delta`
may carry live assistant text. No secrets or hidden reasoning streamed to UI.

Settings public shape: {model,base_url,odoo_url,odoo_db,odoo_username,
has_model_key,has_odoo_key,environment:'demo'|'configured'}.
save_settings accepts optional model_key/odoo_key; empty means preserve existing.
Keys use Electron safeStorage and are supplied only to the child host process.
Host reads LLM_API_KEY/LLM_BASE_URL/LLM_MODEL and ODOO_URL/ODOO_DB/ODOO_USERNAME/
ODOO_API_KEY. Runtime must be native with host approvals, never bench-auto.

## Acceptance gates

1. Build/typecheck/tests; no root native regression, no MCP import/runtime fallback.
2. Packaged app starts without browser, dev server, global Python/Node or WSL.
3. Sessions, confirmed workspace creation, multiple business tabs, archive/rename,
   approval/rejection, trace navigation and persistence tested through real UI.
4. Duplicate clicks, wrong workspace IDs, expired approvals, stale prestate,
   disconnect/restart and uncertain writes fail safely and visibly.
5. Business facts match independent Odoo reads; token/tool counts match receipts.
6. Inspect 1280x800, 1600x1000 and 1920x1080 desktop layouts, including keyboard,
   empty/error/waiting/completed states. No critical clipping or renderer errors.
7. Live model acceptance uses only the existing small frozen sales scenario and
   cheap configured model after offline tests; preserve natural finish and ~1h
   exceptional timeout. No broad benchmark or persistent Odoo changes.
8. Every accepted change is committed and pushed; raw data, credentials, package
   outputs and screenshots remain ignored. Document limits honestly.
