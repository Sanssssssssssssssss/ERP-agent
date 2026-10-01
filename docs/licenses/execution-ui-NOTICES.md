# 局部展示源码来源

只适配展示代码；审批状态、工具授权、模型通信由原有 native Harness 处理。

| 来源与固定版本 | 本地适配 | 许可证 |
|---|---|---|
| [Langfuse VirtualizedTreeNodeWrapper.tsx](https://github.com/langfuse/langfuse/blob/593677cd4de5fb78dbdf1ca448de89478413229c/web/src/features/traces/components/VirtualizedTreeNodeWrapper.tsx) · `593677cd4de5fb78dbdf1ca448de89478413229c` | `desktop/src/renderer/features/traces/TraceTreeRow.tsx`：深度限制、祖先连接线、末尾分支、独立折叠；改用现有 CSS 和原生按钮。没有引入 Langfuse 服务或 EE 代码。 | MIT；Copyright (c) 2023-2026 ClickHouse, Inc.；[原文](Langfuse-LICENSE.txt) |
| [AI Elements confirmation.tsx](https://github.com/vercel/ai-elements/blob/6a9d5b1822ffb10bba4bd97175f01edd7d8651cd/packages/elements/src/confirmation.tsx) · `6a9d5b1822ffb10bba4bd97175f01edd7d8651cd` | `ApprovalsPage.tsx`：请求与终态分离的条件展示；仅保留最小条件渲染函数，接入原 `ApprovalRow`、Radix 和宿主回调。 | Apache-2.0；Copyright 2023 Vercel, Inc.；[项目声明](AI-Elements-LICENSE.txt)、[完整许可证](Apache-2.0.txt) |
| [pi HTML template.js](https://github.com/earendil-works/pi/blob/534bcbffb7e1e7551d9ee3572dfeb278e203e493/packages/coding-agent/src/core/export-html/template.js) · `534bcbffb7e1e7551d9ee3572dfeb278e203e493` | 工具摘要、结果预览、原始信息展开的交互参考；未复制其静态模板运行时。 | MIT；Copyright (c) 2025 Mario Zechner；[原文](pi-LICENSE.txt) |

上述适配经过修改，不能代表上游原始实现。审批字段差异、工具名称映射、Trace 关联和按需加载继续使用本项目现有代码。其他展示继续复用本项目原桌面代码。
