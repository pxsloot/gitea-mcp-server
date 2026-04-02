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
