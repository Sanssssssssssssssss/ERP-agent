# 业务工作区交互原型

起点：`2472e9a`，2026-09-08。用于验收信息组织与交互；不替换 0.2.1 桌面应用。
文件、单据、回执与用量全部为明确标注的示例。
不连接 Odoo、模型服务或 MCP，不读取桌面配置。刷新页面重置示例状态。

从仓库根目录启动独立桌面预览，复用项目现有 Electron：

```powershell
desktop\node_modules\.bin\electron.cmd experiments\desktop_workbench\prototype\preview.cjs
```

也可用浏览器直接打开同目录 `index.html`。桌面预览使用独立的
`.runtime/workbench-prototype/profile`，禁用远程网络、权限请求及外部导航。

## 对照成熟实现

| 来源 | 采用的具体方式 | 在本原型中的位置 |
| --- | --- | --- |
| [VS Code 布局](https://code.visualstudio.com/docs/configure/custom-layout)与[侧栏准则](https://code.visualstudio.com/api/ux-guidelines/sidebars) | 主工作区、资源导航、辅助面板各有职责；会话可收起 | Session → 业务标签；会话在辅助面板 |
| [n8n LogsPanel](https://github.com/n8n-io/n8n/blob/master/packages/frontend/editor-ui/src/features/execution/logs/components/LogsPanel.vue)与[LogDetailsPanel](https://github.com/n8n-io/n8n/blob/master/packages/frontend/editor-ui/src/features/execution/logs/components/LogDetailsPanel.vue) | 选中某一步再查看输入、输出与错误；浏览历史与当前执行区分 | 执行台的步骤详情、运行详情 |
| [Langfuse TraceLayoutDesktop](https://github.com/langfuse/langfuse/blob/main/web/src/features/traces/components/TraceLayoutDesktop.tsx)与[数据模型](https://langfuse.com/docs/observability/data-model) | 树与详情并列，trace 层级与用量可追溯 | 运行 → 轮次 → 工具 → 回执 |
| 本地 `erp-openai/renderer/src/styles.css`、`components/Inspector.tsx`、`lib/artifacts.ts` | 冷灰白、克制的青色、紧凑边框；资源列表与预览 | 视觉体系、单据与文件 |

仅借鉴布局与交互方式，无第三方源代码移植、依赖安装或旧后端接入。

### 源码复用决策

用户要求参考对象必须有成熟代码，降低正式实施的验证成本。已读源码，不只看截图：

| 对象 | 已核对的实现/测试 | 正式版决策 |
| --- | --- | --- |
| Radix Themes / Primitives | [Tabs 实现](https://github.com/radix-ui/themes/blob/main/packages/radix-ui-themes/src/components/tabs.tsx)、[键盘与焦点测试](https://github.com/radix-ui/primitives/blob/main/packages/react/tabs/src/tabs.test.tsx)；本仓已安装 Themes 3.3.0（MIT）且包含源码 | 直接使用现有 Button、Tabs、Dialog、AlertDialog、Tooltip、Table；不自行重写焦点管理、弹层或 tabs 键盘行为 |
| Langfuse Trace | 已读 `TraceLayoutDesktop.tsx`；导入 `react-resizable-panels`、URL 参数、选择/视图上下文及自己的 UI 组件 | 用作 Trace 主从布局与选择交互参照；不整页复制。若后续摘取代码，固定提交、审查依赖并保留许可声明 |
| n8n 执行日志 | 已读 `LogsPanel.vue`、`LogDetailsPanel.vue`；依赖其 Vue 应用上下文 | 只借鉴步骤选择 → 输入/输出/错误的交互，不移植源码 |
| 当前桌面 | `desktop/src/renderer/App.tsx` 已使用 Radix Button/Badge/Dialog/AlertDialog/Tooltip 和 Lucide | 在已有 React/Electron 基座上调整结构，保留已验证的桥接、审批去重和消息作用域 |

[Langfuse 许可](https://github.com/langfuse/langfuse/blob/main/LICENSE)将非 EE、非另行许可的内容列为 MIT；
上述 Trace 文件位于非 EE 路径。其应用组件仍有上下文依赖，不等于可无修改即插即用。
[n8n 许可](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)为 Sustainable Use License 并有 EE 等例外，
本项目面向客户的实现不将其列为直接代码复用候选。

当前三个静态原型文件只承担页面与样例状态演示，不迁入正式 React 应用作为通用组件库。
正式验收重点是我们的 ERP 适配：证据归属、审批边界、异步状态及失败恢复；
成熟控件已有测试可以减少基础交互的重写成本，不能替代业务正确性测试。

## 页面与事实边界

| 子页 | 用户应获得的答案 | 交互 |
| --- | --- | --- |
| 执行台 | 做什么、做到哪一步、当前动作、下一步需要谁处理 | 选步骤；展开目标；审批；跳到对应证据 |
| 单据与文件 | 单据状态是什么、材料与产物在哪里 | 分类列表 → 预览；明确 Odoo 记录与生成文件的区别 |
| 变更与审批 | 准备改什么、批准了什么、已经改了什么、核验结果是什么 | 字段前后值、动作状态、审批与回执链接 |
| 运行详情 | 哪轮调用哪个工具、返回什么、花多少时间与 token | 轮次/工具选择 → 输入输出、回执、用量 |

业务阶段不是固定工作流引擎；本原型仅演示销售开票这一个场景。
阶段完成必须有对应证据，动画不推进业务状态。后续接入时，模型的公开计划只是
“拟执行”，工具响应只是执行证据，Odoo 独立回读才是已观察状态；三者不能互相代替。

| 示例状态 | 必须显示 | 不可误导 |
| --- | --- | --- |
| 待执行 | 目标、步骤预期、开始操作；尚未读取/创建单据 | 不显示运行用量或已生成产物 |
| 执行中 | 当前步骤、对象、最近回执与时间 | 不提前展示未发生的订单确认或发票过账 |
| 待审批 | 即将修改的对象、前后值、批准/拒绝 | 批准不等于执行或核验完成 |
| 成功 | 已确认订单、已过账发票及回读证据 | 不以模型结束或总结文字代替业务成功 |
| 失败 | 失败步骤与原因、之前已写入的事实、安全后续动作 | 不把整个业务当作自动回滚 |
| 写入待核对 | 已发出请求但结果不确定、最后已知状态、核对入口 | 不提供重复写入；不能将最后已知状态冒充最新状态 |

## 正式接入仍需补齐

现有 `desktop/src/shared/protocol.ts` 已有 Business、Document、Approval、Run、Round、Tool，
可以沿用。`workbench/sale_view.py::_activity` 当前主要从最近工具与运行状态汇总活动，
不能直接提供完整的业务阶段因果关系。

正式实施时先在现有事件与查询结果中补齐：

- 公开目标/计划与阶段 ID；事件关联业务、运行、阶段、动作与回执，允许计划修订。
- 单据来源工具及观察时间；审批前状态、执行返回、独立回读分别呈现。
- 资源清单：Odoo 记录标识、输入材料、实际生成文件路径及来源；文件打开由现有桌面边界校验。
- 用量按原始定义显示：缓存命中是输入子集；reasoning 缺失为“未提供”，不重复加总。

独立只读复核用 mock reader 复现了两个现状约束：

- 历史运行有完整单据、新运行无自身单据时，`refresh_business` 会重读历史对象，并把
  `latest_run_id` 指向新运行；新运行可能得到 `verification_status=passed`。这是业务当前
  事实的重新观测，不能据此证明新运行已完成。必须保留单据来源与当前动作/核验的关联。
- 订单 `draft`、关联发票 `posted` 时，现有“已读取单据状态”等检查也可能全部通过。
  所以现有 `passed` 应称为“回读检查通过”；正式业务成功还需明确要求订单已确认、
  发票已过账及场景目标满足。独立 ERP-Bench/MVP 评分与这些 UI 观测检查分别展示。

上述复核不访问真实 Odoo，生产代码本轮未修改。这两项是正式接入的验收门槛。

本原型不新增正式协议、编排器、队列或第二套模型循环；样例动作不证明以上链路已经接通。

## 验收清单

1. 1280×800 与 1600×1000 下检查六状态与四子页，无页面横向溢出或关键内容遮挡。
2. 目标、当前阶段与下一动作首屏可定位；任何关键单据/文件最多两次点击可预览。
3. 从阶段/变更跳到对应回执；在 Trace 浏览历史不改变当前业务执行状态。
4. 待审批的批准/拒绝分别验证，重复点击不重复推进；失败保留已写入事实。
5. 写入待核对只提供核对；核对证据完整后才显示成功。
6. 切换业务保留各自示例状态；会话面板收起/展开；键盘操作、焦点与 reduced motion。
7. 示例下载内容带示例标识；不存在的 PDF 明确未生成；无任何外部网络请求或页面错误。

以上是检查目标，不是生产验收结论；“五秒理解”还需用户实际观察反馈。
复核脚本与本机检查结果随原型完成后记录在此目录；截图与原始检查输出保存在 `.runtime/`。

复跑交互检查（复用桌面项目已安装的 Playwright，不安装测试框架）：

```powershell
node experiments\desktop_workbench\prototype\check.cjs
```

脚本打开本地 HTML，遍历两个尺寸下六状态、四子页的 48 种组合，再操作审批、拒绝、
只读核对、资源回执跳转和下载。失败退出非零；详细输出与截图在
`.runtime/workbench-prototype/check.json` 及同目录 PNG。该检查是本机模拟交互回归，
不代表真实 ERP 吞吐、生产压力容量、模型成功率或 token 节省。

## 本轮结果（2026-09-09）

| 检查 | 结果 |
| --- | --- |
| 两个尺寸 × 六状态 × 四子页 | 48/48 通过；无页面横向溢出 |
| 审批、拒绝、待核对回读、业务切换、键盘操作、回执定位 | 通过；批准后仍未执行，拒绝保留原因和已有草稿 |
| 示例文件下载 | 两个尺寸各下载 1 份；内容保留示例标识 |
| 浏览器交互回归 | 页面错误 0、控制台错误 0、外部请求 0 |
| 原生 Electron 窗口 | 实测 1280×800、1600×1000 内容尺寸，DPR 1.5；审批和 Trace 已目视复核 |
| 最终样式修正 | 加深次要文字和警示色、辅助文字最低 11px、明确键盘焦点；修正后 48/48 再次通过 |

测试复用现有 Playwright 与 Electron。原始回归结果为
`.runtime/workbench-prototype/check.json`，原生交互记录为同目录 `native-receipt.json`；
最终桌面截图为 `native-1280-approval.png`、`native-1600-trace.png`。
这些本机产物不纳入 Git。生产运行代码未变，真实模型 API 请求与 ERP 写入均为 0。
本轮完成原型技术检查；客户是否能快速理解仍待用户验收，正式 ERP 适配尚未实施。
