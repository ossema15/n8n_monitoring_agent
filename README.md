# n8n Monitoring Agent — Workflow Documentation

> AI-powered infrastructure monitoring with human-in-the-loop command execution over Prometheus, Grafana, and Windows machines via WinRM.

---

## Overview

The automation layer consists of **3 n8n workflows** that work together:

| Workflow | Purpose |
|----------|---------|
| `monitoring_agent_workflow` | Main workflow — triggers, AI agent, tool orchestration |
| `sub_prometheus_query` | Sub-workflow — executes PromQL queries against Prometheus |
| `sub_winrm_execute` | Sub-workflow — safely executes PowerShell commands on Windows machines |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    MAIN WORKFLOW                         │
│                                                         │
│  Chat Trigger ──┐                                       │
│  Alertmanager ──┼──► AI Agent (Claude) ──► Response    │
│  Schedule    ──┘         │                              │
│                    ┌─────┴──────┐                       │
│                    ▼            ▼                       │
│             Sub: Prometheus  Sub: WinRM                 │
└─────────────────────────────────────────────────────────┘
```

---

## Workflow 1 — Main Workflow

**File:** `monitoring_agent_workflow.json`

### Triggers

| Trigger | Description |
|---------|-------------|
| Chat Trigger | Interactive queries via n8n chat UI |
| Alertmanager Webhook | Fires automatically when Prometheus alert thresholds are breached |
| Schedule | Runs a proactive health check every 10 minutes |

### Nodes

```
Chat Trigger ──────────────────────────────────────────► AI Agent
Alertmanager Webhook ──► Normalize Alert Payload ──────► AI Agent
                     └──► Alertmanager Response (immediate 200 OK)
Schedule ──► Build Health Check Prompt ────────────────► AI Agent

AI Agent ◄──── Claude Chat Model (claude-opus-4-5)
         ◄──── Agent Window Memory
         ◄──── Tool: Query Prometheus
         ◄──── Tool: Execute WinRM Command
         ◄──── Tool: Get Incident Memory
         ◄──── Tool: Save Incident Memory
```

### AI Agent Tools

| Tool | Sub-workflow | Description |
|------|-------------|-------------|
| `query_prometheus` | `sub_prometheus_query` | Run PromQL queries against Prometheus |
| `execute_winrm_command` | `sub_winrm_execute` | Execute PowerShell on Windows machines |
| `get_incident_memory` | Code node | Retrieve past incidents from local JSON log |
| `save_incident_memory` | Code node | Save new incidents and resolutions |

### Agent Behavior

The agent follows this decision loop:

1. Query Prometheus for metric context
2. Diagnose the root cause
3. Propose a remediation command with target, reason, and risk level
4. **Wait for human approval** (`yes` / `no`)
5. Execute only after approval
6. Report output back in chat
7. Save significant incidents to memory

### Incident Memory

Stored at `/home/node/.n8n/incident_memory.json` inside the n8n container. Holds up to 200 entries. Each entry contains a timestamp and a plain-text summary of the incident and resolution.

---

## Workflow 2 — Sub: Prometheus Query

**File:** `sub_prometheus_query.json`

### Purpose

Executes PromQL queries against the Prometheus HTTP API and formats the results into readable text that the AI agent can reason about.

### Inputs

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `promql` | String | ✅ | PromQL expression to execute |
| `time_range` | String | ❌ | Optional range e.g. `5m`, `15m`, `1h`. If provided, runs a range query instead of instant |

### Nodes

```
Workflow Input ──► Prometheus HTTP Request ──► Format Result
```

### Prometheus HTTP Request

- **Method:** GET
- **URL:** `http://prometheus:9090/api/v1/query` or `query_range`
- Auto-detects instant vs range based on `time_range` input

### Output

Returns a formatted string of metric results:

```
Prometheus query successful (2 series, type=vector):
  windows_cpu_time_total{instance="machine1"} = 87.4320
  windows_cpu_time_total{instance="machine2"} = 19.0910
```

If no data is returned, the agent is informed the metric may not exist or the target is down.

---

## Workflow 3 — Sub: WinRM Execute

**File:** `sub_winrm_execute.json`

### Purpose

Safely executes PowerShell commands on monitored Windows machines via a local WinRM proxy service. Includes a safety filter that blocks destructive commands before they reach the target machine.

### Inputs

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `target_host` | String | ✅ | Machine name or IP (`machine1`, `machine2`, `192.168.0.20`) |
| `command` | String | ✅ | PowerShell command to execute |
| `command_type` | String | ❌ | `powershell` or `cmd` (defaults to `powershell`) |

### Nodes

```
Workflow Input ──► Prepare & Safety Check ──► Check Not Blocked ──► HTTP Request (WinRM Proxy)
                                                      │
                                                      └──► Return Blocked Message
```

### Safety Filter

Commands are blocked if they contain any of the following patterns:

```
format-volume  |  format c:   |  del /s /q
rm -rf         |  remove-item -recurse -force
fdisk          |  diskpart     |  reg delete
rd /s /q       |  rmdir /s     |  cipher /w
bcdedit        |  shutdown /r  |  shutdown /s
restart-computer
```

Blocked commands return an error message immediately without reaching WinRM.

### Host Resolution

The safety check node resolves machine names to IPs automatically:

| Input | Resolved IP |
|-------|------------|
| `machine1`, `machine 1`, `pc1` | `192.168.0.20` |
| `machine2`, `machine 2`, `pc2` | `192.168.0.252` |

### WinRM Proxy

The HTTP Request node calls a local Python proxy service running on the WSL host:

- **URL:** `http://host.docker.internal:5001/run`
- **Method:** POST
- **Body:**
```json
{
  "target_host": "192.168.0.20",
  "command": "Get-Process | Sort-Object CPU -Descending | Select-Object -First 15",
  "command_type": "powershell"
}
```

The proxy (`winrm_proxy.py`) runs on WSL using `pywinrm` + Flask and handles the full WinRM authentication and execution, returning clean JSON.

### Output

```json
{
  "result": "✅ Output:\nName          Id    CPU  WorkingSet\n..."
}
```

---

## Supported Commands

### Diagnostics — read-only, no special risk

```powershell
# Top CPU processes
Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 | Format-Table Name,Id,CPU,WorkingSet -AutoSize

# Top memory processes
Get-Process | Sort-Object WorkingSet -Descending | Select-Object -First 15 | Format-Table Name,Id,CPU,WorkingSet -AutoSize

# Disk usage
Get-PSDrive | Where-Object {$_.Used -gt 0} | Select-Object Name,Used,Free

# Stopped services
Get-Service | Where-Object {$_.Status -eq 'Stopped'} | Select-Object Name,DisplayName,Status

# Network connections
netstat -ano | findstr LISTENING

# Network config
ipconfig /all

# Recent system errors
Get-EventLog -LogName System -Newest 20 -EntryType Error,Warning | Select-Object TimeGenerated,Source,Message

# Connectivity test
Test-Connection -ComputerName <host> -Count 4
Test-NetConnection -ComputerName <host> -Port <port>
```

### Remediation — always require human approval

```powershell
# Restart a service
Restart-Service -Name <ServiceName> -Force

# Kill a process
Stop-Process -Id <PID> -Force

# Flush DNS
ipconfig /flushdns

# Stop / start a service
net stop <service>
net start <service>
```

---

## Prerequisites

### Infrastructure

- Prometheus running at `http://prometheus:9090`
- Windows Exporter running on monitored machines (port `9182`)
- Alertmanager configured to POST to `http://n8n:5678/webhook/alertmanager-webhook`
- All containers on the same Docker `monitoring` network
- n8n also connected to the `monitoring` network: `docker network connect monitoring n8n`

### WinRM on each Windows machine

```powershell
Enable-PSRemoting -Force -SkipNetworkProfileCheck
winrm set winrm/config/service/auth '@{Basic="true"}'
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\WSMAN\Service" /v "allow_unencrypted" /t REG_DWORD /d 1 /f
winrm set winrm/config/winrs '@{AllowRemoteShellAccess="true"}'
Restart-Service WinRM
```

### WinRM Proxy on WSL

```bash
python3 -m venv ~/winrm_env
source ~/winrm_env/bin/activate
pip install pywinrm flask

# Start proxy
nohup python3 ~/winrm_proxy.py > ~/winrm_proxy.log 2>&1 &
```

### n8n Credentials

| Credential | Type | Used by |
|-----------|------|---------|
| Anthropic API Key | Anthropic | Claude Chat Model node |
| WinRM Basic Auth | HTTP Basic Auth | (legacy — replaced by proxy) |

---

## Limitations

- **WSL dependency** — the WinRM proxy must be running in WSL for command execution to work. Prometheus querying and alerting continue to function if the proxy is down.
- **One machine at a time** — commands execute on one target per request. Bulk execution across multiple machines is not supported.
- **Output truncation** — command outputs are truncated at 2000 characters before being returned to the agent.
- **No autonomous remediation** — the agent cannot execute commands without explicit human approval. This is by design.
- **Incident memory** — stored in a flat JSON file inside the n8n container. Not a database. Capped at 200 entries.
- **Session memory** — the Agent Window Memory resets when n8n restarts. Long-term context comes from the incident memory file only.
