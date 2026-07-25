"""GitLab REST API 客户端：获取 MR 信息、变更文件、文件内容，发表评论与行内 diff 评论。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


@dataclass
class DiffRefs:
    base_sha: str
    head_sha: str
    start_sha: str


@dataclass
class MergeRequestInfo:
    project_id: int
    mr_iid: int
    title: str
    web_url: str
    source_branch: str
    target_branch: str
    diff_refs: DiffRefs


@dataclass
class ChangedFile:
    old_path: str
    new_path: str
    new_file: bool
    deleted_file: bool
    renamed_file: bool
    diff: str  # 统一 diff 文本


class GitLabError(RuntimeError):
    pass


class GitLabClient:
    """轻量 GitLab API v4 客户端（基于 httpx）。"""

    def __init__(self, base_url: str, token: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v4",
            headers={"PRIVATE-TOKEN": token},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "GitLabClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def _pid(self, project_id: int | str) -> str:
        # project id 支持数字或 URL 编码路径
        if isinstance(project_id, str) and "/" in project_id:
            return quote(project_id, safe="")
        return str(project_id)

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self._client.get(path, params=params)
        if resp.status_code >= 400:
            raise GitLabError(f"GET {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    async def _post_json(self, path: str, json: dict[str, Any]) -> Any:
        resp = await self._client.post(path, json=json)
        if resp.status_code >= 400:
            raise GitLabError(f"POST {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    async def get_merge_request(self, project_id: int | str, mr_iid: int) -> MergeRequestInfo:
        pid = self._pid(project_id)
        data = await self._get_json(f"/projects/{pid}/merge_requests/{mr_iid}")
        refs = data.get("diff_refs") or {}
        return MergeRequestInfo(
            project_id=int(data.get("target_project_id", project_id)),
            mr_iid=int(data["iid"]),
            title=data.get("title", ""),
            web_url=data.get("web_url", ""),
            source_branch=data.get("source_branch", ""),
            target_branch=data.get("target_branch", ""),
            diff_refs=DiffRefs(
                base_sha=refs.get("base_sha", ""),
                head_sha=refs.get("head_sha", ""),
                start_sha=refs.get("start_sha", ""),
            ),
        )

    async def get_mr_changes(self, project_id: int | str, mr_iid: int) -> list[ChangedFile]:
        pid = self._pid(project_id)
        data = await self._get_json(
            f"/projects/{pid}/merge_requests/{mr_iid}/changes"
        )
        changes = data.get("changes", [])
        result: list[ChangedFile] = []
        for c in changes:
            result.append(
                ChangedFile(
                    old_path=c.get("old_path", ""),
                    new_path=c.get("new_path", ""),
                    new_file=bool(c.get("new_file", False)),
                    deleted_file=bool(c.get("deleted_file", False)),
                    renamed_file=bool(c.get("renamed_file", False)),
                    diff=c.get("diff", "") or "",
                )
            )
        return result

    async def get_file_content(
        self, project_id: int | str, file_path: str, ref: str
    ) -> str:
        pid = self._pid(project_id)
        encoded = quote(file_path, safe="")
        resp = await self._client.get(
            f"/projects/{pid}/repository/files/{encoded}/raw",
            params={"ref": ref},
        )
        if resp.status_code >= 400:
            raise GitLabError(
                f"GET raw file {file_path}@{ref} -> {resp.status_code}: {resp.text[:200]}"
            )
        return resp.text

    async def post_mr_note(self, project_id: int | str, mr_iid: int, body: str) -> Any:
        pid = self._pid(project_id)
        return await self._post_json(
            f"/projects/{pid}/merge_requests/{mr_iid}/notes",
            json={"body": body},
        )

    async def post_mr_diff_note(
        self,
        project_id: int | str,
        mr_iid: int,
        body: str,
        position: dict[str, Any],
    ) -> Any:
        """在 MR diff 的指定行发表行内评论。position 需包含 position_type/base_sha/head_sha/start_sha/new_path/new_line。"""
        pid = self._pid(project_id)
        payload = {"body": body, "position": position}
        # GitLab: 创建带位置的讨论会作为 diff note 显示在对应行
        try:
            return await self._post_json(
                f"/projects/{pid}/merge_requests/{mr_iid}/discussions", json=payload
            )
        except GitLabError as e:
            # 行内评论失败时降级为普通评论，保证结果可见
            logger.warning("inline note failed (%s); falling back to MR note", e)
            return await self.post_mr_note(
                project_id, mr_iid, f"({position.get('new_path')}:{position.get('new_line')}) {body}"
            )
