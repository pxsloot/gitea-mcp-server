"""Conflict detection for repeated dependency requests.

When two tests request the same resource identity with different
configuration options, this module surfaces the conflict as a clear
diagnostic rather than silently returning the first result.

Classes:
    ConflictError: Raised when a repeated request conflicts with a
                   previously-materialised resource.
    RepoRequest: Frozen contract encoding both the identity (owner, name)
                 and the immutable configuration of a test repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ConflictError(AssertionError):
    """A dependency was requested twice with incompatible configuration.

    Attributes:
        resource: Human-readable resource identifier (e.g. ``"dev/repo"``).
        detail: Multi-line description of the conflicting fields.
    """

    def __init__(self, resource: str, detail: str) -> None:
        self.resource = resource
        self.detail = detail
        super().__init__(f"Conflict for {resource!r}:\n{detail}")


# ---------------------------------------------------------------------------
# RepoRequest — world-level repository contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoRequest:
    """Stable identity and immutable configuration for a test repository.

    The *cache key* is ``(owner, name)`` — same repo identity regardless
    of options.  The *contract* includes every field: a later request for
    the same identity must match or raise :class:`ConflictError`.

    ``user`` and ``scopes`` are deliberately excluded from the contract
    because they are access credentials, not repository configuration.
    """

    owner: str
    name: str
    auto_init: bool = True
    description: str | None = None
    private: bool = False
    branch: str | None = None
    old_branch: str = "main"
    files: tuple[tuple[str, str], ...] = ()
    """Frozen ``((path, content), ...)`` tuples."""
    labels: tuple[tuple[str, str], ...] = ()
    """Frozen ``((name, color), ...)`` tuples."""

    # Fields deliberately excluded from the contract:
    #   user   — access credential (not repo config)
    #   scopes — access credential (not repo config)

    @property
    def cache_key(self) -> str:
        """Return the identity-only key ``"owner/name"``."""
        return f"{self.owner}/{self.name}"

    def assert_compatible(self, other: RepoRequest) -> None:
        """Raise :class:`ConflictError` if *other* differs in any contract field.

        Called on cache hit — the stored request is ``self``, the new
        request is ``other``.  Compatible means every field matches.
        """
        mismatches: list[str] = []
        for field_name in (
            "auto_init",
            "private",
            "branch",
            "old_branch",
            "files",
            "labels",
        ):
            a = getattr(self, field_name)
            b = getattr(other, field_name)
            if a != b:
                mismatches.append(
                    f"  {field_name}: requested {b!r}, "
                    f"already created with {a!r}"
                )
        # description can be None or a string — treat None and "" as
        # equivalent (the API treats missing description as empty).
        if _normalize_str(self.description) != _normalize_str(other.description):
            mismatches.append(
                f"  description: requested {other.description!r}, "
                f"already created with {self.description!r}"
            )
        if mismatches:
            raise ConflictError(self.cache_key, "\n".join(mismatches))


def _normalize_str(value: str | None) -> str:
    """Treat ``None`` and ``""`` as equivalent for optional text fields."""
    return (value or "") if value is not None else ""


# ---------------------------------------------------------------------------
# Per-resource conflict helper (used by RepoState need_* methods)
# ---------------------------------------------------------------------------


def check_conflict(
    entity_type: str,
    identity: str,
    stored_options: dict[str, Any],
    requested_options: dict[str, Any],
) -> None:
    """Raise :class:`ConflictError` if *requested_options* differ from *stored_options*.

    Args:
        entity_type: Label for the resource (e.g. ``"issue"``, ``"label"``).
        identity: Human-readable identity (e.g. ``"Bug report"``).
        stored_options: Options used when the resource was first created.
        requested_options: Options from the current request.
    """
    mismatches: list[str] = []
    for key, stored_val in stored_options.items():
        requested_val = requested_options.get(key)
        if requested_val != stored_val:
            mismatches.append(
                f"  {key}: requested {requested_val!r}, "
                f"already created with {stored_val!r}"
            )
    # Also catch keys present in the new request but absent from stored.
    for key, requested_val in requested_options.items():
        if key not in stored_options:
            mismatches.append(
                f"  {key}: requested {requested_val!r}, "
                f"not present in stored options"
            )
    if mismatches:
        resource = f"{entity_type}({identity!r})"
        raise ConflictError(resource, "\n".join(mismatches))
