# 按能力查找测试

由 `python -m experiments.test_index` 生成；入口见 [TESTING.md](../TESTING.md)。

这里列测试资产，不声明通过。一个套件可出现在多个能力下。历史参考不进入当前业务验收。

付费入口必须先查看原清单、冻结输入和授权；不得批量执行此索引。

## Trace、用量与诊断

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| 实验入口（含准备/评估；并非全部付费） | [fresh_self_debug.py](../experiments/agent_regression/fresh_self_debug.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_input_report.py](../experiments/tool_routing/q4_input_report.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [report.py](../experiments/enterprise_validation/report.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [run_module_slice.py](../experiments/dynamic-tools-budgeted/run_module_slice.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [self_debug_audit.py](../experiments/agent_regression/self_debug_audit.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [self_debug_recovery.py](../experiments/agent_regression/self_debug_recovery.py) | 见入口中的断言 |
| 桌面检查 | [trace-check.mjs](../desktop/scripts/trace-check.mjs) | 见入口中的断言 |
| 桌面检查 | [trace-model-check.mjs](../desktop/scripts/trace-model-check.mjs) | 见入口中的断言 |
| 桌面检查 | [trace-v2-check.mjs](../desktop/scripts/trace-v2-check.mjs) | 见入口中的断言 |
| 真实模型节点 | [M04](../experiments/agent_regression/manual_cases.json) | Policy refusal became unknown failure |
| 离线测试套件 | [test_relation_receipts.py](../tests/test_relation_receipts.py) | 见入口中的断言 |
| 离线测试套件 | [test_reporting.py](../tests/test_reporting.py) | 见入口中的断言 |
| 离线测试套件 | [test_request_receipts.py](../tests/test_request_receipts.py) | 见入口中的断言 |
| 离线测试套件 | [test_run_diagnostics.py](../tests/test_run_diagnostics.py) | 见入口中的断言 |
| 离线测试套件 | [test_runner_budget.py](../tests/test_runner_budget.py) | 见入口中的断言 |
| 离线测试套件 | [test_self_debug_audit.py](../tests/test_self_debug_audit.py) | 见入口中的断言 |
| 离线测试套件 | [test_self_debug_recovery.py](../tests/test_self_debug_recovery.py) | 见入口中的断言 |
| 离线测试套件 | [test_trace_inspector.py](../tests/test_trace_inspector.py) | 见入口中的断言 |
| 离线测试套件 | [test_unbounded_execution.py](../tests/test_unbounded_execution.py) | 见入口中的断言 |

## 上下文、缓存与长期记忆

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| 实验入口（含准备/评估；并非全部付费） | [analyze_token_cost.py](../experiments/desktop_workbench/analyze_token_cost.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [context_followup.py](../experiments/agent_regression/context_followup.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [history_inventory.py](../experiments/tool_routing/history_inventory.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [laya_context_replay.py](../experiments/tool_routing/laya_context_replay.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_prompt_metrics.py](../experiments/tool_routing/q4_prompt_metrics.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_standard_context.py](../experiments/tool_routing/q4_standard_context.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_thinking.py](../experiments/tool_routing/q4_thinking.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_thinking_schema.py](../experiments/tool_routing/q4_thinking_schema.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_thinking_surface.py](../experiments/tool_routing/q4_thinking_surface.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [target_verifier.py](../experiments/token_efficiency_20260910/target_verifier.py) | 见入口中的断言 |
| 真实模型节点 | [HITL01](../experiments/agent_regression/hitl_revision.json) | Approval revision routed to proposal worker; desktop resumed original unchanged instruction; runtime fixed mail copy. |
| 真实模型节点 | [HITL02](../experiments/agent_regression/hitl_revision.json) | Approval revision routed to proposal worker; desktop resumed original unchanged instruction; runtime fixed mail copy. |
| 离线测试套件 | [test_context_window.py](../tests/runtime/test_context_window.py) | 见入口中的断言 |
| 离线测试套件 | [test_cross_provider_history.py](../tests/runtime/test_cross_provider_history.py) | 见入口中的断言 |
| 离线测试套件 | [test_laya_context_replay.py](../tests/test_laya_context_replay.py) | 见入口中的断言 |
| 离线测试套件 | [test_long_term_memory.py](../tests/test_long_term_memory.py) | 见入口中的断言 |
| 离线测试套件 | [test_prompt_caching.py](../tests/runtime/test_prompt_caching.py) | 见入口中的断言 |
| 离线测试套件 | [test_routing_context.py](../tests/test_routing_context.py) | 见入口中的断言 |
| 离线测试套件 | [test_supply_context.py](../tests/test_supply_context.py) | 见入口中的断言 |
| 离线测试套件 | [test_system_prompt.py](../tests/runtime/test_system_prompt.py) | 见入口中的断言 |
| 离线测试套件 | [test_thinking.py](../tests/runtime/test_thinking.py) | 见入口中的断言 |
| 离线测试套件 | [test_tool_history.py](../tests/runtime/test_tool_history.py) | 见入口中的断言 |

## 中断恢复与宿主交接

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| 实验入口（含准备/评估；并非全部付费） | [bench_recovery.py](../experiments/agent_regression/bench_recovery.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [consumption_recovery.py](../experiments/agent_regression/consumption_recovery.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [contract_recovery.py](../experiments/agent_regression/contract_recovery.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [hitl_revision.py](../experiments/agent_regression/hitl_revision.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [interrupted_recovery.py](../experiments/agent_regression/interrupted_recovery.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [recovery_check.py](../experiments/tool_routing/recovery_check.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [recovery_live.py](../experiments/agent_regression/recovery_live.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [routing_recovery.py](../experiments/agent_regression/routing_recovery.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [self_debug_recovery.py](../experiments/agent_regression/self_debug_recovery.py) | 见入口中的断言 |
| 真实模型节点 | [B02](../experiments/agent_regression/hitl_revision.json) | 见冻结清单 |
| 真实模型节点 | [B03](../experiments/agent_regression/hitl_revision.json) | 见冻结清单 |
| 真实模型节点 | [C01 · first_alternate_mail_method_after_pdf_guard](../experiments/agent_regression/cases.py) | failure_recovery |
| 真实模型节点 | [C02 · first_missing_binding_receipt](../experiments/agent_regression/cases.py) | failure_recovery |
| 真实模型节点 | [C03 · blocked_eligibility_before_user_choice](../experiments/agent_regression/cases.py) | failure_recovery |
| 真实模型节点 | [HITL01](../experiments/agent_regression/hitl_revision.json) | Approval revision routed to proposal worker; desktop resumed original unchanged instruction; runtime fixed mail copy. |
| 真实模型节点 | [HITL02](../experiments/agent_regression/hitl_revision.json) | Approval revision routed to proposal worker; desktop resumed original unchanged instruction; runtime fixed mail copy. |
| 真实模型节点 | [IR01](../experiments/agent_regression/interrupted_recovery.json) | host_worker_context |
| 真实模型节点 | [IR02](../experiments/agent_regression/interrupted_recovery.json) | business_readback |
| 真实模型节点 | [M01](../experiments/agent_regression/manual_cases.json) | Host omitted no-business state |
| 真实模型节点 | [M09](../experiments/agent_regression/manual_cases.json) | Pending write prevented applying saved goal update |
| 真实模型节点 | [M13](../experiments/agent_regression/manual_followups.json) | Reviewed purchase cancellation was not explained as including unreceived receipts; unsupported picking cancellation forced handoff |
| 离线测试套件 | [test_approval_resume_scope.py](../tests/test_approval_resume_scope.py) | 见入口中的断言 |
| 离线测试套件 | [test_business_status.py](../tests/test_business_status.py) | 见入口中的断言 |
| 离线测试套件 | [test_interrupted_recovery.py](../tests/test_interrupted_recovery.py) | 见入口中的断言 |
| 离线测试套件 | [test_manual_business_recovery.py](../tests/test_manual_business_recovery.py) | 见入口中的断言 |
| 离线测试套件 | [test_self_debug_recovery.py](../tests/test_self_debug_recovery.py) | 见入口中的断言 |
| 离线测试套件 | [test_transport_recovery.py](../tests/test_transport_recovery.py) | 见入口中的断言 |
| 离线测试套件 | [test_workbench_conversation.py](../tests/test_workbench_conversation.py) | 见入口中的断言 |
| 离线测试套件 | [test_workbench_host.py](../tests/test_workbench_host.py) | 见入口中的断言 |

## 会话、账本与持久化

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| 离线测试套件 | [test_coding_session.py](../tests/runtime/test_coding_session.py) | 见入口中的断言 |
| 离线测试套件 | [test_pi_session_retry.py](../tests/runtime/test_pi_session_retry.py) | 见入口中的断言 |
| 离线测试套件 | [test_session.py](../tests/runtime/test_session.py) | 见入口中的断言 |
| 离线测试套件 | [test_session_manager.py](../tests/runtime/test_session_manager.py) | 见入口中的断言 |
| 离线测试套件 | [test_session_snapshot.py](../tests/test_session_snapshot.py) | 见入口中的断言 |
| 离线测试套件 | [test_session_stats.py](../tests/runtime/test_session_stats.py) | 见入口中的断言 |
| 离线测试套件 | [test_snapshot.py](../tests/test_snapshot.py) | 见入口中的断言 |
| 离线测试套件 | [test_storage.py](../tests/test_storage.py) | 见入口中的断言 |
| 离线测试套件 | [test_world.py](../tests/test_world.py) | 见入口中的断言 |

## 制造、库存与业务日期

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| ERPBench 完整业务 | [2064 · Track Spotlight System 4-Head](../bench/tasks/2064_medium_08_single_bom_single_workcenter/task.toml) | 08_single_bom_single_workcenter |
| ERPBench 完整业务 | [2065 · High-Bay LED Fixture 200W](../bench/tasks/2065_medium_08_single_bom_single_workcenter/task.toml) | 08_single_bom_single_workcenter |
| ERPBench 完整业务 | [2066 · Architectural Pendant Luminaire](../bench/tasks/2066_medium_08_single_bom_single_workcenter/task.toml) | 08_single_bom_single_workcenter |
| ERPBench 完整业务 | [2067 · Architectural Pendant Luminaire](../bench/tasks/2067_medium_08_single_bom_single_workcenter/task.toml) | 08_single_bom_single_workcenter |
| ERPBench 完整业务 | [2068 · Parking Garage Canopy Light](../bench/tasks/2068_medium_08_single_bom_single_workcenter/task.toml) | 08_single_bom_single_workcenter |
| ERPBench 完整业务 | [2069 · Emergency Exit Combo Fixture](../bench/tasks/2069_medium_08_single_bom_single_workcenter/task.toml) | 08_single_bom_single_workcenter |
| ERPBench 完整业务 | [2070 · Parking Garage Canopy Light](../bench/tasks/2070_medium_08_single_bom_single_workcenter/task.toml) | 08_single_bom_single_workcenter |
| ERPBench 完整业务 | [2071 · Architectural Pendant Luminaire](../bench/tasks/2071_medium_08_single_bom_single_workcenter/task.toml) | 08_single_bom_single_workcenter |
| ERPBench 完整业务 | [2072 · High-Bay LED Fixture 200W](../bench/tasks/2072_medium_08_single_bom_single_workcenter/task.toml) | 08_single_bom_single_workcenter |
| ERPBench 完整业务 | [2073 · Linear Strip Light 8ft](../bench/tasks/2073_medium_08_single_bom_single_workcenter/task.toml) | 08_single_bom_single_workcenter |
| ERPBench 完整业务 | [2074 · Bollard Path Light Solar](../bench/tasks/2074_medium_08_single_bom_single_workcenter/task.toml) | 08_single_bom_single_workcenter |
| ERPBench 完整业务 | [2075 · Automatic Case Erector](../bench/tasks/2075_medium_09_single_bom_lowest_cost/task.toml) | 09_single_bom_lowest_cost |
| ERPBench 完整业务 | [2076 · Labeling Applicator System](../bench/tasks/2076_medium_09_single_bom_lowest_cost/task.toml) | 09_single_bom_lowest_cost |
| ERPBench 完整业务 | [2077 · Shrink Wrap Tunnel System](../bench/tasks/2077_medium_09_single_bom_lowest_cost/task.toml) | 09_single_bom_lowest_cost |
| ERPBench 完整业务 | [2078 · Automatic Case Erector](../bench/tasks/2078_medium_09_single_bom_lowest_cost/task.toml) | 09_single_bom_lowest_cost |
| ERPBench 完整业务 | [2079 · Checkweigher Inline Module](../bench/tasks/2079_medium_09_single_bom_lowest_cost/task.toml) | 09_single_bom_lowest_cost |
| ERPBench 完整业务 | [2080 · Vacuum Packaging Chamber Unit](../bench/tasks/2080_medium_09_single_bom_lowest_cost/task.toml) | 09_single_bom_lowest_cost |
| ERPBench 完整业务 | [2081 · Shrink Wrap Tunnel System](../bench/tasks/2081_medium_09_single_bom_lowest_cost/task.toml) | 09_single_bom_lowest_cost |
| ERPBench 完整业务 | [2082 · Strapping Machine Automatic](../bench/tasks/2082_medium_09_single_bom_lowest_cost/task.toml) | 09_single_bom_lowest_cost |
| ERPBench 完整业务 | [2083 · Stretch Wrapper Turntable Unit](../bench/tasks/2083_medium_09_single_bom_lowest_cost/task.toml) | 09_single_bom_lowest_cost |
| ERPBench 完整业务 | [2084 · Automatic Case Erector](../bench/tasks/2084_medium_09_single_bom_lowest_cost/task.toml) | 09_single_bom_lowest_cost |
| ERPBench 完整业务 | [2085 · Vertical Form-Fill-Seal Machine](../bench/tasks/2085_medium_09_single_bom_lowest_cost/task.toml) | 09_single_bom_lowest_cost |
| ERPBench 完整业务 | [2086 · Dust Containment Enclosure](../bench/tasks/2086_medium_10_single_bom_split_by_capacity/task.toml) | 10_single_bom_split_by_capacity |
| ERPBench 完整业务 | [2087 · Explosion-Proof Electrical Cabinet](../bench/tasks/2087_medium_10_single_bom_split_by_capacity/task.toml) | 10_single_bom_split_by_capacity |
| ERPBench 完整业务 | [2088 · Machine Guard Perimeter Fence](../bench/tasks/2088_medium_10_single_bom_split_by_capacity/task.toml) | 10_single_bom_split_by_capacity |
| ERPBench 完整业务 | [2089 · Interlocked Access Gate Panel](../bench/tasks/2089_medium_10_single_bom_split_by_capacity/task.toml) | 10_single_bom_split_by_capacity |
| ERPBench 完整业务 | [2090 · Machine Guard Perimeter Fence](../bench/tasks/2090_medium_10_single_bom_split_by_capacity/task.toml) | 10_single_bom_split_by_capacity |
| ERPBench 完整业务 | [2091 · Chemical Storage Safety Cabinet](../bench/tasks/2091_medium_10_single_bom_split_by_capacity/task.toml) | 10_single_bom_split_by_capacity |
| ERPBench 完整业务 | [2092 · Dust Containment Enclosure](../bench/tasks/2092_medium_10_single_bom_split_by_capacity/task.toml) | 10_single_bom_split_by_capacity |
| ERPBench 完整业务 | [2093 · Laser Safety Curtain System](../bench/tasks/2093_medium_10_single_bom_split_by_capacity/task.toml) | 10_single_bom_split_by_capacity |
| ERPBench 完整业务 | [2094 · Explosion-Proof Electrical Cabinet](../bench/tasks/2094_medium_10_single_bom_split_by_capacity/task.toml) | 10_single_bom_split_by_capacity |
| ERPBench 完整业务 | [2095 · Interlocked Access Gate Panel](../bench/tasks/2095_medium_10_single_bom_split_by_capacity/task.toml) | 10_single_bom_split_by_capacity |
| ERPBench 完整业务 | [2096 · Laser Safety Curtain System](../bench/tasks/2096_medium_10_single_bom_split_by_capacity/task.toml) | 10_single_bom_split_by_capacity |
| ERPBench 完整业务 | [2097 · Solar Shingle Roof Tile Set](../bench/tasks/2097_hard_11_restricted_subassembly_qualified_workcenters/task.toml) | 11_restricted_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2098 · Carport Solar Canopy Module](../bench/tasks/2098_hard_11_restricted_subassembly_qualified_workcenters/task.toml) | 11_restricted_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2099 · Portable Solar Generator Kit](../bench/tasks/2099_hard_11_restricted_subassembly_qualified_workcenters/task.toml) | 11_restricted_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2100 · Commercial Rooftop Array 10kW](../bench/tasks/2100_hard_11_restricted_subassembly_qualified_workcenters/task.toml) | 11_restricted_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2101 · Solar Shingle Roof Tile Set](../bench/tasks/2101_hard_11_restricted_subassembly_qualified_workcenters/task.toml) | 11_restricted_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2102 · Building-Integrated PV Module](../bench/tasks/2102_hard_11_restricted_subassembly_qualified_workcenters/task.toml) | 11_restricted_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2103 · Building-Integrated PV Module](../bench/tasks/2103_hard_11_restricted_subassembly_qualified_workcenters/task.toml) | 11_restricted_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2104 · Floating Solar Platform Unit](../bench/tasks/2104_hard_11_restricted_subassembly_qualified_workcenters/task.toml) | 11_restricted_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2105 · Ground-Mount Tracker System](../bench/tasks/2105_hard_11_restricted_subassembly_qualified_workcenters/task.toml) | 11_restricted_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2106 · Portable Solar Generator Kit](../bench/tasks/2106_hard_11_restricted_subassembly_qualified_workcenters/task.toml) | 11_restricted_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2107 · Cartesian Gantry Robot 25kg](../bench/tasks/2107_medium_12_single_subassembly_lowest_cost/task.toml) | 12_single_subassembly_lowest_cost |
| ERPBench 完整业务 | [2108 · Painting Robot 6-Axis EX-Proof](../bench/tasks/2108_medium_12_single_subassembly_lowest_cost/task.toml) | 12_single_subassembly_lowest_cost |
| ERPBench 完整业务 | [2109 · Cartesian Gantry Robot 25kg](../bench/tasks/2109_medium_12_single_subassembly_lowest_cost/task.toml) | 12_single_subassembly_lowest_cost |
| ERPBench 完整业务 | [2110 · Welding Robot Cell Package](../bench/tasks/2110_medium_12_single_subassembly_lowest_cost/task.toml) | 12_single_subassembly_lowest_cost |
| ERPBench 完整业务 | [2111 · Delta Parallel Robot 3kg](../bench/tasks/2111_medium_12_single_subassembly_lowest_cost/task.toml) | 12_single_subassembly_lowest_cost |
| ERPBench 完整业务 | [2112 · Painting Robot 6-Axis EX-Proof](../bench/tasks/2112_medium_12_single_subassembly_lowest_cost/task.toml) | 12_single_subassembly_lowest_cost |
| ERPBench 完整业务 | [2113 · 6-Axis Articulated Robot 10kg](../bench/tasks/2113_medium_12_single_subassembly_lowest_cost/task.toml) | 12_single_subassembly_lowest_cost |
| ERPBench 完整业务 | [2114 · Dual-Arm Assembly Robot](../bench/tasks/2114_medium_12_single_subassembly_lowest_cost/task.toml) | 12_single_subassembly_lowest_cost |
| ERPBench 完整业务 | [2115 · Dual-Arm Assembly Robot](../bench/tasks/2115_medium_12_single_subassembly_lowest_cost/task.toml) | 12_single_subassembly_lowest_cost |
| ERPBench 完整业务 | [2116 · Palletizing Robot 4-Axis 200kg](../bench/tasks/2116_medium_12_single_subassembly_lowest_cost/task.toml) | 12_single_subassembly_lowest_cost |
| ERPBench 完整业务 | [2117 · Delta Parallel Robot 3kg](../bench/tasks/2117_medium_12_single_subassembly_lowest_cost/task.toml) | 12_single_subassembly_lowest_cost |
| ERPBench 完整业务 | [2118 · Point-of-Use Dispenser Module](../bench/tasks/2118_medium_13_single_subassembly_qualified_workcenters/task.toml) | 13_single_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2119 · Deionization Polishing Unit](../bench/tasks/2119_medium_13_single_subassembly_qualified_workcenters/task.toml) | 13_single_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2120 · UV Sterilization Unit Inline](../bench/tasks/2120_medium_13_single_subassembly_qualified_workcenters/task.toml) | 13_single_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2121 · Sediment Pre-Filter Assembly](../bench/tasks/2121_medium_13_single_subassembly_qualified_workcenters/task.toml) | 13_single_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2122 · UV Sterilization Unit Inline](../bench/tasks/2122_medium_13_single_subassembly_qualified_workcenters/task.toml) | 13_single_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2123 · Multi-Stage Filtration Tower](../bench/tasks/2123_medium_13_single_subassembly_qualified_workcenters/task.toml) | 13_single_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2124 · Deionization Polishing Unit](../bench/tasks/2124_medium_13_single_subassembly_qualified_workcenters/task.toml) | 13_single_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2125 · Activated Carbon Filter Bank](../bench/tasks/2125_medium_13_single_subassembly_qualified_workcenters/task.toml) | 13_single_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2126 · Deionization Polishing Unit](../bench/tasks/2126_medium_13_single_subassembly_qualified_workcenters/task.toml) | 13_single_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2127 · Softener Exchange Tank System](../bench/tasks/2127_medium_13_single_subassembly_qualified_workcenters/task.toml) | 13_single_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2128 · Multi-Stage Filtration Tower](../bench/tasks/2128_medium_13_single_subassembly_qualified_workcenters/task.toml) | 13_single_subassembly_qualified_workcenters |
| ERPBench 完整业务 | [2129 · Car Frame Platform 2500kg](../bench/tasks/2129_medium_14_single_subassembly_shared_overflow_capacity/task.toml) | 14_single_subassembly_shared_overflow_capacity |
| ERPBench 完整业务 | [2130 · Car Frame Platform 2500kg](../bench/tasks/2130_medium_14_single_subassembly_shared_overflow_capacity/task.toml) | 14_single_subassembly_shared_overflow_capacity |
| ERPBench 完整业务 | [2131 · Landing Door Assembly Center-Open](../bench/tasks/2131_medium_14_single_subassembly_shared_overflow_capacity/task.toml) | 14_single_subassembly_shared_overflow_capacity |
| ERPBench 完整业务 | [2132 · Machine-Room-Less Drive Unit](../bench/tasks/2132_medium_14_single_subassembly_shared_overflow_capacity/task.toml) | 14_single_subassembly_shared_overflow_capacity |
| ERPBench 完整业务 | [2133 · Landing Door Assembly Center-Open](../bench/tasks/2133_medium_14_single_subassembly_shared_overflow_capacity/task.toml) | 14_single_subassembly_shared_overflow_capacity |
| ERPBench 完整业务 | [2134 · Pit Equipment Safety Package](../bench/tasks/2134_medium_14_single_subassembly_shared_overflow_capacity/task.toml) | 14_single_subassembly_shared_overflow_capacity |
| ERPBench 完整业务 | [2135 · Landing Door Assembly Single-Speed](../bench/tasks/2135_medium_14_single_subassembly_shared_overflow_capacity/task.toml) | 14_single_subassembly_shared_overflow_capacity |
| ERPBench 完整业务 | [2136 · Gearless Traction Controller 10-Stop](../bench/tasks/2136_medium_14_single_subassembly_shared_overflow_capacity/task.toml) | 14_single_subassembly_shared_overflow_capacity |
| ERPBench 完整业务 | [2137 · Pit Equipment Safety Package](../bench/tasks/2137_medium_14_single_subassembly_shared_overflow_capacity/task.toml) | 14_single_subassembly_shared_overflow_capacity |
| ERPBench 完整业务 | [2138 · Hydraulic Door Operator Assembly](../bench/tasks/2138_medium_14_single_subassembly_shared_overflow_capacity/task.toml) | 14_single_subassembly_shared_overflow_capacity |
| ERPBench 完整业务 | [2139 · Landing Door Assembly Single-Speed](../bench/tasks/2139_medium_14_single_subassembly_shared_overflow_capacity/task.toml) | 14_single_subassembly_shared_overflow_capacity |
| ERPBench 完整业务 | [2140 · Modular Rack Battery System 20kWh](../bench/tasks/2140_hard_15_parallel_subassemblies_branch_assigned/task.toml) | 15_parallel_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2141 · Portable Power Station 3kWh](../bench/tasks/2141_hard_15_parallel_subassemblies_branch_assigned/task.toml) | 15_parallel_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2142 · Portable Power Station 3kWh](../bench/tasks/2142_hard_15_parallel_subassemblies_branch_assigned/task.toml) | 15_parallel_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2143 · LFP Energy Storage Module 51.2V](../bench/tasks/2143_hard_15_parallel_subassemblies_branch_assigned/task.toml) | 15_parallel_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2144 · Portable Power Station 3kWh](../bench/tasks/2144_hard_15_parallel_subassemblies_branch_assigned/task.toml) | 15_parallel_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2145 · LFP Energy Storage Module 51.2V](../bench/tasks/2145_hard_15_parallel_subassemblies_branch_assigned/task.toml) | 15_parallel_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2146 · Modular Rack Battery System 20kWh](../bench/tasks/2146_hard_15_parallel_subassemblies_branch_assigned/task.toml) | 15_parallel_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2147 · LFP Energy Storage Module 51.2V](../bench/tasks/2147_hard_15_parallel_subassemblies_branch_assigned/task.toml) | 15_parallel_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2148 · EV Traction Pack 400V 60kWh](../bench/tasks/2148_hard_15_parallel_subassemblies_branch_assigned/task.toml) | 15_parallel_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2149 · Solar Storage Battery Wall-Mount](../bench/tasks/2149_hard_15_parallel_subassemblies_branch_assigned/task.toml) | 15_parallel_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2150 · Booster Compressor 500PSI](../bench/tasks/2150_hard_16_serial_subassemblies_branch_assigned/task.toml) | 16_serial_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2151 · Centrifugal Compressor 200HP](../bench/tasks/2151_hard_16_serial_subassemblies_branch_assigned/task.toml) | 16_serial_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2152 · Booster Compressor 500PSI](../bench/tasks/2152_hard_16_serial_subassemblies_branch_assigned/task.toml) | 16_serial_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2153 · Centrifugal Compressor 200HP](../bench/tasks/2153_hard_16_serial_subassemblies_branch_assigned/task.toml) | 16_serial_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2154 · Centrifugal Compressor 200HP](../bench/tasks/2154_hard_16_serial_subassemblies_branch_assigned/task.toml) | 16_serial_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2155 · Low-Pressure Blower Package 25HP](../bench/tasks/2155_hard_16_serial_subassemblies_branch_assigned/task.toml) | 16_serial_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2156 · Booster Compressor 500PSI](../bench/tasks/2156_hard_16_serial_subassemblies_branch_assigned/task.toml) | 16_serial_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2157 · Reciprocating Piston Compressor 15HP](../bench/tasks/2157_hard_16_serial_subassemblies_branch_assigned/task.toml) | 16_serial_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2158 · Booster Compressor 500PSI](../bench/tasks/2158_hard_16_serial_subassemblies_branch_assigned/task.toml) | 16_serial_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2159 · Duplex Compressor System 30HP](../bench/tasks/2159_hard_16_serial_subassemblies_branch_assigned/task.toml) | 16_serial_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2160 · Standpipe and Hose System Package](../bench/tasks/2160_hard_17_shared_component_subassemblies_branch_assigned/task.toml) | 17_shared_component_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2161 · Dry Pipe Sprinkler System](../bench/tasks/2161_hard_17_shared_component_subassemblies_branch_assigned/task.toml) | 17_shared_component_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2162 · Clean Agent FM-200 System](../bench/tasks/2162_hard_17_shared_component_subassemblies_branch_assigned/task.toml) | 17_shared_component_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2163 · CO2 Total Flooding System](../bench/tasks/2163_hard_17_shared_component_subassemblies_branch_assigned/task.toml) | 17_shared_component_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2164 · Standpipe and Hose System Package](../bench/tasks/2164_hard_17_shared_component_subassemblies_branch_assigned/task.toml) | 17_shared_component_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2165 · Standpipe and Hose System Package](../bench/tasks/2165_hard_17_shared_component_subassemblies_branch_assigned/task.toml) | 17_shared_component_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2166 · Wet Sprinkler System Package](../bench/tasks/2166_hard_17_shared_component_subassemblies_branch_assigned/task.toml) | 17_shared_component_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2167 · Wet Sprinkler System Package](../bench/tasks/2167_hard_17_shared_component_subassemblies_branch_assigned/task.toml) | 17_shared_component_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2168 · Foam Deluge System Package](../bench/tasks/2168_hard_17_shared_component_subassemblies_branch_assigned/task.toml) | 17_shared_component_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2169 · CO2 Total Flooding System](../bench/tasks/2169_hard_17_shared_component_subassemblies_branch_assigned/task.toml) | 17_shared_component_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2170 · Fire Alarm Control Panel FACP](../bench/tasks/2170_hard_17_shared_component_subassemblies_branch_assigned/task.toml) | 17_shared_component_subassemblies_branch_assigned |
| ERPBench 完整业务 | [2171 · Gowning Room Airlock System](../bench/tasks/2171_medium_18_manufacture_only_policy_forbidden/task.toml) | 18_manufacture_only_policy_forbidden |
| ERPBench 完整业务 | [2172 · Softwall Curtain Cleanroom ISO 8](../bench/tasks/2172_medium_18_manufacture_only_policy_forbidden/task.toml) | 18_manufacture_only_policy_forbidden |
| ERPBench 完整业务 | [2173 · Gowning Room Airlock System](../bench/tasks/2173_medium_18_manufacture_only_policy_forbidden/task.toml) | 18_manufacture_only_policy_forbidden |
| ERPBench 完整业务 | [2174 · Laminar Flow Bench Horizontal](../bench/tasks/2174_medium_18_manufacture_only_policy_forbidden/task.toml) | 18_manufacture_only_policy_forbidden |
| ERPBench 完整业务 | [2175 · Modular Cleanroom Panel System ISO 7](../bench/tasks/2175_medium_18_manufacture_only_policy_forbidden/task.toml) | 18_manufacture_only_policy_forbidden |
| ERPBench 完整业务 | [2176 · Air Shower Personnel Decontamination](../bench/tasks/2176_medium_18_manufacture_only_policy_forbidden/task.toml) | 18_manufacture_only_policy_forbidden |
| ERPBench 完整业务 | [2177 · Air Shower Personnel Decontamination](../bench/tasks/2177_medium_18_manufacture_only_policy_forbidden/task.toml) | 18_manufacture_only_policy_forbidden |
| ERPBench 完整业务 | [2178 · Softwall Curtain Cleanroom ISO 8](../bench/tasks/2178_medium_18_manufacture_only_policy_forbidden/task.toml) | 18_manufacture_only_policy_forbidden |
| ERPBench 完整业务 | [2179 · Laminar Flow Bench Horizontal](../bench/tasks/2179_medium_18_manufacture_only_policy_forbidden/task.toml) | 18_manufacture_only_policy_forbidden |
| ERPBench 完整业务 | [2180 · Biosafety Cabinet Class II Type A2](../bench/tasks/2180_medium_18_manufacture_only_policy_forbidden/task.toml) | 18_manufacture_only_policy_forbidden |
| ERPBench 完整业务 | [2181 · Laminar Flow Bench Horizontal](../bench/tasks/2181_medium_18_manufacture_only_policy_forbidden/task.toml) | 18_manufacture_only_policy_forbidden |
| ERPBench 完整业务 | [2182 · Refrigerated Dock Leveler Package](../bench/tasks/2182_medium_19_manufacture_only_no_buy_route/task.toml) | 19_manufacture_only_no_buy_route |
| ERPBench 完整业务 | [2183 · Vertical Storing Dock Leveler](../bench/tasks/2183_medium_19_manufacture_only_no_buy_route/task.toml) | 19_manufacture_only_no_buy_route |
| ERPBench 完整业务 | [2184 · Rail Dock Leveler Board 40000lb](../bench/tasks/2184_medium_19_manufacture_only_no_buy_route/task.toml) | 19_manufacture_only_no_buy_route |
| ERPBench 完整业务 | [2185 · Rail Dock Leveler Board 40000lb](../bench/tasks/2185_medium_19_manufacture_only_no_buy_route/task.toml) | 19_manufacture_only_no_buy_route |
| ERPBench 完整业务 | [2186 · Hydraulic Dock Leveler 25000lb](../bench/tasks/2186_medium_19_manufacture_only_no_buy_route/task.toml) | 19_manufacture_only_no_buy_route |
| ERPBench 完整业务 | [2187 · Mechanical Dock Leveler 30000lb](../bench/tasks/2187_medium_19_manufacture_only_no_buy_route/task.toml) | 19_manufacture_only_no_buy_route |
| ERPBench 完整业务 | [2188 · Vertical Storing Dock Leveler](../bench/tasks/2188_medium_19_manufacture_only_no_buy_route/task.toml) | 19_manufacture_only_no_buy_route |
| ERPBench 完整业务 | [2189 · Air-Powered Dock Leveler 35000lb](../bench/tasks/2189_medium_19_manufacture_only_no_buy_route/task.toml) | 19_manufacture_only_no_buy_route |
| ERPBench 完整业务 | [2190 · Rail Dock Leveler Board 40000lb](../bench/tasks/2190_medium_19_manufacture_only_no_buy_route/task.toml) | 19_manufacture_only_no_buy_route |
| ERPBench 完整业务 | [2191 · Mechanical Dock Leveler 30000lb](../bench/tasks/2191_medium_19_manufacture_only_no_buy_route/task.toml) | 19_manufacture_only_no_buy_route |
| ERPBench 完整业务 | [2192 · Rail Dock Leveler Board 40000lb](../bench/tasks/2192_medium_19_manufacture_only_no_buy_route/task.toml) | 19_manufacture_only_no_buy_route |
| ERPBench 完整业务 | [2193 · Pad-Mount Transformer 750kVA](../bench/tasks/2193_medium_20_manufacture_only_no_available_vendors/task.toml) | 20_manufacture_only_no_available_vendors |
| ERPBench 完整业务 | [2194 · Cast Resin Transformer 300kVA](../bench/tasks/2194_medium_20_manufacture_only_no_available_vendors/task.toml) | 20_manufacture_only_no_available_vendors |
| ERPBench 完整业务 | [2195 · Dry-Type Transformer 500kVA](../bench/tasks/2195_medium_20_manufacture_only_no_available_vendors/task.toml) | 20_manufacture_only_no_available_vendors |
| ERPBench 完整业务 | [2196 · Oil-Filled Transformer 1000kVA](../bench/tasks/2196_medium_20_manufacture_only_no_available_vendors/task.toml) | 20_manufacture_only_no_available_vendors |
| ERPBench 完整业务 | [2197 · Auto-Transformer 250kVA](../bench/tasks/2197_medium_20_manufacture_only_no_available_vendors/task.toml) | 20_manufacture_only_no_available_vendors |
| ERPBench 完整业务 | [2198 · Isolation Transformer 150kVA](../bench/tasks/2198_medium_20_manufacture_only_no_available_vendors/task.toml) | 20_manufacture_only_no_available_vendors |
| ERPBench 完整业务 | [2199 · Cast Resin Transformer 300kVA](../bench/tasks/2199_medium_20_manufacture_only_no_available_vendors/task.toml) | 20_manufacture_only_no_available_vendors |
| ERPBench 完整业务 | [2200 · Step-Down Transformer 2000kVA](../bench/tasks/2200_medium_20_manufacture_only_no_available_vendors/task.toml) | 20_manufacture_only_no_available_vendors |
| ERPBench 完整业务 | [2201 · Auto-Transformer 250kVA](../bench/tasks/2201_medium_20_manufacture_only_no_available_vendors/task.toml) | 20_manufacture_only_no_available_vendors |
| ERPBench 完整业务 | [2202 · Dry-Type Transformer 500kVA](../bench/tasks/2202_medium_20_manufacture_only_no_available_vendors/task.toml) | 20_manufacture_only_no_available_vendors |
| ERPBench 完整业务 | [2203 · Dry-Type Transformer 500kVA](../bench/tasks/2203_medium_20_manufacture_only_no_available_vendors/task.toml) | 20_manufacture_only_no_available_vendors |
| ERPBench 完整业务 | [2204 · Cast Resin Transformer 300kVA](../bench/tasks/2204_medium_20_manufacture_only_no_available_vendors/task.toml) | 20_manufacture_only_no_available_vendors |
| ERPBench 完整业务 | [2205 · Stretch Wrapper Turntable Unit](../bench/tasks/2205_medium_21_single_bom_lowest_cost_screened_mixed_seeded/task.toml) | 21_single_bom_lowest_cost_screened_mixed_seeded |
| ERPBench 完整业务 | [2206 · Carton Sealer Top-Bottom](../bench/tasks/2206_medium_21_single_bom_lowest_cost_screened_mixed_seeded/task.toml) | 21_single_bom_lowest_cost_screened_mixed_seeded |
| ERPBench 完整业务 | [2207 · Palletizer Stacking Module](../bench/tasks/2207_medium_21_single_bom_lowest_cost_screened_mixed_seeded/task.toml) | 21_single_bom_lowest_cost_screened_mixed_seeded |
| ERPBench 完整业务 | [2208 · Stretch Wrapper Turntable Unit](../bench/tasks/2208_medium_21_single_bom_lowest_cost_screened_mixed_seeded/task.toml) | 21_single_bom_lowest_cost_screened_mixed_seeded |
| ERPBench 完整业务 | [2209 · Vertical Form-Fill-Seal Machine](../bench/tasks/2209_medium_21_single_bom_lowest_cost_screened_mixed_seeded/task.toml) | 21_single_bom_lowest_cost_screened_mixed_seeded |
| ERPBench 完整业务 | [2210 · Checkweigher Inline Module](../bench/tasks/2210_medium_21_single_bom_lowest_cost_screened_mixed_seeded/task.toml) | 21_single_bom_lowest_cost_screened_mixed_seeded |
| ERPBench 完整业务 | [2211 · Vertical Form-Fill-Seal Machine](../bench/tasks/2211_medium_21_single_bom_lowest_cost_screened_mixed_seeded/task.toml) | 21_single_bom_lowest_cost_screened_mixed_seeded |
| ERPBench 完整业务 | [2212 · Checkweigher Inline Module](../bench/tasks/2212_medium_21_single_bom_lowest_cost_screened_mixed_seeded/task.toml) | 21_single_bom_lowest_cost_screened_mixed_seeded |
| ERPBench 完整业务 | [2213 · Palletizer Stacking Module](../bench/tasks/2213_medium_21_single_bom_lowest_cost_screened_mixed_seeded/task.toml) | 21_single_bom_lowest_cost_screened_mixed_seeded |
| ERPBench 完整业务 | [2214 · Strapping Machine Automatic](../bench/tasks/2214_medium_21_single_bom_lowest_cost_screened_mixed_seeded/task.toml) | 21_single_bom_lowest_cost_screened_mixed_seeded |
| ERPBench 完整业务 | [2215 · Shrink Wrap Tunnel System](../bench/tasks/2215_medium_21_single_bom_lowest_cost_screened_mixed_seeded/task.toml) | 21_single_bom_lowest_cost_screened_mixed_seeded |
| ERPBench 完整业务 | [2216 · Labeling Applicator System](../bench/tasks/2216_medium_21_single_bom_lowest_cost_screened_mixed_seeded/task.toml) | 21_single_bom_lowest_cost_screened_mixed_seeded |
| ERPBench 完整业务 | [2217 · Conveyor Tunnel Guard System](../bench/tasks/2217_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2218 · Conveyor Tunnel Guard System](../bench/tasks/2218_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2219 · Machine Guard Perimeter Fence](../bench/tasks/2219_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2220 · Welding Screen Partition Set](../bench/tasks/2220_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2221 · Chemical Storage Safety Cabinet](../bench/tasks/2221_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2222 · Acoustic Isolation Booth](../bench/tasks/2222_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2223 · Laser Safety Curtain System](../bench/tasks/2223_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2224 · Dust Containment Enclosure](../bench/tasks/2224_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2225 · Welding Screen Partition Set](../bench/tasks/2225_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2226 · Dust Containment Enclosure](../bench/tasks/2226_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2227 · Machine Guard Perimeter Fence](../bench/tasks/2227_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2228 · Acoustic Isolation Booth](../bench/tasks/2228_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2229 · Solar Shingle Roof Tile Set](../bench/tasks/2229_hard_23_restricted_subassembly_qualified_workcenters_screened_all_seeded/task.toml) | 23_restricted_subassembly_qualified_workcenters_screened_all_seeded |
| ERPBench 完整业务 | [2230 · Floating Solar Platform Unit](../bench/tasks/2230_hard_23_restricted_subassembly_qualified_workcenters_screened_all_seeded/task.toml) | 23_restricted_subassembly_qualified_workcenters_screened_all_seeded |
| ERPBench 完整业务 | [2231 · Commercial Rooftop Array 10kW](../bench/tasks/2231_hard_23_restricted_subassembly_qualified_workcenters_screened_all_seeded/task.toml) | 23_restricted_subassembly_qualified_workcenters_screened_all_seeded |
| ERPBench 完整业务 | [2232 · Bifacial Solar Module 450W](../bench/tasks/2232_hard_23_restricted_subassembly_qualified_workcenters_screened_all_seeded/task.toml) | 23_restricted_subassembly_qualified_workcenters_screened_all_seeded |
| ERPBench 完整业务 | [2233 · Carport Solar Canopy Module](../bench/tasks/2233_hard_23_restricted_subassembly_qualified_workcenters_screened_all_seeded/task.toml) | 23_restricted_subassembly_qualified_workcenters_screened_all_seeded |
| ERPBench 完整业务 | [2234 · Portable Solar Generator Kit](../bench/tasks/2234_hard_23_restricted_subassembly_qualified_workcenters_screened_all_seeded/task.toml) | 23_restricted_subassembly_qualified_workcenters_screened_all_seeded |
| ERPBench 完整业务 | [2235 · Monocrystalline Panel 400W](../bench/tasks/2235_hard_23_restricted_subassembly_qualified_workcenters_screened_all_seeded/task.toml) | 23_restricted_subassembly_qualified_workcenters_screened_all_seeded |
| ERPBench 完整业务 | [2236 · Building-Integrated PV Module](../bench/tasks/2236_hard_23_restricted_subassembly_qualified_workcenters_screened_all_seeded/task.toml) | 23_restricted_subassembly_qualified_workcenters_screened_all_seeded |
| ERPBench 完整业务 | [2237 · Bifacial Solar Module 450W](../bench/tasks/2237_hard_23_restricted_subassembly_qualified_workcenters_screened_all_seeded/task.toml) | 23_restricted_subassembly_qualified_workcenters_screened_all_seeded |
| ERPBench 完整业务 | [2238 · Ground-Mount Tracker System](../bench/tasks/2238_hard_23_restricted_subassembly_qualified_workcenters_screened_all_seeded/task.toml) | 23_restricted_subassembly_qualified_workcenters_screened_all_seeded |
| ERPBench 完整业务 | [2239 · Portable Solar Generator Kit](../bench/tasks/2239_hard_23_restricted_subassembly_qualified_workcenters_screened_all_seeded/task.toml) | 23_restricted_subassembly_qualified_workcenters_screened_all_seeded |
| ERPBench 完整业务 | [2240 · Car Frame Platform 2500kg](../bench/tasks/2240_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2241 · Car Frame Platform 4000kg](../bench/tasks/2241_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2242 · Landing Door Assembly Center-Open](../bench/tasks/2242_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2243 · Car Frame Platform 4000kg](../bench/tasks/2243_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2244 · Gearless Traction Controller 10-Stop](../bench/tasks/2244_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2245 · Landing Door Assembly Single-Speed](../bench/tasks/2245_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2246 · Elevator Cab Interior Module](../bench/tasks/2246_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2247 · Car Frame Platform 2500kg](../bench/tasks/2247_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2248 · Elevator Cab Interior Module](../bench/tasks/2248_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2249 · Landing Door Assembly Single-Speed](../bench/tasks/2249_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2250 · Elevator Cab Interior Module](../bench/tasks/2250_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2251 · Foam Deluge System Package](../bench/tasks/2251_hard_25_shared_component_subassemblies_branch_assigned_screened_all_seeded/task.toml) | 25_shared_component_subassemblies_branch_assigned_screened_all_seeded |
| ERPBench 完整业务 | [2252 · Kitchen Hood Suppression Unit](../bench/tasks/2252_hard_25_shared_component_subassemblies_branch_assigned_screened_all_seeded/task.toml) | 25_shared_component_subassemblies_branch_assigned_screened_all_seeded |
| ERPBench 完整业务 | [2253 · Fire Alarm Control Panel FACP](../bench/tasks/2253_hard_25_shared_component_subassemblies_branch_assigned_screened_all_seeded/task.toml) | 25_shared_component_subassemblies_branch_assigned_screened_all_seeded |
| ERPBench 完整业务 | [2254 · Dry Pipe Sprinkler System](../bench/tasks/2254_hard_25_shared_component_subassemblies_branch_assigned_screened_all_seeded/task.toml) | 25_shared_component_subassemblies_branch_assigned_screened_all_seeded |
| ERPBench 完整业务 | [2255 · Clean Agent FM-200 System](../bench/tasks/2255_hard_25_shared_component_subassemblies_branch_assigned_screened_all_seeded/task.toml) | 25_shared_component_subassemblies_branch_assigned_screened_all_seeded |
| ERPBench 完整业务 | [2256 · Wet Sprinkler System Package](../bench/tasks/2256_hard_25_shared_component_subassemblies_branch_assigned_screened_all_seeded/task.toml) | 25_shared_component_subassemblies_branch_assigned_screened_all_seeded |
| ERPBench 完整业务 | [2257 · Fire Alarm Control Panel FACP](../bench/tasks/2257_hard_25_shared_component_subassemblies_branch_assigned_screened_all_seeded/task.toml) | 25_shared_component_subassemblies_branch_assigned_screened_all_seeded |
| ERPBench 完整业务 | [2258 · CO2 Total Flooding System](../bench/tasks/2258_hard_25_shared_component_subassemblies_branch_assigned_screened_all_seeded/task.toml) | 25_shared_component_subassemblies_branch_assigned_screened_all_seeded |
| ERPBench 完整业务 | [2259 · CO2 Total Flooding System](../bench/tasks/2259_hard_25_shared_component_subassemblies_branch_assigned_screened_all_seeded/task.toml) | 25_shared_component_subassemblies_branch_assigned_screened_all_seeded |
| ERPBench 完整业务 | [2260 · Foam Deluge System Package](../bench/tasks/2260_hard_25_shared_component_subassemblies_branch_assigned_screened_all_seeded/task.toml) | 25_shared_component_subassemblies_branch_assigned_screened_all_seeded |
| ERPBench 完整业务 | [2261 · Fire Alarm Control Panel FACP](../bench/tasks/2261_hard_25_shared_component_subassemblies_branch_assigned_screened_all_seeded/task.toml) | 25_shared_component_subassemblies_branch_assigned_screened_all_seeded |
| 真实模型节点 | [M02](../experiments/agent_regression/manual_cases.json) | Read surface omitted BOM and stock facts |
| 真实模型节点 | [M06](../experiments/agent_regression/manual_cases.json) | Replacement copied an expired delivery date |
| 真实模型节点 | [M08](../experiments/agent_regression/manual_cases.json) | Partial allocation incorrectly marked uncertain |
| 真实模型节点 | [MC01](../experiments/agent_regression/manufacturing_consumption.json) | Model skipped the SOP consumption step; runtime _bom_consumption counts quantity even when picked=false, unlike Odoo _get_consumption_issues. Completion returned a warning wizard; post-state check paused safely. |
| 真实模型节点 | [MD01](../experiments/agent_regression/manufacturing_dates.json) | Missing planned/actual transition semantics; earliest historical introduction not proven |
| 真实模型节点 | [MD02](../experiments/agent_regression/manufacturing_dates.json) | Missing planned/actual transition semantics; earliest historical introduction not proven |
| 真实模型节点 | [MD03](../experiments/agent_regression/manufacturing_dates.json) | Missing planned/actual transition semantics; earliest historical introduction not proven |
| 离线测试套件 | [test_supply_context.py](../tests/test_supply_context.py) | 见入口中的断言 |
| 隔离完整业务 | [E01 · 部分交付与收货](../experiments/enterprise_validation/README.md) | 最终状态＋安全约束＋效率；历史通过不代表当前通过 |
| 隔离完整业务 | [E02 · 多级制造与采购补料](../experiments/enterprise_validation/README.md) | 最终状态＋安全约束＋效率；历史通过不代表当前通过 |

## 历史参考与基准适配

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| 历史参考测试 | [test_access_helpers.py](../bench/reference/mcp/tests/test_access_helpers.py) | 见入口中的断言 |
| 历史参考测试 | [test_accounting_tools.py](../bench/reference/mcp/tests/test_accounting_tools.py) | 见入口中的断言 |
| 历史参考测试 | [test_agent_tools.py](../bench/reference/mcp/tests/test_agent_tools.py) | 见入口中的断言 |
| 历史参考测试 | [test_attachment.py](../bench/reference/mcp/tests/test_attachment.py) | 见入口中的断言 |
| 历史参考测试 | [test_attachment_upload.py](../bench/reference/mcp/tests/test_attachment_upload.py) | 见入口中的断言 |
| 历史参考测试 | [test_audit.py](../bench/reference/mcp/tests/test_audit.py) | 见入口中的断言 |
| 历史参考测试 | [test_auth.py](../bench/reference/mcp/tests/test_auth.py) | 见入口中的断言 |
| 历史参考测试 | [test_batch_write.py](../bench/reference/mcp/tests/test_batch_write.py) | 见入口中的断言 |
| 历史参考测试 | [test_cli.py](../bench/reference/mcp/tests/test_cli.py) | 见入口中的断言 |
| 历史参考测试 | [test_config.py](../bench/reference/mcp/tests/test_config.py) | 见入口中的断言 |
| 历史参考测试 | [test_cross_instance.py](../bench/reference/mcp/tests/test_cross_instance.py) | 见入口中的断言 |
| 历史参考测试 | [test_data_quality.py](../bench/reference/mcp/tests/test_data_quality.py) | 见入口中的断言 |
| 历史参考测试 | [test_diagnostics.py](../bench/reference/mcp/tests/test_diagnostics.py) | 见入口中的断言 |
| 历史参考测试 | [test_field_acl_enforcement.py](../bench/reference/mcp/tests/test_field_acl_enforcement.py) | 见入口中的断言 |
| 历史参考测试 | [test_field_policy.py](../bench/reference/mcp/tests/test_field_policy.py) | 见入口中的断言 |
| 历史参考测试 | [test_knowledge_index.py](../bench/reference/mcp/tests/test_knowledge_index.py) | 见入口中的断言 |
| 历史参考测试 | [test_migration_workbench.py](../bench/reference/mcp/tests/test_migration_workbench.py) | 见入口中的断言 |
| 历史参考测试 | [test_odoo_client.py](../bench/reference/mcp/tests/test_odoo_client.py) | 见入口中的断言 |
| 历史参考测试 | [test_plugins.py](../bench/reference/mcp/tests/test_plugins.py) | 见入口中的断言 |
| 历史参考测试 | [test_rate_limit.py](../bench/reference/mcp/tests/test_rate_limit.py) | 见入口中的断言 |
| 历史参考测试 | [test_reliability.py](../bench/reference/mcp/tests/test_reliability.py) | 见入口中的断言 |
| 历史参考测试 | [test_schema_cache.py](../bench/reference/mcp/tests/test_schema_cache.py) | 见入口中的断言 |
| 历史参考测试 | [test_schemas.py](../bench/reference/mcp/tests/test_schemas.py) | 见入口中的断言 |
| 历史参考测试 | [test_server.py](../bench/reference/mcp/tests/test_server.py) | 见入口中的断言 |
| 历史参考测试 | [test_setup_wizard.py](../bench/reference/mcp/tests/test_setup_wizard.py) | 见入口中的断言 |
| 历史参考测试 | [test_task_queue.py](../bench/reference/mcp/tests/test_task_queue.py) | 见入口中的断言 |
| 历史参考测试 | [test_tool_helpers.py](../bench/reference/mcp/tests/test_tool_helpers.py) | 见入口中的断言 |
| 历史参考测试 | [test_tools_async_knowledge_accounting.py](../bench/reference/mcp/tests/test_tools_async_knowledge_accounting.py) | 见入口中的断言 |
| 历史参考测试 | [test_workflow_prompts.py](../bench/reference/mcp/tests/test_workflow_prompts.py) | 见入口中的断言 |
| 历史参考测试 | [test_write_policy.py](../bench/reference/mcp/tests/test_write_policy.py) | 见入口中的断言 |
| 基准适配测试 | [test_baseline_fixes.py](../bench/reference/tests/test_baseline_fixes.py) | 见入口中的断言 |
| 基准适配测试 | [test_capabilities.py](../bench/reference/tests/test_capabilities.py) | 见入口中的断言 |
| 基准适配测试 | [test_knowledge.py](../bench/reference/tests/test_knowledge.py) | 见入口中的断言 |
| 基准适配测试 | [test_native_reads.py](../bench/reference/tests/test_native_reads.py) | 见入口中的断言 |

## 工具契约、SOP 与能力选择

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| 实验入口（含准备/评估；并非全部付费） | [build_cases.py](../experiments/tool_routing/build_cases.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [collect_dataset.py](../experiments/tool_routing/collect_dataset.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [competitive_probe.py](../experiments/tool_routing/competitive_probe.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [decision_dataset.py](../experiments/tool_routing/decision_dataset.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [final_check.py](../experiments/tool_routing/final_check.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [format_probe.py](../experiments/tool_routing/format_probe.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [history_inventory.py](../experiments/tool_routing/history_inventory.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [host_routing.py](../experiments/agent_regression/host_routing.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [host_state_dataset.py](../experiments/tool_routing/host_state_dataset.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [input_probe.py](../experiments/tool_routing/input_probe.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [intent_probe.py](../experiments/tool_routing/intent_probe.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [laya_additive_replay.py](../experiments/tool_routing/laya_additive_replay.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [laya_context_replay.py](../experiments/tool_routing/laya_context_replay.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [laya_prefix_regression.py](../experiments/tool_routing/laya_prefix_regression.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [laya_probe.py](../experiments/tool_routing/laya_probe.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [live_trial.py](../experiments/tool_routing/live_trial.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [model_comparison.py](../experiments/tool_routing/model_comparison.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [openjev_regression.py](../experiments/tool_routing/openjev_regression.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [openjev_worker.py](../experiments/tool_routing/openjev_worker.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [phase_dataset.py](../experiments/tool_routing/phase_dataset.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [publication_probe.py](../experiments/tool_routing/publication_probe.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_decision_readout.py](../experiments/tool_routing/q4_decision_readout.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_dedup.py](../experiments/tool_routing/q4_dedup.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_failure_replay.py](../experiments/tool_routing/q4_failure_replay.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_input_report.py](../experiments/tool_routing/q4_input_report.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_inputs.py](../experiments/tool_routing/q4_inputs.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_prompt_metrics.py](../experiments/tool_routing/q4_prompt_metrics.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_standard_context.py](../experiments/tool_routing/q4_standard_context.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_thinking.py](../experiments/tool_routing/q4_thinking.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_thinking_schema.py](../experiments/tool_routing/q4_thinking_schema.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [q4_thinking_surface.py](../experiments/tool_routing/q4_thinking_surface.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [recovery_check.py](../experiments/tool_routing/recovery_check.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [release_bundle.py](../experiments/tool_routing/release_bundle.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [replay_decider.py](../experiments/tool_routing/replay_decider.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [reviewed_dataset.py](../experiments/tool_routing/reviewed_dataset.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [router.py](../experiments/tool_routing/router.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [routing_recovery.py](../experiments/agent_regression/routing_recovery.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [run_module_slice.py](../experiments/dynamic-tools-budgeted/run_module_slice.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [semantic_probe.py](../experiments/tool_routing/semantic_probe.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [train_decider.py](../experiments/tool_routing/train_decider.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [train_head.py](../experiments/tool_routing/train_head.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [train_joint.py](../experiments/tool_routing/train_joint.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [train_reviewed.py](../experiments/tool_routing/train_reviewed.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [training_probe.py](../experiments/tool_routing/training_probe.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [verify_prefix.py](../experiments/tool_routing/verify_prefix.py) | 见入口中的断言 |
| 真实模型节点 | [A01 · first_unregistered_tool_intent](../experiments/agent_regression/cases.py) | tool_contract |
| 真实模型节点 | [A02 · first_method_as_field_write](../experiments/agent_regression/cases.py) | tool_contract |
| 真实模型节点 | [A03 · correct_action_after_metadata_access_failure](../experiments/agent_regression/cases.py) | tool_contract |
| 真实模型节点 | [A04 · first request consuming the runtime endorsement, before the erroneous execute_method](../experiments/agent_regression/cases.py) | tool_contract |
| 真实模型节点 | [BC2010](../experiments/agent_regression/bench_recovery_cases.json) | normal_control |
| 真实模型节点 | [BT2010-01](../experiments/agent_regression/bench_recovery_cases.json) | sop_inputs |
| 真实模型节点 | [BT2010-02](../experiments/agent_regression/bench_recovery_cases.json) | sop_inputs |
| 真实模型节点 | [BT2010-03](../experiments/agent_regression/bench_recovery_cases.json) | sop_inputs |
| 真实模型节点 | [BT2010-04](../experiments/agent_regression/bench_recovery_cases.json) | empty_domain |
| 真实模型节点 | [BT2010-05](../experiments/agent_regression/bench_recovery_cases.json) | empty_domain |
| 真实模型节点 | [BT2010-06](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2010-07](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2010-08](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2010-09](../experiments/agent_regression/bench_recovery_cases.json) | singleton |
| 真实模型节点 | [BT2030-01](../experiments/agent_regression/bench_recovery_cases.json) | sop_inputs |
| 真实模型节点 | [BT2030-02](../experiments/agent_regression/bench_recovery_cases.json) | sop_inputs |
| 真实模型节点 | [BT2030-03](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2030-04](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2030-05](../experiments/agent_regression/bench_recovery_cases.json) | empty_domain |
| 真实模型节点 | [BT2030-06](../experiments/agent_regression/bench_recovery_cases.json) | observation_path |
| 真实模型节点 | [BT2030-07](../experiments/agent_regression/bench_recovery_cases.json) | observation_path |
| 真实模型节点 | [BT2030-08](../experiments/agent_regression/bench_recovery_cases.json) | observation_path |
| 真实模型节点 | [BT2030-09](../experiments/agent_regression/bench_recovery_cases.json) | schema_argument |
| 真实模型节点 | [BT2030-10](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2030-11](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2030-12](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2066-01](../experiments/agent_regression/bench_recovery_cases.json) | sop_inputs |
| 真实模型节点 | [BT2066-02](../experiments/agent_regression/bench_recovery_cases.json) | sop_inputs |
| 真实模型节点 | [BT2066-03](../experiments/agent_regression/bench_recovery_cases.json) | sop_inputs |
| 真实模型节点 | [BT2066-04](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2066-05](../experiments/agent_regression/bench_recovery_cases.json) | empty_domain |
| 真实模型节点 | [BT2066-06](../experiments/agent_regression/bench_recovery_cases.json) | empty_domain |
| 真实模型节点 | [BT2066-07](../experiments/agent_regression/bench_recovery_cases.json) | empty_domain |
| 真实模型节点 | [BT2066-08](../experiments/agent_regression/bench_recovery_cases.json) | empty_domain |
| 真实模型节点 | [BT2066-09](../experiments/agent_regression/bench_recovery_cases.json) | schema_argument |
| 真实模型节点 | [BT2066-10](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2066-11](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2066-12](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2066-13](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2279-01](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2279-02](../experiments/agent_regression/bench_recovery_cases.json) | empty_domain |
| 真实模型节点 | [BT2279-03](../experiments/agent_regression/bench_recovery_cases.json) | empty_domain |
| 真实模型节点 | [BT2279-04](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2279-05](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2279-06](../experiments/agent_regression/bench_recovery_cases.json) | unknown_field |
| 真实模型节点 | [BT2279-07](../experiments/agent_regression/bench_recovery_cases.json) | diagnostic_scope |
| 真实模型节点 | [LA01](../experiments/agent_regression/laya_additive_observations.json) | 见冻结清单 |
| 真实模型节点 | [LA02](../experiments/agent_regression/laya_additive_observations.json) | 见冻结清单 |
| 真实模型节点 | [LA03](../experiments/agent_regression/laya_additive_observations.json) | 见冻结清单 |
| 真实模型节点 | [LA04](../experiments/agent_regression/laya_additive_observations.json) | 见冻结清单 |
| 真实模型节点 | [LA05](../experiments/agent_regression/laya_additive_observations.json) | 见冻结清单 |
| 真实模型节点 | [LA06](../experiments/agent_regression/laya_additive_observations.json) | 见冻结清单 |
| 真实模型节点 | [LA07](../experiments/agent_regression/laya_additive_observations.json) | 见冻结清单 |
| 真实模型节点 | [R01 · 调用未发布工具](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R02 · 启用状态冲突](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R03 · 关系删除参数](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R04 · 历史 active 状态残留](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R05 · 能力撤下后多一轮选择](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R06 · 漏预加载](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R07 · 确认操作名错误](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R08 · 退货明细缺产品](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R09 · 旧库存字段](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R10 · 重复 preview](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R11 · 制造旧字段](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R12 · 猜子记录 ID](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R13 · 空查询条件](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R14 · 未设投产数量](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R15 · 提前完工](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R16 · 不存在的工具](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R17 · 缺 SOP 输入](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 真实模型节点 | [R18 · 财务旧字段](../experiments/agent_regression/README.md) | 详见原节点因果边界；不把首个报错当根因 |
| 离线测试套件 | [test_bench_tool_regression.py](../tests/test_bench_tool_regression.py) | 见入口中的断言 |
| 离线测试套件 | [test_capability_routing.py](../tests/test_capability_routing.py) | 见入口中的断言 |
| 离线测试套件 | [test_dynamic_tools.py](../tests/test_dynamic_tools.py) | 见入口中的断言 |
| 离线测试套件 | [test_host_routing_state.py](../tests/test_host_routing_state.py) | 见入口中的断言 |
| 离线测试套件 | [test_laya_context_replay.py](../tests/test_laya_context_replay.py) | 见入口中的断言 |
| 离线测试套件 | [test_laya_integration.py](../tests/test_laya_integration.py) | 见入口中的断言 |
| 离线测试套件 | [test_openjev_worker.py](../tests/test_openjev_worker.py) | 见入口中的断言 |
| 离线测试套件 | [test_q4_routing_inputs.py](../tests/test_q4_routing_inputs.py) | 见入口中的断言 |
| 离线测试套件 | [test_routing_context.py](../tests/test_routing_context.py) | 见入口中的断言 |
| 离线测试套件 | [test_selector_service.py](../tests/test_selector_service.py) | 见入口中的断言 |
| 离线测试套件 | [test_sops.py](../tests/test_sops.py) | 见入口中的断言 |
| 离线测试套件 | [test_tool_history.py](../tests/runtime/test_tool_history.py) | 见入口中的断言 |
| 离线测试套件 | [test_tool_retrieval.py](../tests/test_tool_retrieval.py) | 见入口中的断言 |
| 选择器数据集（训练/留出须看原清单） | [history_cases.jsonl](../tests/fixtures/capability_routing/history_cases.jsonl) | 见入口中的断言 |
| 选择器数据集（训练/留出须看原清单） | [history_inventory.json](../tests/fixtures/capability_routing/history_inventory.json) | 见入口中的断言 |
| 选择器数据集（训练/留出须看原清单） | [laya_prefix_failures.json](../tests/fixtures/capability_routing/laya_prefix_failures.json) | 见入口中的断言 |
| 选择器数据集（训练/留出须看原清单） | [laya_v7_live_observations.json](../tests/fixtures/capability_routing/laya_v7_live_observations.json) | 见入口中的断言 |
| 选择器数据集（训练/留出须看原清单） | [openjev_observed_failures.json](../tests/fixtures/capability_routing/openjev_observed_failures.json) | 见入口中的断言 |
| 选择器数据集（训练/留出须看原清单） | [ordered_training_expansion.json](../tests/fixtures/capability_routing/ordered_training_expansion.json) | 见入口中的断言 |
| 选择器数据集（训练/留出须看原清单） | [phase_holdout.json](../tests/fixtures/capability_routing/phase_holdout.json) | 见入口中的断言 |
| 选择器数据集（训练/留出须看原清单） | [phase_training.json](../tests/fixtures/capability_routing/phase_training.json) | 见入口中的断言 |
| 选择器数据集（训练/留出须看原清单） | [phase_training_expansion.json](../tests/fixtures/capability_routing/phase_training_expansion.json) | 见入口中的断言 |
| 选择器数据集（训练/留出须看原清单） | [reviewed_expansion.json](../tests/fixtures/capability_routing/reviewed_expansion.json) | 见入口中的断言 |
| 选择器数据集（训练/留出须看原清单） | [reviewed_history_20260925.json](../tests/fixtures/capability_routing/reviewed_history_20260925.json) | 见入口中的断言 |
| 选择器数据集（训练/留出须看原清单） | [reviewed_holdout.json](../tests/fixtures/capability_routing/reviewed_holdout.json) | 见入口中的断言 |
| 选择器数据集（训练/留出须看原清单） | [reviewed_training.json](../tests/fixtures/capability_routing/reviewed_training.json) | 见入口中的断言 |

## 开票、邮件与文件

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| ERPBench 完整业务 | [2008 · 12U Wall-Mount Enclosure](../bench/tasks/2008_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2009 · Co-Location Cage Rack 42U](../bench/tasks/2009_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2010 · 48U High-Density Cabinet](../bench/tasks/2010_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2011 · Micro Edge 8U Pod Enclosure](../bench/tasks/2011_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2012 · 12U Wall-Mount Enclosure](../bench/tasks/2012_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2013 · Micro Edge 8U Pod Enclosure](../bench/tasks/2013_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2014 · 48U High-Density Cabinet](../bench/tasks/2014_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2015 · Co-Location Cage Rack 42U](../bench/tasks/2015_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2016 · Accumulation Zone Conveyor](../bench/tasks/2016_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2017 · Telescoping Loader Conveyor](../bench/tasks/2017_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2018 · Modular Conveyor Section](../bench/tasks/2018_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2019 · Merge-Divert Sorting Module](../bench/tasks/2019_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2020 · Accumulation Zone Conveyor](../bench/tasks/2020_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2021 · Heavy-Duty Pallet Conveyor Module](../bench/tasks/2021_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2022 · Heavy-Duty Pallet Conveyor Module](../bench/tasks/2022_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2023 · Modular Conveyor Section](../bench/tasks/2023_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2024 · L-Shaped Corner Workstation](../bench/tasks/2024_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2025 · Compact Hot-Desk Station](../bench/tasks/2025_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2026 · Compact Hot-Desk Station](../bench/tasks/2026_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2027 · Executive Sit-Stand Desk](../bench/tasks/2027_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2028 · Compact Hot-Desk Station](../bench/tasks/2028_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2029 · Mobile Folding Workstation](../bench/tasks/2029_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2030 · ADA-Compliant Adjustable Desk](../bench/tasks/2030_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2031 · Reception Counter Desk](../bench/tasks/2031_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2053 · Split System Heat Pump 5-Ton](../bench/tasks/2053_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2054 · Rooftop Package Unit 10-Ton](../bench/tasks/2054_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2055 · Split System Heat Pump 5-Ton](../bench/tasks/2055_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2056 · Packaged Terminal Air Conditioner](../bench/tasks/2056_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2057 · Packaged Terminal Air Conditioner](../bench/tasks/2057_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2058 · Water-Source Heat Pump Console](../bench/tasks/2058_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2059 · Water-Source Heat Pump Console](../bench/tasks/2059_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2060 · Water-Source Heat Pump Console](../bench/tasks/2060_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2061 · Packaged Terminal Air Conditioner](../bench/tasks/2061_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2062 · Energy Recovery Ventilator Unit](../bench/tasks/2062_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2063 · Ductless Mini-Split System](../bench/tasks/2063_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2217 · Conveyor Tunnel Guard System](../bench/tasks/2217_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2218 · Conveyor Tunnel Guard System](../bench/tasks/2218_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2219 · Machine Guard Perimeter Fence](../bench/tasks/2219_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2220 · Welding Screen Partition Set](../bench/tasks/2220_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2221 · Chemical Storage Safety Cabinet](../bench/tasks/2221_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2222 · Acoustic Isolation Booth](../bench/tasks/2222_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2223 · Laser Safety Curtain System](../bench/tasks/2223_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2224 · Dust Containment Enclosure](../bench/tasks/2224_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2225 · Welding Screen Partition Set](../bench/tasks/2225_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2226 · Dust Containment Enclosure](../bench/tasks/2226_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2227 · Machine Guard Perimeter Fence](../bench/tasks/2227_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2228 · Acoustic Isolation Booth](../bench/tasks/2228_medium_22_single_bom_split_by_capacity_invoicing/task.toml) | 22_single_bom_split_by_capacity_invoicing |
| ERPBench 完整业务 | [2240 · Car Frame Platform 2500kg](../bench/tasks/2240_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2241 · Car Frame Platform 4000kg](../bench/tasks/2241_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2242 · Landing Door Assembly Center-Open](../bench/tasks/2242_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2243 · Car Frame Platform 4000kg](../bench/tasks/2243_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2244 · Gearless Traction Controller 10-Stop](../bench/tasks/2244_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2245 · Landing Door Assembly Single-Speed](../bench/tasks/2245_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2246 · Elevator Cab Interior Module](../bench/tasks/2246_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2247 · Car Frame Platform 2500kg](../bench/tasks/2247_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2248 · Elevator Cab Interior Module](../bench/tasks/2248_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2249 · Landing Door Assembly Single-Speed](../bench/tasks/2249_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| ERPBench 完整业务 | [2250 · Elevator Cab Interior Module](../bench/tasks/2250_hard_24_single_subassembly_shared_overflow_capacity_screened_invoicing/task.toml) | 24_single_subassembly_shared_overflow_capacity_screened_invoicing |
| 实验入口（含准备/评估；并非全部付费） | [invoice_demo.py](../experiments/enterprise_validation/invoice_demo.py) | 见入口中的断言 |
| 桌面检查 | [document-presentation-check.mjs](../desktop/scripts/document-presentation-check.mjs) | 见入口中的断言 |
| 真实模型节点 | [B02](../experiments/agent_regression/hitl_revision.json) | 见冻结清单 |
| 真实模型节点 | [B03](../experiments/agent_regression/hitl_revision.json) | 见冻结清单 |
| 真实模型节点 | [HITL01](../experiments/agent_regression/hitl_revision.json) | Approval revision routed to proposal worker; desktop resumed original unchanged instruction; runtime fixed mail copy. |
| 真实模型节点 | [HITL02](../experiments/agent_regression/hitl_revision.json) | Approval revision routed to proposal worker; desktop resumed original unchanged instruction; runtime fixed mail copy. |
| 真实模型节点 | [M12](../experiments/agent_regression/manual_cases.json) | Existing successful fixed-deposit control |
| 真实模型节点 | [M16](../experiments/agent_regression/manual_followups.json) | Final reply labels amount_total=791 as untaxed; source request had five net-priced lines totalling700 but the order readback supplied only amount_total. Why the model chose the label is unproven. |
| 离线测试套件 | [test_chatter_verification.py](../tests/test_chatter_verification.py) | 见入口中的断言 |
| 离线测试套件 | [test_document_export.py](../tests/test_document_export.py) | 见入口中的断言 |
| 离线测试套件 | [test_invoice_eligibility.py](../tests/test_invoice_eligibility.py) | 见入口中的断言 |
| 离线测试套件 | [test_invoice_mail.py](../tests/test_invoice_mail.py) | 见入口中的断言 |
| 离线测试套件 | [test_material_extract.py](../tests/test_material_extract.py) | 见入口中的断言 |
| 离线测试套件 | [test_workbench_materials.py](../tests/test_workbench_materials.py) | 见入口中的断言 |
| 隔离完整业务 | [E05 · 客户退货退款](../experiments/enterprise_validation/README.md) | 最终状态＋安全约束＋效率；历史通过不代表当前通过 |
| 隔离完整业务 | [E06 · 供应商退货退款](../experiments/enterprise_validation/README.md) | 最终状态＋安全约束＋效率；历史通过不代表当前通过 |
| 隔离完整业务 | [S01499 · 查单/确认/开票/独立发送/聊天回读](../experiments/agent_regression/RESULTS.md) | 最终状态＋安全约束＋效率；历史通过不代表当前通过 |

## 授权、证据与写入安全

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| 实验入口（含准备/评估；并非全部付费） | [access.py](../experiments/enterprise_validation/access.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [access_check.py](../experiments/enterprise_validation/access_check.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [hitl_revision.py](../experiments/agent_regression/hitl_revision.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [staff.py](../experiments/enterprise_validation/staff.py) | 见入口中的断言 |
| 桌面检查 | [approval-presentation-check.mjs](../desktop/scripts/approval-presentation-check.mjs) | 见入口中的断言 |
| 真实模型节点 | [B01 · first_mail_detour_after_posted_and_pdf](../experiments/agent_regression/cases.py) | phase_boundary |
| 真实模型节点 | [B02](../experiments/agent_regression/hitl_revision.json) | 见冻结清单 |
| 真实模型节点 | [B02 · authorized_delivery_proposal](../experiments/agent_regression/cases.py) | phase_boundary |
| 真实模型节点 | [B03](../experiments/agent_regression/hitl_revision.json) | 见冻结清单 |
| 真实模型节点 | [B03 · correct_final_status_after_smtp_receipt](../experiments/agent_regression/cases.py) | phase_boundary |
| 真实模型节点 | [BP2010](../experiments/agent_regression/bench_recovery_cases.json) | purchase_allocation |
| 真实模型节点 | [BP2030](../experiments/agent_regression/bench_recovery_cases.json) | purchase_allocation |
| 真实模型节点 | [D01 · repaired_internal_company_contact_semantics](../experiments/agent_regression/cases.py) | evidence_semantics |
| 真实模型节点 | [D02 · repaired_ambiguous_company_customer_scope](../experiments/agent_regression/cases.py) | evidence_semantics |
| 真实模型节点 | [D03 · amount_conflict_before_confirmation](../experiments/agent_regression/cases.py) | evidence_semantics |
| 真实模型节点 | [D04 · first model request consuming the unavailable status result, before the false only-proposal answer](../experiments/agent_regression/cases.py) | evidence_semantics |
| 真实模型节点 | [HITL01](../experiments/agent_regression/hitl_revision.json) | Approval revision routed to proposal worker; desktop resumed original unchanged instruction; runtime fixed mail copy. |
| 真实模型节点 | [HITL02](../experiments/agent_regression/hitl_revision.json) | Approval revision routed to proposal worker; desktop resumed original unchanged instruction; runtime fixed mail copy. |
| 真实模型节点 | [M03](../experiments/agent_regression/manual_cases.json) | Reviewed purchase cancellation method absent |
| 真实模型节点 | [M07](../experiments/agent_regression/manual_cases.json) | New verified manufacturing document excluded from task targets |
| 真实模型节点 | [M14](../experiments/agent_regression/manual_followups.json) | Order confirmation approved total and identity without presenting its product quantities |
| 离线测试套件 | [test_actions.py](../tests/test_actions.py) | 见入口中的断言 |
| 离线测试套件 | [test_approval_resume_scope.py](../tests/test_approval_resume_scope.py) | 见入口中的断言 |
| 离线测试套件 | [test_business_facts.py](../tests/test_business_facts.py) | 见入口中的断言 |
| 离线测试套件 | [test_daily_business_integrity.py](../tests/test_daily_business_integrity.py) | 见入口中的断言 |
| 离线测试套件 | [test_enterprise_actions.py](../tests/test_enterprise_actions.py) | 见入口中的断言 |
| 离线测试套件 | [test_evidence_display.py](../tests/test_evidence_display.py) | 见入口中的断言 |
| 离线测试套件 | [test_minimal_write_guards.py](../tests/test_minimal_write_guards.py) | 见入口中的断言 |
| 离线测试套件 | [test_relation_receipts.py](../tests/test_relation_receipts.py) | 见入口中的断言 |
| 离线测试套件 | [test_stage_contract.py](../tests/test_stage_contract.py) | 见入口中的断言 |
| 离线测试套件 | [test_task_evidence.py](../tests/test_task_evidence.py) | 见入口中的断言 |
| 离线测试套件 | [test_typed_action_failures.py](../tests/test_typed_action_failures.py) | 见入口中的断言 |

## 搜索与知识检索

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| 实验入口（含准备/评估；并非全部付费） | [retrieval_check.py](../experiments/enterprise_validation/retrieval_check.py) | 见入口中的断言 |
| 离线测试套件 | [test_enterprise_knowledge.py](../tests/test_enterprise_knowledge.py) | 见入口中的断言 |
| 离线测试套件 | [test_tool_retrieval.py](../tests/test_tool_retrieval.py) | 见入口中的断言 |

## 桌面交互与打包

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| 实验入口（含准备/评估；并非全部付费） | [analyze_token_cost.py](../experiments/desktop_workbench/analyze_token_cost.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [scripted_model.py](../experiments/desktop_workbench/scripted_model.py) | 见入口中的断言 |
| 实验检查 | [check.cjs](../experiments/desktop_workbench/prototype/check.cjs) | 见入口中的断言 |
| 桌面检查 | [approval-presentation-check.mjs](../desktop/scripts/approval-presentation-check.mjs) | 见入口中的断言 |
| 桌面检查 | [document-presentation-check.mjs](../desktop/scripts/document-presentation-check.mjs) | 见入口中的断言 |
| 桌面检查 | [execution-v2-check.mjs](../desktop/scripts/execution-v2-check.mjs) | 见入口中的断言 |
| 桌面检查 | [ipc-check.mjs](../desktop/scripts/ipc-check.mjs) | 见入口中的断言 |
| 桌面检查 | [package-check.mjs](../desktop/scripts/package-check.mjs) | 见入口中的断言 |
| 桌面检查 | [renderer-check.mjs](../desktop/scripts/renderer-check.mjs) | 见入口中的断言 |
| 桌面检查 | [self-check.mjs](../desktop/scripts/self-check.mjs) | 见入口中的断言 |
| 桌面检查 | [trace-check.mjs](../desktop/scripts/trace-check.mjs) | 见入口中的断言 |
| 桌面检查 | [trace-model-check.mjs](../desktop/scripts/trace-model-check.mjs) | 见入口中的断言 |
| 桌面检查 | [trace-v2-check.mjs](../desktop/scripts/trace-v2-check.mjs) | 见入口中的断言 |
| 真实模型节点 | [M10](../experiments/agent_regression/manual_cases.json) | Final reply exposed implementation instead of business result |
| 真实模型节点 | [M15](../experiments/agent_regression/manual_followups.json) | Model-facing Chinese requirement did not reliably control public progress language |
| 离线测试套件 | [test_enterprise_workbench.py](../tests/test_enterprise_workbench.py) | 见入口中的断言 |
| 离线测试套件 | [test_layout.py](../tests/test_layout.py) | 见入口中的断言 |
| 离线测试套件 | [test_runtime_migration.py](../tests/test_runtime_migration.py) | 见入口中的断言 |
| 离线测试套件 | [test_workbench_conversation.py](../tests/test_workbench_conversation.py) | 见入口中的断言 |
| 离线测试套件 | [test_workbench_host.py](../tests/test_workbench_host.py) | 见入口中的断言 |
| 离线测试套件 | [test_workbench_materials.py](../tests/test_workbench_materials.py) | 见入口中的断言 |
| 离线测试套件 | [test_workbench_sale_view.py](../tests/test_workbench_sale_view.py) | 见入口中的断言 |

## 模型协议与传输

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| 离线测试套件 | [test_cross_provider_history.py](../tests/runtime/test_cross_provider_history.py) | 见入口中的断言 |
| 离线测试套件 | [test_http.py](../tests/runtime/test_http.py) | 见入口中的断言 |
| 离线测试套件 | [test_multimodal_provider_payloads.py](../tests/runtime/test_multimodal_provider_payloads.py) | 见入口中的断言 |
| 离线测试套件 | [test_pi_ai.py](../tests/runtime/test_pi_ai.py) | 见入口中的断言 |
| 离线测试套件 | [test_provider_catalog.py](../tests/runtime/test_provider_catalog.py) | 见入口中的断言 |
| 离线测试套件 | [test_provider_config.py](../tests/runtime/test_provider_config.py) | 见入口中的断言 |
| 离线测试套件 | [test_provider_runtime.py](../tests/runtime/test_provider_runtime.py) | 见入口中的断言 |
| 离线测试套件 | [test_stream_events.py](../tests/test_stream_events.py) | 见入口中的断言 |
| 离线测试套件 | [test_transport_recovery.py](../tests/test_transport_recovery.py) | 见入口中的断言 |

## 运行循环与实验支撑

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| 实验入口（含准备/评估；并非全部付费） | [freeze.py](../experiments/agent_regression/freeze.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [incidents.py](../experiments/agent_regression/incidents.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [manual_business.py](../experiments/agent_regression/manual_business.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [manual_business_live.py](../experiments/agent_regression/manual_business_live.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [prepare.py](../experiments/agent_regression/prepare.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [provenance.py](../experiments/agent_regression/provenance.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [runner.py](../experiments/agent_regression/runner.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [start.py](../experiments/demo_odoo/start.py) | 见入口中的断言 |
| 离线测试套件 | [test_agent_harness.py](../tests/runtime/test_agent_harness.py) | 见入口中的断言 |
| 离线测试套件 | [test_agent_loop.py](../tests/runtime/test_agent_loop.py) | 见入口中的断言 |
| 离线测试套件 | [test_agent_regression.py](../tests/test_agent_regression.py) | 见入口中的断言 |
| 离线测试套件 | [test_agent_types.py](../tests/runtime/test_agent_types.py) | 见入口中的断言 |
| 离线测试套件 | [test_capability_failures.py](../tests/test_capability_failures.py) | 见入口中的断言 |
| 离线测试套件 | [test_connection_awareness.py](../tests/test_connection_awareness.py) | 见入口中的断言 |
| 离线测试套件 | [test_message_transform.py](../tests/runtime/test_message_transform.py) | 见入口中的断言 |
| 离线测试套件 | [test_pi_event_protocol.py](../tests/runtime/test_pi_event_protocol.py) | 见入口中的断言 |
| 离线测试套件 | [test_pi_loop_parity.py](../tests/runtime/test_pi_loop_parity.py) | 见入口中的断言 |
| 离线测试套件 | [test_resources.py](../tests/runtime/test_resources.py) | 见入口中的断言 |

## 采购与业务状态

| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |
|---|---|---|
| ERPBench 完整业务 | [2000 · Sound Privacy Panels](../bench/tasks/2000_easy_01_buy_only_baseline/task.toml) | 01_buy_only_baseline |
| ERPBench 完整业务 | [2001 · Door Seal Acoustic Kit](../bench/tasks/2001_easy_01_buy_only_baseline/task.toml) | 01_buy_only_baseline |
| ERPBench 完整业务 | [2002 · Open-Plan Noise Barrier Wall](../bench/tasks/2002_easy_01_buy_only_baseline/task.toml) | 01_buy_only_baseline |
| ERPBench 完整业务 | [2003 · Under-Desk Sound Shield](../bench/tasks/2003_easy_01_buy_only_baseline/task.toml) | 01_buy_only_baseline |
| ERPBench 完整业务 | [2004 · Curved Diffuser Panel](../bench/tasks/2004_easy_01_buy_only_baseline/task.toml) | 01_buy_only_baseline |
| ERPBench 完整业务 | [2005 · Sound Privacy Panels](../bench/tasks/2005_easy_01_buy_only_baseline/task.toml) | 01_buy_only_baseline |
| ERPBench 完整业务 | [2006 · Sound Privacy Panels](../bench/tasks/2006_easy_01_buy_only_baseline/task.toml) | 01_buy_only_baseline |
| ERPBench 完整业务 | [2007 · Sound Privacy Panels](../bench/tasks/2007_easy_01_buy_only_baseline/task.toml) | 01_buy_only_baseline |
| ERPBench 完整业务 | [2008 · 12U Wall-Mount Enclosure](../bench/tasks/2008_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2009 · Co-Location Cage Rack 42U](../bench/tasks/2009_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2010 · 48U High-Density Cabinet](../bench/tasks/2010_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2011 · Micro Edge 8U Pod Enclosure](../bench/tasks/2011_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2012 · 12U Wall-Mount Enclosure](../bench/tasks/2012_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2013 · Micro Edge 8U Pod Enclosure](../bench/tasks/2013_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2014 · 48U High-Density Cabinet](../bench/tasks/2014_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2015 · Co-Location Cage Rack 42U](../bench/tasks/2015_easy_02_buy_only_immediate_invoicing/task.toml) | 02_buy_only_immediate_invoicing |
| ERPBench 完整业务 | [2016 · Accumulation Zone Conveyor](../bench/tasks/2016_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2017 · Telescoping Loader Conveyor](../bench/tasks/2017_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2018 · Modular Conveyor Section](../bench/tasks/2018_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2019 · Merge-Divert Sorting Module](../bench/tasks/2019_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2020 · Accumulation Zone Conveyor](../bench/tasks/2020_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2021 · Heavy-Duty Pallet Conveyor Module](../bench/tasks/2021_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2022 · Heavy-Duty Pallet Conveyor Module](../bench/tasks/2022_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2023 · Modular Conveyor Section](../bench/tasks/2023_easy_03_buy_only_fixed_downpayment/task.toml) | 03_buy_only_fixed_downpayment |
| ERPBench 完整业务 | [2024 · L-Shaped Corner Workstation](../bench/tasks/2024_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2025 · Compact Hot-Desk Station](../bench/tasks/2025_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2026 · Compact Hot-Desk Station](../bench/tasks/2026_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2027 · Executive Sit-Stand Desk](../bench/tasks/2027_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2028 · Compact Hot-Desk Station](../bench/tasks/2028_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2029 · Mobile Folding Workstation](../bench/tasks/2029_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2030 · ADA-Compliant Adjustable Desk](../bench/tasks/2030_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2031 · Reception Counter Desk](../bench/tasks/2031_easy_04_buy_only_percentage_downpayment/task.toml) | 04_buy_only_percentage_downpayment |
| ERPBench 完整业务 | [2032 · Heavy-Duty Specimen Prep Bench](../bench/tasks/2032_medium_05_screened_buy_only_all_seeded/task.toml) | 05_screened_buy_only_all_seeded |
| ERPBench 完整业务 | [2033 · Cleanroom Preparation Bench](../bench/tasks/2033_medium_05_screened_buy_only_all_seeded/task.toml) | 05_screened_buy_only_all_seeded |
| ERPBench 完整业务 | [2034 · Laboratory Work Bench](../bench/tasks/2034_medium_05_screened_buy_only_all_seeded/task.toml) | 05_screened_buy_only_all_seeded |
| ERPBench 完整业务 | [2035 · Mobile Lab Cart Workstation](../bench/tasks/2035_medium_05_screened_buy_only_all_seeded/task.toml) | 05_screened_buy_only_all_seeded |
| ERPBench 完整业务 | [2036 · Laboratory Work Bench](../bench/tasks/2036_medium_05_screened_buy_only_all_seeded/task.toml) | 05_screened_buy_only_all_seeded |
| ERPBench 完整业务 | [2037 · Heavy-Duty Specimen Prep Bench](../bench/tasks/2037_medium_05_screened_buy_only_all_seeded/task.toml) | 05_screened_buy_only_all_seeded |
| ERPBench 完整业务 | [2038 · Fume Hood Bench Station](../bench/tasks/2038_medium_05_screened_buy_only_all_seeded/task.toml) | 05_screened_buy_only_all_seeded |
| ERPBench 完整业务 | [2039 · Corner Peninsula Lab Station](../bench/tasks/2039_medium_05_screened_buy_only_all_seeded/task.toml) | 05_screened_buy_only_all_seeded |
| ERPBench 完整业务 | [2040 · Wet Lab Sink Bench](../bench/tasks/2040_medium_05_screened_buy_only_all_seeded/task.toml) | 05_screened_buy_only_all_seeded |
| ERPBench 完整业务 | [2041 · Wet Lab Sink Bench](../bench/tasks/2041_medium_05_screened_buy_only_all_seeded/task.toml) | 05_screened_buy_only_all_seeded |
| ERPBench 完整业务 | [2042 · Serial Communication Bundle](../bench/tasks/2042_medium_06_screened_buy_only_mixed_seeded/task.toml) | 06_screened_buy_only_mixed_seeded |
| ERPBench 完整业务 | [2043 · Control Signal Cable Assembly](../bench/tasks/2043_medium_06_screened_buy_only_mixed_seeded/task.toml) | 06_screened_buy_only_mixed_seeded |
| ERPBench 完整业务 | [2044 · Hybrid Fiber-Copper Assembly](../bench/tasks/2044_medium_06_screened_buy_only_mixed_seeded/task.toml) | 06_screened_buy_only_mixed_seeded |
| ERPBench 完整业务 | [2045 · Multi-Conductor Trunk Cable](../bench/tasks/2045_medium_06_screened_buy_only_mixed_seeded/task.toml) | 06_screened_buy_only_mixed_seeded |
| ERPBench 完整业务 | [2046 · High-Voltage Power Cable Set](../bench/tasks/2046_medium_06_screened_buy_only_mixed_seeded/task.toml) | 06_screened_buy_only_mixed_seeded |
| ERPBench 完整业务 | [2047 · Sensor Cable Harness](../bench/tasks/2047_medium_06_screened_buy_only_mixed_seeded/task.toml) | 06_screened_buy_only_mixed_seeded |
| ERPBench 完整业务 | [2048 · Serial Communication Bundle](../bench/tasks/2048_medium_06_screened_buy_only_mixed_seeded/task.toml) | 06_screened_buy_only_mixed_seeded |
| ERPBench 完整业务 | [2049 · Multi-Conductor Trunk Cable](../bench/tasks/2049_medium_06_screened_buy_only_mixed_seeded/task.toml) | 06_screened_buy_only_mixed_seeded |
| ERPBench 完整业务 | [2050 · Sensor Cable Harness](../bench/tasks/2050_medium_06_screened_buy_only_mixed_seeded/task.toml) | 06_screened_buy_only_mixed_seeded |
| ERPBench 完整业务 | [2051 · Coaxial Trunk Harness](../bench/tasks/2051_medium_06_screened_buy_only_mixed_seeded/task.toml) | 06_screened_buy_only_mixed_seeded |
| ERPBench 完整业务 | [2052 · Coaxial Trunk Harness](../bench/tasks/2052_medium_06_screened_buy_only_mixed_seeded/task.toml) | 06_screened_buy_only_mixed_seeded |
| ERPBench 完整业务 | [2053 · Split System Heat Pump 5-Ton](../bench/tasks/2053_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2054 · Rooftop Package Unit 10-Ton](../bench/tasks/2054_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2055 · Split System Heat Pump 5-Ton](../bench/tasks/2055_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2056 · Packaged Terminal Air Conditioner](../bench/tasks/2056_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2057 · Packaged Terminal Air Conditioner](../bench/tasks/2057_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2058 · Water-Source Heat Pump Console](../bench/tasks/2058_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2059 · Water-Source Heat Pump Console](../bench/tasks/2059_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2060 · Water-Source Heat Pump Console](../bench/tasks/2060_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2061 · Packaged Terminal Air Conditioner](../bench/tasks/2061_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2062 · Energy Recovery Ventilator Unit](../bench/tasks/2062_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2063 · Ductless Mini-Split System](../bench/tasks/2063_medium_07_screened_buy_only_mixed_seeded_invoicing/task.toml) | 07_screened_buy_only_mixed_seeded_invoicing |
| ERPBench 完整业务 | [2262 · Open-Plan Noise Barrier Wall](../bench/tasks/2262_easy_26_buy_only_net_30_no_adjacent_data/task.toml) | 26_buy_only_net_30_no_adjacent_data |
| ERPBench 完整业务 | [2263 · Desktop Privacy Screen](../bench/tasks/2263_easy_26_buy_only_net_30_no_adjacent_data/task.toml) | 26_buy_only_net_30_no_adjacent_data |
| ERPBench 完整业务 | [2264 · Door Seal Acoustic Kit](../bench/tasks/2264_easy_26_buy_only_net_30_no_adjacent_data/task.toml) | 26_buy_only_net_30_no_adjacent_data |
| ERPBench 完整业务 | [2265 · Wall-Mount Acoustic Tile Set](../bench/tasks/2265_easy_26_buy_only_net_30_no_adjacent_data/task.toml) | 26_buy_only_net_30_no_adjacent_data |
| ERPBench 完整业务 | [2266 · Ceiling Acoustic Baffle](../bench/tasks/2266_easy_26_buy_only_net_30_no_adjacent_data/task.toml) | 26_buy_only_net_30_no_adjacent_data |
| ERPBench 完整业务 | [2267 · Freestanding Sound Divider](../bench/tasks/2267_easy_26_buy_only_net_30_no_adjacent_data/task.toml) | 26_buy_only_net_30_no_adjacent_data |
| ERPBench 完整业务 | [2268 · Desktop Privacy Screen](../bench/tasks/2268_easy_26_buy_only_net_30_no_adjacent_data/task.toml) | 26_buy_only_net_30_no_adjacent_data |
| ERPBench 完整业务 | [2269 · Wall-Mount Acoustic Tile Set](../bench/tasks/2269_easy_26_buy_only_net_30_no_adjacent_data/task.toml) | 26_buy_only_net_30_no_adjacent_data |
| ERPBench 完整业务 | [2270 · Freestanding Sound Divider](../bench/tasks/2270_easy_26_buy_only_net_30_no_adjacent_data/task.toml) | 26_buy_only_net_30_no_adjacent_data |
| ERPBench 完整业务 | [2271 · Under-Desk Sound Shield](../bench/tasks/2271_easy_26_buy_only_net_30_no_adjacent_data/task.toml) | 26_buy_only_net_30_no_adjacent_data |
| ERPBench 完整业务 | [2272 · Desktop Privacy Screen](../bench/tasks/2272_easy_repair_plan_easy/task.toml) | repair_plan_easy |
| ERPBench 完整业务 | [2273 · Desktop Privacy Screen](../bench/tasks/2273_easy_repair_plan_easy/task.toml) | repair_plan_easy |
| ERPBench 完整业务 | [2274 · Door Seal Acoustic Kit](../bench/tasks/2274_easy_repair_plan_easy/task.toml) | repair_plan_easy |
| ERPBench 完整业务 | [2275 · Wall-Mount Acoustic Tile Set](../bench/tasks/2275_easy_repair_plan_easy/task.toml) | repair_plan_easy |
| ERPBench 完整业务 | [2276 · Ceiling Acoustic Baffle](../bench/tasks/2276_easy_repair_plan_easy/task.toml) | repair_plan_easy |
| ERPBench 完整业务 | [2277 · Conference Pod Enclosure](../bench/tasks/2277_easy_repair_plan_easy/task.toml) | repair_plan_easy |
| ERPBench 完整业务 | [2278 · Door Seal Acoustic Kit](../bench/tasks/2278_easy_repair_plan_easy/task.toml) | repair_plan_easy |
| ERPBench 完整业务 | [2279 · Ceiling Acoustic Baffle](../bench/tasks/2279_easy_repair_plan_easy/task.toml) | repair_plan_easy |
| ERPBench 完整业务 | [2280 · Clean Room Sealed Fixture](../bench/tasks/2280_medium_repair_plan_medium/task.toml) | repair_plan_medium |
| ERPBench 完整业务 | [2281 · Bollard Path Light Solar](../bench/tasks/2281_medium_repair_plan_medium/task.toml) | repair_plan_medium |
| ERPBench 完整业务 | [2282 · Recessed Troffer Panel 2x4](../bench/tasks/2282_medium_repair_plan_medium/task.toml) | repair_plan_medium |
| ERPBench 完整业务 | [2283 · Outdoor Wall Pack 80W](../bench/tasks/2283_medium_repair_plan_medium/task.toml) | repair_plan_medium |
| ERPBench 完整业务 | [2284 · Emergency Exit Combo Fixture](../bench/tasks/2284_medium_repair_plan_medium/task.toml) | repair_plan_medium |
| ERPBench 完整业务 | [2285 · Emergency Exit Combo Fixture](../bench/tasks/2285_medium_repair_plan_medium/task.toml) | repair_plan_medium |
| ERPBench 完整业务 | [2286 · Linear Strip Light 8ft](../bench/tasks/2286_medium_repair_plan_medium/task.toml) | repair_plan_medium |
| ERPBench 完整业务 | [2287 · Clean Room Sealed Fixture](../bench/tasks/2287_medium_repair_plan_medium/task.toml) | repair_plan_medium |
| ERPBench 完整业务 | [2288 · Linear Strip Light 8ft](../bench/tasks/2288_medium_repair_plan_medium/task.toml) | repair_plan_medium |
| ERPBench 完整业务 | [2289 · Emergency Exit Combo Fixture](../bench/tasks/2289_medium_repair_plan_medium/task.toml) | repair_plan_medium |
| ERPBench 完整业务 | [2290 · Ground-Mount Tracker System](../bench/tasks/2290_hard_repair_plan_hard/task.toml) | repair_plan_hard |
| ERPBench 完整业务 | [2291 · Bifacial Solar Module 450W](../bench/tasks/2291_hard_repair_plan_hard/task.toml) | repair_plan_hard |
| ERPBench 完整业务 | [2292 · Solar Shingle Roof Tile Set](../bench/tasks/2292_hard_repair_plan_hard/task.toml) | repair_plan_hard |
| ERPBench 完整业务 | [2293 · Building-Integrated PV Module](../bench/tasks/2293_hard_repair_plan_hard/task.toml) | repair_plan_hard |
| ERPBench 完整业务 | [2294 · Ground-Mount Tracker System](../bench/tasks/2294_hard_repair_plan_hard/task.toml) | repair_plan_hard |
| ERPBench 完整业务 | [2295 · Ground-Mount Tracker System](../bench/tasks/2295_hard_repair_plan_hard/task.toml) | repair_plan_hard |
| ERPBench 完整业务 | [2296 · Ground-Mount Tracker System](../bench/tasks/2296_hard_repair_plan_hard/task.toml) | repair_plan_hard |
| ERPBench 完整业务 | [2297 · Portable Solar Generator Kit](../bench/tasks/2297_hard_repair_plan_hard/task.toml) | repair_plan_hard |
| ERPBench 完整业务 | [2298 · Portable Solar Generator Kit](../bench/tasks/2298_hard_repair_plan_hard/task.toml) | repair_plan_hard |
| ERPBench 完整业务 | [2299 · Carport Solar Canopy Module](../bench/tasks/2299_hard_repair_plan_hard/task.toml) | repair_plan_hard |
| 实验入口（含准备/评估；并非全部付费） | [access.py](../experiments/enterprise_validation/access.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [access_check.py](../experiments/enterprise_validation/access_check.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [audit_business.py](../experiments/enterprise_validation/audit_business.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [audit_payments.py](../experiments/enterprise_validation/audit_payments.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [benchmark.py](../experiments/enterprise_validation/benchmark.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [channel_check.py](../experiments/enterprise_validation/channel_check.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [channel_setup.py](../experiments/enterprise_validation/channel_setup.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [evaluate.py](../experiments/enterprise_validation/evaluate.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [invoice_demo.py](../experiments/enterprise_validation/invoice_demo.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [live.py](../experiments/enterprise_validation/live.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [manage.py](../experiments/enterprise_validation/manage.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [manual_pressure.py](../experiments/enterprise_validation/manual_pressure.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [report.py](../experiments/enterprise_validation/report.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [retrieval_check.py](../experiments/enterprise_validation/retrieval_check.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [selfcheck.py](../experiments/dynamic_business_mvp/tests/selfcheck.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [staff.py](../experiments/enterprise_validation/staff.py) | 见入口中的断言 |
| 实验入口（含准备/评估；并非全部付费） | [verifier.py](../experiments/dynamic_business_mvp/verifier.py) | 见入口中的断言 |
| 实验检查 | [test.sh](../experiments/dynamic_business_mvp/tests/test.sh) | 见入口中的断言 |
| 真实模型节点 | [BP2010](../experiments/agent_regression/bench_recovery_cases.json) | purchase_allocation |
| 真实模型节点 | [BP2030](../experiments/agent_regression/bench_recovery_cases.json) | purchase_allocation |
| 真实模型节点 | [M03](../experiments/agent_regression/manual_cases.json) | Reviewed purchase cancellation method absent |
| 真实模型节点 | [M05](../experiments/agent_regression/manual_cases.json) | History used as current supplier commitment |
| 真实模型节点 | [M11](../experiments/agent_regression/manual_cases.json) | Existing successful sales confirmation control |
| 真实模型节点 | [M13](../experiments/agent_regression/manual_followups.json) | Reviewed purchase cancellation was not explained as including unreceived receipts; unsupported picking cancellation forced handoff |
| 真实模型节点 | [M16](../experiments/agent_regression/manual_followups.json) | Final reply labels amount_total=791 as untaxed; source request had five net-priced lines totalling700 but the order readback supplied only amount_total. Why the model chose the label is unproven. |
| 离线测试套件 | [test_business_mvp_verifier.py](../tests/test_business_mvp_verifier.py) | 见入口中的断言 |
| 离线测试套件 | [test_enterprise_actions.py](../tests/test_enterprise_actions.py) | 见入口中的断言 |
| 离线测试套件 | [test_enterprise_knowledge.py](../tests/test_enterprise_knowledge.py) | 见入口中的断言 |
| 离线测试套件 | [test_enterprise_workbench.py](../tests/test_enterprise_workbench.py) | 见入口中的断言 |
| 离线测试套件 | [test_purchase_allocation.py](../tests/test_purchase_allocation.py) | 见入口中的断言 |
| 离线测试套件 | [test_workbench_sale_view.py](../tests/test_workbench_sale_view.py) | 见入口中的断言 |
| 隔离完整业务 | [E03 · 客户收款核销](../experiments/enterprise_validation/README.md) | 最终状态＋安全约束＋效率；历史通过不代表当前通过 |
| 隔离完整业务 | [E04 · 供应商付款核销](../experiments/enterprise_validation/README.md) | 最终状态＋安全约束＋效率；历史通过不代表当前通过 |
| 隔离完整业务 | [SALE · 销售确认保护](../experiments/enterprise_validation/README.md) | 最终状态＋安全约束＋效率；历史通过不代表当前通过 |
