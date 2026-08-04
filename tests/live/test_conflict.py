"""Unit tests for conflict detection types in ``tests/live/conflict.py``.

These tests verify the :class:`RepoRequest` contract and
:func:`check_conflict` helper — they do **not** require a live
Gitea/Forgejo instance.
"""

from __future__ import annotations

import pytest

from tests.live.conflict import ConflictError, RepoRequest, check_conflict

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

    def test_none_and_empty_description_equivalent(self) -> None:
        """None and '' are treated as equivalent for description."""
        r1 = RepoRequest("dev", "repo", description=None)
        r2 = RepoRequest("dev", "repo", description="")
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

    def test_description_mismatch(self) -> None:
        r1 = RepoRequest("dev", "repo", description="A test")
        r2 = RepoRequest("dev", "repo", description="Different")
        with pytest.raises(ConflictError) as exc:
            r1.assert_compatible(r2)
        assert "description" in exc.value.detail

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

    def test_empty_string_vs_none_are_different(self) -> None:
        """Empty string and None differ — they're different default semantics."""
        with pytest.raises(ConflictError):
            check_conflict(
                "milestone", "v1",
                {"description": None},
                {"description": ""},
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
