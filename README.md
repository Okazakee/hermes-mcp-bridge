# Hermes MCP Bridge

**Remote Linux machine control via MCP over HTTP.** Run this on any Linux machine you want Hermes Agent to manage. Execute shell commands, manage Docker containers, check systemd services, browse files, and monitor system health over an encrypted WireGuard mesh with Bearer token authentication.

- **Author:** okazakee
- **Version:** 1.1.0
- **License:** MIT

---

## What It Solves

Hermes Agent controls the machine it runs on, but what about your other machines? Raspberry Pis in the garage, home servers in the basement, VPS instances in the cloud, laptops on the move. The MCP Bridge extends Hermes Agent's reach to **any Linux machine** through a lightweight HTTP API. Pair it with WireGuard and you have a secure, encrypted control plane across your entire infrastructure.

---

## Features

- **9 MCP-compatible tools** exposed over HTTP and MCP JSON-RPC
  - `list_directory` - List files in a directory
  - `read_file` - Read a file's contents
  - `write_file` - Write or overwrite a file
  - `search_files` - Search for patterns inside files (grep)
  - `execute_command` - Execute shell commands (allowlist-enforced)
  - `docker_ps` - List Docker containers
  - `docker_logs` - Get container logs
  - `system_info` - CPU, RAM, disk, uptime, load average
  - `service_status` - Check systemd service status
- **MCP JSON-RPC transport** - Full `initialize`, `tools/list`, `tools/call` protocol support over HTTP POST
- **MCP SSE transport** - Server-Sent Events with endpoint discovery for streaming MCP clients
- **Bearer token authentication** on every protected endpoint
- **Allowlist-based command execution** - only approved binaries can run, with fnmatch glob support
- **systemd integration** - one `deploy.sh` and you have a production service with hardening
- **Structured audit logging** - every tool call logged to stderr/journald with timestamp, tool name, arguments, and status
- **Zero hardcoded IPs** - all configuration via environment variables

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| Python | 3.10 or later with `venv` module |
| Operating system | Any systemd-based Linux (Debian, Ubuntu, Fedora, Arch, etc.) |
| WireGuard | Recommended for secure remote access between machines |
| Hermes Agent | Running on your control machine with MCP server support |

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/Okazakee/hermes-mcp-bridge.git
cd hermes-mcp-bridge
```

You can clone directly into the install directory if you prefer:

```bash
sudo git clone https://github.com/Okazakee/hermes-mcp-bridge.git /opt/hermes-mcp-bridge
cd /opt/hermes-mcp-bridge
```

### 2. Deploy

```bash
sudo bash deploy.sh
```

This creates:

| Path | Purpose |
|------|---------|
| `/opt/hermes-mcp-bridge/server.py` | Main server (FastAPI + uvicorn) |
| `/opt/hermes-mcp-bridge/.venv/` | Python virtual environment with dependencies |
| `/opt/hermes-mcp-bridge/.env` | Runtime configuration (copied from `.env.example`) |
| `/etc/systemd/system/hermes-mcp-bridge.service` | systemd unit file |

To use a custom install directory:

```bash
MCP_BRIDGE_DIR=/srv/mcp-bridge sudo bash deploy.sh
```

### 3. Configure

Edit the `.env` file at your install location:

```bash
# Required: Set a strong random token
# Generate with: openssl rand -hex 32
MCP_BRIDGE_TOKEN=a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4

# Required for remote access: Bind to this machine's IP
# Use the WireGuard IP (e.g. 10.0.0.5) or 0.0.0.0 for all interfaces
MCP_BRIDGE_HOST=10.0.0.5

# Optional: Customize port (default: 8000)
MCP_BRIDGE_PORT=8000

# Optional: Restrict which binaries execute_command can run
MCP_EXECUTE_ALLOWLIST=docker,systemctl,journalctl,df,free,ls,cat,grep,ps

# Optional: Default timeout for commands in seconds (default: 30)
MCP_TOOL_TIMEOUT=30
```

**Important:** Set `MCP_BRIDGE_HOST` to this machine's IP address. The default `127.0.0.1` only accepts local connections. For remote access, use the machine's WireGuard IP or `0.0.0.0`.

### 4. Start the service

```bash
sudo systemctl start hermes-mcp-bridge
sudo systemctl enable hermes-mcp-bridge    # auto-start on boot
sudo systemctl status hermes-mcp-bridge    # verify it is running
```

### 5. Test connectivity

```bash
# Health check (no authentication required)
curl http://10.0.0.5:8000/health

# List available tools (authentication required)
curl -H "Authorization: Bearer YOUR_TOKEN" http://10.0.0.5:8000/tools

# Call a tool (authentication required)
curl -X POST http://10.0.0.5:8000/call_tool \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tool": "system_info", "args": {}}'
```

### 6. Configure Hermes Agent

Add the bridge as an MCP server in your Hermes Agent configuration so it can discover and call tools on remote machines:

```yaml
# Hermes Agent config.yaml
mcp_servers:
  # Example: Home NAS on WireGuard IP 10.0.0.5
  nas:
    url: "http://10.0.0.5:8000/"
    headers:
      Authorization: "Bearer YOUR_TOKEN"
    timeout: 30

  # Example: Cloud VPS on WireGuard IP 10.0.0.10
  vps:
    url: "http://10.0.0.10:8000/"
    headers:
      Authorization: "Bearer YOUR_TOKEN"
    timeout: 30
```

Tools from MCP servers are prefixed as `mcp_<server_name>_<tool_name>`. For example, with a server named `nas`:

| Bridge Tool | Hermes Tool Name |
|-------------|-----------------|
| `system_info` | `mcp_nas_system_info` |
| `docker_ps` | `mcp_nas_docker_ps` |
| `execute_command` | `mcp_nas_execute_command` |
| `service_status` | `mcp_nas_service_status` |
| `read_file` | `mcp_nas_read_file` |

Verify connectivity after restarting Hermes Agent:

```bash
# List all MCP tools visible to Hermes
hermes tools | grep mcp_

# Test a call directly
hermes run mcp_nas_system_info
```

---

## Architecture

```
+-----------------------+                              +-----------------------+
|   Hermes Machine       |                              |   Target Machine       |
|   (Control Plane)      |                              |   (Any Linux Host)     |
|                        |     WireGuard Mesh           |                        |
|  +------------------+  |   10.0.0.0/24                |  +------------------+  |
|  |  Hermes Agent    |  |   HTTP :8000                |  |  MCP Bridge      |  |
|  |  (MCP Client)    |--|--- Bearer Auth -------------|->|  server.py       |  |
|  +------------------+  |                              |  |                  |  |
|                        |                              |  |  Tools:          |  |
|  IP: 10.0.0.1          |                              |  |  - list_directory|  |
+-----------------------+                              |  |  - read_file     |  |
                                                        |  |  - write_file    |  |
                                                        |  |  - search_files  |  |
                                                        |  |  - execute_cmd   |  |
                                                        |  |  - docker_ps     |  |
                                                        |  |  - docker_logs   |  |
                                                        |  |  - system_info   |  |
                                                        |  |  - service_status|  |
                                                        |  +------------------+  |
                                                        |                        |
                                                        |  IP: 10.0.0.X          |
                                                        +-----------------------+
```

- **Control plane:** Hermes Agent on one machine acts as the MCP client
- **Data plane:** WireGuard provides encrypted connectivity between all machines
- **Bridge:** `server.py` runs on each target machine, bound to its WireGuard IP
- **Authentication:** Every tool call requires a Bearer token; health checks are open

Add as many target machines as you need. Each one runs its own bridge instance with its own configuration.

---

## API Reference

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/` | No | Health check, returns status JSON |
| `POST` | `/` | Yes | MCP JSON-RPC (forwarded to `/messages`) |
| `GET` | `/health` | No | Health check, returns status JSON |
| `GET` | `/ping` | No | Ping check, returns `{"pong": true, "timestamp": "..."}` |
| `GET` | `/tools` | Yes | List available tools in MCP format |
| `POST` | `/call_tool` | Yes | Legacy direct tool invocation |
| `POST` | `/messages` | Yes | MCP JSON-RPC endpoint |
| `GET` | `/sse` | Yes | MCP Server-Sent Events stream |

### MCP JSON-RPC (`POST /messages`)

This is the primary endpoint for Hermes Agent's native MCP client. It accepts standard MCP protocol messages:

**Initialize:**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": { "name": "Hermes Agent", "version": "1.1.0" }
  }
}
```

**List tools:**

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list"
}
```

**Call a tool:**

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "execute_command",
    "arguments": {
      "command": "df -h",
      "timeout": 10
    }
  }
}
```

**Non-standard `raw` parameter:** When sending a `tools/call` request to `/messages`, you can include `"raw": true` at the top level of the JSON-RPC body. This causes the server to return the raw tool result directly (without the standard JSON-RPC wrapper), which is useful for debugging or for simpler clients that don't implement the full MCP protocol.

### Legacy direct invocation (`POST /call_tool`)

Kept for backward compatibility with simpler clients:

```json
{
  "tool": "execute_command",
  "args": {
    "command": "df -h",
    "timeout": 10
  }
}
```

---

## Tools Reference

Each tool is callable via both the MCP JSON-RPC endpoint and the legacy `/call_tool` endpoint.

### `list_directory`

List files in a directory (runs `ls -la`).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | No | `.` | Directory path to list |

### `read_file`

Read the contents of a file.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | | File path to read |

### `write_file`

Write or overwrite content to a file. Creates parent directories as needed.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | string | Yes | | File path to write |
| `content` | string | No | `""` | Text content to write |

### `search_files`

Search for a pattern inside files (wraps `grep`).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pattern` | string | Yes | | Regex or search pattern |
| `path` | string | No | `.` | Directory to search in |
| `file_glob` | string | No | `*` | File glob to filter results |
| `recursive` | boolean | No | `true` | Search subdirectories recursively |

### `execute_command`

Execute a shell command. **Only binaries in the allowlist can run.** The allowlist is enforced on the first token of the command. Supports shell syntax (pipes, redirects) via `shell=True`.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `command` | string | Yes | | Shell command to execute |
| `timeout` | number | No | `30` | Timeout in seconds |

### `docker_ps`

List Docker containers.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `all` | boolean | No | `false` | Show all containers including stopped ones |

### `docker_logs`

Get logs from a Docker container.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `container` | string | Yes | | Container name or ID |
| `tail` | number | No | `100` | Number of lines to return |

### `system_info`

Get system information: CPU model and core count, RAM usage, disk usage on `/`, uptime, load average, and kernel version. No parameters required.

### `service_status`

Check the status of a systemd service (runs `systemctl status`).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `service` | string | Yes | | Systemd service name |

---

## Security

### Authentication

Every protected endpoint requires a Bearer token sent in the `Authorization` header:

```
Authorization: Bearer YOUR_MCP_BRIDGE_TOKEN
```

Unauthenticated requests receive HTTP 401. The following endpoints are exempt from authentication:

- `GET /`: health check
- `GET /health`: health check
- `GET /ping`: ping check

### Command Allowlist

The `execute_command` tool validates the binary name (first token of the command) against a configurable allowlist. The allowlist supports `fnmatch` glob patterns. For example, `docker*` matches `docker`, `docker-compose`, and `docker-credential-helpers`.

Default allowlist: `docker`, `systemctl`, `journalctl`, `df`, `free`, `ls`, `cat`, `grep`, `ps`, `top`, `htop`, `who`, `uptime`, `uname`, `id`, `pwd`, `echo`, `date`

Customize via `MCP_EXECUTE_ALLOWLIST` in your `.env` file.

### Binding

By default the server binds to `127.0.0.1` (localhost only). Set `MCP_BRIDGE_HOST` to a specific IP for controlled exposure. Never bind to `0.0.0.0` on a public-facing interface without additional firewall rules.

### Audit Logging

Every tool call is logged to stderr (captured by journald when running under systemd) with:

- ISO 8601 timestamp (UTC)
- Tool name
- Arguments (truncated to 200 characters for log safety)
- Success or failure status

View logs:

```bash
journalctl -u hermes-mcp-bridge -f
```

### Systemd Hardening

The included systemd unit enables:

- `NoNewPrivileges=yes`: prevent privilege escalation
- `ProtectSystem=full`: mount `/usr` and `/boot` as read-only
- `ProtectHome=read-only`: home directories are read-only
- `ReadWritePaths=/tmp`: only `/tmp` is writable

### Recommended Network Setup

For maximum security, pair the bridge with WireGuard:

1. Create a WireGuard mesh between your Hermes machine and all target machines
2. On each target, set `MCP_BRIDGE_HOST` to the machine's WireGuard IP
3. Configure iptables/nftables to restrict port `8000` to the WireGuard interface only:

```bash
# Allow MCP Bridge traffic only on the WireGuard interface (wg0)
iptables -A INPUT -i wg0 -p tcp --dport 8000 -j ACCEPT
iptables -A INPUT -p tcp --dport 8000 -j DROP
```

4. Use a unique, strong `MCP_BRIDGE_TOKEN` on each machine, or share a single token across machines within a trusted mesh

---

## Safety: Limiting Tool Access

### Disable execute_command on sensitive machines

For read-only monitoring machines, restrict the allowlist in `.env`:

```bash
# Minimal allowlist: no command execution, Docker, or file writes
MCP_EXECUTE_ALLOWLIST=
```

### Per-machine tool filtering in Hermes config

If your Hermes Agent supports tool filtering, exclude dangerous tools per server:

```yaml
mcp_servers:
  monitoring-only:
    url: "http://10.0.0.5:8000/"
    headers:
      Authorization: "Bearer YOUR_TOKEN"
    tools:
      exclude:
        - execute_command
        - write_file
```

---

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MCP_BRIDGE_TOKEN` | Yes | | Bearer token for all authenticated requests |
| `MCP_BRIDGE_HOST` | No | `127.0.0.1` | IP address to bind to. Set to WireGuard IP for remote access |
| `MCP_BRIDGE_PORT` | No | `8000` | HTTP port to listen on |
| `MCP_EXECUTE_ALLOWLIST` | No | See above | Comma-separated list of allowed binaries. Supports `fnmatch` globs |
| `MCP_TOOL_TIMEOUT` | No | `30` | Default timeout in seconds for tool commands |

---

## Troubleshooting

```bash
# Follow service logs in real time
journalctl -u hermes-mcp-bridge -f

# View structured tool call logs
journalctl -u hermes-mcp-bridge -o cat

# Verify the server is listening
ss -tlnp | grep 8000

# Test locally (no auth)
curl http://localhost:8000/health

# Test with authentication
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8000/tools

# Check systemd service status
sudo systemctl status hermes-mcp-bridge

# Restart the service after config changes
sudo systemctl restart hermes-mcp-bridge
```

### Common Issues

**Service fails to start:** Check that `MCP_BRIDGE_TOKEN` is set in `.env`. The server refuses to start without it.

**Connection refused from remote machine:** Verify `MCP_BRIDGE_HOST` is set to the machine's IP (not `127.0.0.1`) and that firewall rules allow traffic on port `8000`.

**401 Unauthorized:** Ensure the `Authorization: Bearer <token>` header matches the `MCP_BRIDGE_TOKEN` in `.env`.

**Command not allowed:** The binary must be in `MCP_EXECUTE_ALLOWLIST`. Use fnmatch globs like `docker*` to match variants.

**Permission denied on file operations:** The bridge runs as the user specified in the systemd service. Ensure that user has read/write permissions on the paths you are accessing.

---

## Files

```
hermes-mcp-bridge/
├── server.py           # Main server application (FastAPI + uvicorn)
├── deploy.sh           # Deployment script (creates venv + systemd service)
├── .env.example        # Configuration template
├── requirements.txt    # Python dependencies
├── .gitignore
├── LICENSE             # MIT license
└── README.md
```

---

## Contributing

Contributions are welcome. Please open an issue or pull request on [GitHub](https://github.com/Okazakee/hermes-mcp-bridge).

---

## License

MIT. See [LICENSE](./LICENSE) for the full text.
