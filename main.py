"""Next.js 代码审查 MCP 服务入口。

提供：
  - GET  /            健康检查
  - GET  /rules       列出审查规则
  - POST /webhook/gitlab   接收 GitLab MR webhook，触发代码审查并把结果评论回 MR
  - MCP streamable HTTP 端点挂载在 /mcp，供 MCP 客户端调用审查工具
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from config import get_settings
from mcp_server import list_rules, mcp, run_github_pr_review, run_merge_request_review

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 启动 MCP streamable HTTP session manager 的 task group。
    # FastAPI 挂载的 Starlette 子应用 lifespan 不会自动运行，需在宿主 lifespan 中手动启动。
    # streamable_http_app() 在下方挂载时已创建 session_manager，此处复用同一实例。
    async with mcp.session_manager.run():
        yield


app = FastAPI(
    title="Next.js Review MCP",
    description="通过 GitLab MR webhook 触发 Next.js 代码审查并回写评论的 MCP 服务",
    version="0.1.0",
    lifespan=lifespan,
)

# 后台任务引用，避免被 GC 回收
_background_tasks: set[asyncio.Task[Any]] = set()


@app.get("/")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "service": "nextjs-review-mcp",
        "status": "ok",
        "mcp_endpoint": "/mcp",
        "gitlab_configured": bool(settings.gitlab_token),
        "github_configured": bool(settings.github_token),
        "llm_configured": bool(settings.openai_api_key),
        "gitlab_webhook_secret_configured": bool(settings.webhook_secret),
        "github_webhook_secret_configured": bool(settings.github_webhook_secret),
    }


@app.get("/rules")
def rules() -> list[dict]:
    return list_rules()


# 触发审查的 MR action
_TRIGGER_ACTIONS = {"open", "reopen", "update"}


@app.post("/webhook/gitlab")
async def gitlab_webhook(
    request: Request,
    x_gitlab_token: str | None = Header(default=None, alias="X-Gitlab-Token"),
) -> dict[str, Any]:
    settings = get_settings()

    # 校验 webhook secret（若配置了）
    if settings.webhook_secret:
        if x_gitlab_token != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="invalid X-Gitlab-Token")
    else:
        logger.warning("GITLAB_WEBHOOK_SECRET 未配置，跳过 webhook 校验（仅限开发环境）")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    if payload.get("object_kind") != "merge_request":
        return {"status": "ignored", "reason": "not a merge_request event"}

    attrs = payload.get("object_attributes") or {}
    action = attrs.get("action")
    if action not in _TRIGGER_ACTIONS:
        return {"status": "ignored", "reason": f"action={action} not in {_TRIGGER_ACTIONS}"}

    mr_iid = attrs.get("iid")
    project_id = attrs.get("target_project_id") or (payload.get("project") or {}).get("id")
    if not mr_iid or not project_id:
        raise HTTPException(status_code=400, detail="missing mr iid or project id")

    # 异步执行审查，立即返回 200，避免 webhook 超时
    task = asyncio.create_task(_safe_review(project_id, mr_iid))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    logger.info("webhook 触发审查: project=%s mr=!%s action=%s", project_id, mr_iid, action)
    return {"status": "accepted", "project_id": project_id, "mr_iid": mr_iid, "action": action}


async def _safe_review(project_id: int | str, mr_iid: int) -> None:
    """包装审查任务，捕获异常并记录日志。"""
    try:
        result = await run_merge_request_review(project_id, mr_iid, post_comments=True)
        if result.error:
            logger.error("审查 project=%s mr=!%s 失败: %s", project_id, mr_iid, result.error)
        else:
            logger.info(
                "审查完成 project=%s mr=!%s: 审查 %d 文件, 发现 %d 问题, 行内评论 %d, 汇总=%s",
                project_id, mr_iid, result.reviewed_files, len(result.findings),
                result.inline_comments_posted, result.summary_posted,
            )
    except Exception:
        logger.exception("审查任务异常 project=%s mr=!%s", project_id, mr_iid)


# ---------------------------------------------------------------------------
# GitHub Webhook
# ---------------------------------------------------------------------------

_GITHUB_TRIGGER_ACTIONS = {"opened", "reopened", "synchronize"}


def _verify_github_signature(body: bytes, signature: str, secret: str) -> bool:
    """验证 GitHub webhook HMAC-SHA256 签名。"""
    import hashlib
    import hmac

    if not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict[str, Any]:
    settings = get_settings()

    # 校验 webhook secret（若配置了）
    if settings.github_webhook_secret:
        body = await request.body()
        if not x_hub_signature_256:
            raise HTTPException(status_code=401, detail="missing X-Hub-Signature-256")
        if not _verify_github_signature(body, x_hub_signature_256, settings.github_webhook_secret):
            raise HTTPException(status_code=401, detail="invalid X-Hub-Signature-256")
        # 重新解析 body（因为 request.body() 已被消费）
        try:
            payload = json.loads(body)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid JSON body")
    else:
        logger.warning("GITHUB_WEBHOOK_SECRET 未配置，跳过 webhook 校验（仅限开发环境）")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid JSON body")

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event={x_github_event} not pull_request"}

    action = payload.get("action")
    if action not in _GITHUB_TRIGGER_ACTIONS:
        return {"status": "ignored", "reason": f"action={action} not in {_GITHUB_TRIGGER_ACTIONS}"}

    pr_number = payload.get("number")
    repo_full_name = (payload.get("repository") or {}).get("full_name", "")
    if not pr_number or not repo_full_name or "/" not in repo_full_name:
        raise HTTPException(status_code=400, detail="missing pr number or repo full_name")

    owner, repo = repo_full_name.split("/", 1)

    # 异步执行审查，立即返回 200，避免 webhook 超时
    task = asyncio.create_task(_safe_github_review(owner, repo, pr_number))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    logger.info("GitHub webhook 触发审查: %s/%s PR#%s action=%s", owner, repo, pr_number, action)
    return {"status": "accepted", "owner": owner, "repo": repo, "pr_number": pr_number, "action": action}


async def _safe_github_review(owner: str, repo: str, pr_number: int) -> None:
    """包装 GitHub PR 审查任务，捕获异常并记录日志。"""
    try:
        result = await run_github_pr_review(owner, repo, pr_number, post_comments=True)
        if result.error:
            logger.error("GitHub PR 审查 %s/%s#%d 失败: %s", owner, repo, pr_number, result.error)
        else:
            logger.info(
                "GitHub PR 审查完成 %s/%s#%d: 审查 %d 文件, 发现 %d 问题, 行内评论 %d, 汇总=%s, tokens=%s",
                owner, repo, pr_number, result.reviewed_files, len(result.findings),
                result.inline_comments_posted, result.summary_posted, result.llm_tokens_used,
            )
    except Exception:
        logger.exception("GitHub PR 审查任务异常 %s/%s#%d", owner, repo, pr_number)


# 挂载 MCP streamable HTTP 端点（端点路径为 /mcp）
# 注意：挂载需在其它路由声明之后，使 FastAPI 路由优先匹配。
app.mount("/", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
