"""LLM 客户端：使用 OpenAI API 对代码进行智能审查。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from config import settings
from reviewer import Finding

logger = logging.getLogger(__name__)


@dataclass
class LLMReviewResult:
    findings: list[Finding]
    raw_response: str
    token_usage: dict | None = None


_SYSTEM_PROMPT = """你是一位资深的 Next.js/React 全栈代码审查专家。你的任务是对 Pull Request 的代码变更进行深度审查。

## 审查维度（按优先级）
1. **性能问题**：bundle 体积、渲染优化（避免不必要重渲染）、数据获取瀑布、SSR/RSC 正确使用
2. **代码质量**：类型安全、错误处理、代码风格、可维护性、代码重复
3. **安全性**：XSS、CSRF、敏感信息泄露、不安全的依赖使用、SQL/NoSQL 注入
4. **Next.js 最佳实践**：App Router 正确使用、Server/Client Component 边界、Image/Link 组件、缓存策略
5. **可访问性**：ARIA 标签、键盘导航、语义化 HTML

## 输出格式要求
你必须只返回 JSON 数组，不要有任何 Markdown 代码块标记或其他说明文字。

每个元素格式：
{
  "line": <文件中的1-based行号>,
  "severity": "critical|high|medium|low",
  "message": "<简洁的问题描述，最多100字>",
  "suggestion": "<具体的修改建议，可包含代码示例>"
}

如果代码没有问题，返回空数组 []。
"""


def _build_user_prompt(file_path: str, patch: str, content: str, language: str = "typescript") -> str:
    added_lines = _extract_added_line_numbers(patch)
    added_range = f"{min(added_lines)}-{max(added_lines)}" if added_lines else "none"

    return f"""## 文件信息
- 文件路径: {file_path}
- 语言: {language}
- 新增/修改行号范围: {added_range}

## 变更内容（diff patch）
```diff
{patch}
```

## 文件完整内容
```{language}
{content}
```

请审查上述代码变更，只关注**新增或修改的代码行**中的问题。返回 JSON 数组。"""


def _extract_added_line_numbers(patch: str) -> list[int]:
    """从 patch 中提取新增行的行号。"""
    import re
    lines: list[int] = []
    new_line = 0
    for line in patch.splitlines():
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            if m:
                new_line = int(m.group(1)) - 1
        elif line.startswith("+") and not line.startswith("+++"):
            new_line += 1
            lines.append(new_line)
        elif not line.startswith("-") and not line.startswith("---"):
            new_line += 1
    return lines


def _parse_llm_response(raw: str, file_path: str) -> list[Finding]:
    """解析 LLM 返回的 JSON，转换为 Finding 列表。"""
    findings: list[Finding] = []

    # 尝试提取 JSON 数组（LLM 有时会在外层加 markdown 代码块）
    text = raw.strip()
    # 去除可能的 markdown 代码块标记
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("LLM 返回的不是有效 JSON: %s\nRaw: %s", e, raw[:500])
        return findings

    if not isinstance(data, list):
        logger.warning("LLM 返回的不是数组: %s", type(data))
        return findings

    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            line = int(item.get("line", 0))
            if line <= 0:
                continue
            severity = str(item.get("severity", "medium")).lower()
            if severity not in ("critical", "high", "medium", "low", "info"):
                severity = "medium"
            message = str(item.get("message", "")).strip()
            suggestion = str(item.get("suggestion", "")).strip()
            if not message:
                continue
            findings.append(
                Finding(
                    rule_id="llm-review",
                    file_path=file_path,
                    line=line,
                    severity=severity,
                    message=message,
                    suggestion=suggestion,
                )
            )
        except Exception:
            continue

    return findings


class LLMClient:
    """基于 OpenAI API 的代码审查 LLM 客户端。"""

    def __init__(self) -> None:
        kwargs: dict = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        self._client = AsyncOpenAI(**kwargs)
        self._model = settings.openai_model
        self._max_tokens = settings.llm_max_tokens
        self._temperature = settings.llm_temperature

    async def review_file(
        self,
        file_path: str,
        patch: str,
        content: str,
    ) -> LLMReviewResult:
        """对单个文件进行 LLM 审查。"""
        if not patch.strip():
            return LLMReviewResult(findings=[], raw_response="", token_usage=None)

        language = _detect_language(file_path)
        user_prompt = _build_user_prompt(file_path, patch, content, language)

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.error("LLM API 调用失败: %s", e)
            return LLMReviewResult(findings=[], raw_response=str(e), token_usage=None)

        raw = response.choices[0].message.content or "[]"
        usage = response.usage.model_dump() if response.usage else None

        # OpenAI 的 json_object 模式会返回一个对象，我们需要提取其中的数组
        # 有些模型可能返回 {"findings": [...]} 或直接返回 [...]
        findings = _parse_llm_response(raw, file_path)

        # 如果解析为空但 raw 非空，尝试包装为数组再解析
        if not findings and raw.strip() and not raw.strip().startswith("["):
            # 可能是 {"findings": [...]} 格式
            try:
                data = json.loads(raw.strip())
                if isinstance(data, dict) and "findings" in data:
                    wrapped = json.dumps(data["findings"])
                    findings = _parse_llm_response(wrapped, file_path)
            except Exception:
                pass

        return LLMReviewResult(
            findings=findings,
            raw_response=raw,
            token_usage=usage,
        )


def _detect_language(file_path: str) -> str:
    """根据文件扩展名推断语言。"""
    ext = file_path.split(".")[-1].lower() if "." in file_path else ""
    mapping = {
        "ts": "typescript",
        "tsx": "typescript",
        "js": "javascript",
        "jsx": "javascript",
        "mjs": "javascript",
        "cjs": "javascript",
        "py": "python",
        "go": "go",
        "rs": "rust",
        "java": "java",
        "md": "markdown",
        "json": "json",
        "yml": "yaml",
        "yaml": "yaml",
    }
    return mapping.get(ext, "")
