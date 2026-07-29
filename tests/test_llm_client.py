"""llm_client.py 单元测试：测试 prompt 构建、响应解析与 LLMClient 行为。"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_client import (
    LLMClient,
    _build_user_prompt,
    _detect_language,
    _extract_added_line_numbers,
    _parse_llm_response,
)
from reviewer import Finding


# ---------------------------------------------------------------------------
# _extract_added_line_numbers
# ---------------------------------------------------------------------------

def test_extract_added_line_numbers_basic():
    patch = "@@ -1,2 +1,3 @@\n line1\n+line2\n line3"
    lines = _extract_added_line_numbers(patch)
    assert lines == [2]


def test_extract_added_line_numbers_multiple_hunks():
    patch = """@@ -1,1 +1,2 @@
 line1
+line2
@@ -5,1 +6,2 @@
 keep
+new
"""
    lines = _extract_added_line_numbers(patch)
    assert lines == [2, 7]


def test_extract_added_line_numbers_empty():
    assert _extract_added_line_numbers("") == []


# ---------------------------------------------------------------------------
# _parse_llm_response
# ---------------------------------------------------------------------------

def test_parse_llm_response_valid_json():
    raw = json.dumps([
        {"line": 5, "severity": "high", "message": "bad import", "suggestion": "use subpath"},
    ])
    findings = _parse_llm_response(raw, "a.ts")
    assert len(findings) == 1
    assert findings[0].line == 5
    assert findings[0].severity == "high"
    assert findings[0].message == "bad import"
    assert findings[0].rule_id == "llm-review"


def test_parse_llm_response_with_markdown_code_block():
    raw = "```json\n" + json.dumps([
        {"line": 1, "severity": "medium", "message": "issue", "suggestion": "fix"},
    ]) + "\n```"
    findings = _parse_llm_response(raw, "a.ts")
    assert len(findings) == 1
    assert findings[0].line == 1


def test_parse_llm_response_invalid_json():
    findings = _parse_llm_response("not json", "a.ts")
    assert findings == []


def test_parse_llm_response_not_list():
    findings = _parse_llm_response('{"findings": []}', "a.ts")
    assert findings == []


def test_parse_llm_response_skips_invalid_items():
    raw = json.dumps([
        {"line": 5, "severity": "high", "message": "good"},
        {"line": 0, "severity": "high", "message": "bad line"},
        {"severity": "high", "message": "no line"},
        {"line": 3, "message": ""},
        "not a dict",
    ])
    findings = _parse_llm_response(raw, "a.ts")
    assert len(findings) == 1
    assert findings[0].line == 5


def test_parse_llm_response_normalizes_severity():
    raw = json.dumps([
        {"line": 1, "severity": "UNKNOWN", "message": "x"},
    ])
    findings = _parse_llm_response(raw, "a.ts")
    assert findings[0].severity == "medium"


# ---------------------------------------------------------------------------
# _detect_language
# ---------------------------------------------------------------------------

def test_detect_language_typescript():
    assert _detect_language("app/page.tsx") == "typescript"
    assert _detect_language("utils.ts") == "typescript"


def test_detect_language_javascript():
    assert _detect_language("script.js") == "javascript"
    assert _detect_language("component.jsx") == "javascript"
    assert _detect_language("lib.mjs") == "javascript"


def test_detect_language_python():
    assert _detect_language("main.py") == "python"


def test_detect_language_unknown():
    assert _detect_language("file.unknown") == ""
    assert _detect_language("README") == ""


# ---------------------------------------------------------------------------
# _build_user_prompt
# ---------------------------------------------------------------------------

def test_build_user_prompt_contains_file_path():
    prompt = _build_user_prompt("app/page.tsx", "@@ -1,1 +1,1 @@\n+const x = 1;", "const x = 1;")
    assert "app/page.tsx" in prompt
    assert "typescript" in prompt


def test_build_user_prompt_shows_added_range():
    patch = "@@ -1,1 +1,2 @@\n line1\n+line2"
    prompt = _build_user_prompt("a.ts", patch, "content")
    # 新增行只有 line2（行号 2），范围是 "2-2"
    assert "2-2" in prompt


def test_build_user_prompt_none_for_empty_patch():
    prompt = _build_user_prompt("a.ts", "", "content")
    assert "none" in prompt


# ---------------------------------------------------------------------------
# LLMClient.review_file
# ---------------------------------------------------------------------------

async def test_llm_client_review_file_success(monkeypatch):
    """成功调用 OpenAI API 并解析返回的 JSON。"""
    fake_settings = MagicMock()
    fake_settings.openai_api_key = "sk-test"
    fake_settings.openai_base_url = ""
    fake_settings.openai_model = "GLM-4.5-Air"
    fake_settings.llm_max_tokens = 4096
    fake_settings.llm_temperature = 0.2
    monkeypatch.setattr("llm_client.get_settings", lambda: fake_settings)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps([
        {"line": 2, "severity": "high", "message": "use const", "suggestion": "change to const"}
    ])
    mock_response.usage = MagicMock()
    mock_response.usage.model_dump.return_value = {"total_tokens": 100}

    mock_openai = MagicMock()
    mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("llm_client.AsyncOpenAI", return_value=mock_openai):
        client = LLMClient()
        result = await client.review_file("a.ts", "@@ -1,1 +1,2 @@\n line1\n+let x = 1;", "let x = 1;")

    assert len(result.findings) == 1
    assert result.findings[0].line == 2
    assert result.token_usage == {"total_tokens": 100}
    assert "high" in result.raw_response


async def test_llm_client_review_file_empty_patch(monkeypatch):
    """空 patch 时不调用 API，直接返回空结果。"""
    fake_settings = MagicMock()
    fake_settings.openai_api_key = "sk-test"
    fake_settings.openai_base_url = ""
    fake_settings.openai_model = "GLM-4.5-Air"
    fake_settings.llm_max_tokens = 4096
    fake_settings.llm_temperature = 0.2
    monkeypatch.setattr("llm_client.get_settings", lambda: fake_settings)

    mock_openai = MagicMock()
    mock_openai.chat.completions.create = AsyncMock()

    with patch("llm_client.AsyncOpenAI", return_value=mock_openai):
        client = LLMClient()
        result = await client.review_file("a.ts", "", "content")

    assert result.findings == []
    assert result.raw_response == ""
    assert result.token_usage is None
    mock_openai.chat.completions.create.assert_not_called()


async def test_llm_client_review_file_api_error(monkeypatch):
    """API 调用失败时返回空 findings 并记录错误。"""
    fake_settings = MagicMock()
    fake_settings.openai_api_key = "sk-test"
    fake_settings.openai_base_url = ""
    fake_settings.openai_model = "GLM-4.5-Air"
    fake_settings.llm_max_tokens = 4096
    fake_settings.llm_temperature = 0.2
    monkeypatch.setattr("llm_client.get_settings", lambda: fake_settings)

    mock_openai = MagicMock()
    mock_openai.chat.completions.create = AsyncMock(side_effect=RuntimeError("API down"))

    with patch("llm_client.AsyncOpenAI", return_value=mock_openai):
        client = LLMClient()
        result = await client.review_file("a.ts", "+let x = 1;", "let x = 1;")

    assert result.findings == []
    assert "API down" in result.raw_response


async def test_llm_client_review_file_wraps_findings_object(monkeypatch):
    """LLM 返回 {'findings': [...]} 对象时也能正确解析。"""
    fake_settings = MagicMock()
    fake_settings.openai_api_key = "sk-test"
    fake_settings.openai_base_url = ""
    fake_settings.openai_model = "GLM-4.5-Air"
    fake_settings.llm_max_tokens = 4096
    fake_settings.llm_temperature = 0.2
    monkeypatch.setattr("llm_client.get_settings", lambda: fake_settings)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    # openai json_object 模式可能返回对象
    mock_response.choices[0].message.content = json.dumps({
        "findings": [
            {"line": 1, "severity": "low", "message": "missing semicolon", "suggestion": "add ;"}
        ]
    })
    mock_response.usage = None

    mock_openai = MagicMock()
    mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("llm_client.AsyncOpenAI", return_value=mock_openai):
        client = LLMClient()
        result = await client.review_file("a.ts", "+const x = 1", "const x = 1")

    assert len(result.findings) == 1
    assert result.findings[0].message == "missing semicolon"
