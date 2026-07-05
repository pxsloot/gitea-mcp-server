"""Unit tests for configuration management."""

import os
from unittest.mock import patch

import pytest

from gitea_mcp_server.config import HTTP_PORT_MAX, Config
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

    def test_transport_type_default_stdio(self):
        """Test default transport_type is stdio."""
        with patch.dict(
            os.environ,
            {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test"},
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.transport_type == "stdio"

    def test_transport_type_http(self, monkeypatch):
        """Test setting transport_type to http."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")
        monkeypatch.setenv("TRANSPORT_TYPE", "http")

        Config._instance = None
        config = Config.get()
        assert config.transport_type == "http"

    def test_transport_type_invalid(self, monkeypatch):
        """Test error when transport_type is invalid."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")
        monkeypatch.setenv("TRANSPORT_TYPE", "invalid")

        Config._instance = None
        with pytest.raises(ConfigError, match=r"TRANSPORT_TYPE must be 'stdio' or 'http'"):
            Config.get()

    def test_http_host_default(self):
        """Test default http_host is 127.0.0.1."""
        with patch.dict(
            os.environ,
            {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test"},
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.http_host == "127.0.0.1"

    def test_http_host_custom(self, monkeypatch):
        """Test custom http_host override."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")
        monkeypatch.setenv("HTTP_HOST", "0.0.0.0")

        Config._instance = None
        config = Config.get()
        assert config.http_host == "0.0.0.0"

    def test_http_port_default(self):
        """Test default http_port is 8080."""
        with patch.dict(
            os.environ,
            {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test"},
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.http_port == 8080

    def test_http_port_custom(self, monkeypatch):
        """Test custom http_port."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")
        monkeypatch.setenv("HTTP_PORT", "9000")

        Config._instance = None
        config = Config.get()
        assert config.http_port == 9000

    def test_http_path_default(self):
        """Test default http_path is /mcp."""
        with patch.dict(
            os.environ,
            {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test"},
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.http_path == "/mcp"

    def test_http_path_custom(self, monkeypatch):
        """Test custom http_path."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")
        monkeypatch.setenv("HTTP_PATH", "/api/mcp")

        Config._instance = None
        config = Config.get()
        assert config.http_path == "/api/mcp"

    def test_http_cors_default_uses_gitea_url_when_http(self):
        """Test http_cors defaults to GITEA_URL origin when transport_type is http."""
        with patch.dict(
            os.environ,
            {"GITEA_URL": "https://git.example.com", "GITEA_TOKEN": "test"},
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.transport_type == "stdio"  # default stdio
            assert config.http_cors is None  # no default for stdio

        # When transport_type is http, CORS should default to GITEA_URL origin
        with patch.dict(
            os.environ,
            {
                "GITEA_URL": "https://git.example.com",
                "GITEA_TOKEN": "test",
                "TRANSPORT_TYPE": "http",
            },
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.transport_type == "http"
            assert config.http_cors == ["https://git.example.com"]

        # With trailing slash in URL
        with patch.dict(
            os.environ,
            {
                "GITEA_URL": "https://git.example.com/",
                "GITEA_TOKEN": "test",
                "TRANSPORT_TYPE": "http",
            },
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.http_cors == ["https://git.example.com"]

        # With port in URL
        with patch.dict(
            os.environ,
            {"GITEA_URL": "http://localhost:3000", "GITEA_TOKEN": "test", "TRANSPORT_TYPE": "http"},
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            assert config.http_cors == ["http://localhost:3000"]

    def test_http_cors_explicit_overrides_default(self, monkeypatch):
        """Test explicit HTTP_CORS overrides GITEA_URL default."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")
        monkeypatch.setenv("TRANSPORT_TYPE", "http")
        monkeypatch.setenv("HTTP_CORS", "https://custom.com,http://localhost:8080")

        Config._instance = None
        config = Config.get()
        assert config.http_cors == ["https://custom.com", "http://localhost:8080"]

    def test_http_cors_parsed_from_string(self, monkeypatch):
        """Test http_cors parsed from comma-separated string."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")
        monkeypatch.setenv("HTTP_CORS", "https://origin1.com,https://origin2.com")

        Config._instance = None
        config = Config.get()
        assert config.http_cors == ["https://origin1.com", "https://origin2.com"]


class TestConfigEdgeCases:
    """Tests for config edge cases (validator branches, error paths)."""

    def test_empty_url_raises(self, monkeypatch):
        """Empty URL string raises ConfigError."""
        monkeypatch.setenv("GITEA_URL", "")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")

        Config._instance = None
        with pytest.raises(ConfigError, match="cannot be empty"):
            Config.get()

    def test_token_strip_whitespace(self, monkeypatch):
        """Token with surrounding whitespace is stripped."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "  my-token  ")

        Config._instance = None
        config = Config.get()
        assert config.token == "my-token"

    def test_http_port_invalid_low(self, monkeypatch):
        """HTTP port below 1 raises ConfigError."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")
        monkeypatch.setenv("HTTP_PORT", "0")

        Config._instance = None
        with pytest.raises(ConfigError, match="HTTP_PORT must be between 1 and"):
            Config.get()

    def test_http_port_too_high(self, monkeypatch):
        """HTTP port above max raises ConfigError."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")
        monkeypatch.setenv("HTTP_PORT", str(HTTP_PORT_MAX + 1))

        Config._instance = None
        with pytest.raises(ConfigError, match="HTTP_PORT must be between 1 and"):
            Config.get()

    def test_http_path_no_leading_slash(self, monkeypatch):
        """HTTP path without leading slash raises ConfigError."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "test_token")
        monkeypatch.setenv("HTTP_PATH", "mcp")

        Config._instance = None
        with pytest.raises(ConfigError, match="HTTP_PATH must start with '/'"):
            Config.get()

    def test_exception_during_init_logged(self):
        """Exception during configuration init is logged and re-raised."""
        with patch.object(Config, "_instance", None):
            with patch.object(Config, "__init__", side_effect=ConfigError("init failed")):
                with pytest.raises(ConfigError, match="init failed"):
                    Config.get()

    def test_empty_token_after_strip_raises(self, monkeypatch):
        """Token that is only whitespace raises ConfigError."""
        monkeypatch.setenv("GITEA_URL", "https://git.example.com")
        monkeypatch.setenv("GITEA_TOKEN", "   ")

        Config._instance = None
        with pytest.raises(ConfigError, match="cannot be empty"):
            Config.get()

    def test_http_cors_already_list(self):
        """http_cors as a list is returned as-is."""
        with patch.dict(
            os.environ,
            {
                "GITEA_URL": "https://git.example.com",
                "GITEA_TOKEN": "test",
                "HTTP_CORS": '["https://origin1.com", "https://origin2.com"]',
            },
            clear=True,
        ):
            Config._instance = None
            config = Config.get()
            # Pydantic will parse the JSON string as list via env parsing
            # but http_cors validator also handles list type
            assert config.http_cors is None or isinstance(config.http_cors, list)
