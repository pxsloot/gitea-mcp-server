"""Orthogonal quality contracts for live workflow steps.

Workflows describe what a Gitea user does.  Contracts describe what quality we
expect from the MCP boundary: result shape, content, format equivalence, and
useful errors.  Keeping these concerns separate prevents every workflow from
reimplementing the same assertions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tests.helpers.mcp_results import extract_text_content
from tests.live.assertions import (
    assert_content,
    assert_formats_equivalent,
    assert_key_types,
    assert_keys,
    assert_result_ok,
)

if TYPE_CHECKING:
    from mcp import ClientSession


class QualityContract:
    """Protocol-like base for contracts applied to a tool result."""

    async def verify(
        self,
        mcp: ClientSession,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
    ) -> None:
        """Verify one result; subclasses implement one quality concern."""
        raise NotImplementedError


@dataclass(frozen=True)
class JsonShape(QualityContract):
    """Assert JSON result type, required keys, and selected key types."""

    expected_type: type
    keys: tuple[str, ...] = ()
    key_types: tuple[tuple[str, type], ...] = ()

    async def verify(
        self, mcp: ClientSession, tool_name: str, args: dict[str, Any], result: Any
    ) -> None:
        data = assert_result_ok(result)
        assert isinstance(data, self.expected_type), (
            f"{tool_name}: expected {self.expected_type.__name__}, got {type(data).__name__}"
        )
        if isinstance(data, dict):
            assert_keys(data, *self.keys)
            assert_key_types(data, **dict(self.key_types))


@dataclass(frozen=True)
class JsonContent(QualityContract):
    """Assert exact values on a JSON object result."""

    expected: tuple[tuple[str, Any], ...]

    async def verify(
        self, mcp: ClientSession, tool_name: str, args: dict[str, Any], result: Any
    ) -> None:
        data = assert_result_ok(result)
        assert isinstance(data, dict), f"{tool_name}: content contract requires an object result"
        assert_content(data, **dict(self.expected))


@dataclass(frozen=True)
class FormatsEquivalent(QualityContract):
    """Assert that JSON and Markdown expose equivalent information."""

    skip_values: bool = False

    async def verify(
        self, mcp: ClientSession, tool_name: str, args: dict[str, Any], result: Any
    ) -> None:
        await assert_formats_equivalent(mcp, tool_name, args, skip_values=self.skip_values)


@dataclass(frozen=True)
class ErrorContent(QualityContract):
    """Assert a failed call contains useful, stable diagnostic text."""

    fragments: tuple[str, ...]

    async def verify(
        self, mcp: ClientSession, tool_name: str, args: dict[str, Any], result: Any
    ) -> None:
        assert result.isError, f"{tool_name}: expected an error result"
        text = extract_text_content(result.content).lower()
        missing = [fragment for fragment in self.fragments if fragment.lower() not in text]
        assert not missing, (
            f"{tool_name}: error omitted expected fragments {missing}; received {text[:500]!r}"
        )


@dataclass(frozen=True)
class TextContains(QualityContract):
    """Assert that a non-JSON result contains each required fragment."""

    fragments: tuple[str, ...]

    async def verify(
        self, mcp: ClientSession, tool_name: str, args: dict[str, Any], result: Any
    ) -> None:
        assert not result.isError, (
            f"{tool_name}: tool call failed: {extract_text_content(result.content)[:500]}"
        )
        text = extract_text_content(result.content)
        missing = [fragment for fragment in self.fragments if fragment not in text]
        assert not missing, (
            f"{tool_name}: output omitted expected fragments {missing}; received {text[:500]!r}"
        )


async def verify_contracts(
    contracts: tuple[QualityContract, ...],
    *,
    mcp: ClientSession,
    tool_name: str,
    args: dict[str, Any],
    result: Any,
) -> None:
    """Apply contracts in declaration order."""
    for contract in contracts:
        await contract.verify(mcp, tool_name, args, result)
