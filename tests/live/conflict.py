"""Conflict detection for repeated dependency requests.

When two tests request the same resource identity with different
configuration options, this module surfaces the conflict as a clear
diagnostic rather than silently returning the first result.

Classes:
    ConflictError: Raised when a repeated request conflicts with a
                   previously-materialised resource.
    BootstrapVerificationError: Raised when a pre-existing bootstrap
                   entity (user, org, team) does not match expected
                   configuration.
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


class BootstrapVerificationError(AssertionError):
    """A pre-existing bootstrap entity does not match expected configuration.

    Raised when ``need_user``, ``need_org``, or ``need_team`` encounters
    an entity that already exists on the Gitea instance but whose
    configuration differs from what the live suite requires.

    Attributes:
        entity: Human-readable entity identifier (e.g. ``"user live-dev-abc"``).
        field: The configuration field that mismatched.
        expected: The value required by the test suite.
        observed: The value found on the Gitea instance.
    """

    def __init__(
        self, entity: str, field: str, expected: object, observed: object,
    ) -> None:
        self.entity = entity
        self.field = field
        self.expected = expected
        self.observed = observed
        super().__init__(
            f"Bootstrap entity mismatch for {entity!r}: "
            f"{field} expected {expected!r}, got {observed!r}"
        )


# ---------------------------------------------------------------------------
# RepoRequest — world-level repository contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoRequest:
    """Stable identity and immutable configuration for a test repository.

    The *cache key* is ``(owner, name)`` — same repo identity regardless
    of options.  The *contract* covers structural fields that affect how
    the repo is set up (*auto_init*, *private*, *branch*, *old_branch*,
    *files*, *labels*).  Cosmetic fields (*description*) are ignored —
    different tests can set different descriptions for the same repo
    without conflict.

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
        # description is NOT checked — cosmetic metadata, not structural.
        if mismatches:
            raise ConflictError(self.cache_key, "\n".join(mismatches))


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

    A requested value of ``None`` means "don't care" — the field is
    skipped in the comparison.  This lets callers retrieve a cached
    entity without repeating every creation parameter.

    Args:
        entity_type: Label for the resource (e.g. ``"issue"``, ``"label"``).
        identity: Human-readable identity (e.g. ``"Bug report"``).
        stored_options: Options used when the resource was first created.
        requested_options: Options from the current request.
    """
    mismatches: list[str] = []
    for key, stored_val in stored_options.items():
        requested_val = requested_options.get(key)
        # None means "don't care" — skip the comparison for this field
        if requested_val is None:
            continue
        if requested_val != stored_val:
            mismatches.append(
                f"  {key}: requested {requested_val!r}, "
                f"already created with {stored_val!r}"
            )
    # Also catch keys present in the new request but absent from stored.
    for key, requested_val in requested_options.items():
        if requested_val is None:
            continue
        if key not in stored_options:
            mismatches.append(
                f"  {key}: requested {requested_val!r}, "
                f"not present in stored options"
            )
    if mismatches:
        resource = f"{entity_type}({identity!r})"
        raise ConflictError(resource, "\n".join(mismatches))
