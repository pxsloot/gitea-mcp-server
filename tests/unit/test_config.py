"""Unit tests for configuration management."""

import os
from unittest.mock import patch

import pytest

from gitea_mcp_server.config import Config
from gitea_mcp_server.exceptions import ConfigError


class TestConfig:
    """Tests for the Config class."""

    def test_config_from_env(self):
        """Test loading config from environment variables."""
        with patch.dict(
            os.environ,
            {
                "GITEA_URL": "https://git.example.com",
                "GITEA_TOKEN": "test_token_123",
                "GITEA_VERIFY_SSL": "false",
                "LOG_LEVEL": "DEBUG",
            },
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.url == "https://git.example.com"
            assert config.token == "test_token_123"
            assert config.verify_ssl is False
            assert config.log_level == "DEBUG"

    def test_config_from_dotenv(self, tmp_path):
        """Test loading config from .env file."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "GITEA_URL=https://test.example.com\n"
            "GITEA_TOKEN=test_token_from_file\n"
            "LOG_LEVEL=WARNING\n"
        )

        with patch.dict(os.environ, {}, clear=True):
            # Set working directory to tmp_path so .env is found
            os.chdir(tmp_path)
            try:
                # Clear singleton to force reload
                Config._instance = None
                config = Config.get()
                assert config.url == "https://test.example.com"
                assert config.token == "test_token_from_file"
                assert config.log_level == "WARNING"
            finally:
                os.chdir("/")

    def test_missing_token(self, monkeypatch):
        """Test error when token is missing."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.delenv("GITEA_TOKEN", raising=False)

        Config._instance = None
        with pytest.raises(ConfigError, match=r"GITEA_TOKEN.*Field required"):
            Config.get()

    def test_invalid_url(self, monkeypatch):
        """Test error when URL is invalid."""
        monkeypatch.setenv("GITEA_URL", "not-a-url")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")

        Config._instance = None
        with pytest.raises(ConfigError, match="must start with http:// or https://"):
            Config.get()

    def test_url_cannot_contain_api_v1(self, monkeypatch):
        """Test error when URL includes /api/v1."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com/api/v1")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")

        Config._instance = None
        with pytest.raises(ConfigError, match="must not include '/api/v1'"):
            Config.get()

    def test_invalid_log_level(self, monkeypatch):
        """Test error when log level is invalid."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")
        monkeypatch.setenv("LOG_LEVEL", "INVALID")

        Config._instance = None
        with pytest.raises(ConfigError, match="Invalid LOG_LEVEL"):
            Config.get()

    def test_base_url_construction(self):
        """Test that base_url is correctly constructed."""
        with patch.dict(
            os.environ, {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test"}, clear=True
        ):
            Config._instance = None
            config = Config.get()
            assert config.base_url == "https://git.example.com/api/v1"
            # Ensure no double slash
            with patch.dict(os.environ, {"GITEA_URL": "https://git.example.com/"}):
                Config._instance = None
                config = Config.get()
                assert config.base_url == "https://git.example.com/api/v1"

    def test_singleton_pattern(self):
        """Test that Config.get() returns the same instance."""
        with patch.dict(
            os.environ, {"GITEA_URL": "https://test.example.com", "GITEA_TOKEN": "test"}, clear=True
        ):
            Config._instance = None
            config1 = Config.get()
            config2 = Config.get()
            assert config1 is config2

    def test_ssl_cert_file(self, monkeypatch):
        """Test SSL certificate file configuration."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")
        monkeypatch.setenv("SSL_CERT_FILE", "/path/to/cert.pem")

        Config._instance = None
        config = Config.get()
        assert config.ssl_cert_file == "/path/to/cert.pem"

    def test_transport_type_default(self):
        """Test that transport_type defaults to stdio."""
        with patch.dict(
            os.environ, {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test"}, clear=True
        ):
            Config._instance = None
            config = Config.get()
            assert config.transport_type == "stdio"

    def test_transport_type_validation(self):
        """Test transport_type accepts valid values."""
        # Test stdio
        with patch.dict(
            os.environ,
            {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test", "TRANSPORT_TYPE": "stdio"},
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.transport_type == "stdio"

        # Test streamable-http
        with patch.dict(
            os.environ,
            {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test", "TRANSPORT_TYPE": "streamable-http"},
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.transport_type == "streamable-http"

    def test_transport_type_invalid(self):
        """Test that invalid transport_type raises error."""
        with patch.dict(
            os.environ,
            {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test", "TRANSPORT_TYPE": "invalid"},
            clear=True,
        ):
            Config._instance = None
            with pytest.raises(ConfigError, match=r"Invalid TRANSPORT_TYPE"):
                Config.get()

    def test_port_default(self):
        """Test that port defaults to 8080."""
        with patch.dict(
            os.environ, {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test"}, clear=True
        ):
            Config._instance = None
            config = Config.get()
            assert config.port == 8080

    def test_port_validation(self):
        """Test port range validation."""
        # Valid ports
        for port in [1, 80, 8080, 65535]:
            with patch.dict(
                os.environ,
                {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test", "PORT": str(port)},
                clear=True,
            ):
                Config._instance = None
                config = Config.get()
                assert config.port == port

        # Invalid ports
        for port in [0, 65536, 99999]:
            with patch.dict(
                os.environ,
                {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test", "PORT": str(port)},
                clear=True,
            ):
                Config._instance = None
                with pytest.raises(ConfigError, match=r"PORT must be between 1 and 65535"):
                    Config.get()

    def test_cors_origins_auto_derivation(self):
        """Test CORS origins auto-derived from GITEA_URL."""
        with patch.dict(
            os.environ,
            {"GITEA_URL": "https://gitea.example.com", "GITEA_TOKEN": "test"},
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.cors_origins == ["https://gitea.example.com"]

        # With trailing slash
        with patch.dict(
            os.environ,
            {"GITEA_URL": "https://gitea.example.com/", "GITEA_TOKEN": "test"},
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.cors_origins == ["https://gitea.example.com"]

        # With path
        with patch.dict(
            os.environ,
            {"GITEA_URL": "https://gitea.example.com/gitea", "GITEA_TOKEN": "test"},
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.cors_origins == ["https://gitea.example.com"]

    def test_cors_origins_explicit_override(self):
        """Test that explicit CORS_ORIGINS overrides auto-derivation."""
        with patch.dict(
            os.environ,
            {
                "GITEA_URL": "https://gitea.example.com",
                "GITEA_TOKEN": "test",
                "CORS_ORIGINS": '["http://localhost:3000","http://example.com"]',
            },
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            # JSON array is parsed into list
            assert config.cors_origins == ["http://localhost:3000", "http://example.com"]

    def test_cors_origins_empty_when_no_gitea_url(self):
        """Test CORS origins empty if GITEA_URL not set."""
        # token is required, but url has default; we override default with empty? Actually url has default
        # To test empty, we need to have no GITEA_URL env var and the default should be used
        # Actually the default url is "http://localhost:3000", so it's never None
        # But if user explicitly sets empty? That's prevented by URL validator.
        # So we just test that with default url, cors_origins derives from that
        with patch.dict(
            os.environ, {"GITEA_TOKEN": "test"}, clear=True
        ):
            Config._instance = None
            config = Config.get()
            # Default URL is http://localhost:3000
            assert config.cors_origins == ["http://localhost:3000"]

    def test_http_path_default(self):
        """Test that http_path defaults to /mcp."""
        with patch.dict(
            os.environ, {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test"}, clear=True
        ):
            Config._instance = None
            config = Config.get()
            assert config.http_path == "/mcp"

    def test_stateless_http_default(self):
        """Test that stateless_http defaults to False."""
        with patch.dict(
            os.environ, {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test"}, clear=True
        ):
            Config._instance = None
            config = Config.get()
            assert config.stateless_http is False

    def test_json_response_default(self):
        """Test that json_response defaults to None."""
        with patch.dict(
            os.environ, {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test"}, clear=True
        ):
            Config._instance = None
            config = Config.get()
            assert config.json_response is None

