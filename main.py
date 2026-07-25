"""Next.js 代码审查 MCP 服务入口。

提供：
  - GET  /            健康检查
  - GET  /rules       列出审查规则
  - POST /webhook/gitlab   接收 GitLab MR webhook，触发代码审查并把结果评论回 MR
  - MCP streamable HTTP 端点挂载在 /mcp，供 MCP 客户端调用审查工具
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from config import get_settings
from mcp_server import list_rules, mcp, run_merge_request_review

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
        "webhook_secret_configured": bool(settings.webhook_secret),
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


# 挂载 MCP streamable HTTP 端点（端点路径为 /mcp）
# 注意：挂载需在其它路由声明之后，使 FastAPI 路由优先匹配。
app.mount("/", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
