"""main.py 单元测试：FastAPI 路由、webhook 处理、token 校验。

使用 httpx.AsyncClient + ASGITransport 进行异步测试，避免 TestClient 的同步事件循环冲突。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import main
from mcp_server import ReviewResult


@pytest.fixture
def settings_no_secret(monkeypatch):
    """配置 token 但不设 webhook secret（跳过校验）。"""
    import config
    fake = MagicMock()
    fake.gitlab_url = "https://gitlab.example.com"
    fake.gitlab_token = "tok"
    fake.webhook_secret = ""
    fake.github_token = "ghp-test"
    fake.github_webhook_secret = ""
    fake.openai_api_key = "sk-test"
    fake.max_files_per_review = 50
    fake.max_inline_comments = 20
    fake.max_file_size_bytes = 200_000
    fake.host = "0.0.0.0"
    fake.port = 8000
    monkeypatch.setattr(config, "settings", fake)
    monkeypatch.setattr(main, "settings", fake)
    return fake


@pytest.fixture
def settings_with_secret(monkeypatch):
    """配置 token 与 webhook secret。"""
    import config
    fake = MagicMock()
    fake.gitlab_url = "https://gitlab.example.com"
    fake.gitlab_token = "tok"
    fake.webhook_secret = "my-secret"
    fake.github_token = "ghp-test"
    fake.github_webhook_secret = "gh-secret"
    fake.openai_api_key = "sk-test"
    fake.max_files_per_review = 50
    fake.max_inline_comments = 20
    fake.max_file_size_bytes = 200_000
    fake.host = "0.0.0.0"
    fake.port = 8000
    monkeypatch.setattr(config, "settings", fake)
    monkeypatch.setattr(main, "settings", fake)
    return fake


@pytest.fixture
async def client():
    """构建 ASGI 测试客户端。

    不触发 lifespan（MCP session manager 的 anyio task group 与 pytest-asyncio
    的 task 边界冲突）。test_main 只测路由逻辑，不测 /mcp 端点，无需 session manager。
    """
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

async def test_health_ok(client: httpx.AsyncClient, settings_no_secret):
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "nextjs-review-mcp"
    assert data["status"] == "ok"
    assert data["mcp_endpoint"] == "/mcp"
    assert data["gitlab_configured"] is True


async def test_health_token_not_configured(client: httpx.AsyncClient, monkeypatch):
    import config
    fake = MagicMock()
    fake.gitlab_token = ""
    fake.webhook_secret = ""
    fake.github_token = ""
    fake.github_webhook_secret = ""
    fake.openai_api_key = ""
    monkeypatch.setattr(config, "settings", fake)
    monkeypatch.setattr(main, "settings", fake)
    resp = await client.get("/")
    assert resp.json()["gitlab_configured"] is False
    assert resp.json()["github_configured"] is False
    assert resp.json()["llm_configured"] is False


# ---------------------------------------------------------------------------
# GET /rules
# ---------------------------------------------------------------------------

async def test_rules_endpoint(client: httpx.AsyncClient, settings_no_secret):
    resp = await client.get("/rules")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "rule_id" in data[0]


# ---------------------------------------------------------------------------
# POST /webhook/gitlab — token 校验
# ---------------------------------------------------------------------------

async def test_webhook_rejected_without_secret_token(client: httpx.AsyncClient, settings_with_secret):
    """配置了 secret 但请求未带 X-Gitlab-Token -> 401。"""
    resp = await client.post("/webhook/gitlab", json={"object_kind": "merge_request"})
    assert resp.status_code == 401


async def test_webhook_rejected_wrong_secret(client: httpx.AsyncClient, settings_with_secret):
    """X-Gitlab-Token 不匹配 -> 401。"""
    resp = await client.post(
        "/webhook/gitlab",
        json={"object_kind": "merge_request"},
        headers={"X-Gitlab-Token": "wrong"},
    )
    assert resp.status_code == 401


async def test_webhook_accepted_correct_secret(client: httpx.AsyncClient, settings_with_secret):
    """X-Gitlab-Token 匹配但非 MR 事件 -> ignored。"""
    resp = await client.post(
        "/webhook/gitlab",
        json={"object_kind": "push"},
        headers={"X-Gitlab-Token": "my-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


# ---------------------------------------------------------------------------
# POST /webhook/gitlab — 事件过滤
# ---------------------------------------------------------------------------

async def test_webhook_ignores_non_merge_request(client: httpx.AsyncClient, settings_no_secret):
    resp = await client.post("/webhook/gitlab", json={"object_kind": "push"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


async def test_webhook_ignores_unsupported_action(client: httpx.AsyncClient, settings_no_secret):
    resp = await client.post("/webhook/gitlab", json={
        "object_kind": "merge_request",
        "object_attributes": {"action": "close", "iid": 1},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ignored"
    assert "action=close" in body["reason"]


async def test_webhook_accepts_trigger_actions(client: httpx.AsyncClient, settings_no_secret):
    """open/reopen/update 应被接受。"""
    for action in ("open", "reopen", "update"):
        with patch.object(main, "_safe_review", new=AsyncMock()):
            resp = await client.post("/webhook/gitlab", json={
                "object_kind": "merge_request",
                "object_attributes": {
                    "action": action,
                    "iid": 5,
                    "target_project_id": 42,
                },
                "project": {"id": 42},
            })
        assert resp.status_code == 200, f"action={action}"
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["mr_iid"] == 5
        assert body["project_id"] == 42
        assert body["action"] == action


# ---------------------------------------------------------------------------
# POST /webhook/gitlab — 参数校验
# ---------------------------------------------------------------------------

async def test_webhook_missing_iid(client: httpx.AsyncClient, settings_no_secret):
    resp = await client.post("/webhook/gitlab", json={
        "object_kind": "merge_request",
        "object_attributes": {"action": "open"},  # 无 iid
        "project": {"id": 42},
    })
    assert resp.status_code == 400


async def test_webhook_missing_project_id(client: httpx.AsyncClient, settings_no_secret):
    resp = await client.post("/webhook/gitlab", json={
        "object_kind": "merge_request",
        "object_attributes": {"action": "open", "iid": 5},
    })
    assert resp.status_code == 400


async def test_webhook_invalid_json(client: httpx.AsyncClient, settings_no_secret):
    resp = await client.post(
        "/webhook/gitlab",
        content="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /webhook/gitlab — 项目 ID 来源
# ---------------------------------------------------------------------------

async def test_webhook_project_id_from_project_object(client: httpx.AsyncClient, settings_no_secret):
    """target_project_id 缺失时从 project.id 取。"""
    with patch.object(main, "_safe_review", new=AsyncMock()) as mock_review:
        resp = await client.post("/webhook/gitlab", json={
            "object_kind": "merge_request",
            "object_attributes": {"action": "open", "iid": 5},  # 无 target_project_id
            "project": {"id": 99},
        })
    assert resp.status_code == 200
    assert resp.json()["project_id"] == 99
    # 后台任务被调度
    mock_review.assert_called_once_with(99, 5)


# ---------------------------------------------------------------------------
# POST /webhook/gitlab — 后台任务
# ---------------------------------------------------------------------------

async def test_webhook_triggers_background_review(client: httpx.AsyncClient, settings_no_secret):
    """webhook 应异步触发 _safe_review。"""
    with patch.object(main, "_safe_review", new=AsyncMock()) as mock_review:
        resp = await client.post("/webhook/gitlab", json={
            "object_kind": "merge_request",
            "object_attributes": {"action": "open", "iid": 7, "target_project_id": 42},
        })
    assert resp.status_code == 200
    # 给后台任务一点时间执行
    import asyncio
    await asyncio.sleep(0.05)
    mock_review.assert_called_once_with(42, 7)


async def test_safe_review_handles_error(monkeypatch):
    """_safe_review 捕获异常不抛出。"""
    async def boom(*a, **kw):
        raise RuntimeError("crash")
    monkeypatch.setattr(main, "run_merge_request_review", boom)
    # 不应抛出异常
    await main._safe_review(42, 7)


async def test_safe_review_logs_error_result(monkeypatch):
    """_safe_review 在 result.error 时记录日志（不抛出）。"""
    async def returning_error(*a, **kw):
        return ReviewResult(
            project_id=42, mr_iid=7, reviewed_files=0, skipped_files=0,
            findings=[], inline_comments_posted=0, summary_posted=False,
            error="something wrong",
        )
    monkeypatch.setattr(main, "run_merge_request_review", returning_error)
    await main._safe_review(42, 7)  # 不抛异常即可


# ---------------------------------------------------------------------------
# GitHub Webhook
# ---------------------------------------------------------------------------

_GITHUB_PAYLOAD = {
    "action": "opened",
    "number": 1,
    "repository": {"full_name": "owner/repo"},
}


def _make_github_signature(body: bytes, secret: str) -> str:
    import hashlib
    import hmac
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def test_github_webhook_rejected_without_signature(client: httpx.AsyncClient, settings_with_secret):
    """配置了 secret 但请求未带 X-Hub-Signature-256 -> 401。"""
    resp = await client.post("/webhook/github", json=_GITHUB_PAYLOAD)
    assert resp.status_code == 401


async def test_github_webhook_rejected_wrong_signature(client: httpx.AsyncClient, settings_with_secret):
    """X-Hub-Signature-256 不匹配 -> 401。"""
    resp = await client.post(
        "/webhook/github",
        json=_GITHUB_PAYLOAD,
        headers={"X-Hub-Signature-256": "sha256=bad", "X-GitHub-Event": "pull_request"},
    )
    assert resp.status_code == 401


async def test_github_webhook_accepts_correct_signature(client: httpx.AsyncClient, settings_with_secret):
    """签名正确但 event 不是 pull_request -> ignored。"""
    import json
    body = json.dumps({"action": "opened", "number": 1, "repository": {"full_name": "owner/repo"}}).encode()
    sig = _make_github_signature(body, "gh-secret")
    resp = await client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "push",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


async def test_github_webhook_ignores_unsupported_action(client: httpx.AsyncClient, settings_no_secret):
    resp = await client.post(
        "/webhook/github",
        json={"action": "closed", "number": 1, "repository": {"full_name": "owner/repo"}},
        headers={"X-GitHub-Event": "pull_request"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ignored"
    assert "closed" in body["reason"]


async def test_github_webhook_accepts_trigger_actions(client: httpx.AsyncClient, settings_no_secret):
    """opened/reopened/synchronize 应被接受。"""
    import json
    for action in ("opened", "reopened", "synchronize"):
        payload = {"action": action, "number": 5, "repository": {"full_name": "owner/repo"}}
        with patch.object(main, "_safe_github_review", new=AsyncMock()):
            resp = await client.post(
                "/webhook/github",
                json=payload,
                headers={"X-GitHub-Event": "pull_request"},
            )
        assert resp.status_code == 200, f"action={action}"
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["pr_number"] == 5
        assert body["owner"] == "owner"
        assert body["repo"] == "repo"
        assert body["action"] == action


async def test_github_webhook_missing_repo_full_name(client: httpx.AsyncClient, settings_no_secret):
    resp = await client.post(
        "/webhook/github",
        json={"action": "opened", "number": 1},
        headers={"X-GitHub-Event": "pull_request"},
    )
    assert resp.status_code == 400


async def test_github_webhook_triggers_background_review(client: httpx.AsyncClient, settings_no_secret):
    """webhook 应异步触发 _safe_github_review。"""
    import asyncio
    with patch.object(main, "_safe_github_review", new=AsyncMock()) as mock_review:
        resp = await client.post(
            "/webhook/github",
            json={"action": "opened", "number": 7, "repository": {"full_name": "owner/repo"}},
            headers={"X-GitHub-Event": "pull_request"},
        )
    assert resp.status_code == 200
    await asyncio.sleep(0.05)
    mock_review.assert_called_once_with("owner", "repo", 7)


async def test_safe_github_review_handles_error(monkeypatch):
    """_safe_github_review 捕获异常不抛出。"""
    async def boom(*a, **kw):
        raise RuntimeError("crash")
    monkeypatch.setattr(main, "run_github_pr_review", boom)
    await main._safe_github_review("owner", "repo", 1)


async def test_safe_github_review_logs_error_result(monkeypatch):
    """_safe_github_review 在 result.error 时记录日志（不抛出）。"""
    from mcp_server import GitHubReviewResult
    async def returning_error(*a, **kw):
        return GitHubReviewResult(
            owner="owner", repo="repo", pr_number=1,
            reviewed_files=0, skipped_files=0, findings=[],
            inline_comments_posted=0, summary_posted=False,
            error="something wrong",
        )
    monkeypatch.setattr(main, "run_github_pr_review", returning_error)
    await main._safe_github_review("owner", "repo", 1)
