"""mcp_server.py 单元测试：mock GitLabClient，测试审查编排逻辑与 MCP 工具。

注意：mcp_server 模块在导入时会创建 FastMCP 实例并注册工具，
我们通过 patch GitLabClient 和 get_settings 来隔离外部依赖。
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import mcp_server
from gitlab_client import ChangedFile, DiffRefs, GitLabError, MergeRequestInfo


# ---------------------------------------------------------------------------
# 测试夹具：构造 mock GitLabClient
# ---------------------------------------------------------------------------

def _make_mr() -> MergeRequestInfo:
    return MergeRequestInfo(
        project_id=42, mr_iid=7, title="feat: x",
        web_url="http://mr/7", source_branch="feat", target_branch="main",
        diff_refs=DiffRefs(base_sha="aaa", head_sha="bbb", start_sha="ccc"),
    )


def _make_change(path: str, content_diff: str) -> ChangedFile:
    return ChangedFile(
        old_path=path, new_path=path, new_file=False,
        deleted_file=False, renamed_file=False, diff=content_diff,
    )


def _mock_gl_factory(mr, changes, file_contents):
    """构建一个 mock GitLabClient，aclose 为 no-op，方法可被断言。"""
    gl = AsyncMock()
    gl.get_merge_request = AsyncMock(return_value=mr)
    gl.get_mr_changes = AsyncMock(return_value=changes)
    gl.get_file_content = AsyncMock(side_effect=lambda pid, path, ref: file_contents.get(path, ""))
    gl.post_mr_note = AsyncMock(return_value={"id": 1})
    gl.post_mr_diff_note = AsyncMock(return_value={"id": "d1"})
    gl.aclose = AsyncMock()
    gl.__aenter__ = AsyncMock(return_value=gl)
    gl.__aexit__ = AsyncMock(return_value=None)
    return gl


def _patch_settings(monkeypatch, token="tok", secret="", github_token="ghp-test", openai_key="sk-test"):
    """patch settings 返回带 token 的配置。"""
    import config
    fake = MagicMock()
    fake.gitlab_url = "https://gitlab.example.com"
    fake.gitlab_token = token
    fake.webhook_secret = secret
    fake.github_token = github_token
    fake.github_webhook_secret = ""
    fake.openai_api_key = openai_key
    fake.openai_base_url = ""
    fake.openai_model = "GLM-4.5-Air"
    fake.llm_max_tokens = 4096
    fake.llm_temperature = 0.2
    fake.max_files_per_review = 50
    fake.max_inline_comments = 20
    fake.max_file_size_bytes = 200_000
    fake.host = "0.0.0.0"
    fake.port = 8000
    # patch config.settings，所有模块导入的是同一个对象
    monkeypatch.setattr(config, "settings", fake)
    monkeypatch.setattr(mcp_server, "settings", fake)
    import llm_client
    monkeypatch.setattr(llm_client, "settings", fake)


# ---------------------------------------------------------------------------
# run_merge_request_review
# ---------------------------------------------------------------------------

async def test_review_no_token_returns_error(monkeypatch):
    """无 token 时立即返回错误。"""
    _patch_settings(monkeypatch, token="")
    result = await mcp_server.run_merge_request_review(42, 7)
    assert result.error == "GITLAB_TOKEN 未配置"
    assert result.reviewed_files == 0


async def test_review_gitlab_api_error(monkeypatch):
    """GitLab API 获取 MR 失败时返回错误。"""
    _patch_settings(monkeypatch)
    gl = _mock_gl_factory(_make_mr(), [], {})
    gl.get_merge_request = AsyncMock(side_effect=GitLabError("404 not found"))
    with patch("mcp_server.GitLabClient", return_value=gl):
        result = await mcp_server.run_merge_request_review(42, 7)
    assert result.error is not None
    assert "获取 MR 失败" in result.error
    assert result.reviewed_files == 0


async def test_review_success_with_findings(monkeypatch):
    """成功审查并在新增行上发现问题的完整流程。"""
    _patch_settings(monkeypatch)
    # diff 让第 1 行（import）成为新增行
    diff = "@@ -0,0 +1,2 @@\n+import { Icon } from \"react-icons\";\n+const x = 1;\n"
    change = _make_change("app/page.tsx", diff)
    file_contents = {"app/page.tsx": "import { Icon } from \"react-icons\";\nconst x = 1;\n"}
    gl = _mock_gl_factory(_make_mr(), [change], file_contents)

    with patch("mcp_server.GitLabClient", return_value=gl):
        result = await mcp_server.run_merge_request_review(42, 7, post_comments=True)

    assert result.reviewed_files == 1
    assert result.skipped_files == 0
    # 应命中 barrel imports 规则
    rule_ids = {f.rule_id for f in result.findings}
    assert "bundle-barrel-imports" in rule_ids
    # 评论：至少 1 条行内 + 1 条汇总
    assert result.inline_comments_posted >= 1
    assert result.summary_posted is True
    gl.post_mr_diff_note.assert_called()
    gl.post_mr_note.assert_called_once()


async def test_review_filters_to_added_lines(monkeypatch):
    """不在新增行上的发现不产生行内评论。"""
    _patch_settings(monkeypatch)
    # 第 1 行是 keep（未改动），第 2 行是新增行
    diff = "@@ -1,1 +1,2 @@\n keep\n+const x = 1;\n"
    # 文件内容里第 1 行有问题（let），但 diff 显示第 1 行未改动；
    # 第 2 行（const）是新增行但无问题 -> 不产生行内评论
    file_contents = {"app/page.tsx": "let counter = 0;\nconst x = 1;\n"}
    change = _make_change("app/page.tsx", diff)
    gl = _mock_gl_factory(_make_mr(), [change], file_contents)

    with patch("mcp_server.GitLabClient", return_value=gl):
        result = await mcp_server.run_merge_request_review(42, 7)

    # 新增行是第 2 行（const，无问题），第 1 行的 let 不在新增行内 -> 不产生行内评论
    assert result.inline_comments_posted == 0
    gl.post_mr_diff_note.assert_not_called()


async def test_review_skips_deleted_files(monkeypatch):
    """已删除文件不参与审查。"""
    _patch_settings(monkeypatch)
    deleted = ChangedFile("a.ts", "a.ts", False, True, False, "@@ -1,1 +0,0 @@\n-a\n")
    gl = _mock_gl_factory(_make_mr(), [deleted], {})

    with patch("mcp_server.GitLabClient", return_value=gl):
        result = await mcp_server.run_merge_request_review(42, 7)

    assert result.reviewed_files == 0
    gl.get_file_content.assert_not_called()


async def test_review_skips_unsupported_extensions(monkeypatch):
    """非 TS/JS 扩展名的文件跳过。"""
    _patch_settings(monkeypatch)
    change = _make_change("README.md", "@@ -0,0 +1,1 @@\n+# title\n")
    gl = _mock_gl_factory(_make_mr(), [change], {})

    with patch("mcp_server.GitLabClient", return_value=gl):
        result = await mcp_server.run_merge_request_review(42, 7)

    assert result.reviewed_files == 0
    gl.get_file_content.assert_not_called()


async def test_review_skips_large_files(monkeypatch):
    """超过大小限制的文件跳过。"""
    import config
    fake = MagicMock()
    fake.gitlab_url = "https://gitlab.example.com"
    fake.gitlab_token = "tok"
    fake.max_files_per_review = 50
    fake.max_inline_comments = 20
    fake.max_file_size_bytes = 10  # 极小限制
    fake.webhook_secret = ""
    fake.host = "0.0.0.0"
    fake.port = 8000
    monkeypatch.setattr(config, "settings", fake)
    monkeypatch.setattr(mcp_server, "settings", fake)

    diff = "@@ -0,0 +1,1 @@\n+import { Icon } from \"react-icons\";\n"
    change = _make_change("a.ts", diff)
    big_content = "x" * 100
    gl = _mock_gl_factory(_make_mr(), [change], {"a.ts": big_content})

    with patch("mcp_server.GitLabClient", return_value=gl):
        result = await mcp_server.run_merge_request_review(42, 7)

    assert result.reviewed_files == 0
    assert result.skipped_files == 1


async def test_review_file_content_error_skipped(monkeypatch):
    """读取文件内容失败时跳过该文件，不中断整体审查。"""
    _patch_settings(monkeypatch)
    diff = "@@ -0,0 +1,1 @@\n+let x = 0;\n"
    change = _make_change("a.ts", diff)
    gl = _mock_gl_factory(_make_mr(), [change], {})
    gl.get_file_content = AsyncMock(side_effect=GitLabError("404"))

    with patch("mcp_server.GitLabClient", return_value=gl):
        result = await mcp_server.run_merge_request_review(42, 7)

    assert result.reviewed_files == 0
    assert result.skipped_files == 1


async def test_review_no_findings_posts_clean_summary(monkeypatch):
    """无问题时仍发表一条“通过”汇总评论，不发表行内评论。"""
    _patch_settings(monkeypatch)
    diff = "@@ -0,0 +1,1 @@\n+const a = 1;\n"
    change = _make_change("a.ts", diff)
    gl = _mock_gl_factory(_make_mr(), [change], {"a.ts": "const a = 1;\n"})

    with patch("mcp_server.GitLabClient", return_value=gl):
        result = await mcp_server.run_merge_request_review(42, 7)

    assert len(result.findings) == 0
    assert result.inline_comments_posted == 0
    assert result.summary_posted is True
    gl.post_mr_diff_note.assert_not_called()
    # 汇总评论应包含通过标记
    summary_body = gl.post_mr_note.call_args.kwargs.get("json", {}).get("body", "") or \
        gl.post_mr_note.call_args.args[-1]
    assert "✅" in summary_body or "未发现" in summary_body


async def test_review_inline_comment_failure_does_not_break(monkeypatch):
    """行内评论失败不中断后续评论与汇总。"""
    _patch_settings(monkeypatch)
    diff = "@@ -0,0 +1,1 @@\n+import { Icon } from \"react-icons\";\n"
    change = _make_change("a.ts", diff)
    gl = _mock_gl_factory(_make_mr(), [change], {"a.ts": "import { Icon } from \"react-icons\";\n"})
    gl.post_mr_diff_note = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("mcp_server.GitLabClient", return_value=gl):
        result = await mcp_server.run_merge_request_review(42, 7)

    assert result.inline_comments_posted == 0
    assert result.summary_posted is True


async def test_review_summary_failure_handled(monkeypatch):
    """汇总评论失败被捕获，summary_posted 为 False。"""
    _patch_settings(monkeypatch)
    diff = "@@ -0,0 +1,1 @@\n+const a = 1;\n"
    change = _make_change("a.ts", diff)
    gl = _mock_gl_factory(_make_mr(), [change], {"a.ts": "const a = 1;\n"})
    gl.post_mr_note = AsyncMock(side_effect=GitLabError("403"))

    with patch("mcp_server.GitLabClient", return_value=gl):
        result = await mcp_server.run_merge_request_review(42, 7)

    assert result.summary_posted is False


async def test_review_post_comments_false(monkeypatch):
    """post_comments=False 时不发表任何评论。"""
    _patch_settings(monkeypatch)
    diff = "@@ -0,0 +1,1 @@\n+import { Icon } from \"react-icons\";\n"
    change = _make_change("a.ts", diff)
    gl = _mock_gl_factory(_make_mr(), [change], {"a.ts": "import { Icon } from \"react-icons\";\n"})

    with patch("mcp_server.GitLabClient", return_value=gl):
        result = await mcp_server.run_merge_request_review(42, 7, post_comments=False)

    assert len(result.findings) >= 1
    assert result.inline_comments_posted == 0
    assert result.summary_posted is False
    gl.post_mr_diff_note.assert_not_called()
    gl.post_mr_note.assert_not_called()


async def test_review_inline_comment_limit(monkeypatch):
    """行内评论达到上限后停止发送。"""
    import config
    fake = MagicMock()
    fake.gitlab_url = "https://gitlab.example.com"
    fake.gitlab_token = "tok"
    fake.max_files_per_review = 50
    fake.max_inline_comments = 2  # 只发 2 条
    fake.max_file_size_bytes = 200_000
    fake.webhook_secret = ""
    fake.host = "0.0.0.0"
    fake.port = 8000
    monkeypatch.setattr(config, "settings", fake)
    monkeypatch.setattr(mcp_server, "settings", fake)

    # 多个文件多个问题
    diffs = []
    contents = {}
    for i in range(5):
        path = f"a{i}.ts"
        diffs.append(_make_change(path, f"@@ -0,0 +1,2 @@\n+let x{i} = 0;\n+import {{I}} from \"react-icons\";\n"))
        contents[path] = f"let x{i} = 0;\nimport {{I}} from \"react-icons\";\n"
    gl = _mock_gl_factory(_make_mr(), diffs, contents)

    with patch("mcp_server.GitLabClient", return_value=gl):
        result = await mcp_server.run_merge_request_review(42, 7)

    assert result.inline_comments_posted == 2
    assert gl.post_mr_diff_note.call_count == 2


# ---------------------------------------------------------------------------
# MCP 工具
# ---------------------------------------------------------------------------

async def test_tool_list_review_rules(monkeypatch):
    """list_review_rules 工具返回 JSON 规则列表。"""
    out = mcp_server.list_review_rules()
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) > 0
    assert "rule_id" in data[0]


async def test_tool_review_code_reviewable(monkeypatch):
    """review_code 对可审查文件返回格式化结果。"""
    out = mcp_server.review_code("a.ts", 'import {I} from "react-icons";\n')
    assert "bundle-barrel-imports" in out or "🤖" in out or "✅" in out


async def test_tool_review_code_unsupported(monkeypatch):
    """review_code 对不支持的扩展名返回提示。"""
    out = mcp_server.review_code("a.md", "# title")
    assert "不在可审查范围" in out


async def test_tool_review_merge_request_success(monkeypatch):
    """review_merge_request 工具成功时返回汇总信息。"""
    _patch_settings(monkeypatch)
    diff = "@@ -0,0 +1,1 @@\n+const a = 1;\n"
    change = _make_change("a.ts", diff)
    gl = _mock_gl_factory(_make_mr(), [change], {"a.ts": "const a = 1;\n"})
    with patch("mcp_server.GitLabClient", return_value=gl):
        out = await mcp_server.review_merge_request(42, 7)
    assert "✅" in out
    assert "!7" in out


async def test_tool_review_merge_request_error(monkeypatch):
    """review_merge_request 工具失败时返回错误信息。"""
    _patch_settings(monkeypatch, token="")
    out = await mcp_server.review_merge_request(42, 7)
    assert "❌" in out
    assert "GITLAB_TOKEN" in out


async def test_tool_post_mr_comment_success(monkeypatch):
    """post_mr_comment 工具成功发表评论。"""
    _patch_settings(monkeypatch)
    gl = _mock_gl_factory(_make_mr(), [], {})
    with patch("mcp_server.GitLabClient", return_value=gl):
        out = await mcp_server.post_mr_comment(42, 7, "hello")
    assert "✅" in out
    gl.post_mr_note.assert_called_once()


async def test_tool_post_mr_comment_no_token(monkeypatch):
    """post_mr_comment 无 token 时返回错误。"""
    _patch_settings(monkeypatch, token="")
    out = await mcp_server.post_mr_comment(42, 7, "hello")
    assert "❌" in out


async def test_tool_post_mr_comment_failure(monkeypatch):
    """post_mr_comment 发表失败时返回错误。"""
    _patch_settings(monkeypatch)
    gl = _mock_gl_factory(_make_mr(), [], {})
    gl.post_mr_note = AsyncMock(side_effect=GitLabError("403"))
    with patch("mcp_server.GitLabClient", return_value=gl):
        out = await mcp_server.post_mr_comment(42, 7, "hello")
    assert "❌" in out


# ---------------------------------------------------------------------------
# run_github_pr_review
# ---------------------------------------------------------------------------

def _make_pr():
    from github_client import PullRequestInfo
    return PullRequestInfo(
        owner="owner", repo="repo", number=1,
        title="feat: x", html_url="http://pr/1",
        head_sha="abc123", base_sha="def456",
    )


def _make_pr_file(path: str, patch: str, status="modified"):
    from github_client import PRFile
    return PRFile(
        filename=path, status=status, patch=patch,
        additions=1, deletions=0, changes=1,
    )


def _mock_gh_factory(pr, files, file_contents):
    """构建一个 mock GitHubClient。"""
    gh = AsyncMock()
    gh.get_pull_request = AsyncMock(return_value=pr)
    gh.get_pr_files = AsyncMock(return_value=files)
    gh.get_file_content = AsyncMock(side_effect=lambda o, r, p, ref: file_contents.get(p, ""))
    gh.post_pr_comment = AsyncMock(return_value={"id": 1})
    gh.post_pr_review_comment = AsyncMock(return_value={"id": 2})
    gh.aclose = AsyncMock()
    gh.__aenter__ = AsyncMock(return_value=gh)
    gh.__aexit__ = AsyncMock(return_value=None)
    return gh


def _mock_llm_factory(findings):
    """构建一个 mock LLMClient，返回指定的 findings。"""
    llm = AsyncMock()
    from llm_client import LLMReviewResult
    llm.review_file = AsyncMock(return_value=LLMReviewResult(
        findings=findings, raw_response="", token_usage={"total_tokens": 50}
    ))
    return llm


async def test_github_review_no_github_token_returns_error(monkeypatch):
    """无 GITHUB_TOKEN 时立即返回错误。"""
    _patch_settings(monkeypatch, github_token="")
    result = await mcp_server.run_github_pr_review("owner", "repo", 1)
    assert result.error == "GITHUB_TOKEN 未配置"
    assert result.reviewed_files == 0


async def test_github_review_no_openai_key_returns_error(monkeypatch):
    """无 OPENAI_API_KEY 时立即返回错误。"""
    _patch_settings(monkeypatch, openai_key="")
    result = await mcp_server.run_github_pr_review("owner", "repo", 1)
    assert result.error == "OPENAI_API_KEY 未配置"
    assert result.reviewed_files == 0


async def test_github_review_pr_api_error(monkeypatch):
    """GitHub API 获取 PR 失败时返回错误。"""
    _patch_settings(monkeypatch)
    from github_client import GitHubError
    gh = _mock_gh_factory(_make_pr(), [], {})
    gh.get_pull_request = AsyncMock(side_effect=GitHubError("404 not found"))
    with patch("mcp_server.GitHubClient", return_value=gh):
        result = await mcp_server.run_github_pr_review("owner", "repo", 1)
    assert result.error is not None
    assert "获取 PR 失败" in result.error
    assert result.reviewed_files == 0


async def test_github_review_success_with_findings(monkeypatch):
    """成功使用 LLM 审查并发表评论的完整流程。"""
    _patch_settings(monkeypatch)
    patch_text = "@@ -1,1 +1,2 @@\n line1\n+let x = 0;"
    pr_file = _make_pr_file("app/page.tsx", patch_text)
    gh = _mock_gh_factory(_make_pr(), [pr_file], {"app/page.tsx": "line1\nlet x = 0;"})

    from reviewer import Finding
    finding = Finding(
        rule_id="llm-review", file_path="app/page.tsx", line=2,
        severity="high", message="use const", suggestion="change to const"
    )
    llm = _mock_llm_factory([finding])

    with patch("mcp_server.GitHubClient", return_value=gh):
        with patch("mcp_server.LLMClient", return_value=llm):
            result = await mcp_server.run_github_pr_review("owner", "repo", 1, post_comments=True)

    assert result.reviewed_files == 1
    assert result.skipped_files == 0
    assert len(result.findings) == 1
    assert result.inline_comments_posted == 1
    assert result.summary_posted is True
    gh.post_pr_review_comment.assert_called()
    gh.post_pr_comment.assert_called_once()


async def test_github_review_skips_deleted_files(monkeypatch):
    """已删除文件不参与审查。"""
    _patch_settings(monkeypatch)
    pr_file = _make_pr_file("a.ts", "", status="removed")
    gh = _mock_gh_factory(_make_pr(), [pr_file], {})
    with patch("mcp_server.GitHubClient", return_value=gh):
        result = await mcp_server.run_github_pr_review("owner", "repo", 1)
    assert result.reviewed_files == 0
    gh.get_file_content.assert_not_called()


async def test_github_review_skips_unsupported_extensions(monkeypatch):
    """非 TS/JS 扩展名的文件跳过。"""
    _patch_settings(monkeypatch)
    pr_file = _make_pr_file("README.md", "@@ -0,0 +1,1 @@\n+# title")
    gh = _mock_gh_factory(_make_pr(), [pr_file], {})
    with patch("mcp_server.GitHubClient", return_value=gh):
        result = await mcp_server.run_github_pr_review("owner", "repo", 1)
    assert result.reviewed_files == 0
    gh.get_file_content.assert_not_called()


async def test_github_review_skips_large_files(monkeypatch):
    """超过大小限制的文件跳过。"""
    import config
    fake = MagicMock()
    fake.gitlab_url = "https://gitlab.example.com"
    fake.gitlab_token = "tok"
    fake.webhook_secret = ""
    fake.github_token = "ghp-test"
    fake.github_webhook_secret = ""
    fake.openai_api_key = "sk-test"
    fake.openai_base_url = ""
    fake.openai_model = "GLM-4.5-Air"
    fake.llm_max_tokens = 4096
    fake.llm_temperature = 0.2
    fake.max_files_per_review = 50
    fake.max_inline_comments = 20
    fake.max_file_size_bytes = 10  # 极小限制
    fake.host = "0.0.0.0"
    fake.port = 8000
    monkeypatch.setattr(config, "settings", fake)
    monkeypatch.setattr(mcp_server, "settings", fake)
    import llm_client
    monkeypatch.setattr(llm_client, "settings", fake)

    patch_text = "@@ -0,0 +1,1 @@\n+import { Icon } from \"react-icons\";"
    pr_file = _make_pr_file("a.ts", patch_text)
    gh = _mock_gh_factory(_make_pr(), [pr_file], {"a.ts": "x" * 100})
    with patch("mcp_server.GitHubClient", return_value=gh):
        result = await mcp_server.run_github_pr_review("owner", "repo", 1)
    assert result.reviewed_files == 0
    assert result.skipped_files == 1


async def test_github_review_post_comments_false(monkeypatch):
    """post_comments=False 时不发表任何评论。"""
    _patch_settings(monkeypatch)
    patch_text = "@@ -1,1 +1,2 @@\n line1\n+let x = 0;"
    pr_file = _make_pr_file("a.ts", patch_text)
    gh = _mock_gh_factory(_make_pr(), [pr_file], {"a.ts": "line1\nlet x = 0;"})
    from reviewer import Finding
    llm = _mock_llm_factory([Finding(
        rule_id="llm-review", file_path="a.ts", line=2,
        severity="medium", message="issue", suggestion="fix"
    )])
    with patch("mcp_server.GitHubClient", return_value=gh):
        with patch("mcp_server.LLMClient", return_value=llm):
            result = await mcp_server.run_github_pr_review("owner", "repo", 1, post_comments=False)
    assert len(result.findings) == 1
    assert result.inline_comments_posted == 0
    assert result.summary_posted is False
    gh.post_pr_review_comment.assert_not_called()
    gh.post_pr_comment.assert_not_called()


# ---------------------------------------------------------------------------
# GitHub MCP 工具
# ---------------------------------------------------------------------------

async def test_tool_review_github_pull_request_success(monkeypatch):
    """review_github_pull_request 工具成功时返回汇总信息。"""
    _patch_settings(monkeypatch)
    patch_text = "@@ -1,1 +1,2 @@\n line1\n+let x = 0;"
    pr_file = _make_pr_file("a.ts", patch_text)
    gh = _mock_gh_factory(_make_pr(), [pr_file], {"a.ts": "line1\nlet x = 0;"})
    from reviewer import Finding
    llm = _mock_llm_factory([Finding(
        rule_id="llm-review", file_path="a.ts", line=2,
        severity="medium", message="issue", suggestion="fix"
    )])
    with patch("mcp_server.GitHubClient", return_value=gh):
        with patch("mcp_server.LLMClient", return_value=llm):
            out = await mcp_server.review_github_pull_request("owner", "repo", 1)
    assert "✅" in out
    assert "#1" in out
    assert "owner/repo" in out


async def test_tool_review_github_pull_request_error(monkeypatch):
    """review_github_pull_request 工具失败时返回错误信息。"""
    _patch_settings(monkeypatch, github_token="")
    out = await mcp_server.review_github_pull_request("owner", "repo", 1)
    assert "❌" in out
    assert "GITHUB_TOKEN" in out
