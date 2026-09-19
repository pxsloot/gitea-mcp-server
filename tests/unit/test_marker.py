"""Unit tests for gitea_mcp_server/marker.py.

The canonical agent-facing ``$ref`` marker contract (#763):

- ``ref_marker`` — build the marker
- ``is_ref_marker`` — structurally strict detection
- ``ref_marker_label`` — render the marker for markdown/scalar output

Cross-producer and cross-surface agreement is tested in ``test_contract.py``.
"""

from gitea_mcp_server.marker import (
    is_ref_marker,
    ref_marker,
    ref_marker_label,
)


class TestRefMarker:
    """The canonical agent-facing ``$ref`` marker contract (#763)."""

    def test_object_marker_is_single_key(self) -> None:
        assert ref_marker("User") == {"$ref": "User"}

    def test_list_marker_carries_count(self) -> None:
        assert ref_marker("Label", 2) == {"$ref": "Label", "count": 2}

    def test_zero_count_is_kept(self) -> None:
        """A collapsed empty list still carries ``count: 0`` (not omitted)."""
        assert ref_marker("Label", 0) == {"$ref": "Label", "count": 0}

    def test_is_ref_marker_accepts_both_forms(self) -> None:
        assert is_ref_marker({"$ref": "User"}) is True
        assert is_ref_marker({"$ref": "Label", "count": 2}) is True
        assert is_ref_marker({"$ref": "Label", "count": 0}) is True

    def test_is_ref_marker_rejects_other_shapes(self) -> None:
        assert is_ref_marker("$ref:User") is False  # the retired string form
        assert is_ref_marker({"$ref": 1}) is False  # type name must be a string
        assert is_ref_marker({}) is False
        assert is_ref_marker(None) is False
        assert is_ref_marker([{"$ref": "User"}]) is False
        # Extra keys beyond count are not the marker shape.
        assert is_ref_marker({"$ref": "Label", "count": 2, "extra": 1}) is False

    def test_is_ref_marker_count_must_be_int(self) -> None:
        assert is_ref_marker({"$ref": "Label", "count": "2"}) is False
        # ``bool`` is an ``int`` subclass — a boolean count is not a count.
        assert is_ref_marker({"$ref": "Label", "count": True}) is False

    def test_is_ref_marker_rejects_json_schema_pointers(self) -> None:
        """A schema ``$ref`` pointer is not a marker (#763).

        The marker carries a bare type name; the pointer form must be rejected
        so a schema fragment rendered by a formatter is never collapsed to a
        bogus ``$ref:#/...`` label.
        """
        assert is_ref_marker({"$ref": "#/components/schemas/User"}) is False
        assert is_ref_marker({"$ref": "components/schemas/User"}) is False
        assert is_ref_marker({"$ref": ""}) is False

    def test_label_renders_object_marker(self) -> None:
        assert ref_marker_label(ref_marker("User")) == "$ref:User"

    def test_label_renders_list_marker_with_count(self) -> None:
        assert ref_marker_label(ref_marker("Label", 2)) == "$ref:Label[2]"

    def test_label_renders_zero_count_list_marker(self) -> None:
        assert ref_marker_label(ref_marker("Label", 0)) == "$ref:Label[0]"
