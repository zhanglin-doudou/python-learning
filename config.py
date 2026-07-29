"""配置管理：从环境变量读取 GitLab、GitHub 与服务配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return value.strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # GitLab 配置
    gitlab_url: str  # 例如 https://gitlab.com 或自建实例地址
    gitlab_token: str  # 具有 api 读权限 + MR 评论写权限的访问令牌
    webhook_secret: str  # GitLab webhook 中配置的 secret token，用于校验 X-Gitlab-Token

    # GitHub 配置
    github_token: str  # GitHub Personal Access Token，需要 repo 写权限
    github_webhook_secret: str  # GitHub webhook 中配置的 secret token

    # LLM 配置
    openai_api_key: str
    openai_base_url: str  # 可选，用于兼容 OpenAI 兼容 API（如 Azure、SiliconFlow）
    openai_model: str  # 使用的模型，如 GLM-4.5-Air、gpt-4o-mini
    llm_max_tokens: int  # LLM 单次调用最大 token 数
    llm_temperature: float  # LLM 温度参数

    # 审查行为
    max_files_per_review: int  # 单次 MR/PR 最多审查的文件数
    max_inline_comments: int  # 单次 MR/PR 最多发表的行内评论数
    max_file_size_bytes: int  # 超过此大小的文件跳过审查

    # 服务
    host: str
    port: int

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.gitlab_token:
            problems.append("GITLAB_TOKEN 未设置：无法调用 GitLab API")
        if not self.gitlab_url:
            problems.append("GITLAB_URL 未设置")
        return problems


def get_settings() -> Settings:
    return Settings(
        gitlab_url=_env("GITLAB_URL", "https://gitlab.com").rstrip("/"),
        gitlab_token=_env("GITLAB_TOKEN"),
        webhook_secret=_env("GITLAB_WEBHOOK_SECRET"),
        github_token=_env("GITHUB_TOKEN"),
        github_webhook_secret=_env("GITHUB_WEBHOOK_SECRET"),
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_base_url=_env("OPENAI_BASE_URL", ""),
        openai_model=_env("OPENAI_MODEL", "GLM-4.5-Air"),
        llm_max_tokens=_env_int("LLM_MAX_TOKENS", 4096),
        llm_temperature=float(_env("LLM_TEMPERATURE", "0.2")),
        max_files_per_review=_env_int("MAX_FILES_PER_REVIEW", 50),
        max_inline_comments=_env_int("MAX_INLINE_COMMENTS", 20),
        max_file_size_bytes=_env_int("MAX_FILE_SIZE_BYTES", 200_000),
        host=_env("HOST", "0.0.0.0"),
        port=_env_int("PORT", 8000),
    )
