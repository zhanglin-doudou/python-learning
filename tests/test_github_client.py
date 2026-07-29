"""github_client.py 单元测试：用 respx mock httpx，测试 GitHub API 客户端与 patch 解析。"""
from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from github_client import GitHubClient, GitHubError, parse_patch_positions

BASE = "https://api.github.com"
TOKEN = "ghp_test"


@pytest.fixture
async def client():
    c = GitHubClient(TOKEN)
    yield c
    await c.aclose()


# ---------------------------------------------------------------------------
# get_pull_request
# ---------------------------------------------------------------------------

@respx.mock
async def test_get_pull_request_ok(client: GitHubClient):
    respx.get(f"{BASE}/repos/owner/repo/pulls/1").mock(
        return_value=httpx.Response(200, json={
            "number": 1,
            "title": "feat: add x",
            "html_url": "https://github.com/owner/repo/pull/1",
            "head": {"sha": "abc123"},
            "base": {"sha": "def456"},
        })
    )
    pr = await client.get_pull_request("owner", "repo", 1)
    assert pr.number == 1
    assert pr.title == "feat: add x"
    assert pr.head_sha == "abc123"
    assert pr.base_sha == "def456"


@respx.mock
async def test_get_pull_request_404(client: GitHubClient):
    respx.get(f"{BASE}/repos/owner/repo/pulls/1").mock(
        return_value=httpx.Response(404, text="not found")
    )
    with pytest.raises(GitHubError, match="404"):
        await client.get_pull_request("owner", "repo", 1)


# ---------------------------------------------------------------------------
# get_pr_files
# ---------------------------------------------------------------------------

@respx.mock
async def test_get_pr_files_ok(client: GitHubClient):
    respx.get(f"{BASE}/repos/owner/repo/pulls/1/files").mock(
        return_value=httpx.Response(200, json=[
            {
                "filename": "app/page.tsx",
                "status": "modified",
                "patch": "@@ -1,2 +1,3 @@\n line1\n+line2\n line3",
                "additions": 1,
                "deletions": 0,
                "changes": 1,
            },
            {
                "filename": "README.md",
                "status": "added",
                "patch": "@@ -0,0 +1,1 @@\n+# Title",
                "additions": 1,
                "deletions": 0,
                "changes": 1,
            },
        ])
    )
    files = await client.get_pr_files("owner", "repo", 1)
    assert len(files) == 2
    assert files[0].filename == "app/page.tsx"
    assert files[0].status == "modified"
    assert "line2" in files[0].patch


@respx.mock
async def test_get_pr_files_empty(client: GitHubClient):
    respx.get(f"{BASE}/repos/owner/repo/pulls/1/files").mock(
        return_value=httpx.Response(200, json=[])
    )
    files = await client.get_pr_files("owner", "repo", 1)
    assert files == []


# ---------------------------------------------------------------------------
# get_file_content
# ---------------------------------------------------------------------------

@respx.mock
async def test_get_file_content_ok(client: GitHubClient):
    content = "export default function Page() {}"
    encoded = base64.b64encode(content.encode()).decode()
    respx.get(f"{BASE}/repos/owner/repo/contents/app%2Fpage.tsx").mock(
        return_value=httpx.Response(200, json={
            "content": encoded,
            "encoding": "base64",
        })
    )
    result = await client.get_file_content("owner", "repo", "app/page.tsx", "main")
    assert result == content


@respx.mock
async def test_get_file_content_404(client: GitHubClient):
    respx.get(f"{BASE}/repos/owner/repo/contents/missing.ts").mock(
        return_value=httpx.Response(404, text="not found")
    )
    with pytest.raises(GitHubError, match="404"):
        await client.get_file_content("owner", "repo", "missing.ts", "main")


@respx.mock
async def test_get_file_content_passes_ref_param(client: GitHubClient):
    encoded = base64.b64encode(b"x").decode()
    route = respx.get(f"{BASE}/repos/owner/repo/contents/a.ts").mock(
        return_value=httpx.Response(200, json={"content": encoded})
    )
    await client.get_file_content("owner", "repo", "a.ts", "feature-branch")
    assert route.calls.last.request.url.params["ref"] == "feature-branch"


# ---------------------------------------------------------------------------
# post_pr_comment
# ---------------------------------------------------------------------------

@respx.mock
async def test_post_pr_comment_ok(client: GitHubClient):
    route = respx.post(f"{BASE}/repos/owner/repo/issues/1/comments").mock(
        return_value=httpx.Response(201, json={"id": 123})
    )
    result = await client.post_pr_comment("owner", "repo", 1, "hello")
    assert result == {"id": 123}
    body = json.loads(route.calls.last.request.content)
    assert body == {"body": "hello"}


@respx.mock
async def test_post_pr_comment_error(client: GitHubClient):
    respx.post(f"{BASE}/repos/owner/repo/issues/1/comments").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    with pytest.raises(GitHubError, match="403"):
        await client.post_pr_comment("owner", "repo", 1, "hello")


# ---------------------------------------------------------------------------
# post_pr_review_comment
# ---------------------------------------------------------------------------

@respx.mock
async def test_post_pr_review_comment_ok(client: GitHubClient):
    route = respx.post(f"{BASE}/repos/owner/repo/pulls/1/comments").mock(
        return_value=httpx.Response(201, json={"id": 456})
    )
    result = await client.post_pr_review_comment(
        "owner", "repo", 1, "review comment", "sha123", "a.ts", 3, "RIGHT"
    )
    assert result == {"id": 456}
    body = json.loads(route.calls.last.request.content)
    assert body["body"] == "review comment"
    assert body["commit_id"] == "sha123"
    assert body["path"] == "a.ts"
    assert body["position"] == 3
    assert body["side"] == "RIGHT"


@respx.mock
async def test_post_pr_review_comment_fallback_to_pr_comment(client: GitHubClient):
    """行内评论失败时降级为普通 PR 评论。"""
    review_comments = respx.post(f"{BASE}/repos/owner/repo/pulls/1/comments").mock(
        return_value=httpx.Response(422, text="invalid position")
    )
    issue_comments = respx.post(f"{BASE}/repos/owner/repo/issues/1/comments").mock(
        return_value=httpx.Response(201, json={"id": 789})
    )
    result = await client.post_pr_review_comment(
        "owner", "repo", 1, "fallback", "sha", "a.ts", 5
    )
    assert result == {"id": 789}
    assert review_comments.call_count == 1
    assert issue_comments.call_count == 1
    fallback_body = json.loads(issue_comments.calls.last.request.content)["body"]
    assert "a.ts" in fallback_body
    assert "5" in fallback_body


# ---------------------------------------------------------------------------
# parse_patch_positions
# ---------------------------------------------------------------------------

def test_parse_patch_positions_basic():
    patch = """@@ -1,2 +1,3 @@
 line1
+line2
 line3
@@ -5,1 +6,2 @@
 keep
+new
"""
    pos_map = parse_patch_positions(patch)
    # position 从 patch 第一行开始为 1
    # hunk1: @@(1) + line1(2) + +line2(3) + line3(4)
    assert pos_map[2] == 3  # +line2 是新增行，位置为 3
    assert 3 not in pos_map  # line3 是上下文行，不在映射中
    # hunk2: 前面共 4 行，@@(5) + keep(6) + +new(7)
    assert pos_map[7] == 7


def test_parse_patch_positions_no_additions():
    patch = "@@ -1,3 +1,3 @@\n line1\n-line2\n+line2\n line3"
    pos_map = parse_patch_positions(patch)
    # 替换行也算新增行
    assert 2 in pos_map


# ---------------------------------------------------------------------------
# context manager & token header
# ---------------------------------------------------------------------------

@respx.mock
async def test_async_context_manager():
    respx.get(f"{BASE}/repos/owner/repo/pulls/1").mock(
        return_value=httpx.Response(200, json={"number": 1})
    )
    async with GitHubClient(TOKEN) as c:
        pr = await c.get_pull_request("owner", "repo", 1)
        assert pr.number == 1


@respx.mock
async def test_token_header_sent(client: GitHubClient):
    route = respx.get(f"{BASE}/repos/owner/repo/pulls/1").mock(
        return_value=httpx.Response(200, json={"number": 1})
    )
    await client.get_pull_request("owner", "repo", 1)
    assert route.calls.last.request.headers["Authorization"] == f"token {TOKEN}"
