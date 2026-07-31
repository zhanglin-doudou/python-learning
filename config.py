"""配置管理：使用 pydantic-settings 从环境变量读取 GitLab、GitHub 与服务配置。"""
from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置（不可变，自动从环境变量和 .env 文件读取）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        frozen=True,
    )

    # GitLab 配置
    gitlab_url: str = "https://gitlab.com"
    gitlab_token: str = ""
    webhook_secret: str = Field(default="", validation_alias="GITLAB_WEBHOOK_SECRET")

    # GitHub 配置
    github_token: str = ""
    github_webhook_secret: str = ""

    # LLM 配置
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = "GLM-4.5-Air"
    llm_max_tokens: int = 20480
    llm_temperature: float = 0.2

    # 审查行为
    max_files_per_review: int = 50
    max_inline_comments: int = 20
    max_file_size_bytes: int = 200_000

    # 服务
    host: str = "0.0.0.0"
    port: int = 8000

    @field_validator("gitlab_url", mode="after")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        """去掉 URL 末尾斜杠，避免 //api/v4 双斜杠。"""
        return v.rstrip("/")

    @field_validator(
        "gitlab_token", "webhook_secret",
        "github_token", "github_webhook_secret",
        "openai_api_key", "openai_base_url", "openai_model",
        mode="before",
    )
    @classmethod
    def strip_whitespace(cls, v):
        """去除字符串值前后空白。"""
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator(
        "max_files_per_review", "max_inline_comments", "max_file_size_bytes",
        "llm_max_tokens", "port",
        mode="before",
    )
    @classmethod
    def coerce_int_with_default(cls, v, info):
        """非法或空的 int 环境变量回退到默认值。"""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return cls.model_fields[info.field_name].default
            try:
                return int(v)
            except ValueError:
                return cls.model_fields[info.field_name].default
        return v

    @field_validator("llm_temperature", mode="before")
    @classmethod
    def coerce_float_with_default(cls, v, info):
        """非法或空的 float 环境变量回退到默认值。"""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return cls.model_fields[info.field_name].default
            try:
                return float(v)
            except ValueError:
                return cls.model_fields[info.field_name].default
        return v


_settings: Settings | None = None


def get_settings() -> Settings:
    """获取配置单例。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


# 模块级全局配置，方便直接访问
settings = get_settings()
