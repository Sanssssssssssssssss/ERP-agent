# 持久 Odoo demo

```powershell
.venv\Scripts\python.exe experiments\demo_odoo\start.py
```

需要 WSL Ubuntu 与 Docker。独立数据库 `erp_harness_demo`，地址 `http://127.0.0.1:18069`。命名卷保存数据，重复启动不会重复造单。

初始数据：示例客户、供应商、3 个产品及报价/库存、1 张销售草稿、1 张采购草稿。全部是合成数据；不导入 benchmark 答案，也不连接企业真实系统。

本地连接资料在忽略目录 `.runtime/demo-odoo/connection.json`，网页密码在 `admin-password.txt`；仅用于这个本机 demo。Agent 使用正常原生工具维护记录，写入仍经过宿主审批和回读。

本次已配置的独立桌面 profile：

```powershell
$env:WORKBENCH_HOST_ROOT = $PWD.Path
$env:WORKBENCH_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
& desktop/node_modules/electron/dist/electron.exe desktop --user-data-dir="$PWD/.runtime/demo-odoo/desktop-profile"
```

模型密钥通过 Windows 安全存储加密。停止服务可使用同一 compose 文件的 `stop`；不要使用删除数据卷的命令。企业真实数据的导入、同步和字段映射尚未接入。
