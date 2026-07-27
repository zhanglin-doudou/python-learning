"""gitlab_client.py 单元测试：用 respx mock httpx，测试各 API 方法与降级逻辑。"""
from __future__ import annotations

import httpx
import pytest
import respx

from gitlab_client import GitLabClient, GitLabError

BASE = "https://gitlab.example.com/api/v4"
TOKEN = "glpat-test"


@pytest.fixture
async def client():
    c = GitLabClient("https://gitlab.example.com", TOKEN)
    yield c
    await c.aclose()


# ---------------------------------------------------------------------------
# _pid
# ---------------------------------------------------------------------------

def test_pid_numeric():
    c = GitLabClient("https://x", "t")
    assert c._pid(42) == "42"
    assert c._pid("42") == "42"


def test_pid_path_encoded():
    c = GitLabClient("https://x", "t")
    assert c._pid("group/sub") == "group%2Fsub"


# ---------------------------------------------------------------------------
# get_merge_request
# ---------------------------------------------------------------------------

@respx.mock
async def test_get_merge_request_ok(client: GitLabClient):
    respx.get(f"{BASE}/projects/42/merge_requests/7").mock(
        return_value=httpx.Response(200, json={
            "iid": 7,
            "target_project_id": 42,
            "title": "feat: add x",
            "web_url": "https://gitlab.example.com/group/proj/-/merge_requests/7",
            "source_branch": "feat",
            "target_branch": "main",
            "diff_refs": {
                "base_sha": "aaa",
                "head_sha": "bbb",
                "start_sha": "ccc",
            },
        })
    )
    mr = await client.get_merge_request(42, 7)
    assert mr.project_id == 42
    assert mr.mr_iid == 7
    assert mr.title == "feat: add x"
    assert mr.diff_refs.base_sha == "aaa"
    assert mr.diff_refs.head_sha == "bbb"


@respx.mock
async def test_get_merge_request_missing_diff_refs(client: GitLabClient):
    respx.get(f"{BASE}/projects/42/merge_requests/7").mock(
        return_value=httpx.Response(200, json={"iid": 7})
    )
    mr = await client.get_merge_request(42, 7)
    assert mr.diff_refs.base_sha == ""
    assert mr.diff_refs.head_sha == ""


@respx.mock
async def test_get_merge_request_404(client: GitLabClient):
    respx.get(f"{BASE}/projects/42/merge_requests/7").mock(
        return_value=httpx.Response(404, text="not found")
    )
    with pytest.raises(GitLabError, match="404"):
        await client.get_merge_request(42, 7)


@respx.mock
async def test_get_merge_request_with_path_pid(client: GitLabClient):
    respx.get(f"{BASE}/projects/group%2Fproj/merge_requests/7").mock(
        return_value=httpx.Response(200, json={"iid": 7, "target_project_id": 99})
    )
    mr = await client.get_merge_request("group/proj", 7)
    assert mr.project_id == 99


# ---------------------------------------------------------------------------
# get_mr_changes
# ---------------------------------------------------------------------------

@respx.mock
async def test_get_mr_changes_ok(client: GitLabClient):
    respx.get(f"{BASE}/projects/42/merge_requests/7/changes").mock(
        return_value=httpx.Response(200, json={
            "changes": [
                {
                    "old_path": "a.ts",
                    "new_path": "a.ts",
                    "new_file": False,
                    "deleted_file": False,
                    "renamed_file": False,
                    "diff": "@@ -1,1 +1,1 @@\n+a\n",
                },
                {
                    "old_path": "b.ts",
                    "new_path": "b.ts",
                    "new_file": True,
                    "deleted_file": False,
                    "renamed_file": False,
                    "diff": "",
                },
            ]
        })
    )
    changes = await client.get_mr_changes(42, 7)
    assert len(changes) == 2
    assert changes[0].new_path == "a.ts"
    assert changes[0].diff == "@@ -1,1 +1,1 @@\n+a\n"
    assert changes[1].new_file is True


@respx.mock
async def test_get_mr_changes_empty(client: GitLabClient):
    respx.get(f"{BASE}/projects/42/merge_requests/7/changes").mock(
        return_value=httpx.Response(200, json={})
    )
    changes = await client.get_mr_changes(42, 7)
    assert changes == []


# ---------------------------------------------------------------------------
# get_file_content
# ---------------------------------------------------------------------------

@respx.mock
async def test_get_file_content_ok(client: GitLabClient):
    respx.get(f"{BASE}/projects/42/repository/files/src%2Fapp%2Fpage.tsx/raw").mock(
        return_value=httpx.Response(200, text="export default function Page() {}")
    )
    content = await client.get_file_content(42, "src/app/page.tsx", "main")
    assert content == "export default function Page() {}"


@respx.mock
async def test_get_file_content_404(client: GitLabClient):
    respx.get(f"{BASE}/projects/42/repository/files/missing.ts/raw").mock(
        return_value=httpx.Response(404, text="not found")
    )
    with pytest.raises(GitLabError, match="404"):
        await client.get_file_content(42, "missing.ts", "main")


@respx.mock
async def test_get_file_content_passes_ref_param(client: GitLabClient):
    route = respx.get(f"{BASE}/projects/42/repository/files/a.ts/raw").mock(
        return_value=httpx.Response(200, text="x")
    )
    await client.get_file_content(42, "a.ts", "feature-branch")
    assert route.calls.last.request.url.params["ref"] == "feature-branch"


# ---------------------------------------------------------------------------
# post_mr_note
# ---------------------------------------------------------------------------

@respx.mock
async def test_post_mr_note_ok(client: GitLabClient):
    route = respx.post(f"{BASE}/projects/42/merge_requests/7/notes").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    result = await client.post_mr_note(42, 7, "hello")
    assert result == {"id": 1}
    import json
    body = json.loads(route.calls.last.request.content)
    assert body == {"body": "hello"}


@respx.mock
async def test_post_mr_note_error(client: GitLabClient):
    respx.post(f"{BASE}/projects/42/merge_requests/7/notes").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    with pytest.raises(GitLabError, match="403"):
        await client.post_mr_note(42, 7, "hello")


# ---------------------------------------------------------------------------
# post_mr_diff_note
# ---------------------------------------------------------------------------

@respx.mock
async def test_post_mr_diff_note_ok(client: GitLabClient):
    route = respx.post(f"{BASE}/projects/42/merge_requests/7/discussions").mock(
        return_value=httpx.Response(201, json={"id": "d1"})
    )
    position = {
        "position_type": "text",
        "base_sha": "a", "head_sha": "b", "start_sha": "c",
        "new_path": "a.ts", "old_path": "a.ts", "new_line": 5,
    }
    result = await client.post_mr_diff_note(42, 7, "comment", position)
    assert result == {"id": "d1"}
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["body"] == "comment"
    assert body["position"]["new_line"] == 5


@respx.mock
async def test_post_mr_diff_note_fallback_to_note(client: GitLabClient):
    """行内评论失败时降级为普通评论。"""
    discussions = respx.post(f"{BASE}/projects/42/merge_requests/7/discussions").mock(
        return_value=httpx.Response(400, text="bad position")
    )
    notes = respx.post(f"{BASE}/projects/42/merge_requests/7/notes").mock(
        return_value=httpx.Response(201, json={"id": 2})
    )
    position = {"new_path": "a.ts", "new_line": 5}
    result = await client.post_mr_diff_note(42, 7, "comment", position)
    assert result == {"id": 2}
    assert discussions.call_count == 1
    assert notes.call_count == 1
    # 降级评论应包含文件路径与行号
    import json
    fallback_body = json.loads(notes.calls.last.request.content)["body"]
    assert "a.ts" in fallback_body
    assert "5" in fallback_body


# ---------------------------------------------------------------------------
# context manager
# ---------------------------------------------------------------------------

@respx.mock
async def test_async_context_manager():
    respx.get(f"{BASE}/projects/1/merge_requests/1").mock(
        return_value=httpx.Response(200, json={"iid": 1})
    )
    async with GitLabClient("https://gitlab.example.com", TOKEN) as c:
        mr = await c.get_merge_request(1, 1)
        assert mr.mr_iid == 1


# ---------------------------------------------------------------------------
# token header
# ---------------------------------------------------------------------------

@respx.mock
async def test_token_header_sent(client: GitLabClient):
    route = respx.get(f"{BASE}/projects/1/merge_requests/1").mock(
        return_value=httpx.Response(200, json={"iid": 1})
    )
    await client.get_merge_request(1, 1)
    assert route.calls.last.request.headers["PRIVATE-TOKEN"] == TOKEN
