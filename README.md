# Hermes MCP Bridge

**Remote Linux machine control via MCP over HTTP/WireGuard.** Run this on any Linux machine you want Hermes Agent to manage — execute commands, manage Docker, check services, browse files, and monitor system health — all over an encrypted WireGuard mesh with Bearer token auth.

- **Author:** okazakee
- **Version:** 1.0.0
- **License:** MIT

## What Problem It Solves

Hermes Agent can control the machine it runs on, but what about your other machines — Raspberry Pis, home servers, remote VPS instances, laptops? The MCP Bridge gives Hermes Agent remote control over **any Linux machine** via a lightweight HTTP API. Pair it with WireGuard for a secure, encrypted control plane between all your machines.

## Features

- **9 MCP-compatible tools** exposed over HTTP:
  - `list_directory` — List files in a directory
  - `read_file` — Read a file's contents
  - `write_file` — Write/overwrite a file
  - `search_files` — Search for patterns inside files (grep)
  - `execute_command` — Execute shell commands (allowlist-enforced)
  - `docker_ps` — List Docker containers
  - `docker_logs` — Get container logs
  - `system_info` — CPU, RAM, disk, uptime, load
  - `service_status` — Check systemd service status
- **Bearer token auth** on every endpoint (except `/health`, `/ping`)
- **Allowlist-based command execution** — only approved binaries can run
- **MCP SSE transport** — Server-Sent Events endpoint for streaming MCP
- **systemd integration** — one `deploy.sh` and you have a production service
- **Structured logging** — every tool call logged to stderr/journald with timestamp and status
- **Zero hardcoded IPs** — all configuration via environment variables

## Prerequisites

- **Python 3.10+** with `venv` support
- **systemd-based Linux** (Debian, Ubuntu, Fedora, Arch, etc.)
- **(Recommended) WireGuard mesh** between your Hermes machine and target machines
- **Hermes Agent** running on your central/control machine

## Quick Start

### 1. Clone the repo on each target machine

```bash
git clone https://github.com/okazakee/hermes-mcp-bridge.git
cd hermes-mcp-bridge
```

### 2. Deploy

```bash
sudo bash deploy.sh
```

This creates:

- `/opt/hermes-mcp-bridge/server.py` — the server
- `/opt/hermes-mcp-bridge/.venv/` — Python virtualenv with FastAPI + uvicorn
- `/opt/hermes-mcp-bridge/.env` — configuration (from `.env.example`)
- `/etc/systemd/system/hermes-mcp-bridge.service` — systemd unit

To customize the install directory:

```bash
MCP_BRIDGE_DIR=/srv/mcp-bridge sudo bash deploy.sh
```

### 3. Configure

Edit `/opt/hermes-mcp-bridge/.env` (or wherever you installed):

```bash
# REQUIRED: Set a strong random token
# Generate: openssl rand -hex 32
MCP_BRIDGE_TOKEN=your-strong-random-token-here

# REQUIRED for remote access: Bind to this machine's IP
# Set to your WireGuard IP (e.g. 10.0.0.5) or 0.0.0.0
MCP_BRIDGE_HOST=10.0.0.5

# Optional: Customize port, allowlist, timeout
MCP_BRIDGE_PORT=8000
MCP_EXECUTE_ALLOWLIST=docker,systemctl,journalctl,df,free,ls,cat,grep,ps
MCP_TOOL_TIMEOUT=30
```

### 4. Start

```bash
sudo systemctl start hermes-mcp-bridge
sudo systemctl enable hermes-mcp-bridge   # auto-start on boot
sudo systemctl status hermes-mcp-bridge   # verify it's running
```

### 5. Test

```bash
# Health check (no auth required)
curl http://10.0.0.5:8000/health

# List available tools
curl -H "Authorization: Bearer YOUR_TOKEN" http://10.0.0.5:8000/tools

# Call a tool
curl -X POST http://10.0.0.5:8000/call_tool \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tool": "system_info", "args": {}}'
```

### 6. Configure Hermes Agent

Add the bridge as an MCP server in your Hermes Agent `config.yaml` so it can discover and call tools on your remote machines.

```yaml
# Hermes Agent config.yaml
mcp_servers:
  # ── Example: Home NAS on WireGuard IP 10.0.0.5 ──
  nas:
    type: "http"
    url: "http://10.0.0.5:8000"
    transport: "sse"
    headers:
      Authorization: "Bearer ***
    description: "Home NAS — Docker, services, file management"

  # ── Example: VPS on WireGuard IP 10.0.0.10 ──
  vps:
    type: "http"
    url: "http://10.0.0.10:8000"
    transport: "sse"
    headers:
      Authorization: "Bearer ***
    description: "Cloud VPS — web services, monitoring"
```

#### Tool Naming

Tools from MCP servers are prefixed as `mcp_<server_name>_<tool_name>`. For example:

| Bridge Tool | Hermes Tool Name |
|-------------|-----------------|
| `system_info` | `mcp_nas_system_info` |
| `docker_ps` | `mcp_nas_docker_ps` |
| `execute_command` | `mcp_nas_execute_command` |
| `service_status` | `mcp_nas_service_status` |
| `read_file` | `mcp_nas_read_file` |

#### Verifying Connectivity

After restarting Hermes Agent, verify it sees your bridge tools:

```bash
# List all MCP tools visible to Hermes
hermes tools | grep mcp_

# Should show entries like:
#   mcp_nas_system_info
#   mcp_nas_docker_ps
#   mcp_nas_execute_command
#   ...

# Test a call directly
hermes run mcp_nas_system_info
```

#### ⚠️ Safety: Exclude `execute_command` Per Machine

The `execute_command` tool runs shell commands on the target machine. Consider disabling it on machines where you don't need it:

```yaml
# Restrict tools per server (if Hermes supports tool filtering):
mcp_servers:
  nas:
    type: "http"
    url: "http://10.0.0.5:8000"
    transport: "sse"
    headers:
      Authorization: "Bearer ***
    # Only expose safe read-only tools
    exclude_tools:
      - execute_command
      - write_file
```

Alternatively, restrict the allowlist on the target machine's `.env`:

```bash
# On the target machine, set a minimal allowlist:
MCP_EXECUTE_ALLOWLIST=docker,systemctl,journalctl
```

This gives you fine-grained control over what each machine allows without changing your Hermes config.

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MCP_BRIDGE_TOKEN` | **yes** | — | Bearer token for all authenticated requests |
| `MCP_BRIDGE_HOST` | no | `127.0.0.1` | IP address to bind to (set to WireGuard IP for remote access) |
| `MCP_BRIDGE_PORT` | no | `8000` | HTTP port to listen on |
| `MCP_EXECUTE_ALLOWLIST` | no | `docker,systemctl,...` | Comma-separated list of allowed binaries (supports fnmatch globs like `docker*`) |
| `MCP_TOOL_TIMEOUT` | no | `30` | Default timeout in seconds for tool commands |

## Architecture

```
┌─────────────────────┐                              ┌──────────────────────┐
│   Hermes Machine     │                              │   Target Machine      │
│   (Control Plane)    │                              │   (Any Linux host)    │
│                      │     WireGuard mesh           │                       │
│  ┌────────────────┐  │   10.0.0.0/24                │  ┌─────────────────┐  │
│  │ Hermes Agent   │──│──── HTTP :8000 ──────────────│─▶│ MCP Bridge      │  │
│  │ (MCP Client)   │  │    Bearer auth               │  │ server.py       │  │
│  └────────────────┘  │                              │  │                 │  │
│                      │                              │  │ Tools:          │  │
│  IP: 10.0.0.1        │                              │  │ • execute_cmd   │  │
└─────────────────────┘                              │  │ • docker_ps     │  │
                                                      │  │ • system_info   │  │
                                                      │  │ • service_status│  │
                                                      │  │ • file ops...   │  │
                                                      │  └─────────────────┘  │
                                                      │                       │
                                                      │  IP: 10.0.0.X         │
                                                      └──────────────────────┘
```

- **Control plane:** Hermes Agent on one machine acts as MCP client
- **Data plane:** WireGuard provides encrypted connectivity between machines
- **Bridge:** `server.py` runs on each target, bound to its WireGuard IP
- **Auth:** Every tool call requires a Bearer token; health checks are open

Add as many target machines as you want — each runs its own bridge instance.

## Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `list_directory` | List files in a directory (`ls -la`) | `path` (default: `.`) |
| `read_file` | Read a file's contents | `path` |
| `write_file` | Write/overwrite a file | `path`, `content` |
| `search_files` | Search for a pattern inside files (grep) | `pattern`, `path`, `file_glob`, `recursive` |
| `execute_command` | Execute a shell command (allowlist-enforced) | `command`, `timeout` |
| `docker_ps` | List Docker containers | `all` (default: `false`) |
| `docker_logs` | Get container logs | `container`, `tail` (default: `100`) |
| `system_info` | CPU, RAM, disk, uptime, load | _(none)_ |
| `service_status` | Check systemd service status | `service` |

## API

### `GET /health` / `GET /ping`
No auth required. Returns health/ping status.

### `GET /tools`
Auth required. Returns the list of available tools.

### `POST /call_tool`
Auth required. Invoke a tool.

```json
{
  "tool": "execute_command",
  "args": {
    "command": "df -h",
    "timeout": 10
  }
}
```

### `GET /sse`
Auth required. Server-Sent Events endpoint for MCP streaming transport.

## Security

- **Bearer token auth** on every authenticated request — token sent via `Authorization: Bearer <token>` header
- **Allowlist enforcement** — `execute_command` validates the binary name against a configurable allowlist with fnmatch glob support
- **Bind to specific IP** — by default binds to `127.0.0.1`; configure `MCP_BRIDGE_HOST` to a WireGuard IP for controlled exposure
- **No `0.0.0.0` by default** — you must explicitly choose to expose on all interfaces
- **Audit logging** — every tool call is logged to stderr/journald with timestamp, tool name, arguments, and success/failure status
- **Systemd hardening** — the included systemd unit uses `NoNewPrivileges`, `ProtectSystem=full`, and `ProtectHome=read-only`

### Recommended Setup

For maximum security, pair this with WireGuard:

1. Create a WireGuard mesh between your Hermes machine and all targets
2. Set `MCP_BRIDGE_HOST` to the machine's WireGuard IP on each target
3. Firewall the MCP Bridge port (`8000`) to only accept traffic from the WireGuard interface
4. Use a unique, strong `MCP_BRIDGE_TOKEN` on each machine (or share one across your mesh)

## Troubleshooting

```bash
# Check service logs
journalctl -u hermes-mcp-bridge -f

# Check tool call logs (stderr → journald)
journalctl -u hermes-mcp-bridge -o cat

# Verify it's listening
ss -tlnp | grep 8000

# Test locally
curl http://localhost:8000/health

# Test with auth
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8000/tools
```

## Files

```
hermes-mcp-bridge/
├── server.py           # Main server (FastAPI + uvicorn)
├── deploy.sh           # Deployment script (creates venv + systemd service)
├── .env.example        # Configuration template
├── requirements.txt    # Python dependencies
├── README.md
├── LICENSE
└── .gitignore
```

## Contributing

Contributions welcome! Please open an issue or PR on GitHub.

## License

MIT. See [LICENSE](./LICENSE) for full text.
