"""Next.js 代码静态审查器。

规则来源于 Vercel React/Next.js 最佳实践（见 vercel-react-best-practices 技能），
采用基于正则的轻量静态分析，可在无需 Node.js 环境的情况下对变更文件做快速审查。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

# 受支持的文件扩展名（Next.js / TS / JS 相关）
REVIEWED_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass
class Finding:
    rule_id: str
    file_path: str
    line: int  # 新文件中的 1-based 行号
    severity: str
    message: str
    suggestion: str

    @property
    def severity_label(self) -> str:
        return self.severity.upper()


@dataclass
class Rule:
    rule_id: str
    title: str
    severity: str
    description: str
    suggestion: str
    # check(content, file_path) -> list[(line, message)] ；message 为空时用 rule 默认描述
    check: Callable[[str, str], list[tuple[int, str]]]


# ---------------------------------------------------------------------------
# 各规则的检查实现
# ---------------------------------------------------------------------------

# 已知的 barrel 文件包：从根入口导入会拖累 bundle
_BARREL_PACKAGES = (
    "react-icons",            # 应使用 react-icons/fa 等子路径
    "@heroicons/react",       # 应使用 /24/outline 等子路径
    "@tabler/icons-react",
    "@phosphor-icons/react",
)


def _check_barrel_imports(content: str, _path: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    pattern = re.compile(
        r"""^\s*import\s+.*?\s+from\s+['"](""" + "|".join(map(re.escape, _BARREL_PACKAGES)) + r""")['"]"""
    )
    for i, line in enumerate(content.splitlines(), start=1):
        m = pattern.match(line)
        if m:
            findings.append(
                (i, f"从 barrel 包 `{m.group(1)}` 根入口导入，建议改用具体子路径以利 tree-shaking。")
            )
    return findings


# 体积较大的库，建议动态导入或按需子路径
_HEAVY_PACKAGES = (
    "chart.js", "moment", "lodash", "mediasoup-client",
    "pdfjs-dist", "three", "framer-motion", "d3",
)


def _check_heavy_static_imports(content: str, _path: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    pattern = re.compile(
        r"""^\s*import\s+.*?\s+from\s+['"](""" + "|".join(map(re.escape, _HEAVY_PACKAGES)) + r""")(\S*)['"]"""
    )
    for i, line in enumerate(content.splitlines(), start=1):
        m = pattern.match(line)
        if m and m.group(2) == "":  # 只命中根包导入，子路径（如 lodash/get）放行
            findings.append(
                (i, f"静态导入了体积较大的库 `{m.group(1)}`，建议使用 next/dynamic 延迟加载或改用具名子路径。")
            )
    return findings


# 第三方分析/埋点库，建议在 hydration 之后加载
_ANALYTICS_PACKAGES = ("posthog", "mixpanel", "amplitude", "@analytics", "gtag", "google-analytics")


def _check_defer_third_party(content: str, _path: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    pattern = re.compile(
        r"""^\s*import\s+.*?\s+from\s+['"](""" + "|".join(map(re.escape, _ANALYTICS_PACKAGES)) + r""")\S*['"]"""
    )
    for i, line in enumerate(content.splitlines(), start=1):
        m = pattern.match(line)
        if m:
            findings.append(
                (i, f"静态导入分析/埋点库 `{m.group(1)}`，建议延迟到 hydration 之后加载，避免阻塞首屏。")
            )
    return findings


# 动态 import 使用模板字符串，破坏静态分析
def _check_dynamic_template_import(content: str, _path: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    pattern = re.compile(r"import\(\s*`")
    for i, line in enumerate(content.splitlines(), start=1):
        if pattern.search(line):
            findings.append(
                (i, "动态 import 使用了模板字符串，无法被打包器静态分析，可能导致整目录被打入 bundle。")
            )
    return findings


# 连续 await 可并行化
def _check_consecutive_awaits(content: str, _path: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    await_assign = re.compile(r"^\s*(?:const|let)\s+\w+\s*=\s*await\s+")
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        if await_assign.match(lines[i]):
            start = i
            while i < len(lines) and await_assign.match(lines[i]):
                i += 1
            count = i - start
            if count >= 2:
                findings.append(
                    (start + 1, f"检测到 {count} 个连续的独立 await 赋值，建议用 Promise.all 并行化以消除瀑布。")
                )
        else:
            i += 1
    return findings


# 在组件内部定义组件（每次渲染都会创建新类型，导致子树重挂载/重渲染）
def _check_inline_component_def(content: str, _path: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    # 缩进定义的、大写开头的组件定义
    pattern = re.compile(r"^\s{2,}(?:function\s+([A-Z]\w*)|(?:const|let)\s+([A-Z]\w*)\s*=\s*(?:\([^)]*\)|\w+)\s*=>)")
    for i, line in enumerate(content.splitlines(), start=1):
        if pattern.match(line):
            findings.append(
                (i, "在组件内部定义了另一个组件，每次渲染都会创建新组件类型，应将其提升到模块作用域。")
            )
    return findings


# useEffect 内调用 setState 派生状态
def _check_derived_state_in_effect(content: str, _path: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    # 匹配 useEffect(() => { ... }, [...]) 块（跨行）
    for m in re.finditer(
        r"useEffect\(\s*\(\)\s*=>\s*\{(?P<body>.*?)\},\s*\[",
        content,
        flags=re.DOTALL,
    ):
        body = m.group("body")
        if re.search(r"\bset[A-Z]\w*\s*\(", body):
            # 计算起始行号
            line_no = content[: m.start()].count("\n") + 1
            findings.append(
                (line_no, "useEffect 中调用 setState 派生状态，建议在渲染期间直接推导，避免额外渲染与 effect 同步。")
            )
    return findings


# 条件渲染用 && 可能渲染出 0 / 空串
def _check_conditional_and(content: str, _path: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    # 形如 {expr && <Component 或 {expr && (
    pattern = re.compile(r"\{[^{}\n]*&&\s*(?:<|\()")
    for i, line in enumerate(content.splitlines(), start=1):
        if pattern.search(line):
            findings.append(
                (i, "使用 `&&` 条件渲染，当左侧为 0/空串时可能渲染出该值，建议改用三元 `cond ? <X/> : null`。")
            )
    return findings


# .filter().map() 链可合并
def _check_filter_map_chain(content: str, _path: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    pattern = re.compile(r"\.filter\([^)]*\)\.map\(")
    for i, line in enumerate(content.splitlines(), start=1):
        if pattern.search(line):
            findings.append(
                (i, "`.filter().map()` 链会创建中间数组，可用 `.flatMap()` 在一次遍历内完成过滤与映射。")
            )
    return findings


# 非被动事件监听器（scroll/wheel/touchmove）
_NON_PASSIVE_EVENTS = ("scroll", "wheel", "touchmove", "touchstart")


def _check_passive_listeners(content: str, _path: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    pattern = re.compile(
        r"addEventListener\(\s*['\"](" + "|".join(_NON_PASSIVE_EVENTS) + r")['\"]"
    )
    for i, line in enumerate(content.splitlines(), start=1):
        m = pattern.search(line)
        if m and "passive" not in line:
            findings.append(
                (i, f"`addEventListener('{m.group(1)}', ...)` 未声明 `{{ passive: true }}`，会阻塞主线程滚动。")
            )
    return findings


# 模块级可变状态（在 SSR/RSC 中会跨请求共享）
def _check_module_level_mutable(content: str, _path: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    pattern = re.compile(r"^let\s+\w+")
    for i, line in enumerate(content.splitlines(), start=1):
        if pattern.match(line):
            findings.append(
                (i, "模块级 `let` 可变状态在 SSR/RSC 下会跨请求共享，建议改为函数内局部状态或显式缓存。")
            )
    return findings


# 内联对象/数组默认 prop 值
def _check_inline_default_prop(content: str, _path: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    pattern = re.compile(r"\(\s*\{[^}]*=\s*(?:\{\}|\[\])")
    for i, line in enumerate(content.splitlines(), start=1):
        if pattern.search(line):
            findings.append(
                (i, "内联 `{}` / `[]` 作为默认 prop 每次渲染生成新引用，建议提升到模块作用域。")
            )
    return findings


# 规则集合
RULES: list[Rule] = [
    Rule("bundle-barrel-imports", "避免 barrel 文件导入", "critical",
         "从 barrel 文件根入口导入会破坏 tree-shaking，导致整包被打入 bundle。",
         "改用具名子路径导入。", _check_barrel_imports),
    Rule("bundle-dynamic-imports", "大库应动态导入", "critical",
         "体积较大的库静态导入会显著增加首屏 bundle。",
         "使用 next/dynamic 延迟加载，或改用具名子路径导入。", _check_heavy_static_imports),
    Rule("bundle-defer-third-party", "延迟加载第三方分析/埋点", "high",
         "分析/埋点脚本静态导入会阻塞首屏 hydration。",
         "在 hydration 之后动态加载。", _check_defer_third_party),
    Rule("bundle-analyzable-paths", "保持 import 路径可静态分析", "high",
         "动态 import 使用模板字符串会让打包器无法静态分析。",
         "使用静态字符串字面量，或映射到已知路径。", _check_dynamic_template_import),
    Rule("async-parallel", "并行化独立 await", "critical",
         "多个独立的 await 串行执行形成请求瀑布。",
         "用 Promise.all 并行化。", _check_consecutive_awaits),
    Rule("rerender-no-inline-components", "不要在组件内部定义组件", "medium",
         "组件内部定义的组件每次渲染都会生成新类型，导致子树重挂载与重渲染。",
         "将内部组件提升到模块作用域。", _check_inline_component_def),
    Rule("rerender-derived-state-no-effect", "不要在 effect 中派生状态", "medium",
         "在 useEffect 中通过 setState 派生状态会引入额外渲染。",
         "在渲染期间直接推导状态。", _check_derived_state_in_effect),
    Rule("rendering-conditional-render", "条件渲染用三元而非 &&", "low",
         "用 && 条件渲染可能在左侧为 0/空串时渲染出该值。",
         "改用 `cond ? <X/> : null`。", _check_conditional_and),
    Rule("js-combine-iterations", "合并 filter/map 迭代", "low",
         ".filter().map() 链会创建中间数组。",
         "用 .flatMap() 在一次遍历内完成。", _check_filter_map_chain),
    Rule("client-passive-event-listeners", "滚动/触摸事件使用 passive", "medium",
         "scroll/wheel/touchmove 监听未声明 passive 会阻塞主线程。",
         "传入 { passive: true }。", _check_passive_listeners),
    Rule("server-no-shared-module-state", "避免模块级可变状态", "medium",
         "SSR/RSC 下模块级可变状态会跨请求共享，造成数据串扰。",
         "改为请求内局部状态或显式缓存。", _check_module_level_mutable),
    Rule("rerender-memo-with-default-value", "提升内联默认 prop", "low",
         "内联 {} / [] 默认 prop 每次渲染生成新引用，破坏 memo。",
         "将默认值提升到模块作用域。", _check_inline_default_prop),
]


def list_rules() -> list[dict]:
    return [
        {
            "rule_id": r.rule_id,
            "title": r.title,
            "severity": r.severity,
            "description": r.description,
            "suggestion": r.suggestion,
        }
        for r in RULES
    ]


def is_reviewable(file_path: str) -> bool:
    return file_path.endswith(REVIEWED_EXTENSIONS)


def review_file(content: str, file_path: str) -> list[Finding]:
    """对单个文件内容执行全部规则，返回所有发现。"""
    findings: list[Finding] = []
    for rule in RULES:
        try:
            hits = rule.check(content, file_path)
        except Exception:
            # 单条规则异常不应中断整体审查
            continue
        for line, message in hits:
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    file_path=file_path,
                    line=line,
                    severity=rule.severity,
                    message=message or rule.description,
                    suggestion=rule.suggestion,
                )
            )
    findings.sort(key=lambda f: (f.file_path, f.line, SEVERITY_ORDER.get(f.severity, 9)))
    return findings


def review_files(files: dict[str, str]) -> list[Finding]:
    """files: {file_path: content}。返回全部发现。"""
    all_findings: list[Finding] = []
    for path, content in files.items():
        if not is_reviewable(path):
            continue
        all_findings.extend(review_file(content, path))
    return all_findings


def parse_added_new_lines(diff: str) -> set[int]:
    """解析统一 diff，返回新文件中被新增/修改的行号集合（1-based）。"""
    added: set[int] = set()
    new_line = 0
    for line in diff.splitlines():
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,\d+)?\s+@@", line)
            if m:
                new_line = int(m.group(1)) - 1
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            new_line += 1
            added.add(new_line)
        elif line.startswith("-"):
            continue
        else:
            new_line += 1
    return added


def filter_to_added_lines(findings: list[Finding], added_lines: set[int]) -> list[Finding]:
    """只保留位于新增行上的发现，使 MR 审查聚焦于本次改动。"""
    return [f for f in findings if f.line in added_lines]


def format_summary(findings: list[Finding], mr_title: str = "", mr_url: str = "") -> str:
    """将发现汇总为 GitLab MR 评论 markdown。"""
    if not findings:
        return (
            "### ✅ Next.js 代码审查完成\n\n"
            f"未发现需要处理的问题。{('MR: ' + mr_title) if mr_title else ''}\n\n"
            "已基于 Vercel React/Next.js 最佳实践对变更文件做静态检查。"
        )

    by_severity: dict[str, list[Finding]] = {}
    for f in findings:
        by_severity.setdefault(f.severity, []).append(f)

    lines = ["### 🤖 Next.js 代码审查结果", ""]
    if mr_title:
        lines.append(f"**MR**: {mr_title}")
    if mr_url:
        lines.append(f"**链接**: {mr_url}")
    lines.append("")

    counts = ", ".join(
        f"{sev}: {len(by_severity[sev])}"
        for sev in ("critical", "high", "medium", "low")
        if sev in by_severity
    )
    lines.append(f"共发现 **{len(findings)}** 处问题（{counts}）。")
    lines.append("")

    # 按文件分组
    by_file: dict[str, list[Finding]] = {}
    for f in findings:
        by_file.setdefault(f.file_path, []).append(f)

    for path in sorted(by_file):
        lines.append(f"#### `{path}`")
        lines.append("")
        lines.append("| 行 | 级别 | 规则 | 说明 | 建议 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for f in sorted(by_file[path], key=lambda x: (x.line, SEVERITY_ORDER.get(x.severity, 9))):
            lines.append(
                f"| {f.line} | {f.severity_label} | `{f.rule_id}` | "
                f"{f.message} | {f.suggestion} |"
            )
        lines.append("")

    lines.append(
        "---\n*由 Next.js Review MCP 服务基于 Vercel 最佳实践自动生成。"
        "行内评论仅针对本次 MR 的新增行。*"
    )
    return "\n".join(lines)
