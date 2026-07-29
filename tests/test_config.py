"""config.py 单元测试。"""
from __future__ import annotations

import importlib

import config


def _reload_config():
    """重新加载 config 模块以读取最新环境变量。"""
    importlib.reload(config)
    return config


def test_defaults(monkeypatch):
    """未设置任何环境变量时使用默认值。"""
    for key in (
        "GITLAB_URL", "GITLAB_TOKEN", "GITLAB_WEBHOOK_SECRET",
        "GITHUB_TOKEN", "GITHUB_WEBHOOK_SECRET",
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
        "LLM_MAX_TOKENS", "LLM_TEMPERATURE",
        "MAX_FILES_PER_REVIEW", "MAX_INLINE_COMMENTS", "MAX_FILE_SIZE_BYTES",
        "HOST", "PORT",
    ):
        monkeypatch.delenv(key, raising=False)

    mod = _reload_config()
    s = mod.get_settings()
    assert s.gitlab_url == "https://gitlab.com"
    assert s.gitlab_token == ""
    assert s.webhook_secret == ""
    assert s.github_token == ""
    assert s.github_webhook_secret == ""
    assert s.openai_api_key == ""
    assert s.openai_base_url == ""
    assert s.openai_model == "GLM-4.5-Air"
    assert s.llm_max_tokens == 4096
    assert s.llm_temperature == 0.2
    assert s.max_files_per_review == 50
    assert s.max_inline_comments == 20
    assert s.max_file_size_bytes == 200_000
    assert s.host == "0.0.0.0"
    assert s.port == 8000


def test_env_overrides(monkeypatch):
    """环境变量覆盖默认值。"""
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

    mod = _reload_config()
    s = mod.get_settings()
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
    monkeypatch.setenv("MAX_FILES_PER_REVIEW", "not-a-number")
    monkeypatch.setenv("PORT", "")
    mod = _reload_config()
    s = mod.get_settings()
    assert s.max_files_per_review == 50
    assert s.port == 8000


def test_env_strips_whitespace(monkeypatch):
    """环境变量值前后空白被去除。"""
    monkeypatch.setenv("GITLAB_TOKEN", "  token-with-spaces  ")
    monkeypatch.setenv("GITLAB_WEBHOOK_SECRET", "\tsecret\t")
    mod = _reload_config()
    s = mod.get_settings()
    assert s.gitlab_token == "token-with-spaces"
    assert s.webhook_secret == "secret"


def test_validate_missing_token(monkeypatch):
    """缺少 token 时 validate 报告问题。"""
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.com")
    mod = _reload_config()
    s = mod.get_settings()
    problems = s.validate()
    assert any("GITLAB_TOKEN" in p for p in problems)
    assert all("GITLAB_URL" not in p for p in problems)


def test_validate_missing_url(monkeypatch):
    """gitlab_url 为空时 validate 报告问题（直接构造 Settings）。"""
    monkeypatch.setenv("GITLAB_TOKEN", "tok")
    mod = _reload_config()
    # gitlab_url 有默认值，无法通过环境变量置空，直接构造测试 validate 逻辑
    s = mod.Settings(
        gitlab_url="", gitlab_token="tok", webhook_secret="",
        github_token="", github_webhook_secret="",
        openai_api_key="", openai_base_url="", openai_model="GLM-4.5-Air",
        llm_max_tokens=4096, llm_temperature=0.2,
        max_files_per_review=50, max_inline_comments=20,
        max_file_size_bytes=200_000, host="0.0.0.0", port=8000,
    )
    problems = s.validate()
    assert any("GITLAB_URL" in p for p in problems)


def test_validate_ok(monkeypatch):
    """配置完整时 validate 返回空列表。"""
    monkeypatch.setenv("GITLAB_TOKEN", "tok")
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.com")
    mod = _reload_config()
    s = mod.get_settings()
    assert s.validate() == []


def test_settings_is_frozen(monkeypatch):
    """Settings 是 frozen dataclass，不可变。"""
    monkeypatch.setenv("GITLAB_TOKEN", "tok")
    mod = _reload_config()
    s = mod.get_settings()
    try:
        s.gitlab_token = "other"  # type: ignore[misc]
        raise AssertionError("应抛出 FrozenInstanceError")
    except AttributeError:
        pass
