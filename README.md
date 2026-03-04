# Codex Team Switcher

OpenAI Codex Team 自动切换管理系统 - 当配额不足时自动切换到下一个可用 Team。

## 功能特性

- ✅ 自动监控 Codex Team 配额使用情况（5小时窗口 + 周用量）
- ✅ 配额低于阈值（默认 5%）时自动切换
- ✅ 支持多 Team 轮询管理
- ✅ Web 管理界面
- ✅ 代理服务支持无感切换
- ✅ 自动从 `~/.codex/auth.json` 读取认证信息
- ✅ **零配置启动**：首次运行自动导入当前登录的 Codex 账户
- ✅ **Web界面管理**：支持导入账户、查看用量、手动切换

## 快速开始

### 1. 确保 Codex 已登录

```bash
codex login
```

### 2. 启动服务

```bash
./run.sh
```

首次运行会自动：
1. 创建虚拟环境并安装依赖
2. 从 Codex 读取当前登录的账户信息
3. 启动 Web 管理界面和代理服务

### 3. 访问界面

- **Web 管理界面**: http://localhost:18080
- **代理服务**: http://localhost:18888

## 使用说明

### 查看当前用量

在 Web 界面中可以查看：
- **5小时用量**：5小时窗口内的配额使用情况
- **周用量**：一周内的配额使用情况

### 导入多个账户

1. 重新运行 `codex login` 选择另一个团队账户
2. 在 Web 界面点击「导入当前账户」按钮
3. 账户将添加到 Team 列表中

> 注意：Codex 会在登录时让你选择团队，切换账户后运行本系统的「导入当前账户」即可。

### 手动切换 Team

在 Web 界面点击目标团队的「切换」按钮。

### 配置代理（推荐）

将 Codex 的请求通过代理转发，实现真正的无感切换：

```bash
# 方式1：环境变量
export OPENAI_API_BASE=http://localhost:18888

# 方式2：在 ~/.codex/config.toml 中添加
[app]
api_base = "http://localhost:18888"
```

配置代理后：
- 所有 Codex API 请求都会经过代理服务
- 当配额不足自动切换时，无需修改任何配置
- 代理会自动使用当前活跃的 Team 的凭证

### 验证代理是否生效

```bash
# 测试代理健康检查
curl http://localhost:18888/health

# 查看当前代理使用的 Team
curl http://localhost:18888/team
```

## 命令行选项

```bash
./run.sh              # 启动全部服务（监控 + 代理 + Web界面）
./run.sh --status     # 查看当前状态并退出
./run.sh --check      # 执行一次用量检查并退出
./run.sh --proxy-only # 仅启动代理服务
./run.sh --admin-only # 仅启动 Web 管理界面
./run.sh --no-admin   # 启动服务但禁用 Web 界面
```

## API 接口

### 1. Web 管理界面

访问 http://localhost:18080 查看可视化界面

### 2. 代理服务

将 Codex 请求通过代理转发实现无感切换。

### 3. REST API

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/status` | GET | 获取系统状态 |
| `/api/codex-status` | GET | 获取 Codex 登录状态 |
| `/api/switch` | POST | 手动切换 Team |
| `/api/switch-account` | POST | 切换到指定账户（写入auth.json）|
| `/api/import-account` | POST | 导入当前 Codex 账户 |
| `/api/check` | POST | 触发一次用量检查 |
| `/api/mock-usage` | POST | 设置模拟用量（测试用）|

## 工作原理

1. **用量获取**: 调用 `https://chatgpt.com/backend-api/wham/usage` API 获取配额
   - `primary_window`: 5小时窗口
   - `secondary_window`: 1周窗口
2. **自动监控**: 每 5 分钟检查一次当前 Team 配额
3. **自动切换**: 当配额低于 5% 时自动切换到下一个可用的 Team
4. **无感切换**: 通过代理层转发请求，切换时无需重启 Codex

## 项目结构

```
codexTeamSwitch/
├── src/
│   ├── main.py              # 主入口
│   ├── config/              # 配置模块
│   ├── models/              # 数据模型
│   ├── services/           # 核心服务
│   │   ├── token_manager.py    # Token 管理
│   │   ├── usage_monitor.py   # 用量监控
│   │   ├── team_switcher.py   # Team 切换
│   │   ├── proxy.py          # 代理服务
│   │   └── admin.py          # Web 管理界面
│   └── utils/               # 工具模块
├── venv/                    # 虚拟环境（自动创建）
├── data/                    # 数据目录
│   ├── teams.db             # SQLite 数据库
│   └── .encryption_key      # 加密密钥
├── config.yaml              # 配置文件
├── requirements.txt         # Python 依赖
└── run.sh                   # 启动脚本
```

## 常见问题

### Q: 如何测试自动切换功能？

A: 在 Web 界面中点击目标团队的「模拟4%」按钮，系统会认为该团队配额不足并触发自动切换。

### Q: 切换 Team 后需要重启 Codex 吗？

A: 不需要。如果配置了代理（`OPENAI_API_BASE=http://localhost:18888`），切换是完全无感的。如果没有配置代理，需要手动更新环境变量。

### Q: 如何查看当前是哪个 Team 在使用？

A:
- Web 界面：查看「当前 Team」
- API：`curl http://localhost:18888/team`
- 命令行：`./run.sh --status`

## 注意事项

1. 首次运行会自动从 `~/.codex/auth.json` 导入当前账户
2. 确保 Codex 已登录（运行过 `codex login`）
3. 代理服务端口默认 18888，Web 界面端口 18080
4. 加密密钥保存在 `data/.encryption_key`，请勿删除
5. 如果数据目录被删除，需要重新导入账户
