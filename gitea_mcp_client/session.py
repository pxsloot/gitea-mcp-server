from typing import Any, cast

from gitea_mcp_client.transport import Transport


class SessionError(Exception):
    pass


class Session:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._next_id = 1
        self._tools: list[dict[str, Any]] = []
        self._resources: list[dict[str, Any]] = []

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        msg_id = self._next_id
        self._next_id += 1
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
        }
        if params is not None:
            msg["params"] = params

        self._transport.send(msg)
        response = self._transport.recv()

        if "error" in response:
            err = response["error"]
            msg_text = f"{err.get('message', 'Unknown error')} (code {err.get('code')})"
            raise SessionError(msg_text)
        return cast("dict[str, Any]", response.get("result", {}))

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            msg["params"] = params
        self._transport.send(msg)

    def initialize(self) -> dict[str, Any]:
        result = self._request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {
                "name": "gitea-mcp-client",
                "version": "0.1.0",
            },
        })
        self._notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list")
        self._tools = result.get("tools", [])
        return self._tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return self._request("tools/call", params)

    def list_resources(self) -> list[dict[str, Any]]:
        result = self._request("resources/list")
        self._resources = result.get("resources", [])
        return self._resources

    def read_resource(self, uri: str) -> Any:
        return self._request("resources/read", {"uri": uri})

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self._tools

    @property
    def resources(self) -> list[dict[str, Any]]:
        return self._resources

    def close(self) -> None:
        self._transport.close()
