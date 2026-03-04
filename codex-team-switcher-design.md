# OpenAI Codex Team 自动切换系统设计文档

## 1. 项目概述

### 1.1 背景
当前使用多个 OpenAI Codex team 账户，需要在某个 team 用量不足（低于 5%）时自动切换到下一个可用的 team，以保证服务的连续性。通过 `/status` 命令可以查询当前 team 的用量信息。

### 1.2 目标
- 实时监控各 team 的用量
- 当用量低于 5% 时自动切换到下一个 team
- 用户无感知切换（无需重新登录）
- 支持多 team 轮询管理

### 1.3 核心需求
1. **用量监控**：定期查询当前 team 的剩余配额
2. **自动切换**：配额不足时切换到下一个 team
3. **无感切换**：通过 OAuth token 更新实现，无需用户操作
4. **状态持久化**：记录当前使用的 team 和切换历史

---

## 2. 系统架构

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────┐
│                   用户应用层                          │
│              (Codex Client/IDE Plugin)              │
└──────────────────┬──────────────────────────────────┘
                   │ OAuth Token
                   ↓
┌─────────────────────────────────────────────────────┐
│              Token Manager Service                   │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │ Token Store  │  │ Token Refresh│  │ Token     │ │
│  │              │  │              │  │ Validator │ │
│  └──────────────┘  └──────────────┘  └───────────┘ │
└──────────────────┬──────────────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────────────┐
│            Usage Monitor Service                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │ Usage Poller │  │ Threshold    │  │ Alert     │ │
│  │              │  │ Checker      │  │ Manager   │ │
│  └──────────────┘  └──────────────┘  └───────────┘ │
└──────────────────┬──────────────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────────────┐
│            Team Switcher Service                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │ Team Queue   │  │ Switch Logic │  │ Rollback  │ │
│  │ Manager      │  │              │  │ Handler   │ │
│  └──────────────┘  └──────────────┘  └───────────┘ │
└──────────────────┬──────────────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────────────┐
│                 Codex API                            │
│         (Usage API / Auth API)                       │
└─────────────────────────────────────────────────────┘
```

### 2.2 核心组件

#### 2.2.1 Token Manager Service
**职责**：管理多个 team 的 OAuth token
- Token 存储和加密
- Token 自动刷新
- Token 验证和过期检查

#### 2.2.2 Usage Monitor Service
**职责**：监控各 team 的用量
- 定期轮询用量 API
- 计算剩余配额百分比
- 触发切换告警

#### 2.2.3 Team Switcher Service
**职责**：执行 team 切换逻辑
- 维护 team 队列
- 执行切换操作
- 处理切换失败回滚

---

## 3. 详细设计

### 3.1 数据模型

#### 3.1.1 Team 配置
```json
{
  "teams": [
    {
      "id": "team-001",
      "name": "Team Alpha",
      "oauth": {
        "access_token": "sk-xxx",
        "refresh_token": "xxx",
        "expires_at": "2026-03-10T00:00:00Z",
        "organization_id": "org-xxx"
      },
      "quota": {
        "total": 100000,
        "used": 95000,
        "remaining": 5000,
        "percentage": 5.0,
        "last_checked": "2026-03-03T10:00:00Z"
      },
      "priority": 1,
      "enabled": true,
      "status_command": "/status"
    }
  ],
  "config": {
    "threshold_percentage": 5.0,
    "check_interval_seconds": 300,
    "auto_switch_enabled": true
  }
}
```

#### 3.1.2 切换历史记录
```json
{
  "switch_history": [
    {
      "timestamp": "2026-03-03T10:30:00Z",
      "from_team": "team-001",
      "to_team": "team-002",
      "reason": "quota_low",
      "from_quota_percentage": 4.5,
      "success": true
    }
  ]
}
```

### 3.2 核心流程

#### 3.2.1 用量监控流程
```
1. 定时器触发（每 5 分钟）
2. 获取当前活跃 team 的 token
3. 执行 /status 命令查询用量
4. 解析响应，计算剩余百分比
5. 如果 < 5%，触发切换流程
6. 记录监控日志
```

#### 3.2.2 Team 切换流程
```
1. 检测到用量低于阈值
2. 从 team 队列中获取下一个可用 team
3. 验证下一个 team 的 token 有效性
4. 查询下一个 team 的用量（确保 > 5%）
5. 更新配置文件中的当前 team
6. 刷新客户端的 OAuth token（无需重新登录）
7. 验证切换成功
8. 记录切换历史
```

#### 3.2.3 无感切换实现
```
关键点：通过更新环境变量或配置文件中的 token，
让正在运行的 Codex 客户端自动使用新的 token

方案 1：环境变量注入
- 监控服务更新 OPENAI_API_KEY 环境变量
- Codex 客户端定期重新读取环境变量

方案 2：配置文件热更新
- 监控服务更新 ~/.openai/config.json
- Codex 客户端监听文件变化并重新加载

方案 3：代理层拦截
- 在本地运行代理服务
- Codex 请求通过代理转发
- 代理层动态切换 token
```

---

## 4. 技术实现

### 4.1 技术栈选择

**后端服务**：
- Python 3.10+ / Node.js 18+
- 定时任务：APScheduler (Python) / node-cron (Node.js)
- 配置管理：YAML/JSON
- 日志：structlog / winston

**存储**：
- SQLite（轻量级）或 PostgreSQL（生产环境）
- Redis（可选，用于缓存和分布式锁）

**安全**：
- Token 加密存储（使用 Fernet 或 AES-256）
- 环境变量管理（python-dotenv）

### 4.2 核心代码结构

```
codex-team-switcher/
├── src/
│   ├── config/
│   │   ├── teams.yaml          # Team 配置
│   │   └── settings.py         # 全局设置
│   ├── services/
│   │   ├── token_manager.py    # Token 管理
│   │   ├── usage_monitor.py    # 用量监控
│   │   ├── team_switcher.py    # 切换逻辑
│   │   └── codex_client.py     # Codex API 客户端
│   ├── models/
│   │   ├── team.py             # Team 数据模型
│   │   └── switch_log.py       # 切换日志模型
│   ├── utils/
│   │   ├── crypto.py           # 加密工具
│   │   └── logger.py           # 日志工具
│   └── main.py                 # 主入口
├── tests/
├── config.example.yaml
├── requirements.txt
└── README.md
```

### 4.3 关键实现细节

#### 4.3.1 执行 /status 命令
```python
import subprocess
import json

def get_team_usage(team_token):
    """
    执行 /status 命令获取用量
    假设 Codex CLI 支持通过环境变量传入 token
    """
    env = os.environ.copy()
    env['OPENAI_API_KEY'] = team_token

    result = subprocess.run(
        ['codex', '/status'],
        env=env,
        capture_output=True,
        text=True
    )

    # 解析输出
    status_data = parse_status_output(result.stdout)
    return {
        'total': status_data['quota_total'],
        'used': status_data['quota_used'],
        'remaining': status_data['quota_remaining'],
        'percentage': (status_data['quota_remaining'] / status_data['quota_total']) * 100
    }
```

#### 4.3.2 无感切换实现（代理方案）
```python
from flask import Flask, request, Response
import requests

app = Flask(__name__)
current_team_token = "sk-team-001"

@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def proxy(path):
    """
    代理所有 Codex API 请求
    动态注入当前 team 的 token
    """
    headers = dict(request.headers)
    headers['Authorization'] = f'Bearer {current_team_token}'

    resp = requests.request(
        method=request.method,
        url=f'https://api.openai.com/{path}',
        headers=headers,
        data=request.get_data(),
        params=request.args
    )

    return Response(resp.content, resp.status_code, resp.headers.items())

def switch_team(new_token):
    """切换 team token"""
    global current_team_token
    current_team_token = new_token
    print(f"Switched to new team token: {new_token[:10]}...")
```

#### 4.3.3 监控服务主循环
```python
import schedule
import time

def monitor_and_switch():
    """监控并切换 team"""
    current_team = get_current_team()
    usage = get_team_usage(current_team['oauth']['access_token'])

    print(f"Team {current_team['name']}: {usage['percentage']:.2f}% remaining")

    if usage['percentage'] < 5.0:
        print(f"⚠️  Quota low! Switching team...")
        next_team = get_next_available_team()

        if next_team:
            switch_to_team(next_team)
            log_switch(current_team, next_team, usage['percentage'])
            print(f"✅ Switched to {next_team['name']}")
        else:
            print(f"❌ No available team to switch to!")
            send_alert("All teams quota exhausted!")

# 每 5 分钟检查一次
schedule.every(5).minutes.do(monitor_and_switch)

while True:
    schedule.run_pending()
    time.sleep(1)
```

---

## 5. 部署方案

### 5.1 本地部署
```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 teams
cp config.example.yaml config.yaml
# 编辑 config.yaml，添加所有 team 的 token

# 3. 启动监控服务
python src/main.py

# 4. 启动代理服务（如果使用代理方案）
python src/proxy.py

# 5. 配置 Codex 客户端使用代理
export OPENAI_API_BASE=http://localhost:8080
```

### 5.2 Docker 部署
```dockerfile
FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ ./src/
COPY config.yaml .

CMD ["python", "src/main.py"]
```

### 5.3 系统服务部署（systemd）
```ini
[Unit]
Description=Codex Team Switcher
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/codex-team-switcher
ExecStart=/usr/bin/python3 src/main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

---

## 6. 安全考虑

### 6.1 Token 安全
- ✅ Token 加密存储，不明文保存
- ✅ 使用环境变量或密钥管理服务
- ✅ 定期轮换 token
- ✅ 限制配置文件权限（chmod 600）

### 6.2 访问控制
- ✅ 监控服务仅本地访问
- ✅ 代理服务绑定 localhost
- ✅ 日志脱敏，不记录完整 token

### 6.3 异常处理
- ✅ 切换失败自动回滚
- ✅ 所有 team 耗尽时发送告警
- ✅ API 调用失败重试机制

---

## 7. 监控和告警

### 7.1 监控指标
- 各 team 剩余配额百分比
- 切换频率和成功率
- API 调用延迟
- 错误率

### 7.2 告警规则
- 所有 team 配额 < 10%：发送邮件/Slack 通知
- 切换失败：立即告警
- 监控服务宕机：心跳检测告警

### 7.3 日志记录
```python
# 结构化日志示例
{
  "timestamp": "2026-03-03T10:30:00Z",
  "level": "INFO",
  "event": "team_switched",
  "from_team": "team-001",
  "to_team": "team-002",
  "trigger_reason": "quota_low",
  "quota_percentage": 4.5
}
```

---

## 8. 测试计划

### 8.1 单元测试
- Token 管理功能
- 用量计算逻辑
- 切换决策算法

### 8.2 集成测试
- /status 命令执行
- Team 切换完整流程
- 异常场景处理

### 8.3 压力测试
- 高频切换场景
- 并发请求处理
- 长时间运行稳定性

---

## 9. 未来优化

### 9.1 智能调度
- 根据历史用量预测，提前切换
- 负载均衡，避免单个 team 过载

### 9.2 Web 管理界面
- 实时查看各 team 状态
- 手动触发切换
- 配置管理

### 9.3 多用户支持
- 支持多个用户共享 team 池
- 用户级别的配额管理

---

## 10. 风险和限制

### 10.1 技术风险
- **Codex 客户端兼容性**：不同版本的 Codex 客户端可能不支持热更新 token
- **API 限制**：OpenAI 可能对频繁切换 token 有限制
- **/status 命令格式变化**：命令输出格式可能更新

### 10.2 业务风险
- **配额耗尽**：所有 team 同时耗尽配额
- **切换延迟**：切换过程中可能有短暂的服务中断

### 10.3 缓解措施
- 保留至少 3 个 team 作为备份
- 设置配额告警阈值（如 20%）
- 实现优雅降级机制

---

## 11. 总结

本设计提供了一个完整的 OpenAI Codex Team 自动切换解决方案，核心特性包括：

✅ **自动监控**：通过 `/status` 命令定期检查用量
✅ **智能切换**：配额低于 5% 时自动切换
✅ **无感体验**：通过代理或配置热更新实现无需重新登录
✅ **安全可靠**：Token 加密、异常处理、切换回滚
✅ **易于部署**：支持本地、Docker、系统服务多种部署方式

**建议实施步骤**：
1. 先实现基础的监控和切换逻辑（1-2 天）
2. 测试 `/status` 命令解析和 token 切换（1 天）
3. 实现代理服务或配置热更新（2-3 天）
4. 完善日志、告警和异常处理（1-2 天）
5. 生产环境测试和优化（1 周）

**预计总开发时间**：2-3 周
