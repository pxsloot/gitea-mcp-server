import argparse
import atexit
import cmd
import contextlib
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, cast

from gitea_mcp_client.session import Session, SessionError
from gitea_mcp_client.transport import Transport, TransportError


def _try_pretty_json(text: str) -> bool:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False
    if isinstance(parsed, list):
        _pretty_list(parsed)
    elif isinstance(parsed, dict):
        # If it has a "result" key with a list, show that
        inner = parsed.get("result") or parsed
        if isinstance(inner, list):
            _pretty_list(inner)
        else:
            print(json.dumps(parsed, indent=2))
    else:
        print(parsed)
    return True


def _pretty_content(data: dict[str, Any]) -> None:
    content_list = data.get("content")
    if content_list is None:
        print(json.dumps(data, indent=2))
        return
    if data.get("isError"):
        print("\033[31m[ERROR]\033[0m")
    for item in content_list:
        itype = item.get("type")
        if itype == "text":
            text = item.get("text", "")
            if not _try_pretty_json(text):
                print(text)
        elif itype == "resource":
            res = item.get("resource", {})
            print(f"--- Resource: {res.get('uri', 'unknown')} ---")
            text = res.get("text")
            if text:
                print(text)
            elif "blob" in res:
                print(f"(binary, {len(res['blob'])} bytes)")
            else:
                print(json.dumps(res, indent=2))
        else:
            print(json.dumps(item, indent=2))


def _pretty_list(items: list[Any]) -> None:
    for i, item in enumerate(items):
        if isinstance(item, dict):
            name = item.get("name") or item.get("uri") or item.get("title", "")
            desc = item.get("description", "").replace("\n", " ").strip()
            print(f"  {i + 1}. {name}")
            if desc:
                print(f"     {desc}")
            schema = item.get("inputSchema", {})
            props = schema.get("properties", {})
            required = schema.get("required", [])
            if props:
                print(f"     Args: {', '.join(props.keys())}")
                if required:
                    print(f"     Required: {', '.join(required)}")
        else:
            print(f"  {i + 1}. {item}")


def pretty_print(data: Any) -> None:
    if isinstance(data, dict):
        if "content" in data:
            _pretty_content(data)
        elif "tools" in data or "resources" in data:
            print(json.dumps(data, indent=2))
        else:
            print(json.dumps(data, indent=2))
    elif isinstance(data, (list, tuple)):
        _pretty_list(list(data))
    elif isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, indent=2))


MAX_COMPLETION_ARGS = 2


def _safe_index(items: list[str], idx: int) -> str | None:
    try:
        return items[idx]
    except IndexError:
        return None


class MCPCli(cmd.Cmd):
    intro = (
        "gitea-mcp-client -- interactive MCP client\n"
        "Type help or ? to list commands.\n"
    )
    prompt = "(gitea-mcp) "

    def __init__(self, session: Session, raw: bool = False) -> None:
        super().__init__()
        self._session = session
        self._raw = raw
        self._context: dict[str, str] = {}
        self._setup_completion()

    def _setup_completion(self) -> None:
        try:
            import readline  # noqa: PLC0415 -- only available on Unix

            readline.set_completer(self._complete)
            readline.parse_and_bind("tab: complete")
            histfile = str(Path.home() / ".gitea-mcp-cli-history")
            with contextlib.suppress(FileNotFoundError):
                readline.read_history_file(histfile)
            atexit.register(lambda: readline.write_history_file(histfile))
        except ImportError:
            pass

    def _complete(self, text: str, state: int) -> str | None:
        try:
            import readline  # noqa: PLC0415 -- only available on Unix

            line = readline.get_line_buffer()
            tokens = shlex.split(line) if line.strip() else []
        except (ImportError, ModuleNotFoundError):
            tokens = []

        if not tokens:
            commands = [c[3:] for c in dir(self) if c.startswith("do_") and c != "do_EOF"]
            return _safe_index([c for c in commands if c.startswith(text)], state)

        cmd_name = tokens[0]
        if cmd_name in ("call", "c") and len(tokens) == MAX_COMPLETION_ARGS:
            tools = self._session.tools
            return _safe_index(
                [str(t["name"]) for t in tools if str(t["name"]).startswith(text)],
                state,
            )

        if cmd_name in ("read", "r") and len(tokens) == MAX_COMPLETION_ARGS:
            resources = self._session.resources
            candidates = [
                str(res["uri"])
                for res in resources
                if str(res.get("uri", "")).startswith((text, text.replace("{", "").replace("}", "")))
            ]
            if not candidates:
                candidates = [str(res.get("uri", "")) for res in resources]
            return _safe_index(candidates, state)

        return None

    def _substitute(self, value: str) -> str:
        return value.format(**self._context)

    @staticmethod
    def _parse_args(arg: str) -> dict[str, Any]:
        if not arg.strip():
            return {}
        try:
            return cast("dict[str, Any]", json.loads(arg))
        except json.JSONDecodeError:
            pass
        parts = shlex.split(arg)
        args: dict[str, Any] = {}
        for part in parts:
            if "=" in part:
                key, _, val = part.partition("=")
                args[key] = val
            else:
                try:
                    return cast("dict[str, Any]", json.loads(arg))
                except json.JSONDecodeError:
                    print(f"Could not parse argument: {part}")
                    return {}
        return args

    def _resolve_resource_uri(self, text: str) -> str:
        resolved = self._substitute(text)
        for res in self._session.resources:
            uri = str(res.get("uri", ""))
            if uri == resolved:
                return uri
            if "{" in uri:
                return resolved
        return resolved

    # ---- Commands ----

    def do_list_tools(self, _arg: str) -> None:
        """List all available tools. Alias: ls, lt"""
        try:
            tools = self._session.list_tools()
        except SessionError as e:
            print(f"Error: {e}")
            return
        if not tools:
            print("No tools available.")
            return
        print(f"\n=== Tools ({len(tools)}) ===\n")
        for t in tools:
            name = t.get("name", "?")
            desc = t.get("description", "").replace("\n", " ").strip()
            print(f"  \033[1m{name}\033[0m")
            if desc:
                print(f"    {desc}")
            schema = t.get("inputSchema", {})
            props = schema.get("properties", {})
            required = schema.get("required", [])
            if props:
                print(f"    Arguments: {', '.join(props.keys())}")
                if required:
                    print(f"    Required:  {', '.join(required)}")
            print()

    do_ls = do_list_tools
    do_lt = do_list_tools

    def do_call_tool(self, arg: str) -> None:
        """Call a tool. Usage: call <tool_name> [args...]
        Args can be JSON: call tool {"key":"val"}
        Or key=value:  call tool key=val key2=val2
        Context vars ({owner}, {repo}) are substituted in arg values."""
        if not arg.strip():
            print("Usage: call <tool_name> [args...]")
            return

        parts = shlex.split(arg)
        name = parts[0]
        args_raw = " ".join(parts[1:]) if len(parts) > 1 else ""

        parsed = self._parse_args(args_raw)
        parsed = {k: self._substitute(v) if isinstance(v, str) else v for k, v in parsed.items()}

        try:
            result = self._session.call_tool(name, parsed if parsed else None)
        except SessionError as e:
            print(f"Error: {e}")
            return

        if self._raw:
            print(json.dumps(result, indent=2))
        else:
            pretty_print(result)

    do_call = do_call_tool
    do_c = do_call_tool

    def do_list_resources(self, _arg: str) -> None:
        """List all available resources. Alias: lr, lsres"""
        try:
            resources = self._session.list_resources()
        except SessionError as e:
            print(f"Error: {e}")
            return
        if not resources:
            print("No resources available.")
            return

        print(f"\n=== Resources ({len(resources)}) ===\n")
        for r in resources:
            uri = r.get("uri", "?")
            name = r.get("name", "")
            rtype = r.get("type", "")
            mime = r.get("mimeType", "")
            desc = r.get("description", "").replace("\n", " ").strip()
            tags = r.get("tags", [])
            scope = r.get("required_scope")

            print(f"  \033[1m{uri}\033[0m")
            if name:
                print(f"    Name: {name}")
            if rtype:
                print(f"    Type: {rtype}")
            if mime:
                print(f"    MIME: {mime}")
            if desc:
                print(f"    {desc}")
            if tags:
                print(f"    Tags: {', '.join(tags)}")
            if scope:
                print(f"    Scope: {scope}")
            print()

    do_lr = do_list_resources
    do_lsres = do_list_resources

    def do_read_resource(self, arg: str) -> None:
        """Read a resource. Usage: read <uri>
        Context vars ({owner}, {repo}) are substituted in URI."""
        if not arg.strip():
            print("Usage: read <uri>")
            return

        uri = self._resolve_resource_uri(arg.strip())

        try:
            result = self._session.read_resource(uri)
        except SessionError as e:
            print(f"Error: {e}")
            return

        if self._raw:
            print(json.dumps(result, indent=2))
        else:
            pretty_print(result)

    do_read = do_read_resource
    do_r = do_read_resource

    def do_context(self, arg: str) -> None:
        """Show or set context variables. Usage:
          context          -- show all variables
          context key=val  -- set a variable
          context key=     -- unset a variable
        Context vars ({key}) are substituted in tool arg values and resource URIs."""
        if not arg.strip():
            if not self._context:
                print("No context variables set.")
                print("Use: context key=value")
                return
            print("Context variables:")
            for k, v in sorted(self._context.items()):
                print(f"  {k} = {v}")
            return

        if "=" in arg:
            key, _, val = arg.partition("=")
            key = key.strip()
            val = val.strip()
            if val:
                self._context[key] = val
                print(f"Set {key} = {val}")
            else:
                self._context.pop(key, None)
                print(f"Unset {key}")
        else:
            print("Usage: context key=value")

    do_ctx = do_context

    def do_search_tools(self, arg: str) -> None:
        """Search tools via server's search_tools. Usage: search <query>"""
        if not arg.strip():
            print("Usage: search <query>")
            return

        try:
            result = self._session.call_tool("gitea_search_tools", {"query": arg.strip()})
        except SessionError as e:
            print(f"Error: {e}")
            return

        if self._raw:
            print(json.dumps(result, indent=2))
        else:
            pretty_print(result)

    do_search = do_search_tools
    do_find = do_search_tools
    do_tools = do_search_tools

    def do_tool_info(self, arg: str) -> None:
        """Get full schema for a tool. Usage: info <tool_name>"""
        if not arg.strip():
            print("Usage: info <tool_name>")
            return

        try:
            result = self._session.call_tool("gitea_tool_info", {"name": arg.strip()})
        except SessionError as e:
            print(f"Error: {e}")
            return

        if self._raw:
            print(json.dumps(result, indent=2))
        else:
            pretty_print(result)

    do_info = do_tool_info

    def do_pretty(self, _arg: str) -> None:
        """Toggle pretty-print mode (default: on)."""
        self._raw = False
        print("Pretty-print mode ON")

    def do_raw(self, _arg: str) -> None:
        """Toggle raw JSON output mode."""
        self._raw = True
        print("Raw JSON mode ON")

    def do_reconnect(self, _arg: str) -> None:
        """Reconnect: re-list tools and resources."""
        try:
            tools = self._session.list_tools()
            resources = self._session.list_resources()
            print(f"Reconnected. {len(tools)} tools, {len(resources)} resources.")
        except SessionError as e:
            print(f"Error: {e}")

    def do_EOF(self, _arg: str) -> bool:
        """Exit on Ctrl-D"""
        return True

    def do_exit(self, _arg: str) -> bool:
        """Exit the client."""
        return True

    do_quit = do_exit
    do_q = do_exit

    def default(self, line: str) -> None:
        if line.strip():
            print(f"Unknown command: {line.strip()}")
            print("Type help or ? for available commands.")

    def emptyline(self) -> bool:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive MCP client for gitea-mcp-server")
    parser.add_argument(
        "--server-cmd",
        default=os.environ.get("GITEA_MCP_SERVER_CMD", "gitea-mcp"),
        help="Server command (default: gitea-mcp, or $GITEA_MCP_SERVER_CMD)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Start in raw JSON mode (default: pretty-print)",
    )
    args = parser.parse_args()

    server_cmd = shlex.split(args.server_cmd)
    transport: Transport | None = None

    try:
        transport = Transport(server_cmd)
        transport.wait_ready()
        session = Session(transport)

        server_info = session.initialize()
        server_name = server_info.get("serverInfo", {}).get("name", "MCP server")
        server_ver = server_info.get("serverInfo", {}).get("version", "?")
        proto = server_info.get("protocolVersion", "?")
        print(f"Connected: {server_name} v{server_ver}")
        print(f"Protocol: {proto}")

        session.list_tools()
        session.list_resources()

        cli = MCPCli(session, raw=args.raw)
        cli.cmdloop()
    except TransportError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nGoodbye.")
    finally:
        if transport is not None:
            transport.close()
