"""The MCP server (design, sections 10 and 15.2).

``forest mcp --as <agent>`` speaks stdio: one JSON-RPC message per line, UTF-8,
with no embedded newline. Logs go to stderr. stdout carries only protocol
messages.

``--http PORT`` binds to loopback. ``--http HOST:PORT`` on any other address is
the owner's own network decision. Every bind, loopback included, requires
``.forest/mcp/token`` in mode 0600. The first start creates it and never prints
it. A request is refused unless the bearer token matches those bytes, the body
is ``application/json``, and, when ``Origin`` or ``Host`` is present, it names
the loopback server.

What each protocol revision says, and this server does:

- An argument that fails a tool's input schema (``limit`` below 1, ``lines``
  not written A-B, an argument the tool does not take) is a tool result with
  ``isError: true`` under 2025-11-25 ("Input validation errors" are tool
  execution errors) and JSON-RPC ``-32602`` under the earlier revisions
  ("Invalid arguments" are protocol errors). An unknown tool, or arguments that
  are not an object, is ``-32602`` under every revision.
- An error answering a message whose id could not be read has no ``id`` under
  2025-11-25 on stdio, and on an HTTP error status from 2025-03-26 on. The
  sentence that allows a body with no ``id`` covers an error status only. A
  200 payload keeps ``id: null``, as JSON-RPC 2.0 requires when the id could
  not be read. Elsewhere the ``id`` is null as well.
- A stdio line is read at most ``MESSAGE_LIMIT`` bytes at a time. A longer line
  is answered with one error and skipped to its end; the next line is read.
- On stdio a batch is refused before ``initialize`` (no revision is agreed yet)
  and when it holds ``initialize``, which 2025-03-26 says must not be part of a
  batch. HTTP has no session: the revision is the ``MCP-Protocol-Version``
  header, and a 2025-03-26 batch that does not contain ``initialize`` is
  answered. A batch that contains ``initialize`` is refused on both transports.

The clients checked on a development machine (``claude mcp``, ``codex mcp``,
``grok mcp``) speak protocol versions 2024-11-05, 2025-03-26, 2025-06-18 and
2025-11-25, over stdio and streamable HTTP. Claude and Grok also offer the
older SSE transport; this server does not. The server answers with the newest
of those versions that the client names, or 2025-11-25 when the client names
another. A JSON-RPC batch is accepted only for 2025-03-26, the revision that
added batches; the revision before it had none, and 2025-06-18 removed them.

A tool that refuses answers ``isError: true`` with one JSON object as its text:
``code``, ``message``, ``details`` and, when there is one, the next move
(``next`` with ``command`` or ``after_seconds``). An input validation error
under 2025-11-25 is ``arguments_invalid`` in the same shape. What the forest
holds, and what the caller sent, is returned exactly. What the server says
itself (a refusal, a lock owner, a status field) carries no host name, process
id, user name, home folder or absolute path: those are removed at this door,
and only on a refusal. A lock another writer holds is ``lock_busy``, next
``retry``. A lock this process already holds is ``lock_already_held``, next
``fix``. A lock whose holder is stopped is ``lock_holder_stopped``, next
``report``, because waiting does not release it. Threads of this server wait
their turn for ``memory_log``.

The reader's id is ``--as``. A tool argument cannot set it, and cannot set the
role or origin of ``memory_log``. ``--as-group`` is the other connection: one
group of agents, and every tool then requires ``agent``, the caller's own name,
resolved only to a member of that group. The first tool call of each agent is
recorded under ``.forest/mcp/seen.json``, and each later call records when the
agent was last seen, so a later doctor can prove the server was used.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import socket
import sys
import urllib.parse
from collections.abc import Mapping
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import __version__, groups, intake, layout, names, search, system, timeline
from .config import load_config
from .errors import MOVE_DEFINITIONS, MemoryForestError, door_refusal
from .locking import atomic_write
from .safety import require_real_root


PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
# 2025-03-26 added JSON-RPC batches. 2024-11-05 has none. 2025-06-18 removed them.
BATCH_VERSIONS = frozenset({"2025-03-26"})
# 2025-11-25: "Input validation errors" are tool execution errors (``isError: true``), not protocol errors.
INPUT_ERROR_RESULT_VERSIONS = frozenset({"2025-11-25"})
# An error response with no ``id``: allowed by 2025-11-25's base protocol, and for HTTP error bodies from 2025-03-26.
NO_ID_VERSIONS = frozenset({"2025-11-25"})
NO_ID_HTTP_VERSIONS = frozenset({"2025-03-26", "2025-06-18", "2025-11-25"})
ASSUMED_VERSION = "2025-03-26"
SEEN_FILE = f"{layout.STATE_DIR}/mcp/seen.json"
TOKEN_FILE = f"{layout.STATE_DIR}/mcp/token"
MESSAGE_LIMIT = 1_000_000
# The greatest turn, in bytes of its words as UTF-8: the same number for ``memory_log`` and ``forest log``.
# A protocol line has its own larger bound, MESSAGE_LIMIT: a turn of this size still fits in one line when
# every byte of it is written as a six-byte escape.
TURN_MAX_BYTES = 131_072
READ_TIMEOUT_SECONDS = 30.0
# A read rewrites the seen record only when the last value is older than this, or absent.
SEEN_INTERVAL_SECONDS = 60.0
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
    "The server sets who spoke and where the turn came from.\n"
    "A refusal's next is one of four moves:\n"
    + "\n".join(
        f"{move}: {MOVE_DEFINITIONS[move]}; {action}."
        for move, action in (
            ("fix", "correct the call as the message says and call again"),
            ("retry", "call again after after_seconds"),
            ("run", "run the command the refusal names, then call again"),
            ("report", "tell the owner what the message says and do not call again"),
        )
    )
)
GROUP_INSTRUCTIONS = (
    INSTRUCTIONS
    + "\nThis connection is one group. Pass agent, your own name, on every call. "
    "The agent is its name."
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
                "source": {"type": "string"},
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
                "source": {"type": "string"},
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
                "source": {"type": "string"},
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
                "turn_id": {
                    "description": "Your own id for this turn, when you may send it twice: the same id in the "
                    "same session is stored once.",
                    "type": "string",
                },
            },
            "required": ["text"],
            "type": "object",
        },
    },
)


def _with_caller(tool: dict[str, Any]) -> dict[str, Any]:
    """The same tool, with the caller's own name required. Used only on a group connection."""
    schema = tool["inputSchema"]
    properties = {"agent": {"description": "Your own name, as this app calls you.", "type": "string"}}
    properties.update(schema["properties"])
    return {
        **tool,
        "inputSchema": {
            "additionalProperties": False,
            "properties": properties,
            "required": ["agent", *schema.get("required", ())],
            "type": "object",
        },
    }


_GROUP_TOOLS: tuple[dict[str, Any], ...] = tuple(_with_caller(tool) for tool in _TOOLS)


class _BadArguments(Exception):
    """The tool name or the arguments do not match the tool. This is JSON-RPC -32602."""

    def __init__(self, message: str) -> None:
        self.message = message


class _ParseError:
    """One line was not a JSON value."""


class _Oversize:
    """One line was longer than ``MESSAGE_LIMIT``; it was read to its end and dropped."""


_PARSE = _ParseError()
_OVERSIZE = _Oversize()


def run(
    root: str | os.PathLike[str],
    reader: str | None = None,
    http: str | None = None,
    *,
    group: str | None = None,
) -> int:
    """Serve until the client disconnects (stdio) or the process is stopped (HTTP).

    Exactly one of ``reader`` (``--as``) and ``group`` (``--as-group``) is set.
    """
    safe = require_real_root(root)
    if reader and group:
        raise MemoryForestError(
            "mcp_reader_conflict",
            "Pass --as or --as-group, not both. A tool argument cannot change which one it is.",
            next_step="fix",
        )
    if group is not None:
        if not layout.is_slug(group):
            raise MemoryForestError(
                "mcp_group_invalid",
                "Name the group with --as-group. The group id is a slug.",
                next_step="fix",
            )
        groups.require_group(load_config(safe), group)
    elif not reader or not layout.is_slug(reader):
        raise MemoryForestError(
            "mcp_agent_unset",
            "Name the agent with --as, or name the group with --as-group. Pass one of them.",
        )
    if http is None:
        serve_stdio(safe, reader, group=group)
        return 0
    host, port = parse_bind(http)
    token = ensure_token(safe)
    server = _server((host, port), _handler(safe, reader or "", token, READ_TIMEOUT_SECONDS, group=group))
    bound = server.server_address
    sys.stderr.write(f"forest mcp: listening on {bound[0]}:{bound[1]}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    return 0


def serve_stdio(
    root: str | os.PathLike[str],
    reader: str | None = None,
    *,
    stdin: Any = None,
    stdout: Any = None,
    group: str | None = None,
) -> None:
    """Read one JSON-RPC message per line and write replies the same way. One bad line does not end the server."""
    incoming = sys.stdin.buffer if stdin is None else stdin
    outgoing = sys.stdout.buffer if stdout is None else stdout
    negotiated: str | None = None
    while True:
        try:
            message = read_message(incoming)
        except Exception as exc:
            _log(f"forest mcp: the request could not be read ({exc.__class__.__name__})")
            continue
        if message is None:
            return
        if message is _PARSE:
            if not _write_safely(outgoing, _unread_error(-32700, "Parse error.", negotiated, "stdio")):
                return
            continue
        if message is _OVERSIZE:
            refused = _unread_error(-32600, f"The message is longer than {MESSAGE_LIMIT} bytes.", negotiated, "stdio")
            if not _write_safely(outgoing, refused):
                return
            continue
        try:
            response, negotiated = handle_message(root, reader or "", message, "stdio", negotiated, group=group)
        except Exception as exc:
            _log(f"forest mcp: one request failed ({exc.__class__.__name__}) and the next will be read")
            response = _reply_error(message, -32603, "The request could not be answered.", negotiated, "stdio")
        if response is not None and not _write_safely(outgoing, response):
            return


def serve_http(
    root: str | os.PathLike[str],
    reader: str,
    host: str,
    port: int,
    *,
    token: bytes | None = None,
    read_timeout: float = READ_TIMEOUT_SECONDS,
    group: str | None = None,
) -> ThreadingHTTPServer:
    """Bind and serve in a daemon thread. The caller stops the returned server.

    ``token`` None creates or reads ``.forest/mcp/token``. The token is not printed.
    ``group`` set means this connection is that group: each call names its agent.
    """
    token_bytes = token if token is not None else ensure_token(root)
    handler = _handler(root, reader, token_bytes, read_timeout, group=group)
    server = _server((host, port), handler)
    server.daemon_threads = True
    import threading

    thread = threading.Thread(target=server.serve_forever, name="forest-mcp", daemon=True)
    thread.start()
    return server


def handle_message(
    root: str | os.PathLike[str],
    reader: str,
    message: Any,
    transport: str,
    negotiated: str | None,
    *,
    group: str | None = None,
) -> tuple[Any, str | None]:
    """One stdio or HTTP payload. A notification or a client response returns None.

    ``negotiated`` is the revision agreed by ``initialize`` on stdio (None before it), or the one an HTTP request
    states. ``group`` set means the reader of a tool call is the member named by ``agent``.
    """
    if isinstance(message, list):
        refusal = _batch_refusal(negotiated, message)
        if refusal is not None:
            return _unread_error(-32600, refusal, negotiated, transport), negotiated
        replies: list[dict[str, Any]] = []
        for item in message:
            reply, _ignored = _dispatch_one(root, reader, item, transport, negotiated, group=group)
            if reply is not None:
                replies.append(reply)
        if not replies:
            return None, negotiated
        return replies, negotiated
    return _dispatch_one(root, reader, message, transport, negotiated, group=group)


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
    """Whether ``host`` is only the local machine. This resolves names; the request check does not."""
    if _loopback_literal(host):
        return True
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    addresses = {item[4][0] for item in infos}
    return bool(addresses) and all(_loopback_literal(address) for address in addresses)


def ensure_token(root: str | os.PathLike[str]) -> bytes:
    """The token bytes. The first start writes ``.forest/mcp/token`` in mode 0600 and does not print it."""
    safe = require_real_root(root)
    path = layout.resolve(safe, TOKEN_FILE)
    if system.look_or_none(path) is not None:
        return read_token(safe)
    layout.ensure_folder(safe, f"{layout.STATE_DIR}/mcp")
    token = secrets.token_hex(32).encode("ascii")
    if not system.create_new(path, token + b"\n", mode=0o600):
        return read_token(safe)
    return token


def read_token(root: str | os.PathLike[str]) -> bytes:
    """The token file as bytes. The value is for comparison and must not be printed."""
    path = layout.resolve(root, TOKEN_FILE)
    try:
        handle = system.open_file(path)
    except (system.Absent, system.Link, system.NotAFile) as exc:
        raise MemoryForestError(
            "mcp_token_missing",
            "The MCP server needs .forest/mcp/token, a regular file in mode 0600.",
        ) from exc
    with handle:
        if handle.look.mode != 0o600:
            raise MemoryForestError(
                "mcp_token_mode",
                "The MCP token file must be mode 0600. Its contents were not shown.",
            )
        raw = handle.read()
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    if not raw or b"\n" in raw or b"\r" in raw:
        raise MemoryForestError("mcp_token_invalid", "The MCP token file must hold one line.")
    return raw


def read_message(stream: Any) -> Any:
    """One newline-delimited JSON-RPC message, ``None`` at end of stream, or a parse or size failure.

    A line is read at most ``MESSAGE_LIMIT`` bytes and its newline at a time; a longer one is read on to its
    end in bounded pieces and dropped, so no line is ever held whole in memory.
    """
    while True:
        line = stream.readline(MESSAGE_LIMIT + 1)
        if not line:
            return None
        if len(line) > MESSAGE_LIMIT and not line.endswith(b"\n"):
            while True:
                rest = stream.readline(1 << 16)
                if not rest or rest.endswith(b"\n"):
                    break
            return _OVERSIZE
        try:
            text = line.decode("utf-8").strip("\r\n")
        except UnicodeDecodeError:
            return _PARSE
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return _PARSE


def write_message(stream: Any, payload: Mapping[str, Any] | list[Any]) -> None:
    """One JSON-RPC message and a newline. The line holds no raw newline."""
    if isinstance(payload, list):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    else:
        body = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    data = body.encode("utf-8").replace(b"\r", b"").replace(b"\n", b"")
    stream.write(data + b"\n")
    stream.flush()


def _dispatch_one(
    root: str | os.PathLike[str],
    reader: str,
    message: Any,
    transport: str,
    negotiated: str | None,
    *,
    group: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(message, dict):
        return _unread_error(-32600, "The request is not a JSON-RPC object.", negotiated, transport), negotiated
    if not isinstance(message.get("method"), str):
        # A client's response, or any object with no method, gets no error reply.
        return None, negotiated
    method = message["method"]
    if "id" not in message:
        return None, negotiated
    msg_id = message["id"]
    if not _valid_id(msg_id):
        # A string or an integer. A boolean, a fraction and null are not.
        return _unread_error(-32600, "A request id is a string or an integer.", negotiated, transport), negotiated
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    try:
        if method == "initialize":
            result = _initialize(params, group=group is not None)
            return _result(msg_id, result), str(result["protocolVersion"])
        if method == "ping":
            return _result(msg_id, {}), negotiated
        if method == "tools/list":
            tools = _GROUP_TOOLS if group else _TOOLS
            return _result(msg_id, {"tools": [dict(tool) for tool in tools]}), negotiated
        if method == "tools/call":
            return _result(msg_id, _call(root, reader, params, transport, negotiated, group=group)), negotiated
    except _BadArguments as exc:
        return _error(msg_id, -32602, exc.message), negotiated
    return _error(msg_id, -32601, "Method not found."), negotiated


def _initialize(params: Mapping[str, Any], *, group: bool = False) -> dict[str, Any]:
    asked = params.get("protocolVersion")
    version = asked if isinstance(asked, str) and asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
    return {
        "capabilities": {"tools": {}},
        "instructions": GROUP_INSTRUCTIONS if group else INSTRUCTIONS,
        "protocolVersion": version,
        "serverInfo": {"name": "memory-forest", "version": __version__},
    }


def _call(
    root: str | os.PathLike[str],
    reader: str,
    params: Mapping[str, Any],
    transport: str,
    version: str | None = None,
    *,
    group: str | None = None,
) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments", {})
    known = {tool["name"]: tool for tool in (_GROUP_TOOLS if group else _TOOLS)}
    if not isinstance(name, str) or name not in known:
        raise _BadArguments("Name a tool this server serves.")
    if not isinstance(arguments, dict):
        raise _BadArguments("Pass the tool's arguments as an object.")
    try:
        _check_arguments(name, arguments, known[name])
    except _BadArguments as exc:
        if version in INPUT_ERROR_RESULT_VERSIONS:
            return _tool_error(MemoryForestError("arguments_invalid", exc.message), root)
        raise
    admitted = ""
    try:
        from .config import selected_root
        root = selected_root(root)
        if group:
            resolved = groups.resolve_caller(root, group, str(arguments["agent"]))
            reader = resolved.id
            admitted = resolved.id if resolved.wrote else ""
            arguments = {key: value for key, value in arguments.items() if key != "agent"}
        payload = _invoke(root, reader, name, arguments, transport, group=group)
    except _BadArguments:
        raise
    except MemoryForestError as exc:
        return _tool_error(admitted_on(exc, admitted), root)
    except OSError as exc:
        # What the door answered and no code decided on: a refusal by its kind, never a fault of this code.
        return _tool_error(admitted_on(search.edge_refusal(exc, root), admitted), root)
    except Exception:
        return _tool_error(admitted_on(MemoryForestError("tool_failed", "The tool could not answer."), admitted), root)
    if admitted:
        payload = {**payload, "admitted": admitted}
    try:
        # The answer is masked with the forest's settings and ledger, read now: a fault there is a refusal,
        # except for a turn already written, which is never told as unwritten.
        text = _tool_text(payload, root, indent=2)
    except (MemoryForestError, OSError) as exc:
        if name != "memory_log":
            refusal = exc if isinstance(exc, MemoryForestError) else search.edge_refusal(exc, root)
            return _tool_error(refusal, root, fixed=True)
        text = json.dumps(withheld(payload, exc), indent=2, sort_keys=True)
    except Exception:
        return _tool_error(MemoryForestError("tool_failed", "The tool could not answer."), root)
    try:
        _remember(root, reader, name, transport)
    except Exception:
        _log("forest mcp: the seen record could not be written")
    return {"content": [{"type": "text", "text": text}], "isError": False}


def admitted_on(refusal: MemoryForestError, admitted: str) -> MemoryForestError:
    """A refusal after this call admitted the caller to its group: it says so, since that write took effect."""
    if admitted:
        refusal.details["admitted"] = admitted
    return refusal


def withheld(body: Mapping[str, Any], exc: Exception) -> dict[str, Any]:
    """A written turn's answer when the masking cannot be read: the words of a fixed vocabulary only.

    ``wrote``, ``indexed`` and the reason stay, so the turn is never told as unwritten and never sent again;
    the id and the path are withheld, with the code of what kept the masking from being read.
    """
    code = exc.code if isinstance(exc, MemoryForestError) else door_refusal(exc).code
    kept = {key: body[key] for key in ("index", "indexed", "reason", "stored", "tree", "wrote") if key in body}
    kept["withheld"] = code
    return kept


def _check_arguments(name: str, arguments: Mapping[str, Any], tool: Mapping[str, Any]) -> None:
    schema = tool["inputSchema"]
    allowed = set(schema["properties"])
    extra = set(arguments) - allowed
    if extra & _FORBIDDEN or "role" in arguments or "origin" in arguments:
        raise _BadArguments("The server sets the role and the origin. A call cannot name its reader or another agent.")
    if extra:
        raise _BadArguments("That argument is not part of this tool.")
    required = set(schema.get("required") or ())
    if any(key not in arguments for key in required):
        raise _BadArguments("A required argument is missing.")
    for value in arguments.values():
        if isinstance(value, str) and names.forbidden(value):
            raise _BadArguments("A text argument holds no Unicode control character, surrogate or line break.")
    if "agent" in schema["properties"]:
        caller = arguments.get("agent")
        if not isinstance(caller, str) or not caller.strip():
            raise _BadArguments("The agent is the caller's own name.")
    if "source" in arguments and not isinstance(arguments.get("source"), str):
        raise _BadArguments("The source is a string.")
    if name == "memory_search":
        if not isinstance(arguments.get("words"), str):
            raise _BadArguments("The words are a string.")
        if "limit" in arguments:
            limit = arguments["limit"]
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise _BadArguments("The limit is an integer of at least 1.")
    elif name == "memory_read":
        if not isinstance(arguments.get("path"), str):
            raise _BadArguments("The path is a string.")
        if "lines" in arguments:
            if not isinstance(arguments["lines"], str):
                raise _BadArguments("Lines are written A-B.")
            try:
                search.parse_lines(arguments["lines"])
            except MemoryForestError as exc:
                raise _BadArguments(exc.message) from exc
    elif name == "memory_timeline":
        phrase = arguments.get("phrase")
        subject = arguments.get("subject")
        if (phrase is None) == (subject is None):
            raise _BadArguments("Pass a phrase or a subject id, one of them.")
        if phrase is not None and (not isinstance(phrase, str) or not phrase.strip()):
            raise _BadArguments("The phrase is a string.")
        if subject is not None and (not isinstance(subject, str) or not subject.strip()):
            raise _BadArguments("The subject id is a string.")
    elif name == "memory_navigate":
        if "path" in arguments and not isinstance(arguments["path"], str):
            raise _BadArguments("The path is a string.")
    elif name == "memory_log":
        text = arguments.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _BadArguments("A turn needs text.")
        if "session" in arguments and not isinstance(arguments["session"], str):
            raise _BadArguments("The session is a string.")
        if "turn_id" in arguments and not isinstance(arguments["turn_id"], str):
            raise _BadArguments("The turn id is a string.")


def _invoke(
    root: str | os.PathLike[str],
    reader: str,
    name: str,
    arguments: Mapping[str, Any],
    transport: str,
    *,
    group: str | None = None,
) -> dict[str, Any]:
    source = arguments.get("source") if isinstance(arguments.get("source"), str) else None
    if name == "memory_search":
        limit = arguments.get("limit", 10)
        return search.query(
            root, str(arguments.get("words") or ""), reader=reader, limit=int(limit), source=source
        )
    if name == "memory_read":
        lines = arguments.get("lines") if isinstance(arguments.get("lines"), str) else None
        return search.read(
            root, str(arguments.get("path") or ""), reader=reader, lines=search.parse_lines(lines), source=source
        )
    if name == "memory_timeline":
        phrase = arguments.get("phrase")
        subject = arguments.get("subject")
        if phrase:
            return timeline.query(root, str(phrase), reader=reader, source=source)
        return timeline.subject(root, str(subject), reader=reader, source=source)
    if name == "memory_navigate":
        path = arguments.get("path")
        return search.navigate(root, reader=reader, path=str(path) if path else None)
    if name == "memory_log":
        turn_id = arguments.get("turn_id")
        return record_turn(
            root,
            reader,
            str(arguments.get("text") or ""),
            origin="interactive" if transport == "stdio" else "headless",
            session=str(arguments.get("session") or "mcp"),
            door="mcp",
            group=group or "",
            turn_id=turn_id if isinstance(turn_id, str) else None,
        )
    raise _BadArguments("That tool is not served.")


def record_turn(
    root: str | os.PathLike[str],
    agent: str,
    text: str,
    *,
    origin: str,
    session: str,
    door: str = "mcp",
    group: str = "",
    turn_id: str | None = None,
    at: datetime | None = None,
    tree_wait: float | None = None,
) -> dict[str, Any]:
    """Append one turn through the lane's one writer; then the agent's tree and the index follow.

    The role is ``agent``. The caller does not choose the role or the origin. What the call answers depends on
    the write: once the lane's line is stored (``stored``: ``written``, ``already stored`` or ``joined``, see
    ``intake.append_owned``), the answer says so, and the tree (``made``, ``already there``, ``pending`` or
    ``none``) and the index (``updated`` or ``pending``) are named as they came out. Neither turns the answer
    into a refusal. A tree is pending when the writer lock stays held past ``levels.TREE_WAIT_SECONDS``; the
    next write of the agent, or the level pass, makes it. An index is pending when it was busy or failed; the
    next read brings the lane in.
    """
    from . import levels

    data = text.encode("utf-8", "surrogatepass")
    if len(data) > TURN_MAX_BYTES:
        raise MemoryForestError(
            "log_too_large",
            f"The turn is longer than {TURN_MAX_BYTES} bytes. It was not stored.",
            details={"limit": TURN_MAX_BYTES},
            next_step="fix",
        )
    refused = search.unwritable(root)
    if refused is not None:
        raise _log_read_only(agent) if refused.code == "index_read_only" else refused
    # A denial inside the append is told by the door's edge as what was denied: the turn may already be in the
    # lane, so it is never "not written".
    record = intake.append_owned(
        root, agent, text, role="agent", origin=origin, session=session or "mcp", door=door, group=group,
        turn_id=turn_id, at=at,
    )
    body: dict[str, Any] = {
        "id": record["id"],
        "index": "pending",
        "indexed": False,
        "path": record["path"],
        "sources": list(record.get("sources") or []),
        "stored": str(record.get("stored") or "written"),
        "tree": levels.TREE_PENDING,
        "wrote": bool(record["wrote"]),
    }
    # The turn is in the lane: from here a fault is told as the state of what follows, never as a refusal, so
    # the caller does not write the turn again.
    try:
        body["tree"] = levels.agent_tree(
            root, agent, wait_seconds=levels.TREE_WAIT_SECONDS if tree_wait is None else tree_wait
        )
    except MemoryForestError as exc:
        body["tree_reason"] = exc.code
    except OSError as exc:
        body["tree_reason"] = door_refusal(exc).code
    except Exception:
        _log("forest: the turn was written and the agent's tree was not made")
        body["tree_reason"] = "tree_failed"
    try:
        outcome = search.update_files(root, [record["path"]], wait_seconds=0)
        body["indexed"] = bool(outcome.get("indexed"))
        if body["indexed"]:
            body["index"] = "updated"
        else:
            body["reason"] = str(outcome.get("reason") or "index_busy")
    except MemoryForestError as exc:
        body["reason"] = exc.code
    except OSError as exc:
        body["reason"] = door_refusal(exc).code
    except Exception:
        _log("forest: the turn was written and the index was not updated")
        body["reason"] = "index_failed"
    return body


def _log_read_only(agent: str) -> MemoryForestError:
    """A turn that cannot be written: this account cannot write the forest. Nothing was written."""
    return MemoryForestError(
        "log_read_only",
        "This account cannot write the forest's .forest folder or the agent's raw lane, so the turn was not "
        f"written. An account that can write the forest keeps it with `forest log --as {agent}`, the turn on "
        "stdin.",
        details={"agent": agent},
        next_step="report",
    )


def _seen_fresh(last: str, now: str) -> bool:
    """True when ``last`` is not older than :data:`SEEN_INTERVAL_SECONDS`."""
    try:
        then = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        moment = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (moment - then).total_seconds() <= SEEN_INTERVAL_SECONDS


def _load_seen(path: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(system.read_text(path))
    except (system.Absent, system.Link, system.NotAFile, UnicodeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _remember(root: str | os.PathLike[str], reader: str, tool: str, transport: str) -> None:
    """Record when this agent was last seen, at most once per :data:`SEEN_INTERVAL_SECONDS`.

    A lock that is already held is skipped. The call is not failed and not slowed.
    """
    safe = require_real_root(root)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = layout.resolve(safe, SEEN_FILE)
    try:
        current = _load_seen(path)
    except system.Outcome:
        return
    entry = current.get(reader)
    if isinstance(entry, dict) and _seen_fresh(str(entry.get("last") or entry.get("at") or ""), now):
        return
    try:
        layout.ensure_folder(safe, f"{layout.STATE_DIR}/mcp")
        with groups.exclusive(safe, "mcp-seen", wait_seconds=0, label="mcp seen"):
            current = _load_seen(path)
            entry = current.get(reader)
            if isinstance(entry, dict) and _seen_fresh(str(entry.get("last") or entry.get("at") or ""), now):
                return
            if not isinstance(entry, dict):
                current[reader] = {"at": now, "last": now, "tool": tool, "transport": transport}
            else:
                entry["last"] = now
                entry.setdefault("at", now)
                entry.setdefault("tool", tool)
                entry.setdefault("transport", transport)
            atomic_write(path, json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            system.set_mode(path, 0o600)
    except MemoryForestError:
        return
    except system.Outcome:
        return


def _handler(
    root: str | os.PathLike[str],
    reader: str,
    token: bytes,
    read_timeout: float,
    group: str | None = None,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self.request.settimeout(read_timeout)

        def do_POST(self) -> None:  # noqa: N802 - the library names the verb this way
            try:
                self._post()
            except (TimeoutError, socket.timeout):
                self.close_connection = True
            except Exception as exc:
                _log(f"forest mcp: one connection failed ({exc.__class__.__name__})")
                self.close_connection = True

        def do_GET(self) -> None:  # noqa: N802
            self._refused_method()

        def do_DELETE(self) -> None:  # noqa: N802
            self._refused_method()

        def do_PUT(self) -> None:  # noqa: N802
            self._refused_method()

        def log_message(self, _format: str, *_args: Any) -> None:
            """Write nothing. A request line can carry the bearer token."""
            return

        def _post(self) -> None:
            stated = self._stated_version()
            version = stated if stated is not None else ASSUMED_VERSION
            if not self._request_allowed(version):
                return
            if stated is None:
                refused = _error_body("The protocol version is not one this server speaks.", ASSUMED_VERSION)
                self._send(400, refused, ASSUMED_VERSION)
                return
            if not _json_content_type(self.headers.get("Content-Type")):
                self._send(415, _error_body("The body must be application/json.", version), version)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > MESSAGE_LIMIT:
                self.close_connection = True
                self._send(413, _error_body("The request is too large.", version), version)
                return
            raw = self.rfile.read(length)
            if len(raw) < length:
                self._send(400, _error_body("The request body was cut short.", version), version)
                return
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                broken = _unread_error(
                    -32700, "Parse error.", version, "http", omit_id=_http_error_omits_id(version)
                )
                self._send(400, json.dumps(broken).encode("utf-8"), version)
                return
            if isinstance(message, list) and version not in BATCH_VERSIONS:
                self._send(
                    400,
                    _error_body("This protocol version does not accept a batch.", version),
                    version,
                )
                return
            try:
                response, _negotiated = handle_message(root, reader, message, "http", version, group=group)
            except Exception as exc:
                _log(f"forest mcp: one request failed ({exc.__class__.__name__}) and the server is still listening")
                response = _reply_error(message, -32603, "The request could not be answered.", version, "http")
            answered = _response_version(response, version)
            if response is None:
                self._send(202, b"", answered)
                return
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self._send(200, body, answered)

        def _refused_method(self) -> None:
            version = self._stated_version() or ASSUMED_VERSION
            if version is None or not self._request_allowed(version):
                return
            self._send(405, b"", version)

        def _request_allowed(self, version: str) -> bool:
            host, port = _bound(self.server)
            if not _host_allowed(self.headers.get("Host"), host, port):
                self._send(403, _error_body("The host is not this server.", version), version)
                return False
            if not _origin_allowed(self.headers.get("Origin")):
                self._send(403, _error_body("The origin is not loopback.", version), version)
                return False
            if not _bearer_matches(self.headers.get("Authorization", ""), token):
                self._send(401, _error_body("The token does not match.", version), version)
                return False
            return True

        def _stated_version(self) -> str | None:
            header = self.headers.get("MCP-Protocol-Version")
            if header is None:
                return ASSUMED_VERSION
            if header in PROTOCOL_VERSIONS:
                return header
            return None

        def _send(self, status: int, body: bytes, version: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("MCP-Protocol-Version", version if version in PROTOCOL_VERSIONS else ASSUMED_VERSION)
            self.end_headers()
            if body:
                self.wfile.write(body)

    return Handler


def _bound(server: Any) -> tuple[str, int]:
    address = server.server_address
    return str(address[0]), int(address[1])


def _host_allowed(header: str | None, server_host: str, server_port: int) -> bool:
    """A loopback server accepts only a loopback name with its own port.

    A bind the owner set to another address also accepts that address, or any
    host when the bind is every interface, because that port was opened on
    purpose. The token is still required. The name is not resolved.
    """
    if not header:
        return False
    name, port = _split_host(header)
    if port != server_port:
        return False
    if _loopback_literal(name):
        return True
    if _loopback_literal(server_host):
        return False
    if server_host in {"0.0.0.0", "::", ""}:
        return True
    return name.lower() == server_host.lower()


def _origin_allowed(origin: str | None) -> bool:
    """Absent is a non-browser client. Present must be an HTTP loopback origin."""
    if origin is None:
        return True
    parsed = urllib.parse.urlsplit(origin.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    return _loopback_literal(parsed.hostname)


def _loopback_literal(host: str) -> bool:
    """``localhost``, ``::1``, or an address in 127.0.0.0/8. No name is resolved."""
    name = host.strip().lower().strip("[]").rstrip(".")
    if name.startswith("::ffff:"):
        name = name.removeprefix("::ffff:")
    if name in {"localhost", "::1"}:
        return True
    parts = name.split(".")
    if len(parts) != 4:
        return False
    numbers: list[int] = []
    for part in parts:
        if not part.isdigit():
            return False
        value = int(part)
        if value > 255:
            return False
        numbers.append(value)
    return numbers[0] == 127


def _split_host(value: str) -> tuple[str, int | None]:
    text = value.strip()
    if text.startswith("["):
        name, separator, rest = text[1:].partition("]")
        if not separator:
            return text, None
        if rest.startswith(":"):
            try:
                return name, int(rest[1:])
            except ValueError:
                return name, None
        return name, None
    if text.count(":") == 1:
        name, _, port_text = text.partition(":")
        try:
            return name, int(port_text)
        except ValueError:
            return text, None
    return text, None


def _json_content_type(value: str | None) -> bool:
    if not value:
        return False
    return value.split(";", 1)[0].strip().lower() == "application/json"


def _bearer_matches(header: str, token: bytes) -> bool:
    """Constant-time compare of the header's bytes. A non-ASCII header does not raise."""
    prefix = "Bearer "
    if not isinstance(header, str) or not header.startswith(prefix):
        hmac.compare_digest(token, token)
        return False
    try:
        presented = header[len(prefix) :].encode("latin-1")
    except UnicodeEncodeError:
        hmac.compare_digest(token, token)
        return False
    if len(presented) != len(token):
        hmac.compare_digest(token, token)
        return False
    return hmac.compare_digest(presented, token)


def _response_version(response: Any, applied: str) -> str:
    payloads = response if isinstance(response, list) else [response]
    for item in payloads:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("protocolVersion") in PROTOCOL_VERSIONS:
            return str(result["protocolVersion"])
    return applied if applied in PROTOCOL_VERSIONS else ASSUMED_VERSION


def _server(address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    class Server(ThreadingHTTPServer):
        def handle_error(self, request: Any, client_address: Any) -> None:
            _log("forest mcp: one connection failed and the server is still listening")

    server = Server(address, handler)
    server.daemon_threads = True
    return server


def _write_safely(stream: Any, payload: Mapping[str, Any] | list[Any]) -> bool:
    try:
        write_message(stream, payload)
    except Exception as exc:
        _log(f"forest mcp: the reply could not be written ({exc.__class__.__name__})")
        return False
    return True


def _error_body(message: str, version: str | None) -> bytes:
    """An HTTP error status. The body may omit ``id``; a 200 payload may not."""
    reply = _unread_error(-32600, message, version, "http", omit_id=_http_error_omits_id(version))
    return json.dumps(reply, ensure_ascii=False).encode("utf-8")


def _http_error_omits_id(version: str | None) -> bool:
    """True when this revision allows an error-status body to leave ``id`` out."""
    return version in NO_ID_VERSIONS or version in NO_ID_HTTP_VERSIONS


def _batch_refusal(negotiated: str | None, message: list[Any]) -> str | None:
    """Why a batch is not answered item by item, or None when it is."""
    if negotiated is None:
        return "A batch is accepted only after initialize."
    if negotiated not in BATCH_VERSIONS:
        return "This protocol version does not accept a batch."
    if not message:
        return "The batch is empty."
    if any(isinstance(item, dict) and item.get("method") == "initialize" for item in message):
        return "The initialize request is not part of a batch."
    return None


def _valid_id(value: Any) -> bool:
    """A request id is a string or an integer. A boolean is not an integer, and neither is a fraction.

    Every revision this server speaks says a request id is a string or an integer, and that it is never
    null. JSON ``1`` is an integer. JSON ``1.5`` and ``1.0`` are not.
    """
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _reply_error(message: Any, code: int, text: str, version: str | None, transport: str) -> dict[str, Any]:
    """An error answering ``message``: its own id when it could be read."""
    if isinstance(message, dict) and _valid_id(message.get("id")):
        return _error(message["id"], code, text)
    return _unread_error(code, text, version, transport)


def _unread_error(
    code: int, text: str, version: str | None, transport: str, *, omit_id: bool = False
) -> dict[str, Any]:
    """An error whose id could not be read.

    ``id`` is left out on stdio under 2025-11-25, and on an HTTP error status when ``omit_id`` is set.
    A 200 payload keeps ``id: null``.
    """
    reply = _error(None, code, text)
    drop = omit_id or (transport != "http" and version in NO_ID_VERSIONS)
    if drop:
        del reply["id"]
    return reply


def _port(text: str) -> int:
    port = int(text)
    if port < 0 or port > 65535:
        raise ValueError
    return port


# Keys the server adds. The caller can retry; it cannot use a machine's name or a process id.
_MACHINE_KEYS = frozenset({"gid", "host", "hostname", "pid", "ppid", "uid", "user", "username"})
# Resolved paths are for the person at the terminal. A tool never sends them.
_TERMINAL_KEYS = frozenset({"real_path", "root"})
_LOCK_BUSY = frozenset({"lock_already_held", "lock_busy"})
# A period is not an edge: ``root.sqlite`` is a longer name. A sentence period is handled on its own.
_PATH_EDGE = set(" \t\n\r\"'`,:;()[]{}<>|")


def _posix_absolute(text: str) -> bool:
    """An absolute path as this platform writes one. The package does not claim Windows."""
    return text.strip().startswith("/")


def _fold_prefix(text: str, prefix: str, *, alone: str, followed: str) -> str:
    """Replace ``prefix`` at a path boundary. A longer name that merely begins with it stays."""
    if not prefix:
        return text
    pieces: list[str] = []
    start = 0
    while True:
        index = text.find(prefix, start)
        if index < 0:
            pieces.append(text[start:])
            return "".join(pieces)
        after = index + len(prefix)
        before_ok = index == 0 or text[index - 1] in _PATH_EDGE
        # A period ends the path only when it ends a sentence (``in <root>.``). ``<root>.sqlite`` stays.
        period = after < len(text) and text[after] == "." and (
            after + 1 >= len(text) or text[after + 1] in _PATH_EDGE
        )
        if after >= len(text) or period:
            kind = "end"
        elif text[after] == "/":
            kind = "slash"
        elif text[after] in _PATH_EDGE:
            kind = "end"
        else:
            kind = "inside"
        if before_ok and kind != "inside":
            pieces.append(text[start:index])
            if kind == "slash":
                pieces.append(followed)
                start = after + 1
            else:
                pieces.append(alone)
                start = after + (1 if period else 0)
            continue
        pieces.append(text[start:after])
        start = after


def _identity_roots(root: str | os.PathLike[str]) -> list[str]:
    """The forest root as it was given and as it resolves, longer first."""
    given = str(root)
    expanded = str(system.absolute(given))
    try:
        resolved = str(system.real_path(expanded))
    except system.Outcome:
        resolved = expanded
    found: list[str] = []
    for item in (given, expanded, resolved):
        cleaned = item.rstrip("/")
        if cleaned and cleaned not in found:
            found.append(cleaned)
    found.sort(key=len, reverse=True)
    return found


def _home_folder() -> str:
    home = system.home_folder()
    if not home or home == "~" or home == "/":
        return ""
    return home.rstrip("/")


def _fold_refusal(value: Any, root: str | os.PathLike[str]) -> Any:
    """A refusal carries no host, process, user, home folder or absolute forest path.

    The forest root, as given and as resolved, is folded first so what follows reads as a path of the
    forest (the root alone becomes ``.``). The home folder then becomes ``~``. A success result does
    not come through here: a page is returned exactly, whatever it looks like.
    """
    roots = _identity_roots(root)
    home = _home_folder()

    def fold(item: Any) -> Any:
        if isinstance(item, dict):
            kept: dict[Any, Any] = {}
            for key, child in item.items():
                name = str(key)
                if name in _MACHINE_KEYS or name in _TERMINAL_KEYS:
                    continue
                if name == "source" and isinstance(child, str) and _posix_absolute(child):
                    continue
                kept[key] = fold(child)
            return kept
        if isinstance(item, list):
            return [fold(child) for child in item]
        if isinstance(item, str):
            text = item
            for prefix in roots:
                text = _fold_prefix(text, prefix, alone=".", followed="")
            if home:
                text = _fold_prefix(text, home, alone="~", followed="~/")
            return text
        return item

    return fold(value)


def _masking(root: str | os.PathLike[str]):
    """The ledger, already read, or the state's refusal when it cannot be had."""
    from .leaving import Masking

    try:
        config = load_config(root)
        from . import credential_ledger

        return Masking(config, credential_ledger.for_config(config))
    except MemoryForestError as exc:
        return exc
    except OSError as exc:
        return search.edge_refusal(exc, root)


def _tool_text(
    body: Any,
    root: str | os.PathLike[str],
    *,
    indent: int | None = None,
    drop_details: bool = False,
    refusal: bool = False,
) -> str:
    """The form of a tool answer. The body itself is ``leaving.body``."""
    from .leaving import body as present

    loaded = _masking(root)
    if isinstance(loaded, MemoryForestError):
        if not drop_details and not refusal:
            raise loaded
        payload = present(loaded, reader="agent", masking=None, root=str(root))
    else:
        payload = present(body, reader="agent", masking=loaded, root=str(root))
    return json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=True)


def agent_refusal(refusal: MemoryForestError, root: str | os.PathLike[str]) -> dict[str, Any]:
    """A refusal as an agent reads it. The body is the same function the command line uses."""
    from .leaving import body as present

    loaded = _masking(root)
    if isinstance(loaded, MemoryForestError):
        return present(loaded, reader="agent", masking=None, root=str(root))
    return present(refusal, reader="agent", masking=loaded, root=str(root))


# The details a refusal keeps when its values cannot be masked: words of a fixed vocabulary, never a value.
_KEPT_DETAILS = ("access", "kind", "state")


def _tool_error(
    refusal: MemoryForestError, root: str | os.PathLike[str], *, fixed: bool = False
) -> dict[str, Any]:
    """Every refusal of a tool is one JSON object with its next move, as ``leaving.body`` makes it.

    ``fixed`` is a refusal whose masking could not be read: only the fixed words leave.
    """
    if fixed:
        from .leaving import body as present

        payload = present(refusal, reader="agent", masking=None, root=str(root))
    else:
        payload = agent_refusal(refusal, root)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _result(msg_id: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": msg_id, "jsonrpc": "2.0", "result": dict(payload)}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}, "id": msg_id, "jsonrpc": "2.0"}


def _log(text: str) -> None:
    sys.stderr.write(text.rstrip("\n") + "\n")
