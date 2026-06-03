#!/usr/bin/env python3
"""
pytestdoc.py

Generate Markdown documentation from pytest-style tests without importing or running them.

Usage:
    python3 pytestdoc.py ./backend/app/tests -o ./docs

Default output directory:
    ./docs
"""

from __future__ import annotations

import argparse
import ast
import inspect
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


SPEC_RE = re.compile(r"^\s*@?\s*spec[_\s-]?id\s*(?::|=|\s)\s*(.+?)\s*$", re.IGNORECASE)
GWT_RE = re.compile(r"^\s*(?:#{1,6}\s*)?(given|when|then)\b\s*(?:[:：\-]\s*)?(.*)$", re.IGNORECASE)
OTHER_HEADING_RE = re.compile(r"^\s*(?:#{1,6}\s*)?[A-Za-z][A-Za-z0-9 _-]{0,40}\s*[:：]\s*.*$")
BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


@dataclass
class ParsedDocstring:
    summary: str = ""
    description: str = ""
    spec_ids: list[str] = field(default_factory=list)
    given: list[str] = field(default_factory=list)
    when: list[str] = field(default_factory=list)
    then: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def has_gwt(self) -> bool:
        return bool(self.given or self.when or self.then)


@dataclass
class AssertDoc:
    line: int
    expression: str
    message: str = ""


@dataclass
class TestDoc:
    name: str
    qualname: str
    kind: str
    line: int
    doc: ParsedDocstring
    asserts: list[AssertDoc]
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False


@dataclass
class ClassDoc:
    name: str
    qualname: str
    line: int
    doc: ParsedDocstring
    tests: list[TestDoc] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)


@dataclass
class FileDoc:
    source_path: Path
    relative_path: Path
    module_doc: ParsedDocstring
    tests: list[TestDoc] = field(default_factory=list)
    classes: list[ClassDoc] = field(default_factory=list)
    parse_error: Optional[str] = None


@dataclass
class RunStats:
    files_seen: int = 0
    markdown_written: int = 0
    tests_found: int = 0
    parse_errors: int = 0


def is_test_file(path: Path) -> bool:
    """Return True for pytestdoc's discovery pattern: Python files starting with test/Test."""
    return path.suffix == ".py" and path.name.startswith(("test", "Test"))


def is_test_name(name: str) -> bool:
    """Return True for classes/functions/methods starting with test/Test."""
    return name.startswith(("test", "Test"))


def iter_test_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        if is_test_file(root):
            yield root
        return

    for path in sorted(root.rglob("*.py")):
        parts = set(path.parts)
        if "__pycache__" in parts:
            continue
        if is_test_file(path):
            yield path


def clean_source_segment(source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node)
    if segment is None:
        return ""
    return " ".join(segment.strip().split())


def split_spec_ids(value: str) -> list[str]:
    # Spec IDs usually do not contain commas. Preserve spaces inside a single ID unless comma-separated.
    parts = [part.strip() for part in value.split(",")]
    return [part for part in parts if part]


def normalize_block_lines(lines: list[str]) -> list[str]:
    """Trim surrounding blank lines, remove common bullet prefixes, and keep paragraph breaks."""
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    normalized: list[str] = []
    previous_blank = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if normalized and not previous_blank:
                normalized.append("")
            previous_blank = True
            continue
        normalized.append(BULLET_RE.sub("", stripped))
        previous_blank = False
    return normalized


def parse_docstring(docstring: Optional[str]) -> ParsedDocstring:
    if not docstring:
        return ParsedDocstring()

    raw = inspect.cleandoc(docstring)
    lines = raw.splitlines()

    spec_ids: list[str] = []
    gwt: dict[str, list[str]] = {"given": [], "when": [], "then": []}
    description_lines: list[str] = []
    current_gwt: Optional[str] = None

    for line in lines:
        stripped = line.strip()

        spec_match = SPEC_RE.match(stripped)
        if spec_match:
            spec_ids.extend(split_spec_ids(spec_match.group(1)))
            current_gwt = None
            continue

        gwt_match = GWT_RE.match(stripped)
        if gwt_match:
            current_gwt = gwt_match.group(1).lower()
            rest = gwt_match.group(2).strip()
            if rest:
                gwt[current_gwt].append(rest)
            continue

        # Allow normal sections such as "Notes:" after Given/When/Then to return to description.
        if current_gwt and OTHER_HEADING_RE.match(stripped):
            current_gwt = None
            description_lines.append(line.rstrip())
            continue

        if current_gwt:
            gwt[current_gwt].append(line.rstrip())
        else:
            description_lines.append(line.rstrip())

    description_lines = normalize_block_lines(description_lines)
    description = "\n".join(description_lines).strip()

    summary = ""
    for line in description_lines:
        if line.strip():
            summary = line.strip()
            break

    return ParsedDocstring(
        summary=summary,
        description=description,
        spec_ids=spec_ids,
        given=normalize_block_lines(gwt["given"]),
        when=normalize_block_lines(gwt["when"]),
        then=normalize_block_lines(gwt["then"]),
        raw=raw,
    )


class AssertCollector(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self.source = source
        self.asserts: list[AssertDoc] = []

    def visit_Assert(self, node: ast.Assert) -> None:  # noqa: N802 - ast visitor naming convention
        expression = clean_source_segment(self.source, node.test) or ast.dump(node.test)
        message = clean_source_segment(self.source, node.msg) if node.msg else ""
        self.asserts.append(AssertDoc(line=node.lineno, expression=expression, message=message))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        # Do not collect asserts inside nested helper functions by accident.
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        return


def collect_asserts(func_node: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> list[AssertDoc]:
    collector = AssertCollector(source)
    for statement in func_node.body:
        collector.visit(statement)
    return collector.asserts


def decorators_to_strings(node: ast.AST, source: str) -> list[str]:
    decorators = getattr(node, "decorator_list", [])
    values: list[str] = []
    for decorator in decorators:
        segment = clean_source_segment(source, decorator)
        if segment:
            values.append("@" + segment)
    return values


def build_test_doc(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source: str,
    *,
    class_name: Optional[str] = None,
) -> TestDoc:
    qualname = f"{class_name}.{node.name}" if class_name else node.name
    return TestDoc(
        name=node.name,
        qualname=qualname,
        kind="method" if class_name else "function",
        line=node.lineno,
        doc=parse_docstring(ast.get_docstring(node, clean=False)),
        asserts=collect_asserts(node, source),
        decorators=decorators_to_strings(node, source),
        is_async=isinstance(node, ast.AsyncFunctionDef),
    )


def parse_test_file(path: Path, root: Path) -> FileDoc:
    try:
        relative_path = path.resolve().relative_to(root.resolve()) if root.is_dir() else Path(path.name)
    except ValueError:
        relative_path = Path(path.name)

    file_doc = FileDoc(
        source_path=path,
        relative_path=relative_path,
        module_doc=ParsedDocstring(),
    )

    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        source = path.read_text(encoding="utf-8-sig")

    try:
        tree = ast.parse(source, filename=str(path), type_comments=True)
    except SyntaxError as exc:
        location = f"line {exc.lineno}, column {exc.offset}" if exc.lineno else "unknown location"
        file_doc.parse_error = f"SyntaxError at {location}: {exc.msg}"
        return file_doc

    file_doc.module_doc = parse_docstring(ast.get_docstring(tree, clean=False))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_test_name(node.name):
            file_doc.tests.append(build_test_doc(node, source))
            continue

        if isinstance(node, ast.ClassDef) and is_test_name(node.name):
            class_doc = ClassDoc(
                name=node.name,
                qualname=node.name,
                line=node.lineno,
                doc=parse_docstring(ast.get_docstring(node, clean=False)),
                decorators=decorators_to_strings(node, source),
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_test_name(child.name):
                    class_doc.tests.append(build_test_doc(child, source, class_name=node.name))
            file_doc.classes.append(class_doc)

    return file_doc


def md_escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def inline_code(value: str) -> str:
    if "`" not in value:
        return f"`{value}`"
    return "`` " + value + " ``"


def format_list(lines: list[str]) -> list[str]:
    if not lines:
        return []
    rendered: list[str] = []
    for line in lines:
        if line.strip():
            rendered.append(f"- {line.strip()}")
        else:
            rendered.append("")
    return rendered


def format_doc_sections(doc: ParsedDocstring) -> list[str]:
    lines: list[str] = []

    if doc.spec_ids:
        lines.append(f"**spec_id:** {', '.join(inline_code(spec_id) for spec_id in doc.spec_ids)}")
        lines.append("")

    if doc.description:
        lines.append("**Docstring**")
        lines.append("")
        lines.append(doc.description)
        lines.append("")

    if doc.given:
        lines.append("**Given**")
        lines.append("")
        lines.extend(format_list(doc.given))
        lines.append("")

    if doc.when:
        lines.append("**When**")
        lines.append("")
        lines.extend(format_list(doc.when))
        lines.append("")

    if doc.then:
        lines.append("**Then**")
        lines.append("")
        lines.extend(format_list(doc.then))
        lines.append("")

    return lines


def format_asserts(asserts: list[AssertDoc]) -> list[str]:
    lines: list[str] = []
    if not asserts:
        return lines

    lines.append("**Asserts**")
    lines.append("")
    for assertion in asserts:
        line = f"- L{assertion.line}: {inline_code(assertion.expression)}"
        if assertion.message:
            line += f" / message: {inline_code(assertion.message)}"
        lines.append(line)
    lines.append("")
    return lines


def all_tests(file_doc: FileDoc) -> list[TestDoc]:
    tests = list(file_doc.tests)
    for class_doc in file_doc.classes:
        tests.extend(class_doc.tests)
    return tests


def test_summary_text(test: TestDoc) -> str:
    return test.doc.summary or ""


def gwt_cell(lines: list[str]) -> str:
    if not lines:
        return ""
    return "<br>".join(line for line in lines if line.strip())


def render_summary_table(tests: list[TestDoc]) -> list[str]:
    lines: list[str] = []
    if not tests:
        return ["_No test functions or methods found in this test file._", ""]

    lines.append("| Test | spec_id | Summary | Given | When | Then | Asserts |")
    lines.append("| --- | --- | --- | --- | --- | --- | ---: |")
    for test in tests:
        spec_ids = ", ".join(test.doc.spec_ids)
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape_table(f"`{test.qualname}`"),
                    md_escape_table(spec_ids),
                    md_escape_table(test_summary_text(test)),
                    md_escape_table(gwt_cell(test.doc.given)),
                    md_escape_table(gwt_cell(test.doc.when)),
                    md_escape_table(gwt_cell(test.doc.then)),
                    str(len(test.asserts)),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def render_test_block(test: TestDoc, level: int = 2) -> list[str]:
    marker = "#" * level
    lines: list[str] = [f"{marker} {inline_code(test.qualname)}", ""]
    kind = f"async {test.kind}" if test.is_async else test.kind
    lines.append(f"- Kind: `{kind}`")
    lines.append(f"- Line: `{test.line}`")
    if test.decorators:
        lines.append("- Decorators: " + ", ".join(inline_code(d) for d in test.decorators))
    lines.append("")
    doc_lines = format_doc_sections(test.doc)
    if doc_lines:
        lines.extend(doc_lines)
    else:
        lines.append("_No docstring metadata found._")
        lines.append("")
    lines.extend(format_asserts(test.asserts))
    if not test.asserts:
        lines.append("_No assert statements found._")
        lines.append("")
    return lines


def render_markdown(file_doc: FileDoc) -> str:
    lines: list[str] = []
    title = file_doc.relative_path.as_posix()
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- Source: `{file_doc.relative_path.as_posix()}`")
    lines.append("- Generated by: `pytestdoc.py`")
    lines.append("")

    if file_doc.parse_error:
        lines.append("## Parse error")
        lines.append("")
        lines.append(file_doc.parse_error)
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    if file_doc.module_doc.description or file_doc.module_doc.spec_ids or file_doc.module_doc.has_gwt:
        lines.append("## Module docstring")
        lines.append("")
        lines.extend(format_doc_sections(file_doc.module_doc))

    tests = all_tests(file_doc)
    lines.append("## Summary")
    lines.append("")
    lines.extend(render_summary_table(tests))

    if file_doc.tests:
        lines.append("## Module-level tests")
        lines.append("")
        for test in file_doc.tests:
            lines.extend(render_test_block(test, level=3))

    for class_doc in file_doc.classes:
        lines.append(f"## Class {inline_code(class_doc.name)}")
        lines.append("")
        lines.append(f"- Line: `{class_doc.line}`")
        if class_doc.decorators:
            lines.append("- Decorators: " + ", ".join(inline_code(d) for d in class_doc.decorators))
        lines.append("")

        class_doc_lines = format_doc_sections(class_doc.doc)
        if class_doc_lines:
            lines.extend(class_doc_lines)

        if not class_doc.tests:
            lines.append("_No test methods found in this class._")
            lines.append("")
        else:
            for test in class_doc.tests:
                lines.extend(render_test_block(test, level=3))

    return "\n".join(lines).rstrip() + "\n"


def output_path_for(file_doc: FileDoc, output_root: Path) -> Path:
    return output_root / file_doc.relative_path.with_suffix(".md")


def write_markdown(file_doc: FileDoc, output_root: Path) -> Path:
    out_path = output_path_for(file_doc, output_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(file_doc), encoding="utf-8")
    return out_path


def build_docs(input_path: Path, output_root: Path, quiet: bool = False) -> RunStats:
    stats = RunStats()
    input_path = input_path.resolve()
    output_root = output_root.resolve()

    for test_file in iter_test_files(input_path):
        stats.files_seen += 1
        file_doc = parse_test_file(test_file, input_path)
        write_markdown(file_doc, output_root)
        stats.markdown_written += 1
        tests_count = len(all_tests(file_doc))
        stats.tests_found += tests_count
        if file_doc.parse_error:
            stats.parse_errors += 1
            if not quiet:
                print(f"WARN: {test_file}: {file_doc.parse_error}", file=sys.stderr)

    return stats


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Markdown documentation from pytest-style test docstrings and assert statements."
    )
    parser.add_argument(
        "input",
        help="Test file or test directory to scan recursively. Files must start with test/Test and end with .py.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="./docs",
        help="Output directory. Default: ./docs",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress non-error progress output.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input)
    output_root = Path(args.output)

    if not input_path.exists():
        print(f"ERROR: input path does not exist: {input_path}", file=sys.stderr)
        return 2

    stats = build_docs(input_path, output_root, quiet=args.quiet)

    if not args.quiet:
        print(
            "Generated "
            f"{stats.markdown_written} markdown file(s) "
            f"from {stats.files_seen} test file(s); "
            f"found {stats.tests_found} test(s)."
        )
        if stats.parse_errors:
            print(f"Completed with {stats.parse_errors} parse error(s).", file=sys.stderr)

    return 1 if stats.parse_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
