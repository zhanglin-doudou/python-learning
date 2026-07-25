"""配置管理：从环境变量读取 GitLab 与服务配置。"""
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

    # 审查行为
    max_files_per_review: int  # 单次 MR 最多审查的文件数
    max_inline_comments: int  # 单次 MR 最多发表的行内评论数
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
        max_files_per_review=_env_int("MAX_FILES_PER_REVIEW", 50),
        max_inline_comments=_env_int("MAX_INLINE_COMMENTS", 20),
        max_file_size_bytes=_env_int("MAX_FILE_SIZE_BYTES", 200_000),
        host=_env("HOST", "0.0.0.0"),
        port=_env_int("PORT", 8000),
    )
