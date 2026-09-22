"""Documentation guard for the ``Config`` surface.

Every ``Config`` field must be documented in the enduser config reference
(``README.md``) and the example env file (``.env.example``).  Adding a field
without documenting it fails here rather than shipping an undocumented knob
(#785).  ``ConfigProtocol`` is a hand-written mirror of ``Config``; the same
guard keeps the two from drifting.
"""

from __future__ import annotations

from pathlib import Path

from gitea_mcp_server.config import Config, ConfigProtocol

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
ENV_EXAMPLE = ROOT / ".env.example"


def _config_aliases() -> dict[str, str]:
    """Map every ``Config`` field name to its environment-variable name."""
    return {
        name: str(field.alias) if field.alias else name
        for name, field in Config.model_fields.items()
    }


def test_every_config_field_documented_in_readme() -> None:
    """README documents every Config field by its env-var name."""
    text = README.read_text(encoding="utf-8")
    missing = sorted(alias for alias in _config_aliases().values() if alias not in text)
    assert not missing, f"Config fields missing from README.md: {missing}"


def test_every_config_field_documented_in_env_example() -> None:
    """``.env.example`` lists every Config field by its env-var name."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    missing = sorted(alias for alias in _config_aliases().values() if alias not in text)
    assert not missing, f"Config fields missing from .env.example: {missing}"


def test_config_protocol_mirrors_config_fields() -> None:
    """``ConfigProtocol`` is a manual mirror; it must not drift from Config."""
    protocol_fields = set(ConfigProtocol.__annotations__)
    config_fields = set(Config.model_fields)
    assert protocol_fields == config_fields, (
        f"ConfigProtocol/Config drift — only in protocol: "
        f"{sorted(protocol_fields - config_fields)}; only in Config: "
        f"{sorted(config_fields - protocol_fields)}"
    )
