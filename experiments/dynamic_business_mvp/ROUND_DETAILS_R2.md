# r2 完整工具与模型轮次明细

来源为真实请求和工具收据；本次 29 轮自然结束。
输出已含思考 token，不能重复相加。最终 assistant 文本仅为模型总结，
独立业务验收结论与覆盖边界请以 RESULTS_R2.md 为准。


| metric | value |
|---|---:|
| model | deepseek/deepseek-v4-flash |
| provider | openai-compatible |
| model_rounds | 29 |
| local_control_events | 0 |
| unknown_usage_rounds | 0 |
| http_request_receipts | 29 |
| http_200_responses | 29 |
| input_fresh | 35841 |
| input_cached | 923648 |
| input_cache_write | 0 |
| output | 14887 |
| reasoning | 9090 |
| tool_calls | 42 |
| tool_errors | 4 |
| rounds_with_errors | 4 |
| duration_ms | 194054 |
| request_bytes | 5215446 |
| schema_bytes | 356937 |

HTTP receipts: 29; HTTP 200 responses: 29.
Request/schema bytes are serialized UTF-8 sizes, not token counts; tool durations are not summed.

## Rounds

| 轮次 | 可用工具(schema) | 调用(次数) | 工具错误 | 输入新 | 输入缓存 | 输出 | 思考 | 模型耗时秒 | 结果 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 14 | 3 (list_odoo_sops, mcp_odoo_health_check, list_odoo_capabilities) | 0 | 68 | 3200 | 166 | 95 | 4.3 | toolUse: success; sops=11; success; result=record; success; capabilities=10 |
| 2 | 14 | 2 (configure_odoo_tools, get_odoo_sop) | 0 | 1835 | 3328 | 521 | 404 | 5.3 | toolUse: configured; active=actions; success; result=record |
| 3 | 19 | 3 (mcp_odoo_get_odoo_profile, mcp_odoo_search_records, mcp_odoo_search_records) | 0 | 6903 | 768 | 238 | 44 | 4.5 | toolUse: success; result=record; success; records=1 (Nimbus Bureau); success; records=1 (Open-Plan Noise Barrier Wall) |
| 4 | 19 | 2 (mcp_odoo_read_record, mcp_odoo_get_model_fields) | 0 | 2804 | 7552 | 512 | 312 | 5.9 | toolUse: success; result=record; success; fields=4 |
| 5 | 19 | 2 (mcp_odoo_read_record, mcp_odoo_get_model_fields) | 1 | 390 | 10752 | 442 | 216 | 4.6 | toolUse: success; fields=181; JSON-2 request product.template.read failed with HTTP 500: Invalid field 'detailed_type' on 'product.template' |
| 6 | 19 | 3 (mcp_odoo_read_record, mcp_odoo_search_records, get_current_time) | 0 | 14961 | 11520 | 1171 | 915 | 10.2 | toolUse: success; result=record; success; records=10 (Immediate Payment, 15 Days, 21 Days); success; result=record |
| 7 | 19 | 3 (mcp_odoo_search_records, mcp_odoo_search_records, mcp_odoo_get_model_fields) | 0 | 1732 | 26368 | 2930 | 2581 | 25.7 | toolUse: success; records=1 (Nimbus Bureau); success; records=1 (Open-Plan Noise Barrier Wall); success; fields=10 |
| 8 | 19 | 2 (mcp_odoo_read_record, mcp_odoo_get_model_fields) | 0 | 687 | 30976 | 1310 | 1049 | 14.0 | toolUse: success; result=record; success; fields=7 |
| 9 | 19 | 1 (mcp_odoo_preview_write) | 0 | 440 | 32896 | 1099 | 899 | 10.9 | toolUse: success; result=record |
| 10 | 19 | 1 (mcp_odoo_validate_write) | 1 | 320 | 34432 | 220 | 31 | 3.8 | toolUse: success=false |
| 11 | 19 | 1 (mcp_odoo_validate_write) | 0 | 316 | 34944 | 304 | 106 | 4.4 | toolUse: success; result=record |
| 12 | 19 | 1 (mcp_odoo_execute_approved_write) | 1 | 579 | 35456 | 410 | 115 | 4.7 | toolUse: JSON-2 request sale.order.create failed with HTTP 500: time data '2026-09-30T12:00:00' does not match format '%Y-%m-%d %H:%M:%S' |
| 13 | 19 | 1 (mcp_odoo_preview_write) | 0 | 221 | 36352 | 270 | 67 | 5.5 | toolUse: success; result=record |
| 14 | 19 | 1 (mcp_odoo_validate_write) | 0 | 438 | 36736 | 182 | 0 | 3.7 | toolUse: success; result=record |
| 15 | 19 | 1 (mcp_odoo_execute_approved_write) | 0 | 579 | 37248 | 282 | 0 | 4.0 | toolUse: verified; record_ids=[1] |
| 16 | 19 | 1 (mcp_odoo_read_record) | 0 | 188 | 38016 | 162 | 19 | 4.7 | toolUse: success; result=record |
| 17 | 19 | 1 (mcp_odoo_read_record) | 1 | 256 | 38272 | 249 | 99 | 4.5 | toolUse: JSON-2 request sale.order.line.read failed with HTTP 500: Invalid field 'tax_id' on 'sale.order.line' |
| 18 | 19 | 1 (mcp_odoo_read_record) | 0 | 167 | 38656 | 125 | 0 | 3.0 | toolUse: success; result=record |
| 19 | 19 | 1 (mcp_odoo_execute_method) | 0 | 189 | 38912 | 361 | 246 | 5.7 | toolUse: verified; state=sale |
| 20 | 19 | 2 (mcp_odoo_get_model_fields, mcp_odoo_read_record) | 0 | 147 | 39424 | 1446 | 1211 | 13.6 | toolUse: success; fields=4; success; result=record |
| 21 | 19 | 1 (mcp_odoo_preview_write) | 0 | 435 | 40960 | 478 | 314 | 8.2 | toolUse: success; result=record |
| 22 | 19 | 1 (mcp_odoo_validate_write) | 0 | 265 | 41856 | 147 | 0 | 4.9 | toolUse: success; result=record |
| 23 | 19 | 1 (mcp_odoo_execute_approved_write) | 0 | 299 | 42240 | 233 | 0 | 4.0 | toolUse: verified; record_ids=[1] |
| 24 | 19 | 1 (mcp_odoo_execute_method) | 0 | 120 | 42752 | 171 | 53 | 4.4 | toolUse: verified |
| 25 | 19 | 1 (mcp_odoo_read_record) | 0 | 434 | 43008 | 252 | 83 | 5.2 | toolUse: success; result=record |
| 26 | 19 | 1 (mcp_odoo_read_record) | 0 | 232 | 43648 | 298 | 150 | 6.3 | toolUse: success; result=record |
| 27 | 19 | 1 (mcp_odoo_execute_method) | 0 | 203 | 44160 | 168 | 50 | 5.5 | toolUse: verified; state=posted |
| 28 | 19 | 2 (mcp_odoo_read_record, mcp_odoo_read_record) | 0 | 220 | 44416 | 275 | 31 | 5.3 | toolUse: success; result=record; success; result=record |
| 29 | 19 | 0 () | 0 | 413 | 44800 | 465 | 0 | 6.9 | stop: Workflow complete. Summary of what was done for **Nimbus Bureau** only: **1. Read Internal Notes first** - Customer `res.partner #10 Nimbus Bureau` — Internal Notes (`comment`): empty. - Product `Open-Plan Noise Barrier Wall` (product.templ |

## Local control events

