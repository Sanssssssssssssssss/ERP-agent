# v0.6.0 Windows 预览版

包含当前整合后的后端、业务回读和中断恢复。未决写入先核对，核对通过后沿原会话继续；恢复不会批准新动作或自动重复发送。长期记忆默认关闭；Laya 本地模型与实验数据不装入桌面包，需要单独配置。

下载解压 `Odoo-Workbench-0.6.0-windows-x64.zip`，运行其中的 portable EXE。包内含 Python 后端，不需要另外安装 Python 或 Node。仍需可访问的 Odoo 和模型 API。Windows 包未签名，保持预览版标记。

升级前关闭旧版，备份完整用户 profile。应用身份和数据目录保持兼容；不要同时用两个版本打开同一 profile。源码回退不撤销 Odoo 写入。旧运行日志和动作账本继续保留。

本机标准企业增加持久化 Mailpit 收件箱：http://127.0.0.1:18080 。模拟客户无需注册邮箱，邮件正文和附件可直接查看。它只捕获测试邮件；向真实客户发信需要另外配置企业 SMTP。部署见[企业说明](../experiments/enterprise_validation/README.md)。Mailpit 是独立企业环境服务，不自动装进任意用户的 Odoo。

已验证：中断恢复局部模型 A/B、隔离原会话续跑、相关后端与桌面交互。原企业失败邮件也已通过现有队列投递至测试邮箱，PDF 校验和一致，并完成动作账本核对。[恢复报告](interrupted-recovery.md)保留先前隔离实验的边界；完整测试入口见 [TESTING.md](../TESTING.md)。

发布包另附 `validation.json`、`SHA256SUMS.txt` 和 sidecar `manifest.json`：记录实际打包启动、IPC/重启检查、源代码版本及文件哈希。API key、用户 profile、企业数据库、完整 trace 和模型权重均不随包发布。
