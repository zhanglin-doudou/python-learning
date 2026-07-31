"""config.py 单元测试：基于 pydantic-settings 的 Settings 配置管理。"""
from __future__ import annotations

import pytest

from config import Settings

# 所有需要清理的环境变量名
_ENV_KEYS = (
    "GITLAB_URL", "GITLAB_TOKEN", "GITLAB_WEBHOOK_SECRET",
    "GITHUB_TOKEN", "GITHUB_WEBHOOK_SECRET",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
    "LLM_MAX_TOKENS", "LLM_TEMPERATURE",
    "MAX_FILES_PER_REVIEW", "MAX_INLINE_COMMENTS", "MAX_FILE_SIZE_BYTES",
    "HOST", "PORT",
)


def _clean_env(monkeypatch):
    """删除所有相关环境变量。"""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_defaults(monkeypatch):
    """未设置任何环境变量时使用默认值。"""
    _clean_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.gitlab_url == "https://gitlab.com"
    assert s.gitlab_token == ""
    assert s.webhook_secret == ""
    assert s.github_token == ""
    assert s.github_webhook_secret == ""
    assert s.openai_api_key == ""
    assert s.openai_base_url == ""
    assert s.openai_model == "GLM-4.5-Air"
    assert s.llm_max_tokens == 20480
    assert s.llm_temperature == 0.2
    assert s.max_files_per_review == 50
    assert s.max_inline_comments == 20
    assert s.max_file_size_bytes == 200_000
    assert s.host == "0.0.0.0"
    assert s.port == 8000


def test_env_overrides(monkeypatch):
    """环境变量覆盖默认值。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com/")
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-xxx")
    monkeypatch.setenv("GITLAB_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-xxx")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "gh-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    monkeypatch.setenv("LLM_MAX_TOKENS", "2048")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.5")
    monkeypatch.setenv("MAX_FILES_PER_REVIEW", "5")
    monkeypatch.setenv("MAX_INLINE_COMMENTS", "3")
    monkeypatch.setenv("MAX_FILE_SIZE_BYTES", "1024")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "9000")

    s = Settings(_env_file=None)
    assert s.gitlab_url == "https://gitlab.example.com"  # 末尾斜杠被去掉
    assert s.gitlab_token == "glpat-xxx"
    assert s.webhook_secret == "secret"
    assert s.github_token == "ghp-xxx"
    assert s.github_webhook_secret == "gh-secret"
    assert s.openai_api_key == "sk-test"
    assert s.openai_base_url == "https://api.example.com/v1"
    assert s.openai_model == "gpt-4o"
    assert s.llm_max_tokens == 2048
    assert s.llm_temperature == 0.5
    assert s.max_files_per_review == 5
    assert s.max_inline_comments == 3
    assert s.max_file_size_bytes == 1024
    assert s.host == "127.0.0.1"
    assert s.port == 9000


def test_env_int_invalid_falls_back(monkeypatch):
    """非法的 int 环境变量回退到默认值。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("MAX_FILES_PER_REVIEW", "not-a-number")
    monkeypatch.setenv("PORT", "")
    s = Settings(_env_file=None)
    assert s.max_files_per_review == 50
    assert s.port == 8000


def test_env_float_invalid_falls_back(monkeypatch):
    """非法的 float 环境变量回退到默认值。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_TEMPERATURE", "not-a-float")
    s = Settings(_env_file=None)
    assert s.llm_temperature == 0.2


def test_env_strips_whitespace(monkeypatch):
    """环境变量值前后空白被去除。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GITLAB_TOKEN", "  token-with-spaces  ")
    monkeypatch.setenv("GITLAB_WEBHOOK_SECRET", "\tsecret\t")
    s = Settings(_env_file=None)
    assert s.gitlab_token == "token-with-spaces"
    assert s.webhook_secret == "secret"


def test_gitlab_url_trailing_slash_stripped(monkeypatch):
    """gitlab_url 末尾斜杠被去掉。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com///")
    s = Settings(_env_file=None)
    assert s.gitlab_url == "https://gitlab.example.com"


def test_settings_is_frozen(monkeypatch):
    """Settings 是 frozen model，不可变。"""
    _clean_env(monkeypatch)
    s = Settings(_env_file=None)
    with pytest.raises(Exception):
        s.gitlab_token = "other"  # type: ignore[misc]

