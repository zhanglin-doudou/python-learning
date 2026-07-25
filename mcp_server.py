"""MCP 服务：暴露 Next.js 代码审查工具，并封装 GitLab MR 审查编排逻辑。

工具：
  - list_review_rules: 列出所有审查规则
  - review_code: 审查一段代码内容（无副作用）
  - review_merge_request: 审查 GitLab MR 变更并把结果评论回 MR
  - post_mr_comment: 向 GitLab MR 发表一条评论
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from config import get_settings
from gitlab_client import GitLabClient, GitLabError
from reviewer import (
    Finding,
    format_summary,
    is_reviewable,
    list_rules,
    parse_added_new_lines,
    review_files,
)

logger = logging.getLogger(__name__)

# FastMCP 实例：streamable HTTP 传输，端点挂载在 /mcp
mcp = FastMCP(
    "nextjs-reviewer",
    stateless_http=True,
    streamable_http_path="/mcp",
)


@dataclass
class ReviewResult:
    project_id: int | str
    mr_iid: int
    reviewed_files: int
    skipped_files: int
    findings: list[Finding]
    inline_comments_posted: int
    summary_posted: bool
    error: str | None = None


async def run_merge_request_review(
    project_id: int | str,
    mr_iid: int,
    post_comments: bool = True,
) -> ReviewResult:
    """审查 GitLab MR 变更文件，可选地把结果评论回 MR。

    被 MCP 工具与 GitLab webhook 共用。
    """
    settings = get_settings()
    if not settings.gitlab_token:
        return ReviewResult(
            project_id=project_id, mr_iid=mr_iid, reviewed_files=0,
            skipped_files=0, findings=[], inline_comments_posted=0,
            summary_posted=False, error="GITLAB_TOKEN 未配置",
        )

    findings: list[Finding] = []
    reviewed = 0
    skipped = 0
    inline_posted = 0
    summary_posted = False

    async with GitLabClient(settings.gitlab_url, settings.gitlab_token) as gl:
        try:
            mr = await gl.get_merge_request(project_id, mr_iid)
            changes = await gl.get_mr_changes(project_id, mr_iid)
        except GitLabError as e:
            return ReviewResult(
                project_id=project_id, mr_iid=mr_iid, reviewed_files=0,
                skipped_files=0, findings=[], inline_comments_posted=0,
                summary_posted=False, error=f"获取 MR 失败: {e}",
            )

        if not mr.diff_refs.head_sha:
            logger.warning("MR !%s 缺少 diff_refs.head_sha", mr_iid)

        # 选出可审查的、未删除的文件
        candidates = [
            c for c in changes
            if not c.deleted_file and is_reviewable(c.new_path) and c.diff
        ]
        candidates = candidates[: settings.max_files_per_review]

        contents: dict[str, str] = {}
        added_lines_map: dict[str, set[int]] = {}
        old_path_map: dict[str, str] = {}

        for c in candidates:
            try:
                content = await gl.get_file_content(
                    project_id, c.new_path, mr.diff_refs.head_sha or mr.source_branch
                )
            except GitLabError as e:
                logger.warning("读取文件失败 %s: %s", c.new_path, e)
                skipped += 1
                continue
            if len(content) > settings.max_file_size_bytes:
                skipped += 1
                continue
            contents[c.new_path] = content
            added_lines_map[c.new_path] = parse_added_new_lines(c.diff)
            old_path_map[c.new_path] = c.old_path or c.new_path
            reviewed += 1

        all_findings = review_files(contents)
        # 仅保留位于本次 MR 新增行上的发现，使审查聚焦于本次改动
        findings = [
            f for f in all_findings if f.line in added_lines_map.get(f.file_path, set())
        ]

        if post_comments:
            # 1) 行内评论（按严重度排序，限量）
            ordered = sorted(
                findings,
                key=lambda f: (
                    {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(f.severity, 9),
                    f.file_path,
                    f.line,
                ),
            )
            for f in ordered:
                if inline_posted >= settings.max_inline_comments:
                    break
                position = {
                    "position_type": "text",
                    "base_sha": mr.diff_refs.base_sha,
                    "head_sha": mr.diff_refs.head_sha,
                    "start_sha": mr.diff_refs.start_sha,
                    "new_path": f.file_path,
                    "old_path": old_path_map.get(f.file_path, f.file_path),
                    "new_line": f.line,
                }
                body = (
                    f"**[{f.severity.upper()}] `{f.rule_id}`**\n\n"
                    f"{f.message}\n\n**建议**: {f.suggestion}"
                )
                try:
                    await gl.post_mr_diff_note(project_id, mr_iid, body, position)
                    inline_posted += 1
                except Exception as e:
                    logger.warning("行内评论失败 %s:%d: %s", f.file_path, f.line, e)

            # 2) 汇总评论
            try:
                summary = format_summary(findings, mr.title, mr.web_url)
                await gl.post_mr_note(project_id, mr_iid, summary)
                summary_posted = True
            except GitLabError as e:
                logger.error("发表汇总评论失败: %s", e)

    return ReviewResult(
        project_id=project_id,
        mr_iid=mr_iid,
        reviewed_files=reviewed,
        skipped_files=skipped,
        findings=findings,
        inline_comments_posted=inline_posted,
        summary_posted=summary_posted,
    )


# ---------------------------------------------------------------------------
# MCP 工具
# ---------------------------------------------------------------------------


@mcp.tool()
def list_review_rules() -> str:
    """列出当前可用的 Next.js 代码审查规则及其说明。"""
    rules = list_rules()
    return json.dumps(rules, ensure_ascii=False, indent=2)


@mcp.tool()
def review_code(file_path: str, content: str) -> str:
    """对一段 Next.js/TS/JS 代码内容执行静态审查，返回发现的问题（不会产生任何副作用）。

    参数:
      file_path: 文件路径（用于判断扩展名与结果展示）
      content: 文件内容
    """
    if not is_reviewable(file_path):
        return f"文件 `{file_path}` 不在可审查范围内（支持: .ts/.tsx/.js/.jsx/.mjs/.cjs）。"
    findings = review_files({file_path: content})
    return format_summary(findings)


@mcp.tool()
async def review_merge_request(project_id: int, mr_iid: int) -> str:
    """审查指定 GitLab MR 的变更文件，并将结果评论回该 MR（行内评论 + 汇总评论）。

    参数:
      project_id: GitLab 项目 ID（数字）或 URL 编码路径
      mr_iid: Merge Request 的 iid（项目内编号）
    """
    result = await run_merge_request_review(project_id, mr_iid, post_comments=True)
    if result.error:
        return f"❌ 审查失败: {result.error}"
    parts = [
        f"✅ MR !{result.mr_iid} 审查完成",
        f"- 审查文件数: {result.reviewed_files}（跳过 {result.skipped_files}）",
        f"- 发现问题数: {len(result.findings)}",
        f"- 行内评论: {result.inline_comments_posted}",
        f"- 汇总评论已发布: {'是' if result.summary_posted else '否'}",
    ]
    if result.findings:
        parts.append("")
        parts.append(format_summary(result.findings))
    return "\n".join(parts)


@mcp.tool()
async def post_mr_comment(project_id: int, mr_iid: int, comment: str) -> str:
    """向指定 GitLab MR 发表一条普通评论。

    参数:
      project_id: GitLab 项目 ID（数字）或 URL 编码路径
      mr_iid: Merge Request 的 iid
      comment: 评论内容（支持 markdown）
    """
    settings = get_settings()
    if not settings.gitlab_token:
        return "❌ GITLAB_TOKEN 未配置，无法发表评论。"
    async with GitLabClient(settings.gitlab_url, settings.gitlab_token) as gl:
        try:
            await gl.post_mr_note(project_id, mr_iid, comment)
        except GitLabError as e:
            return f"❌ 发表评论失败: {e}"
    return f"✅ 已在 MR !{mr_iid} 发表评论。"
