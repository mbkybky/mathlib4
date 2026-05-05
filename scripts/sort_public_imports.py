#! /usr/bin/env python3

"""Sort `public import` blocks in Lean files.

This script scans Lean files and, for each *contiguous* block of `public import` commands,
reorders them by module name (lexicographic order).

Supported forms:
- Single-line commands:
    public import Mathlib.Foo.Bar
- Block form:
    public import
      Mathlib.Foo.Bar
      Mathlib.Baz.Qux

By default, it rewrites files in place and writes files using LF (`\n`) line endings.

Usage examples (run from the repo root):
  python scripts/sort_public_imports.py
  python scripts/sort_public_imports.py --check
  python scripts/sort_public_imports.py Mathlib/RingTheory/Ideal/Height.lean --check
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


_PUBLIC_IMPORT_RE = re.compile(r"^(\s*)public\s+import\b(.*)$")


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _split_line_comment(s: str) -> tuple[str, str]:
    """Split `s` into (code, comment) at the first `--`.

    The returned `comment` includes the leading `--` if present.
    """

    idx = s.find("--")
    if idx == -1:
        return s, ""
    return s[:idx], s[idx:]


def _public_import_inline_key(line_no_nl: str) -> Optional[str]:
    """If this is an inline `public import ...` line, return its sortable key.

    Returns None for non-matching lines and for the block form `public import` with no modules.
    """

    m = _PUBLIC_IMPORT_RE.match(line_no_nl)
    if not m:
        return None

    rest = m.group(2)
    code, _comment = _split_line_comment(rest)
    code = code.strip()
    if not code:
        return None

    # Use the module part as the key. Typically this is a single module path.
    return code


@dataclass(frozen=True)
class _Item:
    key: str
    line: str  # includes original newline (if any)


def _sort_public_imports_in_lines(lines: list[str]) -> tuple[list[str], bool]:
    """Return (new_lines, changed)."""

    out: list[str] = []
    changed = False
    i = 0

    while i < len(lines):
        line_no_nl, _nl = _split_line_ending(lines[i])
        m = _PUBLIC_IMPORT_RE.match(line_no_nl)
        if not m:
            out.append(lines[i])
            i += 1
            continue

        # Distinguish inline vs block form.
        inline_key = _public_import_inline_key(line_no_nl)
        if inline_key is not None:
            # Collect a maximal contiguous run of inline `public import ...` lines.
            items: list[_Item] = []
            start_i = i
            while i < len(lines):
                ln, _ = _split_line_ending(lines[i])
                key = _public_import_inline_key(ln)
                if key is None:
                    break
                items.append(_Item(key=key, line=lines[i]))
                i += 1

            sorted_lines = [it.line for it in sorted(items, key=lambda it: it.key.casefold())]
            original_lines = [it.line for it in items]
            if sorted_lines != original_lines:
                changed = True
            out.extend(sorted_lines)
            assert i > start_i
            continue

        # Block form: `public import` with the module list on following indented lines.
        header = lines[i]
        i += 1
        cont: list[str] = []
        while i < len(lines):
            ln, _ = _split_line_ending(lines[i])
            if ln == "" or not ln[:1].isspace():
                break
            cont.append(lines[i])
            i += 1

        # Try to sort continuation lines; if we see any blank/comment-only line, skip sorting.
        items2: list[_Item] = []
        sortable = True
        for raw in cont:
            ln, _ = _split_line_ending(raw)
            stripped = ln.lstrip(" \t")
            if stripped == "" or stripped.startswith("--") or stripped.startswith("/-"):
                sortable = False
                break
            code, _comment = _split_line_comment(stripped)
            key = code.strip()
            if not key:
                sortable = False
                break
            items2.append(_Item(key=key, line=raw))

        out.append(header)
        if not sortable:
            out.extend(cont)
        else:
            sorted_cont = [it.line for it in sorted(items2, key=lambda it: it.key.casefold())]
            if sorted_cont != cont:
                changed = True
            out.extend(sorted_cont)

    return out, changed


def _iter_lean_files(paths: Iterable[Path]) -> Iterable[Path]:
    for p in paths:
        if p.is_dir():
            yield from p.rglob("*.lean")
        else:
            if p.suffix == ".lean":
                yield p


def _default_paths() -> list[Path]:
    cwd = Path.cwd()
    if (cwd / "Mathlib").is_dir():
        return [cwd / "Mathlib"]

    repo_root = Path(__file__).resolve().parents[1]
    if (repo_root / "Mathlib").is_dir():
        return [repo_root / "Mathlib"]

    return [cwd]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Sort contiguous public import blocks in Lean files")
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files/directories to process (default: ./Mathlib if present)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not modify files; exit non-zero if changes would be made",
    )
    parser.add_argument("--verbose", action="store_true", help="Print every changed file")

    args = parser.parse_args(argv)
    paths = args.paths or _default_paths()

    files = sorted({p.resolve() for p in _iter_lean_files(paths)})
    if not files:
        print("No .lean files found.")
        return 0

    checked = 0
    changed_files: list[Path] = []

    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Fall back to raw bytes; Lean files should be UTF-8, but be conservative.
            text = file_path.read_bytes().decode("utf-8", errors="replace")

        lines = text.splitlines(keepends=True)
        new_lines, changed = _sort_public_imports_in_lines(lines)
        checked += 1

        if changed:
            changed_files.append(file_path)
            if args.verbose:
                print(str(file_path))
            if not args.check:
                file_path.write_text("".join(new_lines), encoding="utf-8", newline="\n")

    if args.check:
        if changed_files:
            print(f"{len(changed_files)} / {checked} files would be changed.")
            return 1
        print(f"OK: {checked} files; no changes needed.")
        return 0

    print(f"Done: changed {len(changed_files)} / {checked} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
