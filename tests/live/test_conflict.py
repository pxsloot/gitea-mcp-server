"""Unit tests for conflict detection types in ``tests/live/conflict.py``.

These tests verify the :class:`RepoRequest` contract and
:func:`check_conflict` helper — they do **not** require a live
Gitea/Forgejo instance.
"""

from __future__ import annotations

import pytest

from tests.live.conflict import (
    BootstrapVerificationError,
    ConflictError,
    RepoRequest,
    check_conflict,
)
from tests.live.world import OwnershipLedger

# ---------------------------------------------------------------------------
# RepoRequest — cache key
# ---------------------------------------------------------------------------


class TestRepoRequestCacheKey:
    """The cache key is (owner, name) regardless of options."""

    def test_same_identity_different_options(self) -> None:
        r1 = RepoRequest("dev", "repo", auto_init=True)
        r2 = RepoRequest("dev", "repo", auto_init=False)
        assert r1.cache_key == r2.cache_key == "dev/repo"

    def test_different_repos(self) -> None:
        r1 = RepoRequest("dev", "repo-a")
        r2 = RepoRequest("dev", "repo-b")
        assert r1.cache_key != r2.cache_key


# ---------------------------------------------------------------------------
# RepoRequest — assert_compatible (success path)
# ---------------------------------------------------------------------------


class TestRepoRequestCompatible:
    """Identical configuration is compatible — no ConflictError."""

    def test_identical_defaults(self) -> None:
        r1 = RepoRequest("dev", "repo")
        r2 = RepoRequest("dev", "repo")
        r1.assert_compatible(r2)  # should not raise

    def test_identical_full_options(self) -> None:
        kwargs = {
            "owner": "dev", "name": "repo",
            "auto_init": True, "description": "a test repo",
            "private": True, "branch": "feat",
            "old_branch": "develop",
            "files": (("README.md", "# hello"),),
            "labels": (("bug", "#ff0000"),),
        }
        r1 = RepoRequest(**kwargs)  # type: ignore[arg-type]
        r2 = RepoRequest(**kwargs)  # type: ignore[arg-type]
        r1.assert_compatible(r2)

    def test_none_and_explicit_description_compatible(self) -> None:
        """Description is cosmetic — None and an explicit string are compatible."""
        r1 = RepoRequest("dev", "repo", description=None)
        r2 = RepoRequest("dev", "repo", description="Something")
        r1.assert_compatible(r2)


# ---------------------------------------------------------------------------
# RepoRequest — assert_compatible (conflict path)
# ---------------------------------------------------------------------------


class TestRepoRequestConflicts:
    """Different immutable options raise ConflictError."""

    def test_auto_init_mismatch(self) -> None:
        r1 = RepoRequest("dev", "repo", auto_init=True)
        r2 = RepoRequest("dev", "repo", auto_init=False)
        with pytest.raises(ConflictError) as exc:
            r1.assert_compatible(r2)
        assert exc.value.resource == "dev/repo"
        assert "auto_init" in exc.value.detail

    def test_private_mismatch(self) -> None:
        r1 = RepoRequest("dev", "repo", private=False)
        r2 = RepoRequest("dev", "repo", private=True)
        with pytest.raises(ConflictError) as exc:
            r1.assert_compatible(r2)
        assert "private" in exc.value.detail

    def test_different_descriptions_are_compatible(self) -> None:
        """Description is cosmetic — different values do not conflict."""
        r1 = RepoRequest("dev", "repo", description="A test")
        r2 = RepoRequest("dev", "repo", description="Different")
        r1.assert_compatible(r2)  # should not raise

    def test_branch_mismatch(self) -> None:
        r1 = RepoRequest("dev", "repo", branch="feat")
        r2 = RepoRequest("dev", "repo", branch=None)
        with pytest.raises(ConflictError) as exc:
            r1.assert_compatible(r2)
        assert "branch" in exc.value.detail

    def test_old_branch_mismatch(self) -> None:
        r1 = RepoRequest("dev", "repo", branch="feat", old_branch="main")
        r2 = RepoRequest("dev", "repo", branch="feat", old_branch="develop")
        with pytest.raises(ConflictError) as exc:
            r1.assert_compatible(r2)
        assert "old_branch" in exc.value.detail

    def test_files_mismatch(self) -> None:
        r1 = RepoRequest("dev", "repo", files=(("a.txt", "hello"),))
        r2 = RepoRequest("dev", "repo", files=(("b.txt", "world"),))
        with pytest.raises(ConflictError) as exc:
            r1.assert_compatible(r2)
        assert "files" in exc.value.detail

    def test_labels_mismatch(self) -> None:
        r1 = RepoRequest("dev", "repo", labels=(("bug", "#ff0000"),))
        r2 = RepoRequest("dev", "repo", labels=(("feat", "#00ff00"),))
        with pytest.raises(ConflictError) as exc:
            r1.assert_compatible(r2)
        assert "labels" in exc.value.detail

    def test_multiple_mismatches_reported(self) -> None:
        r1 = RepoRequest("dev", "repo", auto_init=True, private=False)
        r2 = RepoRequest("dev", "repo", auto_init=False, private=True)
        with pytest.raises(ConflictError) as exc:
            r1.assert_compatible(r2)
        assert exc.value.resource == "dev/repo"
        assert "auto_init" in exc.value.detail
        assert "private" in exc.value.detail


# ---------------------------------------------------------------------------
# check_conflict helper
# ---------------------------------------------------------------------------


class TestCheckConflict:
    """Unit tests for the per-resource conflict helper."""

    def test_same_options_no_conflict(self) -> None:
        check_conflict("issue", "#1", {"body": "desc"}, {"body": "desc"})

    def test_different_value_conflict(self) -> None:
        with pytest.raises(ConflictError) as exc:
            check_conflict(
                "issue", "#1",
                {"body": "original"},
                {"body": "different"},
            )
        assert "#1" in exc.value.resource
        assert "body" in exc.value.detail

    def test_new_key_not_in_stored(self) -> None:
        """A key in the request but absent from stored options is a conflict."""
        with pytest.raises(ConflictError) as exc:
            check_conflict(
                "label", "bug",
                {"color": "#ff0000"},
                {"color": "#ff0000", "description": "Crash reports"},
            )
        assert "bug" in exc.value.resource
        assert "description" in exc.value.detail

    def test_none_vs_explicit_match(self) -> None:
        """None vs None values match."""
        check_conflict(
            "milestone", "v1",
            {"description": None, "due_date": None},
            {"description": None, "due_date": None},
        )

    def test_none_requested_means_skip(self) -> None:
        """None in requested means 'don't care' — no conflict."""
        check_conflict(
            "milestone", "v1",
            {"description": "Some text"},
            {"description": None},
        )

    def test_multiple_mismatches(self) -> None:
        with pytest.raises(ConflictError) as exc:
            check_conflict(
                "tag", "v1.0",
                {"target": "main", "message": "release"},
                {"target": "develop", "message": "prerelease"},
            )
        assert "v1.0" in exc.value.resource
        assert "target" in exc.value.detail
        assert "message" in exc.value.detail


# ---------------------------------------------------------------------------
# ConflictError properties
# ---------------------------------------------------------------------------


class TestConflictError:
    """ConflictError carries its resource identity and detail separately."""

    def test_resource_and_detail_accessible(self) -> None:
        err = ConflictError("dev/repo", "  auto_init: requested False")
        assert err.resource == "dev/repo"
        assert "auto_init" in err.detail
        assert isinstance(err, AssertionError)

    def test_str_includes_resource_and_detail(self) -> None:
        err = ConflictError("issue('Bug')", "  body: requested 'new'")
        assert "issue('Bug')" in str(err)
        assert "body" in str(err)


# ---------------------------------------------------------------------------
# BootstrapVerificationError
# ---------------------------------------------------------------------------


class TestBootstrapVerificationError:
    """BootstrapVerificationError carries entity, field, expected, observed."""

    def test_all_fields_accessible(self) -> None:
        err = BootstrapVerificationError(
            "user dev-live", "email",
            "dev@test.local", "wrong@test.local",
        )
        assert err.entity == "user dev-live"
        assert err.field == "email"
        assert err.expected == "dev@test.local"
        assert err.observed == "wrong@test.local"
        assert isinstance(err, AssertionError)

    def test_str_includes_all_context(self) -> None:
        err = BootstrapVerificationError(
            "org live-org", "full_name",
            "Expected", "Observed",
        )
        text = str(err)
        assert "org live-org" in text
        assert "full_name" in text
        assert "'Expected'" in text
        assert "'Observed'" in text

    def test_bool_fields(self) -> None:
        err = BootstrapVerificationError(
            "user dev-live", "active",
            True, False,
        )
        assert err.expected is True
        assert err.observed is False

    def test_none_values(self) -> None:
        err = BootstrapVerificationError(
            "team org/team", "units_map.repo.code",
            "write", None,
        )
        assert err.expected == "write"
        assert err.observed is None


# ---------------------------------------------------------------------------
# OwnershipLedger
# ---------------------------------------------------------------------------


class TestOwnershipLedger:
    """OwnershipLedger tracks entities created by the run."""

    def test_empty_by_default(self) -> None:
        ledger = OwnershipLedger()
        assert not ledger
        assert ledger.owned("user") == []

    def test_record_and_retrieve(self) -> None:
        ledger = OwnershipLedger()
        ledger.record("user", "dev-live", "dev-live")
        assert ledger
        owned = ledger.owned("user")
        assert owned == [("dev-live", "dev-live")]

    def test_multiple_entities_same_type(self) -> None:
        ledger = OwnershipLedger()
        ledger.record("user", "user-a", "user-a")
        ledger.record("user", "user-b", "user-b")
        owned = ledger.owned("user")
        assert len(owned) == 2
        assert ("user-a", "user-a") in owned
        assert ("user-b", "user-b") in owned

    def test_multiple_entity_types(self) -> None:
        ledger = OwnershipLedger()
        ledger.record("user", "dev", "dev")
        ledger.record("org", "live-org", "live-org")
        ledger.record("team", "org/team", "42")
        assert len(ledger.owned("user")) == 1
        assert len(ledger.owned("org")) == 1
        assert len(ledger.owned("team")) == 1

    def test_unknown_type_returns_empty(self) -> None:
        ledger = OwnershipLedger()
        ledger.record("user", "dev", "dev")
        assert ledger.owned("repo") == []

    def test_identifier_and_delete_key_can_differ(self) -> None:
        """Team stores org/name as identifier but team ID as delete_key."""
        ledger = OwnershipLedger()
        ledger.record("team", "live-org/live-team", "42")
        identifier, delete_key = ledger.owned("team")[0]
        assert identifier == "live-org/live-team"
        assert delete_key == "42"
