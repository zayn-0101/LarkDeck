#!/usr/bin/env python3
"""文档守卫：机械检查版本、链接与用户文档边界。

只做**能机械判定**的四类检查（风格见 docs/STYLE.md §4）：
  1. `plugin.yaml` 版本 = README 徽章版本 = CHANGELOG 顶部版本；
  2. README / 文档地图 / 用户手册里的相对链接全部可达；
  3. 用户文档不得出现 `docs/internal/` 链接（内部归档不是用户文档）；
  4. README 行数上限 260（超过说明该拆到 docs/guide/）。

跑法：`python3 tests/check_docs.py`（EXIT 0 = OK，非 0 = 列出每一条问题）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent

README_LINE_LIMIT = 260

# 用户文档：这些文件里出现 docs/internal/ 直接算错。
USER_DOCS = [
    _REPO / "README.md",
    _REPO / "README.en.md",
    *sorted((_REPO / "docs" / "guide").glob("*.md")),
]

# 随仓库发布、必须干净（不链接本地归档）的文档。
PUBLISHED_DOCS = [
    _REPO / "README.md",
    _REPO / "README.en.md",
    _REPO / "CONTRIBUTING.md",
    _REPO / "SECURITY.md",
    _REPO / "CODE_OF_CONDUCT.md",
    _REPO / "CHANGELOG.md",
    _REPO / "AGENTS.md",
    *sorted((_REPO / "docs").glob("*.md")),
    *sorted((_REPO / "docs" / "guide").glob("*.md")),
    *sorted((_REPO / "docs" / "development").glob("*.md")),
    *sorted((_REPO / "docs" / "releases").glob("*.md")),
]

# 参与「相对链接可达」检查的文档（存在才查，缺文件本身另算一项错误）。
LINK_CHECKED = [
    _REPO / "README.md",
    _REPO / "README.en.md",
    _REPO / "CONTRIBUTING.md",
    _REPO / "CHANGELOG.md",
    _REPO / "docs" / "README.md",
    _REPO / "docs" / "STYLE.md",
    *sorted((_REPO / "docs" / "guide").glob("*.md")),
    *sorted((_REPO / "docs" / "development").glob("*.md")),
    *sorted((_REPO / "docs" / "releases").glob("*.md")),
]

_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def _version_consistency() -> list[str]:
    errors: list[str] = []
    yaml_text = (_REPO / "plugin.yaml").read_text(encoding="utf-8")
    m = re.search(r"^version:\s*(\S+)\s*$", yaml_text, re.M)
    if not m:
        return ["plugin.yaml 里找不到 version:"]
    version = m.group(1)

    readme = (_REPO / "README.md").read_text(encoding="utf-8")
    badges = re.findall(r"badge/version-([0-9][^-\s]*)-", readme)
    if not badges:
        errors.append("README.md 里找不到 version 徽章")
    elif set(badges) != {version}:
        errors.append(f"版本不一致：plugin.yaml={version}，README 徽章={sorted(set(badges))}")

    changelog = (_REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    top = re.search(r"^##\s*\[?([0-9][^\s\]]*)", changelog, re.M)
    if not top:
        errors.append("CHANGELOG.md 里找不到顶部版本小节")
    elif top.group(1) != version:
        errors.append(f"版本不一致：plugin.yaml={version}，CHANGELOG 顶部={top.group(1)}")
    return errors


def _relative_links() -> list[str]:
    errors: list[str] = []
    for doc in LINK_CHECKED:
        if not doc.is_file():
            errors.append(f"文档缺失：{doc.relative_to(_REPO)}")
            continue
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for raw in _LINK_RE.findall(line):
                target = raw.split()[0].strip("<>")
                if not target or target.startswith("#") or _SCHEME_RE.match(target):
                    continue
                path_part = target.split("#", 1)[0].split("?", 1)[0]
                if not path_part:
                    continue
                resolved = (doc.parent / path_part).resolve()
                if not resolved.exists():
                    errors.append(
                        f"{doc.relative_to(_REPO)}:{lineno} 链接不可达：{target}")
                elif resolved.is_dir():
                    continue
    return errors


def _internal_leaks() -> list[str]:
    """`docs/internal/` 只在维护者本机，任何随仓库发布的文档都不许**链接**它。

    为什么不能只靠「链接可达」那条：在维护者机器上文件是存在的，可达性检查会放行；
    但公开 clone 里它不存在 —— 这类链接一发布就是 404。
    """
    errors: list[str] = []
    internal_link = re.compile(r"\]\((?:<)?(?:\.\./)*(?:docs/)?internal/")
    for doc in USER_DOCS:
        if not doc.is_file():
            continue
        text = doc.read_text(encoding="utf-8")
        for pattern in ("docs/internal/", "](internal/", "../docs/internal"):
            if pattern in text:
                errors.append(f"{doc.relative_to(_REPO)} 出现内部归档链接：{pattern}")
    for doc in PUBLISHED_DOCS:
        if not doc.is_file():
            continue
        text = doc.read_text(encoding="utf-8")
        if internal_link.search(text):
            errors.append(f"{doc.relative_to(_REPO)} 链接了 docs/internal/（内部归档不随仓库发布）")
    return errors


def _readme_length() -> list[str]:
    errors: list[str] = []
    for name in ("README.md", "README.en.md"):
        path = _REPO / name
        if not path.is_file():
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > README_LINE_LIMIT:
            errors.append(f"{name} {lines} 行，超过上限 {README_LINE_LIMIT}（拆到 docs/guide/）")
    return errors


def _no_placeholders() -> list[str]:
    errors: list[str] = []
    for name in ("SECURITY.md", "CODE_OF_CONDUCT.md"):
        path = _REPO / name
        if path.is_file() and "example.com" in path.read_text(encoding="utf-8"):
            errors.append(f"{name} 仍有 example.com 占位邮箱")
    return errors


def _no_stale_url() -> list[str]:
    errors: list[str] = []
    for doc in USER_DOCS + [_REPO / "CONTRIBUTING.md"]:
        if not doc.is_file():
            continue
        if "zayn-0101/larkdeck" in doc.read_text(encoding="utf-8"):
            errors.append(f"{doc.relative_to(_REPO)} 仍有旧仓库地址 zayn-0101/larkdeck")
    return errors


def main() -> int:
    errors: list[str] = []
    errors += _version_consistency()
    errors += _relative_links()
    errors += _internal_leaks()
    errors += _readme_length()
    errors += _no_placeholders()
    errors += _no_stale_url()
    if errors:
        print("DOCS FAIL:")
        for item in errors:
            print(f"  - {item}")
        return 1
    print("DOCS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
