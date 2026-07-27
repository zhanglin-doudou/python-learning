"""reviewer.py 单元测试：规则检查、diff 解析、结果格式化。"""
from __future__ import annotations

from reviewer import (
    Finding,
    RULES,
    filter_to_added_lines,
    format_summary,
    is_reviewable,
    list_rules,
    parse_added_new_lines,
    review_file,
    review_files,
)


# ---------------------------------------------------------------------------
# is_reviewable
# ---------------------------------------------------------------------------

def test_is_reviewable_supported_extensions():
    for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        assert is_reviewable(f"src/file{ext}") is True


def test_is_reviewable_unsupported():
    for path in ("README.md", "style.css", "data.json", "image.png", "Dockerfile"):
        assert is_reviewable(path) is False


# ---------------------------------------------------------------------------
# list_rules
# ---------------------------------------------------------------------------

def test_list_rules_returns_all():
    rules = list_rules()
    assert len(rules) == len(RULES)
    for r in rules:
        assert {"rule_id", "title", "severity", "description", "suggestion"} <= set(r)


def test_list_rules_severities_valid():
    valid = {"critical", "high", "medium", "low", "info"}
    for r in list_rules():
        assert r["severity"] in valid


# ---------------------------------------------------------------------------
# 各规则检查
# ---------------------------------------------------------------------------

def _rule_ids(findings: list[Finding]) -> set[str]:
    return {f.rule_id for f in findings}


def test_barrel_imports_detected():
    code = 'import { Icon } from "react-icons";\n'
    findings = review_file(code, "app/page.tsx")
    assert "bundle-barrel-imports" in _rule_ids(findings)
    assert findings[0].line == 1


def test_barrel_imports_subpath_allowed():
    code = 'import { FaBeer } from "react-icons/fa";\n'
    findings = review_file(code, "app/page.tsx")
    assert "bundle-barrel-imports" not in _rule_ids(findings)


def test_heavy_static_imports_detected():
    code = 'import _ from "lodash";\nimport get from "lodash/get";\n'
    findings = review_file(code, "app/page.tsx")
    ids = _rule_ids(findings)
    # 根包 lodash 命中，子路径 lodash/get 放行
    assert "bundle-dynamic-imports" in ids


def test_defer_third_party_detected():
    code = 'import posthog from "posthog";\n'
    findings = review_file(code, "app/page.tsx")
    assert "bundle-defer-third-party" in _rule_ids(findings)


def test_dynamic_template_import_detected():
    code = 'const mod = await import(`./pages/${name}`);\n'
    findings = review_file(code, "app/page.tsx")
    assert "bundle-analyzable-paths" in _rule_ids(findings)


def test_consecutive_awaits_detected():
    code = (
        "const a = await fetch('/x');\n"
        "const b = await fetch('/y');\n"
        "const c = await fetch('/z');\n"
    )
    findings = review_file(code, "app/page.tsx")
    hits = [f for f in findings if f.rule_id == "async-parallel"]
    assert len(hits) == 1
    assert hits[0].line == 1


def test_single_await_not_flagged():
    code = "const a = await fetch('/x');\n"
    findings = review_file(code, "app/page.tsx")
    assert "async-parallel" not in _rule_ids(findings)


def test_inline_component_def_detected():
    code = (
        "export default function Page() {\n"
        "  const Inner = () => <div/>;\n"
        "  return <Inner/>;\n"
        "}\n"
    )
    findings = review_file(code, "app/page.tsx")
    assert "rerender-no-inline-components" in _rule_ids(findings)


def test_top_level_component_not_flagged():
    code = "function Inner() { return <div/>; }\n"
    findings = review_file(code, "app/page.tsx")
    assert "rerender-no-inline-components" not in _rule_ids(findings)


def test_derived_state_in_effect_detected():
    code = (
        "useEffect(() => {\n"
        "  setCount(count + 1);\n"
        "}, [count]);\n"
    )
    findings = review_file(code, "app/page.tsx")
    hits = [f for f in findings if f.rule_id == "rerender-derived-state-no-effect"]
    assert len(hits) == 1
    assert hits[0].line == 1


def test_conditional_and_detected():
    code = "return <div>{count && <Comp/>}</div>;\n"
    findings = review_file(code, "app/page.tsx")
    assert "rendering-conditional-render" in _rule_ids(findings)


def test_filter_map_chain_detected():
    code = "const r = arr.filter(x => x).map(x => x * 2);\n"
    findings = review_file(code, "app/page.tsx")
    assert "js-combine-iterations" in _rule_ids(findings)


def test_passive_listeners_detected():
    code = "window.addEventListener('scroll', handler);\n"
    findings = review_file(code, "app/page.tsx")
    assert "client-passive-event-listeners" in _rule_ids(findings)


def test_passive_listeners_with_passive_not_flagged():
    code = "window.addEventListener('scroll', handler, { passive: true });\n"
    findings = review_file(code, "app/page.tsx")
    assert "client-passive-event-listeners" not in _rule_ids(findings)


def test_module_level_mutable_detected():
    code = "let counter = 0;\n"
    findings = review_file(code, "app/page.tsx")
    assert "server-no-shared-module-state" in _rule_ids(findings)


def test_inline_default_prop_detected():
    code = "function Comp({ items = [] }) { return null; }\n"
    findings = review_file(code, "app/page.tsx")
    assert "rerender-memo-with-default-value" in _rule_ids(findings)


def test_clean_code_no_findings():
    code = (
        "import get from 'lodash/get';\n"
        "const A = 1;\n"
        "export default function Page() { return <div/>; }\n"
    )
    findings = review_file(code, "app/page.tsx")
    assert findings == []


def test_finding_severity_label():
    f = Finding("r", "f.ts", 1, "critical", "m", "s")
    assert f.severity_label == "CRITICAL"


# ---------------------------------------------------------------------------
# review_files
# ---------------------------------------------------------------------------

def test_review_files_skips_unsupported():
    files = {"a.ts": "let x = 0;\n", "b.md": "# title"}
    findings = review_files(files)
    assert all(f.file_path == "a.ts" for f in findings)


def test_review_files_empty():
    assert review_files({}) == []


# ---------------------------------------------------------------------------
# parse_added_new_lines
# ---------------------------------------------------------------------------

def test_parse_added_new_lines_basic():
    diff = (
        "@@ -1,2 +1,4 @@\n"
        " ctx\n"
        "-old line\n"
        "+new line 1\n"
        "+new line 2\n"
        " ctx2\n"
    )
    added = parse_added_new_lines(diff)
    # @@ +1,4 表示新文件从第 1 行开始，ctx 是第 1 行，新增两行是 2、3
    assert added == {2, 3}


def test_parse_added_new_lines_multiple_hunks():
    diff = (
        "@@ -1,1 +1,2 @@\n"
        "+added top\n"
        " keep\n"
        "@@ -5,1 +6,2 @@\n"
        " keep2\n"
        "+added bottom\n"
    )
    added = parse_added_new_lines(diff)
    assert added == {1, 7}


def test_parse_added_new_lines_empty():
    assert parse_added_new_lines("") == set()
    assert parse_added_new_lines("no diff here") == set()


def test_parse_added_new_lines_ignores_minus():
    diff = "@@ -1,1 +1,1 @@\n-removed\n+added\n"
    added = parse_added_new_lines(diff)
    assert added == {1}


# ---------------------------------------------------------------------------
# filter_to_added_lines
# ---------------------------------------------------------------------------

def test_filter_to_added_lines():
    findings = [
        Finding("r", "a.ts", 1, "low", "m", "s"),
        Finding("r", "a.ts", 2, "low", "m", "s"),
        Finding("r", "a.ts", 3, "low", "m", "s"),
    ]
    result = filter_to_added_lines(findings, {2})
    assert len(result) == 1
    assert result[0].line == 2


def test_filter_to_added_lines_empty():
    findings = [Finding("r", "a.ts", 1, "low", "m", "s")]
    assert filter_to_added_lines(findings, set()) == []


# ---------------------------------------------------------------------------
# format_summary
# ---------------------------------------------------------------------------

def test_format_summary_no_findings():
    out = format_summary([], "My MR", "http://url")
    assert "✅" in out
    assert "My MR" in out
    assert "未发现" in out


def test_format_summary_with_findings():
    findings = [
        Finding("bundle-barrel-imports", "app/page.tsx", 3, "critical", "barrel", "子路径"),
        Finding("async-parallel", "app/page.tsx", 1, "critical", "瀑布", "Promise.all"),
        Finding("js-combine-iterations", "lib/util.ts", 10, "low", "filter map", "flatMap"),
    ]
    out = format_summary(findings, "Title", "http://url")
    assert "🤖" in out
    assert "Title" in out
    assert "http://url" in out
    assert "3" in out  # 共 3 处
    assert "app/page.tsx" in out
    assert "lib/util.ts" in out
    assert "| 行 |" in out  # 表格表头


def test_format_summary_groups_by_file():
    findings = [
        Finding("r1", "b.ts", 1, "low", "m", "s"),
        Finding("r2", "a.ts", 2, "low", "m", "s"),
        Finding("r3", "a.ts", 3, "low", "m", "s"),
    ]
    out = format_summary(findings)
    # a.ts 应排在 b.ts 前
    assert out.index("a.ts") < out.index("b.ts")


# ---------------------------------------------------------------------------
# review_file 异常隔离
# ---------------------------------------------------------------------------

def test_review_file_isolates_rule_exception(monkeypatch):
    """单条规则抛异常不应中断整体审查。"""
    import reviewer

    def bad_check(content, path):
        raise RuntimeError("boom")

    original = list(reviewer.RULES)
    monkeypatch.setattr(
        reviewer, "RULES",
        [reviewer.Rule("bad", "t", "low", "d", "s", bad_check)] + original,
    )
    findings = review_file("let x = 0;\n", "a.ts")
    # bad 规则被跳过，仍能命中 server-no-shared-module-state
    assert "bad" not in _rule_ids(findings)
