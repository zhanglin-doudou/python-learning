"""GitHub REST API 客户端：获取 PR 信息、变更文件、文件内容，发表评论与行内 PR review 评论。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


@dataclass
class PullRequestInfo:
    owner: str
    repo: str
    number: int
    title: str
    html_url: str
    head_sha: str
    base_sha: str


@dataclass
class PRFile:
    filename: str
    status: str  # "added", "removed", "modified", "renamed"
    patch: str
    additions: int
    deletions: int
    changes: int
    previous_filename: str | None = None


@dataclass
class PRReviewComment:
    body: str
    path: str
    position: int  # patch 中的位置（1-based）
    commit_id: str
    side: str = "RIGHT"


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    """轻量 GitHub API v3 客户端（基于 httpx）。"""

    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self.token = token
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self._client.get(path, params=params)
        if resp.status_code >= 400:
            raise GitHubError(f"GET {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    async def _post_json(self, path: str, json: dict[str, Any]) -> Any:
        resp = await self._client.post(path, json=json)
        if resp.status_code >= 400:
            raise GitHubError(f"POST {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    async def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequestInfo:
        data = await self._get_json(f"/repos/{owner}/{repo}/pulls/{number}")
        head = data.get("head") or {}
        base = data.get("base") or {}
        return PullRequestInfo(
            owner=owner,
            repo=repo,
            number=int(data["number"]),
            title=data.get("title", ""),
            html_url=data.get("html_url", ""),
            head_sha=head.get("sha", ""),
            base_sha=base.get("sha", ""),
        )

    async def get_pr_files(self, owner: str, repo: str, number: int) -> list[PRFile]:
        data = await self._get_json(f"/repos/{owner}/{repo}/pulls/{number}/files")
        result: list[PRFile] = []
        for item in data:
            result.append(
                PRFile(
                    filename=item.get("filename", ""),
                    status=item.get("status", ""),
                    patch=item.get("patch", "") or "",
                    additions=item.get("additions", 0),
                    deletions=item.get("deletions", 0),
                    changes=item.get("changes", 0),
                    previous_filename=item.get("previous_filename"),
                )
            )
        return result

    async def get_file_content(self, owner: str, repo: str, path: str, ref: str) -> str:
        encoded = quote(path, safe="")
        resp = await self._client.get(
            f"/repos/{owner}/{repo}/contents/{encoded}",
            params={"ref": ref},
        )
        if resp.status_code >= 400:
            raise GitHubError(
                f"GET contents {path}@{ref} -> {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
        # GitHub contents API 返回 base64 编码的内容
        import base64
        content = data.get("content", "")
        # 去除换行符后解码（GitHub base64 有换行符）
        return base64.b64decode(content.replace("\n", "")).decode("utf-8")

    async def post_pr_comment(self, owner: str, repo: str, number: int, body: str) -> Any:
        """发表普通 PR 评论（显示在 PR 讨论区）。"""
        return await self._post_json(
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            json={"body": body},
        )

    async def post_pr_review_comment(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
        commit_id: str,
        path: str,
        position: int,
        side: str = "RIGHT",
    ) -> Any:
        """发表 PR review 行内评论。position 是 diff patch 中的位置。"""
        payload = {
            "body": body,
            "commit_id": commit_id,
            "path": path,
            "position": position,
            "side": side,
        }
        try:
            return await self._post_json(
                f"/repos/{owner}/{repo}/pulls/{number}/comments",
                json=payload,
            )
        except GitHubError as e:
            # 行内评论失败时降级为普通评论
            logger.warning("PR review comment failed (%s); falling back to PR comment", e)
            return await self.post_pr_comment(
                owner, repo, number, f"({path}:{position}) {body}"
            )


def parse_patch_positions(patch: str) -> dict[int, int]:
    """解析 GitHub patch，返回新增行号 -> position 的映射。

    GitHub position 从 patch 的第一行（@@ 行）开始计算为 1，
    包括所有 diff 行（上下文行、删除行、新增行）。
    """
    positions: dict[int, int] = {}
    pos = 0
    new_line = 0

    for line in patch.splitlines():
        if line.startswith("@@"):
            # 解析 @@ 行，获取新增文件起始行号
            # 例如：@@ -1,2 +1,3 @@
            m = re.search(r"\+(\d+)(?:,\d+)?", line)
            if m:
                new_line = int(m.group(1)) - 1
            pos += 1
        elif line.startswith("---") or line.startswith("+++"):
            pos += 1
        elif line.startswith("+"):
            new_line += 1
            pos += 1
            positions[new_line] = pos
        elif line.startswith("-"):
            pos += 1
        else:
            # 上下文行
            new_line += 1
            pos += 1

    return positions
