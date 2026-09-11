# r1 逐模型轮次明细

本表只记录已经完成的 r1 实验；本次 r2 尚未启动。
一轮指一次真实模型请求；一个响应可以触发多个工具调用。
所有 24 次模型 HTTP 请求均返回 200。第 25 条 assistant 记录是本地轮数
截停事件，不是付费请求。业务检查通过 7/7，但模型未自然结束。

输出 token 已包含思考 token，思考列只提供拆分，不能重复加到总量。
“模型耗时秒”不含后续工具执行，不能等同整次实验的墙钟耗时。
工具名保留历史 mcp_odoo_ 前缀，但本轮实际执行全部采用 native 后端。

| metric | value |
|---|---:|
| model | deepseek/deepseek-v4-flash |
| provider | openai-compatible |
| model_rounds | 24 |
| local_control_events | 1 |
| http_request_receipts | 24 |
| http_200_responses | 24 |
| input_fresh | 32737 |
| input_cached | 646784 |
| input_cache_write | 0 |
| output | 17849 |
| reasoning | 12740 |
| tool_calls | 47 |
| tool_errors | 3 |
| rounds_with_errors | 2 |
| duration_ms | 229437 |
| request_bytes | 3241952 |
| schema_bytes | 475308 |

HTTP receipts: 24; HTTP 200 responses: 24.
Request/schema bytes are serialized UTF-8 sizes, not token counts; tool durations are not summed.

## Rounds

| 轮次 | 可用工具(schema) | 调用(次数) | 工具错误 | 输入新 | 输入缓存 | 输出 | 思考 | 模型耗时秒 | 结果 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 14 | 3 (list_odoo_sops, mcp_odoo_health_check, mcp_odoo_get_odoo_profile) | 0 | 2884 | 384 | 177 | 92 | 5.5 | toolUse: success; sops=11; success; result=record; success; result=record |
| 2 | 14 | 5 (get_odoo_sop, get_odoo_sop, list_odoo_capabilities, mcp_odoo_search_records, mcp_odoo_search_records) | 1 | 3048 | 3328 | 847 | 569 | 8.5 | toolUse: success; result=record; success; capabilities=10; success; records=1 (Nimbus Bureau); success; records=1 (Open-Plan Noise Barrier Wall); Invalid SOP inputs. |
| 3 | 14 | 3 (mcp_odoo_get_model_fields, mcp_odoo_get_model_fields, configure_odoo_tools) | 0 | 2481 | 6272 | 795 | 574 | 9.1 | toolUse: success; fields=1; success; fields=6; configured; active=actions,accounting,diagnostics |
| 4 | 28 | 2 (mcp_odoo_read_record, mcp_odoo_read_record) | 0 | 13943 | 384 | 525 | 319 | 7.3 | toolUse: success; result=record; success; result=record |
| 5 | 28 | 5 (mcp_odoo_search_records, mcp_odoo_read_record, mcp_odoo_get_model_fields, mcp_odoo_read_record, get_current_time) | 2 | 176 | 14848 | 3202 | 2816 | 33.8 | toolUse: success; records=1 (30 Days); success; fields=30; success; result=record; JSON-2 request res.partner.read failed with HTTP 500: Invalid field 'journal_id' on 'res.partner'; JSON-2 request product.product.read failed with HTTP 500: Invalid field 'uom_po_id' on 'product.product' |
| 6 | 28 | 5 (mcp_odoo_read_record, mcp_odoo_read_record, mcp_odoo_get_model_fields, mcp_odoo_get_model_fields, mcp_odoo_search_records) | 0 | 2256 | 18176 | 2866 | 2456 | 31.7 | toolUse: success; result=record; success; result=record; success; fields=4; success; fields=20; success; records=0 |
| 7 | 28 | 3 (mcp_odoo_search_records, mcp_odoo_search_records, get_odoo_sop) | 0 | 1442 | 23296 | 794 | 521 | 10.6 | toolUse: success; records=0; success; records=1 (Sales); success; result=record |
| 8 | 28 | 1 (mcp_odoo_preview_write) | 0 | 529 | 25472 | 3569 | 3297 | 35.9 | toolUse: success; result=record |
| 9 | 28 | 1 (mcp_odoo_validate_write) | 0 | 401 | 29568 | 246 | 12 | 3.9 | toolUse: success; result=record |
| 10 | 28 | 1 (mcp_odoo_execute_approved_write) | 0 | 488 | 30208 | 360 | 17 | 6.1 | toolUse: verified; record_ids=[1] |
| 11 | 28 | 1 (mcp_odoo_read_record) | 0 | 177 | 30976 | 187 | 44 | 4.1 | toolUse: success; result=record |
| 12 | 28 | 1 (mcp_odoo_execute_method) | 0 | 293 | 31232 | 169 | 63 | 4.0 | toolUse: verified; state=sale |
| 13 | 28 | 3 (mcp_odoo_read_record, mcp_odoo_read_record, mcp_odoo_search_records) | 0 | 187 | 31616 | 453 | 113 | 6.2 | toolUse: success; result=record; success; result=record; success; records=1 (WH/OUT/00001) |
| 14 | 28 | 2 (mcp_odoo_get_model_fields, mcp_odoo_diagnose_odoo_call) | 0 | 516 | 32256 | 848 | 634 | 12.2 | toolUse: success; fields=12; success; result=record |
| 15 | 28 | 1 (mcp_odoo_preview_write) | 0 | 1185 | 33536 | 448 | 292 | 7.3 | toolUse: success; result=record |
| 16 | 28 | 1 (mcp_odoo_validate_write) | 0 | 319 | 35072 | 116 | 0 | 3.1 | toolUse: success; result=record |
| 17 | 28 | 1 (mcp_odoo_execute_approved_write) | 0 | 312 | 35456 | 219 | 0 | 3.4 | toolUse: verified; record_ids=[1] |
| 18 | 28 | 1 (mcp_odoo_execute_method) | 0 | 122 | 35968 | 239 | 123 | 5.4 | toolUse: verified |
| 19 | 28 | 2 (mcp_odoo_read_record, mcp_odoo_get_model_fields) | 0 | 503 | 36224 | 467 | 202 | 5.6 | toolUse: success; result=record; success; fields=8 |
| 20 | 28 | 1 (mcp_odoo_read_record) | 0 | 616 | 37120 | 586 | 431 | 8.3 | toolUse: success; result=record |
| 21 | 28 | 1 (mcp_odoo_preview_write) | 0 | 235 | 38272 | 242 | 98 | 4.1 | toolUse: success; result=record |
| 22 | 28 | 1 (mcp_odoo_validate_write) | 0 | 274 | 38656 | 113 | 0 | 5.8 | toolUse: success; result=record |
| 23 | 28 | 1 (mcp_odoo_execute_approved_write) | 0 | 218 | 39040 | 201 | 0 | 3.8 | toolUse: verified; record_ids=[1] |
| 24 | 28 | 1 (mcp_odoo_execute_method) | 0 | 132 | 39424 | 180 | 67 | 3.7 | toolUse: verified; state=posted |

## Local control events

- 2026-09-08T07:03:56.448770+00:00: error — Agent stopped after max_turns=24

