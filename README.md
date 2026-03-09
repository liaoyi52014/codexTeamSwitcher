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
- ✅ **Web界面管理**：支持导入账户、查看用量、切换账号（可自动结束已有 Codex CLI 会话）
- ✅ **同账号多工作空间支持**：同一 ChatGPT 账号可导入并切换多个 workspace

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

### 同一账号导入多个 workspace

如果同一个 ChatGPT 账号下有多个 workspace，可重复执行：

1. `codex login`（切到目标 workspace）
2. 点击「导入当前账户」

系统会按 `account + workspace` 识别为不同 Team，支持独立切换。

### 页面切换账号（默认会结束已有 CLI 会话）

在 Web 界面点击目标团队的「切换账号」按钮后，系统会：
1. 调用 `/api/switch-account` 写入该团队的 `~/.codex/auth.json`
2. 同步更新系统当前活跃 Team
3. 结束当前机器上已打开的 Codex CLI 进程

结束后请重新打开 Codex CLI 会话。

如需继续之前会话，可执行：

```bash
codex resume --last
```

如果当前目录下没有显示旧会话，可使用：

```bash
codex resume --all
```

### 仅切换代理层活跃 Team（不改 auth.json）

如果你只希望代理层切换，不想动本机 Codex 登录态，可调用 `/api/switch`（例如脚本或手动 API 调用）。

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
- 当配额不足自动切换时，无需修改任何配置（代理层无感）
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
| `/api/switch` | POST | 仅切换系统内部当前 Team（主要影响代理层，不写入 auth.json） |
| `/api/switch-account` | POST | 切换到指定账户（写入 auth.json；支持同步内部 Team、结束 Codex CLI 会话）|
| `/api/import-account` | POST | 导入当前 Codex 账户 |
| `/api/check` | POST | 触发一次用量检查 |

`/api/switch-account` 请求体字段：
- `team_id`：目标团队 ID（必填）
- `sync_active_team`：是否同步更新系统内部活跃 Team（默认 `true`）
- `terminate_codex_sessions`：是否结束当前机器上运行中的 Codex CLI 进程（默认 `false`；Web 页面按钮默认也传 `false`）

`/api/switch-account` 成功响应字段（新增）：
- `resume_command`：建议执行的会话恢复命令（如 `codex resume <session_id>` 或 `codex resume --last`）
- `resume_session_id`：检测到的最近本地会话 ID（可能为空）

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

### Q: 切换 Team / 账号后需要重启 Codex 吗？

A:
- 使用代理层自动切换或调用 `/api/switch`：通常不需要重启（代理无感切换）。
- 使用页面「切换账号」：默认不会结束已有 Codex CLI 会话；如需结束请显式传 `terminate_codex_sessions=true`。
- 如果未使用代理，切换后需要你在 CLI 侧重新进入会话才能使用新登录态。

### Q: 页面切换账号后，如何让已有 Codex 会话全部退出？

A: 页面“切换账号”按钮会调用 `/api/switch-account`，默认不会终止当前机器上运行中的 Codex CLI 会话。若你需要强制关闭旧会话，可在自定义 API 调用里显式传 `terminate_codex_sessions=true`。

### Q: 切换账号并终止 Codex 进程后，怎么继续之前的会话？

A:
- 推荐直接执行：`codex resume --last`
- 如果你知道会话 ID，可以执行：`codex resume <session_id>`
- 如果当前目录过滤导致找不到会话，可执行：`codex resume --all`

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
