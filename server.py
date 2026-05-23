#!/usr/bin/env python3
"""
Hermes MCP Bridge — Remote Machine Control via MCP over HTTP
=============================================================

Exposes tools over HTTP with Bearer token auth, WireGuard-friendly
binding, and allowlist-based command execution. Designed to run on
each Linux machine you want to control remotely via Hermes Agent.

Environment variables:
  MCP_BRIDGE_TOKEN          — Bearer token for auth (REQUIRED)
  MCP_BRIDGE_HOST           — IP to bind to (default: 127.0.0.1)
  MCP_BRIDGE_PORT           — Port (default: 8000)
  MCP_EXECUTE_ALLOWLIST     — Comma-separated allowed binaries
                              (default: docker,systemctl,journalctl,...)
  MCP_TOOL_TIMEOUT          — Default command timeout in seconds (default: 30)
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import shlex
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

# ── Configuration from environment ──────────────────────────────────────────

AUTH_TOKEN: str = os.environ.get("MCP_BRIDGE_TOKEN", "")
BIND_HOST: str = os.environ.get("MCP_BRIDGE_HOST", "127.0.0.1")
BIND_PORT: int = int(os.environ.get("MCP_BRIDGE_PORT", "8000"))

_DEFAULT_ALLOWLIST = (
    "docker,systemctl,journalctl,df,free,ls,cat,grep,ps,top,"
    "htop,who,uptime,uname,id,pwd,echo,date"
)
EXECUTE_ALLOWLIST: List[str] = [
    b.strip().lower()
    for b in os.environ.get("MCP_EXECUTE_ALLOWLIST", _DEFAULT_ALLOWLIST).split(",")
    if b.strip()
]

TOOL_TIMEOUT: int = int(os.environ.get("MCP_TOOL_TIMEOUT", "30"))


# ── Helpers ─────────────────────────────────────────────────────────────────

def _log(tool_name: str, args: Dict[str, Any], ok: bool, detail: str = "") -> None:
    """Write a structured log line to stderr."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    status = "OK" if ok else "FAIL"
    msg = f"[{ts}] tool={tool_name} status={status}"
    if args:
        # Keep args log-safe by truncating long values
        safe = {k: (v if len(str(v)) < 200 else str(v)[:200] + "...") for k, v in args.items()}
        msg += f" args={json.dumps(safe, default=str)}"
    if detail:
        msg += f" detail={detail}"
    print(msg, file=sys.stderr, flush=True)


def _run(cmd: List[str], timeout: int = TOOL_TIMEOUT, cwd: Optional[str] = None) -> Dict[str, Any]:
    """Run a shell command safely, return {'success': bool, 'stdout': ..., 'stderr': ..., 'code': int}."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"Command timed out after {timeout}s", "code": -1}
    except FileNotFoundError:
        return {"success": False, "stdout": "", "stderr": f"Binary not found: {cmd[0]}", "code": -2}
    except Exception as exc:
        return {"success": False, "stdout": "", "stderr": str(exc), "code": -3}


def _json_ok(data: Any) -> str:
    return json.dumps({"success": True, "data": data}, default=str)


def _json_err(message: str) -> str:
    return json.dumps({"success": False, "error": message})


def _safe_path(path: str) -> Path:
    """Resolve a path safely."""
    p = Path(path).expanduser().resolve()
    return p


def _check_execute_allowlist(command: str) -> Optional[str]:
    """
    Validate that the first token of *command* is in the allowlist.
    Returns None if allowed, otherwise an error string.
    """
    tokens = shlex.split(command)
    if not tokens:
        return "Empty command"
    binary_name = os.path.basename(tokens[0]).lower()

    for allowed in EXECUTE_ALLOWLIST:
        # Use fnmatch for glob-style matching (e.g. "docker*" matches "docker-compose")
        if fnmatch.fnmatch(binary_name, allowed):
            return None

    return f"Binary '{binary_name}' not in execute allowlist"


# ── FastAPI app ─────────────────────────────────────────────────────────────

app = FastAPI(title="Hermes MCP Bridge", version="1.0.0")


# ── Auth middleware ─────────────────────────────────────────────────────────

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Allow health/root endpoints without auth
    if request.url.path in ("/", "/health", "/ping"):
        return await call_next(request)

    # Allow OPTIONS preflight
    if request.method == "OPTIONS":
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"success": False, "error": "Missing or malformed Authorization header. Expected: Bearer <token>"},
        )
    token = auth_header[len("Bearer "):]
    if token != AUTH_TOKEN:
        return JSONResponse(
            status_code=401,
            content={"success": False, "error": "Invalid Bearer token"},
        )
    return await call_next(request)


# ── Root / health ───────────────────────────────────────────────────────────

@app.api_route("/", methods=["GET", "POST"])
@app.get("/health")
async def health(request: Request = None):
    if request and request.method == "POST":
        # Forward MCP JSON-RPC messages sent to root
        return await messages(request)
    return {"status": "ok", "service": "Hermes MCP Bridge", "version": "1.0.0"}


@app.get("/ping")
async def ping():
    return {"pong": True, "timestamp": datetime.now(timezone.utc).isoformat()}


# ── MCP SSE transport ───────────────────────────────────────────────────────

@app.get("/sse")
async def sse_endpoint(request: Request):
    """
    Server-Sent Events endpoint for MCP client connections.
    The client sends tool calls as POST requests to /call_tool
    and receives results via this SSE stream.
    """
    async def event_stream():
        yield "event: ready\ndata: {}\n\n"

        # Keep the connection alive with periodic heartbeats
        while True:
            disconnected = await request.is_disconnected()
            if disconnected:
                break
            yield f": heartbeat {int(time.time())}\n\n"
            await asyncio.sleep(15)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Tool call endpoint ──────────────────────────────────────────────────────

@app.post("/messages")
async def messages(request: Request):
    """
    MCP JSON-RPC endpoint.
    Accepts standard MCP protocol messages (initialize, tools/list, tools/call).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
        )

    req_id = body.get("id", 0)
    method = body.get("method", "")
    params = body.get("params", {})
    raw = body.get("raw", False)  # non-standard: return raw tool result instead of JSON-RPC wrapper

    if method == "initialize":
        return JSONResponse(content={
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": "Hermes MCP Bridge",
                    "version": "1.0.0",
                },
            },
        })

    if method == "notifications/initialized":
        return JSONResponse(content={"jsonrpc": "2.0", "id": req_id, "result": {}})

    if method == "tools/list":
        # Reuse the existing tool definitions
        tools_list = _get_mcp_tools()
        return JSONResponse(content={
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": tools_list},
        })

    if method == "tools/call":
        tool_name = params.get("name", "")
        args = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            _log(tool_name, args, ok=False, detail=f"Unknown tool: {tool_name}")
            return JSONResponse(
                status_code=400,
                content={"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}},
            )

        try:
            result_json = handler(args)
            result = json.loads(result_json)
            ok = result.get("success", False)
            _log(tool_name, args, ok=ok)

            if raw:
                return JSONResponse(content=result)

            if ok:
                return JSONResponse(content={
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": json.dumps(result["data"], default=str)}]},
                })
            else:
                return JSONResponse(content={
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": result.get("error", "Tool execution failed")},
                })
        except Exception as exc:
            tb = traceback.format_exc()
            _log(tool_name, args, ok=False, detail=str(exc))
            print(tb, file=sys.stderr, flush=True)
            return JSONResponse(
                status_code=500,
                content={"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(exc)}},
            )

    # Unknown method
    return JSONResponse(
        status_code=400,
        content={"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}},
    )

# Keep the old /call_tool endpoint for backward compatibility
@app.post("/call_tool")
async def call_tool_legacy(request: Request):
    """
    Legacy direct tool invocation endpoint (backward compatible).
    Body: {"tool": "<name>", "args": {...}}
    Returns JSON with success/error + data.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

    tool_name = body.get("tool", "")
    args = body.get("args", {})

    if not tool_name:
        return JSONResponse(status_code=400, content={"success": False, "error": "Missing 'tool' field"})

    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        _log(tool_name, args, ok=False, detail=f"Unknown tool: {tool_name}")
        return JSONResponse(status_code=400, content={"success": False, "error": f"Unknown tool: {tool_name}"})

    try:
        result_json = handler(args)
        result = json.loads(result_json)
        ok = result.get("success", False)
        _log(tool_name, args, ok=ok)
        return JSONResponse(content=result)
    except Exception as exc:
        tb = traceback.format_exc()
        _log(tool_name, args, ok=False, detail=str(exc))
        print(tb, file=sys.stderr, flush=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


# ── Tool handlers ───────────────────────────────────────────────────────────

def tool_list_directory(args: Dict[str, Any]) -> str:
    path = _safe_path(args.get("path", "."))
    result = _run(["ls", "-la", str(path)], cwd=str(path.parent) if path.is_file() else str(path))
    if result["success"]:
        return _json_ok(result["stdout"])
    return _json_err(result["stderr"])


def tool_read_file(args: Dict[str, Any]) -> str:
    path = _safe_path(args["path"])
    try:
        content = path.read_text()
        return _json_ok(content)
    except Exception as exc:
        return _json_err(str(exc))


def tool_write_file(args: Dict[str, Any]) -> str:
    path = _safe_path(args["path"])
    content = args.get("content", "")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return _json_ok(f"Written {len(content)} bytes to {path}")
    except Exception as exc:
        return _json_err(str(exc))


def tool_search_files(args: Dict[str, Any]) -> str:
    pattern = args.get("pattern", "")
    search_path = args.get("path", ".")
    file_glob = args.get("file_glob", "*")
    recursive = args.get("recursive", True)

    if not pattern:
        return _json_err("Missing 'pattern' argument")

    cmd = ["grep", "-nI"]
    if recursive:
        cmd.append("-r")
    cmd.extend(["--include", file_glob, pattern, str(search_path)])

    result = _run(cmd)
    if result["code"] in (0, 1):  # grep returns 1 for "no matches"
        return _json_ok(result["stdout"])
    return _json_err(result["stderr"])


def tool_execute_command(args: Dict[str, Any]) -> str:
    command = args.get("command", "")
    timeout = args.get("timeout", TOOL_TIMEOUT)

    if not command:
        return _json_err("Missing 'command' argument")

    # Allowlist enforcement
    error = _check_execute_allowlist(command)
    if error:
        return _json_err(error)

    # Use shell=True for pipes/redirects that the user may need.
    # The allowlist already restricts what binary is called, so this is reasonably safe.
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=int(timeout),
        )
        return _json_ok({
            "stdout": result.stdout,
            "stderr": result.stderr,
            "code": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return _json_err(f"Command timed out after {timeout}s")
    except Exception as exc:
        return _json_err(str(exc))


def tool_docker_ps(args: Dict[str, Any]) -> str:
    show_all = args.get("all", False)
    cmd = ["docker", "ps"]
    if show_all:
        cmd.append("-a")
    result = _run(cmd)
    if result["success"]:
        return _json_ok(result["stdout"])
    return _json_err(result["stderr"])


def tool_docker_logs(args: Dict[str, Any]) -> str:
    container = args.get("container", "")
    tail = args.get("tail", 100)
    if not container:
        return _json_err("Missing 'container' argument")
    cmd = ["docker", "logs", "--tail", str(int(tail)), container]
    result = _run(cmd)
    if result["success"]:
        return _json_ok(result["stdout"])
    return _json_err(result["stderr"])


def tool_system_info(args: Dict[str, Any]) -> str:
    """Gather CPU, RAM, disk, uptime, load info."""
    info: Dict[str, Any] = {}

    # Uptime
    try:
        info["uptime"] = Path("/proc/uptime").read_text().split()[0]
    except Exception:
        info["uptime"] = "N/A"

    # Load average
    try:
        info["loadavg"] = Path("/proc/loadavg").read_text().strip()
    except Exception:
        info["loadavg"] = "N/A"

    # CPU info
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                info["cpu_model"] = line.split(":", 1)[1].strip()
                break
        info["cpu_cores"] = sum(1 for _ in Path("/proc/cpuinfo").read_text().splitlines() if _.startswith("processor"))
    except Exception:
        info["cpu_model"] = "N/A"
        info["cpu_cores"] = 0

    # Memory
    mem = _run(["free", "-h"])
    info["memory"] = mem["stdout"] if mem["success"] else mem["stderr"]

    # Disk
    disk = _run(["df", "-h", "/"])
    info["disk_root"] = disk["stdout"] if disk["success"] else disk["stderr"]

    # uname
    uname = _run(["uname", "-a"])
    info["uname"] = uname["stdout"].strip() if uname["success"] else uname["stderr"]

    return _json_ok(info)


def tool_service_status(args: Dict[str, Any]) -> str:
    service = args.get("service", "")
    if not service:
        return _json_err("Missing 'service' argument")
    result = _run(["systemctl", "status", service, "--no-pager", "-l"])
    if result["success"]:
        return _json_ok(result["stdout"])
    return _json_err(result["stderr"])


# ── Tool registry ───────────────────────────────────────────────────────────

TOOL_HANDLERS = {
    "list_directory": tool_list_directory,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "search_files": tool_search_files,
    "execute_command": tool_execute_command,
    "docker_ps": tool_docker_ps,
    "docker_logs": tool_docker_logs,
    "system_info": tool_system_info,
    "service_status": tool_service_status,
}


# ── Tool listing endpoint ───────────────────────────────────────────────────

# ── MCP-compatible tool descriptions ────────────────────────────────────

_TOOL_MCP_DEFS = [
    {
        "name": "list_directory",
        "description": "List files in a directory (ls -la)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: .)"},
            },
        },
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write/overwrite content to a file",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "Text content to write"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_files",
        "description": "Search for a pattern inside files (grep)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex/search pattern"},
                "path": {"type": "string", "description": "Directory to search in (default: .)"},
                "file_glob": {"type": "string", "description": "File glob to filter (default: *)"},
                "recursive": {"type": "boolean", "description": "Search recursively (default: true)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "execute_command",
        "description": "Execute a shell command (allowlist-enforced)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "number", "description": "Timeout in seconds (default: 30)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "docker_ps",
        "description": "List Docker containers",
        "inputSchema": {
            "type": "object",
            "properties": {
                "all": {"type": "boolean", "description": "Show all containers including stopped (default: false)"},
            },
        },
    },
    {
        "name": "docker_logs",
        "description": "Get logs from a Docker container",
        "inputSchema": {
            "type": "object",
            "properties": {
                "container": {"type": "string", "description": "Container name or ID"},
                "tail": {"type": "number", "description": "Number of lines to tail (default: 100)"},
            },
            "required": ["container"],
        },
    },
    {
        "name": "system_info",
        "description": "Get system info: CPU, RAM, disk, uptime, load",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "service_status",
        "description": "Check systemd service status",
        "inputSchema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name"},
            },
            "required": ["service"],
        },
    },
]

def _get_mcp_tools():
    """Return tool definitions in MCP-compatible format."""
    return _TOOL_MCP_DEFS


@app.get("/tools")
async def list_tools():
    """Return the list of available tools (MCP-compatible)."""
    tools = [
        {
            "name": "list_directory",
            "description": "List files in a directory (ls -la)",
            "parameters": {"path": "Directory path (default: .)"},
        },
        {
            "name": "read_file",
            "description": "Read the contents of a file",
            "parameters": {"path": "File path"},
        },
        {
            "name": "write_file",
            "description": "Write/overwrite content to a file",
            "parameters": {"path": "File path", "content": "Text content to write"},
        },
        {
            "name": "search_files",
            "description": "Search for a pattern inside files (grep)",
            "parameters": {
                "pattern": "Regex/search pattern",
                "path": "Directory to search in (default: .)",
                "file_glob": "File glob to filter (default: *)",
                "recursive": "Search recursively (default: true)",
            },
        },
        {
            "name": "execute_command",
            "description": "Execute a shell command (allowlist-enforced)",
            "parameters": {
                "command": "Shell command to execute",
                "timeout": "Timeout in seconds (default: 30)",
            },
        },
        {
            "name": "docker_ps",
            "description": "List Docker containers",
            "parameters": {"all": "Show all containers including stopped (default: false)"},
        },
        {
            "name": "docker_logs",
            "description": "Get logs from a Docker container",
            "parameters": {
                "container": "Container name or ID",
                "tail": "Number of lines to tail (default: 100)",
            },
        },
        {
            "name": "system_info",
            "description": "Get system info: CPU, RAM, disk, uptime, load",
            "parameters": {},
        },
        {
            "name": "service_status",
            "description": "Check systemd service status",
            "parameters": {"service": "Service name"},
        },
    ]
    return {"tools": tools}


# ── Main entrypoint ─────────────────────────────────────────────────────────

def main() -> None:
    if not AUTH_TOKEN:
        print("ERROR: MCP_BRIDGE_TOKEN environment variable is required.", file=sys.stderr)
        sys.exit(1)

    print(f"Hermes MCP Bridge starting on {BIND_HOST}:{BIND_PORT}", file=sys.stderr)
    print(f"  Allowlist: {EXECUTE_ALLOWLIST}", file=sys.stderr)
    print(f"  Auth: Bearer token configured", file=sys.stderr)

    uvicorn.run(
        "server:app",
        host=BIND_HOST,
        port=BIND_PORT,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
