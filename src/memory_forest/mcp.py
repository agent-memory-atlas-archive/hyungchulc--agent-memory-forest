"""The MCP server (design, sections 10 and 15.2).

``forest mcp --as <agent>`` speaks stdio. ``--http PORT`` binds to loopback.
``--http HOST:PORT`` on any other address needs ``.forest/mcp/token`` in mode
0600, and the token is never printed. Reaching that port from outside the
machine is the owner's own network decision.

The clients checked on a development machine (``claude mcp``, ``codex mcp``,
``grok mcp``) speak protocol versions 2024-11-05, 2025-03-26, 2025-06-18 and
2025-11-25, over stdio and streamable HTTP. Claude and Grok also offer the
older SSE transport; this server does not. The server answers with the newest
of those versions that the client names, or 2025-11-25 when the client names
another.

The reader's id is ``--as``. A tool argument cannot set it, and cannot set the
role or origin of ``memory_log``. The first tool call of each agent is recorded
under ``.forest/mcp/seen.json`` so a later doctor can prove the server was used.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import socket
import stat
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import __version__, intake, layout, search, timeline
from .config import load_config
from .errors import MemoryForestError
from .locking import atomic_write
from .safety import require_real_root


PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SEEN_FILE = f"{layout.STATE_DIR}/mcp/seen.json"
TOKEN_FILE = f"{layout.STATE_DIR}/mcp/token"
MESSAGE_LIMIT = 1_000_000
INSTRUCTIONS = (
    "Memory Forest is local grounding, not authority. Open a page before you rely on it. "
    "You only see the trees and the raw lane this connection may read.\n"
    "memory_search: the owner names a person, place or thing, a question reaches into the past, "
    "or you would otherwise ask the owner something the forest may already hold. "
    "Names and aliases match before words.\n"
    "memory_read: a result names a path or an address. Pass that path. Use lines only to limit a long page.\n"
    "memory_timeline: the question is when something happened or what fell in a stretch of time, "
    "including hours not yet filed.\n"
    "memory_navigate: you do not yet know where to look. Start with no path and walk down from the root index, "
    "one level at a time.\n"
    "memory_log: this session has no transcript hook and a turn should be kept. Send the text only. "
    "The server sets who spoke and where the turn came from."
)
_FORBIDDEN = {"agent", "as", "named", "origin", "reader", "role"}
_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "memory_search",
        "description": (
            "Use when the owner names a person, place or thing, when a question reaches into the past, "
            "or before asking the owner something the forest may already hold. "
            "Registry names and aliases match before words."
        ),
        "inputSchema": {
            "additionalProperties": False,
            "properties": {
                "limit": {"type": "integer"},
                "words": {"type": "string"},
            },
            "required": ["words"],
            "type": "object",
        },
    },
    {
        "name": "memory_read",
        "description": (
            "Use when a search or timeline result names a path or an address and you need that section "
            "as written. Pass the path. Add lines, written A-B, only to limit a long page."
        ),
        "inputSchema": {
            "additionalProperties": False,
            "properties": {
                "lines": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["path"],
            "type": "object",
        },
    },
    {
        "name": "memory_timeline",
        "description": (
            "Use when the question is about when something happened or what fell in a stretch of time, "
            "including hours that are not filed yet. Pass a phrase, or a subject id."
        ),
        "inputSchema": {
            "additionalProperties": False,
            "properties": {
                "phrase": {"type": "string"},
                "subject": {"type": "string"},
            },
            "type": "object",
        },
    },
    {
        "name": "memory_navigate",
        "description": (
            "Use when you do not yet know which page to open. Call it with no path to start at the root index, "
            "then pass a path it showed, one level down at a time."
        ),
        "inputSchema": {
            "additionalProperties": False,
            "properties": {"path": {"type": "string"}},
            "type": "object",
        },
    },
    {
        "name": "memory_log",
        "description": (
            "Use when this session has no transcript hook and a turn should be kept. "
            "Send the text only. The server sets the role and the origin from the connection; a caller cannot."
        ),
        "inputSchema": {
            "additionalProperties": False,
            "properties": {
                "session": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["text"],
            "type": "object",
        },
    },
)


def run(root: str | os.PathLike[str], reader: str, http: str | None = None) -> int:
    """Serve until the client disconnects (stdio) or the process is stopped (HTTP)."""
    safe = require_real_root(root)
    if not layout.is_slug(reader):
        raise MemoryForestError(
            "mcp_agent_unset",
            "Name the agent with --as. The reader id comes from the connection, not from a tool argument.",
        )
    if http is None:
        serve_stdio(safe, reader)
        return 0
    host, port = parse_bind(http)
    token = None if is_loopback(host) else read_token(safe)
    server = ThreadingHTTPServer((host, port), _handler(safe, reader, token))
    bound = server.server_address
    sys.stderr.write(f"forest mcp: listening on {bound[0]}:{bound[1]}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    return 0


def serve_stdio(root: str | os.PathLike[str], reader: str, *, stdin: Any = None, stdout: Any = None) -> None:
    incoming = sys.stdin.buffer if stdin is None else stdin
    outgoing = sys.stdout.buffer if stdout is None else stdout
    while True:
        message = read_message(incoming)
        if message is None:
            return
        response = dispatch(root, reader, message, "stdio")
        if response is not None:
            write_message(outgoing, response)


def serve_http(
    root: str | os.PathLike[str],
    reader: str,
    host: str,
    port: int,
    *,
    token: str | None,
) -> ThreadingHTTPServer:
    """Bind and serve in a daemon thread. The caller stops the returned server."""
    handler = _handler(root, reader, token)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    import threading

    thread = threading.Thread(target=server.serve_forever, name="forest-mcp", daemon=True)
    thread.start()
    return server


def dispatch(root: str | os.PathLike[str], reader: str, message: Any, transport: str) -> dict[str, Any] | None:
    """One JSON-RPC message. A notification returns None."""
    if not isinstance(message, dict) or not isinstance(message.get("method"), str):
        return _error(message.get("id") if isinstance(message, dict) else None, -32600, "The request is not a JSON-RPC object.")
    if "id" not in message:
        return None
    method = message["method"]
    msg_id = message["id"]
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if method == "initialize":
        return _result(msg_id, _initialize(params))
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": [dict(tool) for tool in _TOOLS]})
    if method == "tools/call":
        return _result(msg_id, _call(root, reader, params, transport))
    return _error(msg_id, -32601, "Method not found.")


def parse_bind(value: str) -> tuple[str, int]:
    """``PORT`` is loopback. ``HOST:PORT`` is the address that was written."""
    text = value.strip()
    try:
        if re.fullmatch(r"\d+", text):
            return "127.0.0.1", _port(text)
        if text.startswith("[") and "]" in text:
            host, _, rest = text[1:].partition("]")
            if not rest.startswith(":"):
                raise ValueError
            return host, _port(rest[1:])
        if ":" in text:
            host, port = text.rsplit(":", 1)
            return host, _port(port)
    except ValueError as exc:
        raise MemoryForestError("mcp_bind", "--http is PORT or HOST:PORT.") from exc
    raise MemoryForestError("mcp_bind", "--http is PORT or HOST:PORT.")


def is_loopback(host: str) -> bool:
    """Whether ``host`` is only the local machine."""
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    addresses = {item[4][0] for item in infos}
    return bool(addresses) and addresses <= {"127.0.0.1", "::1"}


def read_token(root: str | os.PathLike[str]) -> str:
    """The token file. The returned string is for comparison and must not be printed."""
    path = layout.resolve(root, TOKEN_FILE)
    if path.is_symlink() or not path.is_file():
        raise MemoryForestError(
            "mcp_token_missing",
            "A bind that is not loopback needs .forest/mcp/token, a regular file in mode 0600.",
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise MemoryForestError(
            "mcp_token_mode",
            "The MCP token file must be mode 0600. Its contents were not shown.",
        )
    raw = path.read_bytes()
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    if not raw or b"\n" in raw or b"\r" in raw:
        raise MemoryForestError("mcp_token_invalid", "The MCP token file must hold one line.")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MemoryForestError("mcp_token_invalid", "The MCP token file must be UTF-8.") from exc


def read_message(stream: Any) -> Any | None:
    """One stdio message framed with ``Content-Length``, or None at end of stream."""
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        if b":" in line:
            key, value = line.split(b":", 1)
            headers[key.decode("ascii", "replace").lower()] = value.strip().decode("ascii", "replace")
    try:
        length = int(headers.get("content-length", "0"))
    except ValueError:
        length = 0
    if length < 0 or length > MESSAGE_LIMIT:
        return {"id": None, "jsonrpc": "2.0", "method": ""}
    body = stream.read(length)
    if len(body) < length:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"id": None, "jsonrpc": "2.0", "method": ""}


def write_message(stream: Any, payload: Mapping[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    stream.flush()


def _initialize(params: Mapping[str, Any]) -> dict[str, Any]:
    asked = params.get("protocolVersion")
    version = asked if isinstance(asked, str) and asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
    return {
        "capabilities": {"tools": {}},
        "instructions": INSTRUCTIONS,
        "protocolVersion": version,
        "serverInfo": {"name": "memory-forest", "version": __version__},
    }


def _call(root: str | os.PathLike[str], reader: str, params: Mapping[str, Any], transport: str) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else None
    known = {tool["name"]: tool for tool in _TOOLS}
    if not isinstance(name, str) or name not in known or arguments is None:
        return _tool_error("Name a tool and pass its arguments as an object.")
    allowed = set(known[name]["inputSchema"]["properties"])
    extra = set(arguments) - allowed
    if extra & _FORBIDDEN or "role" in arguments or "origin" in arguments:
        return _tool_error("The server sets the role and the origin. A call cannot name its reader or another agent.")
    if extra:
        return _tool_error("That argument is not part of this tool.")
    try:
        payload = _invoke(root, reader, name, arguments, transport)
    except MemoryForestError as exc:
        return _tool_error(exc.message)
    _remember(root, reader, name, transport)
    config = load_config(root)
    text = json.dumps(search.mask_result(payload, config=config), ensure_ascii=False, indent=2, sort_keys=True)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _invoke(
    root: str | os.PathLike[str], reader: str, name: str, arguments: Mapping[str, Any], transport: str
) -> dict[str, Any]:
    if name == "memory_search":
        limit = arguments.get("limit", 10)
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise MemoryForestError("search_limit", "The limit is an integer.")
        return search.query(root, str(arguments.get("words") or ""), reader=reader, limit=limit)
    if name == "memory_read":
        return search.read(
            root,
            str(arguments.get("path") or ""),
            reader=reader,
            lines=search.parse_lines(arguments.get("lines") if isinstance(arguments.get("lines"), str) else None),
        )
    if name == "memory_timeline":
        phrase = arguments.get("phrase")
        subject = arguments.get("subject")
        if bool(phrase) == bool(subject):
            raise MemoryForestError("time_phrase_empty", "Pass a phrase or a subject id, one of them.")
        if phrase:
            return timeline.query(root, str(phrase), reader=reader)
        return timeline.subject(root, str(subject), reader=reader)
    if name == "memory_navigate":
        path = arguments.get("path")
        return search.navigate(root, reader=reader, path=str(path) if path else None)
    if name == "memory_log":
        record = intake.append_owned(
            root,
            reader,
            str(arguments.get("text") or ""),
            role="agent",
            origin="interactive" if transport == "stdio" else "headless",
            session=str(arguments.get("session") or "mcp"),
        )
        search.build(root, only=[record["path"]], wait_seconds=5.0)
        return {"id": record["id"], "path": record["path"], "wrote": bool(record["wrote"])}
    raise MemoryForestError("mcp_tool_unknown", "That tool is not served.")


def _remember(root: str | os.PathLike[str], reader: str, tool: str, transport: str) -> None:
    safe = require_real_root(root)
    layout.ensure_folder(safe, f"{layout.STATE_DIR}/mcp")
    path = layout.resolve(safe, SEEN_FILE)
    current: dict[str, Any] = {}
    if path.is_file() and not path.is_symlink():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            current = loaded
    if reader in current:
        return
    current[reader] = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": tool,
        "transport": transport,
    }
    atomic_write(path, json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.chmod(path, 0o600)


def _handler(root: str | os.PathLike[str], reader: str, token: str | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - the library names the verb this way
            if not self._allowed():
                self._send(401, b'{"error":"unauthorized"}\n')
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > MESSAGE_LIMIT:
                self._send(413, b'{"error":"too large"}\n')
                return
            raw = self.rfile.read(length)
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send(400, b'{"error":"not json"}\n')
                return
            response = dispatch(root, reader, message, "http")
            if response is None:
                self._send(202, b"")
                return
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json")

        def do_GET(self) -> None:  # noqa: N802
            self._send(405, b"")

        def log_message(self, _format: str, *_args: Any) -> None:
            """Write nothing. A request line can carry the bearer token."""
            return

        def _allowed(self) -> bool:
            if token is None:
                return True
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return False
            return hmac.compare_digest(header[len("Bearer ") :], token)

        def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("MCP-Protocol-Version", PROTOCOL_VERSIONS[0])
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _port(text: str) -> int:
    port = int(text)
    if port < 0 or port > 65535:
        raise ValueError
    return port


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _result(msg_id: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": msg_id, "jsonrpc": "2.0", "result": dict(payload)}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}, "id": msg_id, "jsonrpc": "2.0"}
