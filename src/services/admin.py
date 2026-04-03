"""Web Admin Interface for Codex Team Switcher."""

import json
import os
import re
import shlex
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template_string, jsonify, request, redirect, url_for
from flask_socketio import SocketIO, emit
from typing import Dict, Any, Optional, List

from src.utils.logger import get_logger


def _is_codex_command(command: str) -> bool:
    """Check if a process command line points to the codex CLI."""
    if not command:
        return False

    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    for token in tokens:
        if os.path.basename(token) == "codex":
            return True

        lowered = token.lower()
        if "@openai/codex" in lowered and lowered.endswith(".js"):
            return True

    return False


def _get_ancestor_pids(pid: int) -> List[int]:
    """Get ancestor PIDs for a process (parent, grandparent, ...)."""
    ancestors: List[int] = []
    current_pid = pid

    while current_pid > 1:
        try:
            result = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(current_pid)],
                capture_output=True,
                text=True,
                check=True,
            )
            parent_pid = int(result.stdout.strip())
        except Exception:
            break

        if parent_pid <= 1 or parent_pid in ancestors:
            break

        ancestors.append(parent_pid)
        current_pid = parent_pid

    return ancestors


def _list_codex_processes(exclude_pid: Optional[int] = None) -> List[int]:
    """
    List PIDs for running codex CLI processes.

    Args:
        exclude_pid: Optional PID to exclude (for safety).

    Returns:
        List of matching process IDs.
    """
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return []

    current_pid = os.getpid()
    excluded_pids = {current_pid, *_get_ancestor_pids(current_pid)}
    if exclude_pid:
        excluded_pids.add(exclude_pid)

    pids: List[int] = []

    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue

        pid_str, command = parts
        try:
            pid = int(pid_str)
        except ValueError:
            continue

        if pid in excluded_pids:
            continue

        if _is_codex_command(command):
            pids.append(pid)

    return pids


def _is_pid_alive(pid: int) -> bool:
    """Check if a process PID is still alive."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def terminate_codex_sessions(grace_period_seconds: float = 2.0) -> Dict[str, Any]:
    """
    Terminate all running codex CLI sessions.

    Sends SIGTERM first, then SIGKILL for any process that remains alive
    after the grace period.
    """
    matched = _list_codex_processes()
    failed: List[Dict[str, Any]] = []
    term_sent: List[int] = []
    already_exited: List[int] = []

    for pid in matched:
        try:
            os.kill(pid, signal.SIGTERM)
            term_sent.append(pid)
        except ProcessLookupError:
            already_exited.append(pid)
        except PermissionError:
            failed.append({"pid": pid, "error": "permission_denied_on_sigterm"})

    deadline = time.time() + max(0.0, grace_period_seconds)
    while time.time() < deadline:
        if not any(_is_pid_alive(pid) for pid in term_sent):
            break
        time.sleep(0.1)

    alive_after_term = [pid for pid in term_sent if _is_pid_alive(pid)]
    terminated_gracefully = [pid for pid in term_sent if pid not in alive_after_term]

    kill_sent: List[int] = []
    for pid in alive_after_term:
        try:
            os.kill(pid, signal.SIGKILL)
            kill_sent.append(pid)
        except ProcessLookupError:
            terminated_gracefully.append(pid)
        except PermissionError:
            failed.append({"pid": pid, "error": "permission_denied_on_sigkill"})

    # Small wait to let SIGKILL take effect.
    if kill_sent:
        time.sleep(0.1)

    terminated_by_kill = [pid for pid in kill_sent if not _is_pid_alive(pid)]
    still_alive = [pid for pid in kill_sent if _is_pid_alive(pid)]
    for pid in still_alive:
        failed.append({"pid": pid, "error": "still_alive_after_sigkill"})

    terminated = sorted(set(already_exited + terminated_gracefully + terminated_by_kill))

    return {
        "matched": len(matched),
        "terminated": terminated,
        "force_killed": sorted(terminated_by_kill),
        "still_alive": sorted(still_alive),
        "failed": failed,
    }


def get_latest_codex_session_id() -> Optional[str]:
    """
    Get the latest local Codex session ID from ~/.codex/sessions.

    Returns:
        Session UUID string if found, otherwise None.
    """
    sessions_root = Path.home() / ".codex" / "sessions"
    if not sessions_root.exists():
        return None

    try:
        candidates = list(sessions_root.rglob("rollout-*.jsonl"))
    except Exception:
        return None

    if not candidates:
        return None

    try:
        latest_file = max(candidates, key=lambda p: p.stat().st_mtime_ns)
    except Exception:
        return None

    match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
        latest_file.name,
        re.IGNORECASE,
    )
    if not match:
        return None

    return match.group(1)


# HTML template for the admin interface
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Codex Team Switcher - 管理界面</title>
    <script src="https://cdn.socket.io/4.7.4/socket.io.min.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f5f5;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 16px 24px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .header h1 {
            font-size: 18px;
            font-weight: 600;
        }
        .header .subtitle {
            opacity: 0.8;
            font-size: 12px;
            margin-top: 2px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        .status-bar {
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        .status-card {
            background: white;
            border-radius: 12px;
            padding: 16px;
            flex: 1;
            min-width: 150px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }
        .status-card h3 {
            font-size: 14px;
            color: #666;
            margin-bottom: 8px;
        }
        .status-card .value {
            font-size: 20px;
            font-weight: 600;
            color: #333;
        }
        .status-card.active .value {
            color: #10b981;
        }
        .status-card.warning .value {
            color: #f59e0b;
        }
        .status-card.error .value {
            color: #ef4444;
        }
        .section {
            background: white;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }
        .section h2 {
            font-size: 18px;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid #eee;
        }
        .team-list {
            display: grid;
            gap: 12px;
        }
        .team-item {
            display: grid;
            grid-template-columns: minmax(220px, 1fr) auto;
            gap: 20px;
            align-items: center;
            padding: 14px 16px;
            background: linear-gradient(180deg, #ffffff 0%, #fbfcff 100%);
            border-radius: 12px;
            border: 1px solid #e7ebf3;
            border-left: 4px solid transparent;
            box-shadow: 0 6px 16px rgba(15, 23, 42, 0.06);
            transition: box-shadow 0.2s ease, transform 0.2s ease;
        }
        .team-item:hover {
            transform: translateY(-1px);
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.1);
        }
        .team-item.active {
            border-left-color: #10b981;
            background: #ecfdf5;
        }
        .team-item.quota-low {
            border-left-color: #f59e0b;
            background: #fffbeb;
        }
        .team-item.expired {
            border-left-color: #ef4444;
            background: #fef2f2;
        }
        .team-item.disabled {
            border-left-color: #9ca3af;
            opacity: 0.6;
        }
        .team-info {
            flex: 1;
        }
        .team-name {
            font-weight: 600;
            font-size: 14px;
            margin-bottom: 2px;
        }
        .team-id {
            font-size: 11px;
            color: #999;
        }
        .team-expiry {
            font-size: 11px;
            color: #666;
            margin-top: 4px;
        }
        .team-workspace {
            font-size: 11px;
            color: #667eea;
            margin-top: 3px;
            font-weight: 500;
        }
        .team-stats {
            display: grid;
            grid-template-columns: 176px 176px 248px;
            gap: 12px;
            align-items: center;
        }
        .stat {
            text-align: left;
            padding: 6px 8px;
        }
        .stat-label {
            font-size: 10px;
            color: #999;
            text-transform: uppercase;
        }
        .stat-value {
            font-size: 14px;
            font-weight: 600;
        }
        .stat-refresh {
            font-size: 10px;
            color: #6b7280;
            margin-top: 2px;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
            display: block;
            width: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .usage-track {
            width: 100%;
        }
        .usage-track .stat-refresh,
        .usage-track .quota-bar {
            width: 100%;
        }
        .quota-bar {
            width: 100%;
            height: 6px;
            background: #e5e7eb;
            border-radius: 3px;
            overflow: hidden;
            margin-top: 4px;
        }
        .quota-fill {
            height: 100%;
            border-radius: 4px;
            transition: width 0.3s;
        }
        .quota-fill.high { background: #10b981; }
        .quota-fill.medium { background: #f59e0b; }
        .quota-fill.low { background: #ef4444; }
        .btn {
            padding: 7px 12px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
            white-space: nowrap;
            transition: all 0.2s;
        }
        .btn-primary {
            background: #667eea;
            color: white;
        }
        .btn-primary:hover {
            background: #5568d3;
        }
        .btn-danger {
            background: #ef4444;
            color: white;
        }
        .btn-danger:hover {
            background: #dc2626;
        }
        .btn-success {
            background: #10b981;
            color: white;
        }
        .btn-success:hover {
            background: #059669;
        }
        .actions {
            display: flex;
            gap: 8px;
            justify-content: flex-end;
        }
        @media (max-width: 1220px) {
            .team-item {
                grid-template-columns: 1fr;
                gap: 14px;
            }
            .team-stats {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .actions {
                grid-column: 1 / -1;
                justify-content: flex-start;
                flex-wrap: wrap;
            }
        }
        @media (max-width: 760px) {
            .team-stats {
                grid-template-columns: 1fr;
            }
            .actions .btn {
                flex: 1;
            }
        }
        .refresh-btn {
            position: fixed;
            bottom: 30px;
            right: 30px;
            width: 50px;
            height: 50px;
            border-radius: 50%;
            background: #667eea;
            color: white;
            border: none;
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
            font-size: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.2s;
        }
        .refresh-btn:hover {
            transform: scale(1.1);
        }
        .refresh-btn.loading {
            animation: spin 1s linear infinite;
        }
        @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        .switch-history {
            max-height: 300px;
            overflow-y: auto;
        }
        .history-item {
            display: flex;
            align-items: center;
            padding: 12px;
            border-bottom: 1px solid #eee;
        }
        .history-item:last-child {
            border-bottom: none;
        }
        .history-time {
            font-size: 12px;
            color: #666;
            width: 150px;
        }
        .history-switch {
            flex: 1;
            font-size: 14px;
        }
        .history-status {
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 12px;
        }
        .history-status.success {
            background: #d1fae5;
            color: #065f46;
        }
        .history-status.failed {
            background: #fee2e2;
            color: #991b1b;
        }
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        .tab {
            padding: 10px 20px;
            background: transparent;
            border: none;
            cursor: pointer;
            font-size: 14px;
            border-bottom: 2px solid transparent;
        }
        .tab.active {
            border-bottom-color: #667eea;
            color: #667eea;
            font-weight: 600;
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
        }
        .info-row {
            display: flex;
            padding: 10px 0;
            border-bottom: 1px solid #eee;
        }
        .info-row:last-child {
            border-bottom: none;
        }
        .info-label {
            width: 150px;
            color: #666;
        }
        .info-value {
            flex: 1;
            font-weight: 500;
        }
        .current-badge {
            background: #10b981;
            color: white;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 11px;
            margin-left: 10px;
        }
        .empty-state {
            text-align: center;
            padding: 40px;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="header">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
                <h1>Codex Team Switcher</h1>
                <div class="subtitle">OpenAI Codex Team 自动切换管理系统</div>
            </div>
            <button class="btn btn-primary" onclick="importAccount()" style="margin-top: 10px;">导入当前账户</button>
        </div>
    </div>

    <div class="container">
        <div class="status-bar">
            <div class="status-card">
                <h3>当前 Team</h3>
                <div class="value" id="currentTeam">-</div>
            </div>
            <div class="status-card active" id="proxyStatus">
                <h3>代理状态</h3>
                <div class="value">运行中</div>
            </div>
            <div class="status-card">
                <h3>活跃 Team</h3>
                <div class="value" id="activeTeams">-</div>
            </div>
            <div class="status-card warning">
                <h3>配额不足</h3>
                <div class="value" id="lowQuotaTeams">-</div>
            </div>
        </div>

        <div class="tabs">
            <button class="tab active" onclick="switchTab('teams')">Team 列表</button>
            <button class="tab" onclick="switchTab('history')">切换历史</button>
            <button class="tab" onclick="switchTab('settings')">系统信息</button>
        </div>

        <div id="teams" class="tab-content active">
            <div class="section">
                <h2>Team 状态</h2>
                <div class="team-list" id="teamList">
                    <div class="empty-state">加载中...</div>
                </div>
            </div>
        </div>

        <div id="history" class="tab-content">
            <div class="section">
                <h2>最近切换记录</h2>
                <div class="switch-history" id="switchHistory">
                    <div class="empty-state">暂无记录</div>
                </div>
            </div>
        </div>

        <div id="settings" class="tab-content">
            <div class="section">
                <h2>系统信息</h2>
                <div id="systemInfo">
                    <div class="empty-state">加载中...</div>
                </div>
            </div>
        </div>
    </div>

    <button class="refresh-btn" onclick="refreshData()" title="刷新数据">↻</button>

    <script>
        let currentData = null;

        function switchTab(tabId) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
            document.getElementById(tabId).classList.add('active');
        }

        function parseTimestamp(value) {
            if (!value) return null;
            const normalized = /[zZ]|[+\\-]\\d{2}:\\d{2}$/.test(value) ? value : `${value}Z`;
            const date = new Date(normalized);
            return Number.isNaN(date.getTime()) ? null : date;
        }

        function formatRefreshTime(value) {
            const date = parseTimestamp(value);
            if (!date) return '-';
            return date.toLocaleString('zh-CN', { hour12: false });
        }

        async function refreshData() {
            const btn = document.querySelector('.refresh-btn');
            btn.classList.add('loading');

            try {
                const response = await fetch('/api/status');
                currentData = await response.json();
                renderData();
            } catch (error) {
                console.error('Failed to fetch data:', error);
            }

            btn.classList.remove('loading');
        }

        async function importAccount() {
            if (!confirm('将从当前登录的 Codex 账户导入 Team 信息。是否继续？')) return;

            try {
                const response = await fetch('/api/import-account', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({})
                });

                const result = await response.json();

                if (result.success) {
                    alert('账户导入成功！');
                    refreshData();
                } else {
                    alert('导入失败: ' + (result.error || '未知错误'));
                }
            } catch (error) {
                console.error('Import failed:', error);
                alert('导入失败: ' + error.message);
            }
        }

        function renderData() {
            if (!currentData) return;

            // Status bar
            const current = currentData.current_team;
            document.getElementById('currentTeam').textContent = current ? current.name : '-';

            const teams = currentData.teams;
            document.getElementById('activeTeams').textContent = teams.active.length;
            document.getElementById('lowQuotaTeams').textContent = teams.quota_low.length;

            // Team list
            renderTeamList(teams);

            // Switch history
            renderSwitchHistory(currentData.switch_history);

            // System info
            renderSystemInfo();
        }

        function renderTeamList(teams) {
            const container = document.getElementById('teamList');
            const currentTeamId = currentData && currentData.current_team ? currentData.current_team.id : null;

            if (teams.active.length === 0 && teams.quota_low.length === 0 &&
                teams.expired.length === 0 && teams.disabled.length === 0) {
                container.innerHTML = '<div class="empty-state">暂无 Team 配置</div>';
                return;
            }

            const teamItems = [];
            teams.active.forEach(team => teamItems.push({team, statusClass: 'active'}));
            teams.quota_low.forEach(team => teamItems.push({team, statusClass: 'quota-low'}));
            // 隐藏已退出的工作空间: 不追加到前端显示列表
            // teams.expired.forEach(team => teamItems.push({team, statusClass: 'expired'}));
            // teams.disabled.forEach(team => teamItems.push({team, statusClass: 'disabled'}));

            const currentItems = [];
            const otherItems = [];
            teamItems.forEach(item => {
                if (currentTeamId && item.team.id === currentTeamId) {
                    currentItems.push(item);
                } else {
                    otherItems.push(item);
                }
            });

            const orderedItems = [...currentItems, ...otherItems];
            let html = '';
            orderedItems.forEach(item => {
                html += renderTeamItem(item.team, item.statusClass, currentTeamId);
            });

            container.innerHTML = html;
        }

        function renderTeamItem(team, statusClass, currentTeamId) {
            // Get 5h and weekly quota percentages
            const quota5h = team.quota ? (team.quota.percentage_5h || team.quota.percentage || 0) : 0;
            const quotaWeekly = team.quota ? (team.quota.percentage_weekly || 0) : 0;
            const refresh5h = team.quota ? formatRefreshTime(team.quota.refresh_at_5h) : '-';
            const refreshWeekly = team.quota ? formatRefreshTime(team.quota.refresh_at_weekly) : '-';
            const quotaClass = quota5h > 20 ? 'high' : quota5h > 5 ? 'medium' : 'low';
            const isCurrent = currentTeamId && team.id === currentTeamId;

            // Get subscription expiry info
            const subscription = team.subscription;
            let expiryDisplay = '';
            if (subscription && subscription.subscription_active_until) {
                const expiryDate = new Date(subscription.subscription_active_until);
                const now = new Date();
                const daysLeft = Math.ceil((expiryDate - now) / (1000 * 60 * 60 * 24));
                const expiryStr = expiryDate.toLocaleDateString('zh-CN');
                const daysText = daysLeft > 0 ? `(${daysLeft}天后)` : '(已过期)';
                expiryDisplay = `<div class="team-expiry" title="计划类型: ${subscription.plan_type || '-'}">到期: ${expiryStr} ${daysText}</div>`;
            }

            return `
                <div class="team-item ${statusClass}">
                    <div class="team-info">
                        <div class="team-name" style="display: flex; align-items: center; gap: 6px;">
                            ${team.name}
                            <button onclick="renameTeam('${team.id}', '${team.name.replace(/'/g, "\\'")}')" title="重命名名称" style="background:none; border:none; cursor:pointer; font-size: 13px; opacity: 0.6; padding: 2px;">✏️</button>
                            ${isCurrent ? '<span class="current-badge">当前</span>' : ''}
                        </div>
                        <div class="team-id">${team.id}</div>
                        ${team.organization_name ? `<div class="team-workspace">🏢 ${team.organization_name}</div>` : ''}
                        ${expiryDisplay}
                    </div>
                    <div class="team-stats">
                        <div class="stat">
                            <div class="stat-label">5小时</div>
                            <div class="stat-value">${quota5h.toFixed(1)}%</div>
                            <div class="usage-track">
                                <div class="stat-refresh" title="刷新时间: ${refresh5h}">刷新: ${refresh5h}</div>
                                <div class="quota-bar">
                                    <div class="quota-fill ${quotaClass}" style="width: ${Math.min(quota5h, 100)}%"></div>
                                </div>
                            </div>
                        </div>
                        <div class="stat">
                            <div class="stat-label">周用量</div>
                            <div class="stat-value">${quotaWeekly.toFixed(1)}%</div>
                            <div class="stat-refresh">刷新: ${refreshWeekly}</div>
                        </div>
                        <div class="actions">
                            ${isCurrent ? '' : `
                                ${(statusClass === 'active' || statusClass === 'quota-low') ? `
                                    <button class="btn btn-primary" onclick="switchToTeam('${team.id}')" style="margin-right: 5px;">切换账号</button>
                                ` : ''}
                                <button class="btn btn-danger" onclick="deleteTeam('${team.id}')" title="删除团队">删除</button>
                            `}
                        </div>
                    </div>
                </div>
            `;
        }

        function renderSwitchHistory(history) {
            const container = document.getElementById('switchHistory');

            if (!history || history.length === 0) {
                container.innerHTML = '<div class="empty-state">暂无切换记录</div>';
                return;
            }

            let html = '';
            history.forEach(item => {
                const time = new Date(item.timestamp).toLocaleString('zh-CN');
                const statusClass = item.success ? 'success' : 'failed';
                const statusText = item.success ? '成功' : '失败';

                html += `
                    <div class="history-item">
                        <div class="history-time">${time}</div>
                        <div class="history-switch">
                            ${item.from_team_id} → ${item.to_team_id}
                            <span style="color: #666; margin-left: 10px;">${item.reason}</span>
                        </div>
                        <div class="history-status ${statusClass}">${statusText}</div>
                    </div>
                `;
            });

            container.innerHTML = html;
        }

        function renderSystemInfo() {
            const container = document.getElementById('systemInfo');

            const current = currentData.current_team;
            const proxyUrl = 'http://127.0.0.1:18888';

            // Get quota values
            const quota5h = current && current.quota ? (current.quota.percentage_5h || current.quota.percentage || 0) : 0;
            const quotaWeekly = current && current.quota ? (current.quota.percentage_weekly || 0) : 0;
            const refresh5h = current && current.quota ? formatRefreshTime(current.quota.refresh_at_5h) : '-';
            const refreshWeekly = current && current.quota ? formatRefreshTime(current.quota.refresh_at_weekly) : '-';

            container.innerHTML = `
                <div class="info-row">
                    <div class="info-label">代理地址</div>
                    <div class="info-value">${proxyUrl}</div>
                </div>
                <div class="info-row">
                    <div class="info-label">代理健康检查</div>
                    <div class="info-value"><a href="${proxyUrl}/health" target="_blank">${proxyUrl}/health</a></div>
                </div>
                <div class="info-row">
                    <div class="info-label">当前 Team</div>
                    <div class="info-value">${current ? current.name : '-'}</div>
                </div>
                <div class="info-row">
                    <div class="info-label">5小时用量</div>
                    <div class="info-value">${quota5h.toFixed(1)}%</div>
                </div>
                <div class="info-row">
                    <div class="info-label">周用量</div>
                    <div class="info-value">${quotaWeekly.toFixed(1)}%</div>
                </div>
                <div class="info-row">
                    <div class="info-label">5小时刷新时间</div>
                    <div class="info-value">${refresh5h}</div>
                </div>
                <div class="info-row">
                    <div class="info-label">周用量刷新时间</div>
                    <div class="info-value">${refreshWeekly}</div>
                </div>
                <div class="info-row">
                    <div class="info-label">已用配额</div>
                    <div class="info-value">${current && current.quota ? current.quota.used : '-'}</div>
                </div>
                <div class="info-row">
                    <div class="info-label">剩余配额</div>
                    <div class="info-value">${current && current.quota ? current.quota.remaining : '-'}</div>
                </div>
            `;
        }

        async function switchToTeam(teamId) {
            if (!confirm('确定要切换账号吗？')) return;

            try {
                const response = await fetch('/api/switch-account', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        team_id: teamId,
                        sync_active_team: true,
                        terminate_codex_sessions: false
                    })
                });

                const result = await response.json();

                if (result.success) {
                    const summary = result.terminated_codex_sessions;
                    const terminatedCount = summary && summary.terminated ? summary.terminated.length : 0;
                    const stillAliveCount = summary && summary.still_alive ? summary.still_alive.length : 0;
                    const resumeCommand = result.resume_command || 'codex resume --last';
                    let message = '切换成功！';
                    if (summary) {
                        message += `\\n已结束 ${terminatedCount} 个 Codex 会话。`;
                        if (stillAliveCount > 0) {
                            message += `\\n仍有 ${stillAliveCount} 个会话未结束，请手动关闭。`;
                        }
                    }
                    message += `\\n如需继续之前会话，请执行：${resumeCommand}`;
                    alert(message);
                    refreshData();
                } else {
                    alert('切换失败: ' + result.error);
                }
            } catch (error) {
                alert('请求失败: ' + error);
            }
        }

        async function deleteTeam(teamId) {
            if (!confirm('确定要删除这个团队吗？此操作不可撤销。')) return;

            try {
                const response = await fetch('/api/delete-team', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({team_id: teamId})
                });

                const result = await response.json();
                if (result.success) {
                    alert('团队已删除');
                    refreshData();
                } else {
                    alert('删除失败: ' + result.error);
                }
            } catch (error) {
                alert('请求失败: ' + error);
            }
        }

        async function renameTeam(teamId, oldName) {
            const newName = prompt('请输入新的展示名称:', oldName);
            if (!newName || newName.trim() === '' || newName === oldName) return;

            try {
                const response = await fetch('/api/rename-team', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({team_id: teamId, new_name: newName.trim()})
                });

                const result = await response.json();
                if (result.success) {
                    refreshData();
                } else {
                    alert('重命名失败: ' + result.error);
                }
            } catch (error) {
                alert('请求失败: ' + error);
            }
        }

        // WebSocket connection for real-time updates
        const socket = io();

        socket.on('connect', () => {
            console.log('WebSocket connected');
        });

        socket.on('usage_update', (data) => {
            console.log('Received usage update:', data);
            currentData = data;
            renderData();
        });

        socket.on('disconnect', () => {
            console.log('WebSocket disconnected');
        });

        // Initial load
        refreshData();
    </script>
</body>
</html>
"""


class AdminInterface:
    """
    Web Admin Interface for Codex Team Switcher.

    Provides a Flask-based web interface for:
    - Viewing team status
    - Manual team switching
    - Viewing switch history
    """

    def __init__(self, app_handler, host: str = "0.0.0.0", port: int = 18080):
        """
        Initialize the admin interface.

        Args:
            app_handler: CodexTeamSwitcher instance.
            host: Host to bind to.
            port: Port to listen on (separate from proxy).
        """
        self._app_handler = app_handler
        self._host = host
        self._port = port
        self._logger = get_logger(__name__)

        self._app = Flask(__name__)
        self._socketio = SocketIO(self._app, cors_allowed_origins="*")
        self._setup_routes()
        self._setup_websocket()

    def _setup_routes(self) -> None:
        """Set up Flask routes for admin interface."""

        @self._app.route("/")
        def index():
            """Main admin page."""
            return render_template_string(ADMIN_HTML)

        @self._app.route("/api/status")
        def api_status():
            """Get current system status."""
            try:
                status = self._app_handler.get_status()
                return jsonify(status)
            except Exception as e:
                self._logger.error("api_status_error", error=str(e))
                return jsonify({"error": str(e)}), 500

        @self._app.route("/api/switch", methods=["POST"])
        def api_switch():
            """Switch to a specific team."""
            try:
                data = request.get_json()
                team_id = data.get("team_id")

                if not team_id:
                    return jsonify({"success": False, "error": "team_id required"}), 400

                switcher = getattr(self._app_handler, "_team_switcher", None)
                if not switcher:
                    return jsonify({"success": False, "error": "TeamSwitcher not initialized"}), 500

                success = switcher.switch_to_team(team_id, reason="manual")

                if success:
                    return jsonify({"success": True})
                else:
                    return jsonify({"success": False, "error": "Switch failed"})

            except Exception as e:
                self._logger.error("api_switch_error", error=str(e))
                return jsonify({"success": False, "error": str(e)}), 500

        @self._app.route("/api/rename-team", methods=["POST"])
        def api_rename_team():
            """Rename a team display name."""
            try:
                data = request.get_json()
                team_id = data.get("team_id")
                new_name = data.get("new_name")

                if not team_id or not new_name:
                    return jsonify({"success": False, "error": "team_id and new_name required"}), 400

                token_manager = getattr(self._app_handler, "_token_manager", None)
                if not token_manager:
                    return jsonify({"success": False, "error": "TokenManager not initialized"}), 500

                success = token_manager.update_team_name(team_id, new_name)
                
                if success:
                    return jsonify({"success": True})
                else:
                    return jsonify({"success": False, "error": "Team not found or invalid name"})

            except Exception as e:
                self._logger.error("api_rename_team_error", error=str(e))
                return jsonify({"success": False, "error": str(e)}), 500

        @self._app.route("/api/check", methods=["POST"])
        def api_check():
            """Trigger a manual usage check."""
            try:
                monitor = getattr(self._app_handler, "_usage_monitor", None)
                if not monitor:
                    return jsonify({"error": "UsageMonitor not initialized"}), 500

                result = monitor.run_single_check()
                return jsonify({
                    "success": result.success,
                    "team_id": result.team_id,
                    "usage": {
                        "percentage": result.usage.percentage if result.usage else None
                    } if result.usage else None,
                    "error": result.error
                })
            except Exception as e:
                self._logger.error("api_check_error", error=str(e))
                return jsonify({"error": str(e)}), 500

        @self._app.route("/api/codex-status", methods=["GET"])
        def api_codex_status():
            """Get current Codex login status."""
            try:
                token_manager = getattr(self._app_handler, "_token_manager", None)
                if not token_manager:
                    return jsonify({"error": "TokenManager not initialized"}), 500

                status = token_manager.get_codex_status()
                return jsonify(status)
            except Exception as e:
                self._logger.error("api_codex_status_error", error=str(e))
                return jsonify({"error": str(e)}), 500

        @self._app.route("/api/import-account", methods=["POST"])
        def api_import_account():
            """Import current Codex account as a team."""
            try:
                token_manager = getattr(self._app_handler, "_token_manager", None)
                if not token_manager:
                    return jsonify({"success": False, "error": "TokenManager not initialized"}), 500

                data = request.get_json() or {}
                name = data.get("name")

                team = token_manager.import_current_codex_account(name=name)
                if team:
                    return jsonify({
                        "success": True,
                        "team": team.to_dict()
                    })
                else:
                    return jsonify({
                        "success": False,
                        "error": "Not logged in to Codex. Please run 'codex login' first."
                    }), 400
            except Exception as e:
                self._logger.error("api_import_account_error", error=str(e))
                return jsonify({"success": False, "error": str(e)}), 500

        @self._app.route("/api/switch-account", methods=["POST"])
        def api_switch_account():
            """Switch to a specific team account."""
            try:
                token_manager = getattr(self._app_handler, "_token_manager", None)
                if not token_manager:
                    return jsonify({"success": False, "error": "TokenManager not initialized"}), 500

                data = request.get_json() or {}
                team_id = data.get("team_id")
                sync_active_team = bool(data.get("sync_active_team", True))
                terminate_sessions = bool(data.get("terminate_codex_sessions", False))

                if not team_id:
                    return jsonify({"success": False, "error": "team_id required"}), 400

                success = token_manager.switch_to_team(team_id)
                if not success:
                    return jsonify({
                        "success": False,
                        "error": f"Failed to switch account for team: {team_id}",
                    }), 400

                if sync_active_team:
                    switcher = getattr(self._app_handler, "_team_switcher", None)
                    if switcher:
                        switcher.set_current_team(team_id)

                latest_session_id = get_latest_codex_session_id()
                resume_command = (
                    f"codex resume {latest_session_id}"
                    if latest_session_id
                    else "codex resume --last"
                )

                terminated_summary = None
                if terminate_sessions:
                    terminated_summary = terminate_codex_sessions()
                    self._logger.info(
                        "codex_sessions_terminated",
                        team_id=team_id,
                        matched=terminated_summary["matched"],
                        terminated=len(terminated_summary["terminated"]),
                        force_killed=len(terminated_summary["force_killed"]),
                        still_alive=len(terminated_summary["still_alive"]),
                    )

                return jsonify({
                    "success": True,
                    "team_id": team_id,
                    "terminated_codex_sessions": terminated_summary,
                    "resume_command": resume_command,
                    "resume_session_id": latest_session_id,
                })
            except Exception as e:
                self._logger.error("api_switch_account_error", error=str(e))
                return jsonify({"success": False, "error": str(e)}), 500

        @self._app.route("/api/delete-team", methods=["POST"])
        def api_delete_team():
            """Delete a team."""
            try:
                data = request.get_json()
                team_id = data.get("team_id")

                if not team_id:
                    return jsonify({"success": False, "error": "team_id required"}), 400

                token_manager = getattr(self._app_handler, "_token_manager", None)
                if not token_manager:
                    return jsonify({"success": False, "error": "TokenManager not initialized"}), 500

                success = token_manager.delete_team(team_id)
                return jsonify({"success": success})
            except Exception as e:
                self._logger.error("api_delete_team_error", error=str(e))
                return jsonify({"success": False, "error": str(e)}), 500

    def _setup_websocket(self) -> None:
        """Set up WebSocket event handlers."""

        @self._socketio.on("connect")
        def handle_connect():
            """Handle client connection."""
            self._logger.info("websocket_client_connected")
            emit("connected", {"status": "ok"})

        @self._socketio.on("disconnect")
        def handle_disconnect():
            """Handle client disconnection."""
            self._logger.info("websocket_client_disconnected")

    def broadcast_usage_update(self, data: dict) -> None:
        """
        Broadcast usage update to all connected clients.

        Args:
            data: The usage data to broadcast.
        """
        self._logger.info("broadcasting_usage_update", data_keys=list(data.keys()))
        self._socketio.emit("usage_update", data)

    def start(self, blocking: bool = True) -> None:
        """
        Start the admin interface server.

        Args:
            blocking: If True, runs in blocking mode.
        """
        self._logger.info(
            "starting_admin_interface",
            host=self._host,
            port=self._port,
            url=f"http://localhost:{self._port}",
        )

        if blocking:
            self._socketio.run(
                self._app,
                host=self._host,
                port=self._port,
                allow_unsafe_werkzeug=True,
            )
        else:
            import threading
            thread = threading.Thread(
                target=self._socketio.run,
                kwargs={
                    "app": self._app,
                    "host": self._host,
                    "port": self._port,
                    "allow_unsafe_werkzeug": True,
                },
                daemon=True,
            )
            thread.start()
            self._logger.info("admin_interface_started_background")

    @property
    def url(self) -> str:
        """Get the admin interface URL."""
        return f"http://localhost:{self._port}"
