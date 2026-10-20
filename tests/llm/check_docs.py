"""Repository-local Markdown link/anchor and Python example syntax checks.

Run ``python -m tests.llm.check_docs --output /tmp/docs.json`` from any cwd.
This checks syntax, not execution of examples or the truth of design claims.
"""

import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import unquote, urlsplit


def sections(text):
    prose, snippets, errors = [], [], []
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence is None:
            if match:
                fence = match[1]
                info = match[2].split()
                language = info[0] if info else ""
                start, block = number + 1, []
            else:
                prose.append((number, line))
        elif (match and match[1][0] == fence[0]
              and len(match[1]) >= len(fence) and not match[2].strip()):
            if language in ("python", "py"):
                snippets.append((start, "\n".join(block)))
            fence = None
        else:
            block.append(line)
    if fence is not None:
        errors.append((start - 1, "unclosed code fence"))
    return prose, snippets, errors


def anchors(prose):
    """GitHub-style ATX headings, duplicate suffixes, and explicit HTML anchors."""
    values, counts = set(), Counter()
    for _, line in prose:
        match = re.match(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", line)
        if match:
            title = re.sub(r"<[^>]*>", "", match[1])
            title = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", title)
            slug = "".join(char for char in title.lower()
                           if char in "-_ " or unicodedata.category(char)[0] in "LNM")
            slug = slug.replace(" ", "-")
            suffix = f"-{counts[slug]}" if counts[slug] else ""
            values.add(slug + suffix)
            counts[slug] += 1
        values.update(re.findall(r"""(?:id|name)=["']([^"']+)["']""", line))
    return values


def links(prose):
    definitions = {}
    for number, line in prose:
        match = re.match(r"^\s*\[([^\]]+)\]:\s*(<[^>]+>|\S+)", line)
        if match:
            definitions[match[1].casefold()] = (number, match[2])
    for number, line in prose:
        line = re.sub(r"(`+).*?\1", "", line)
        for match in re.finditer(r"\]\((<[^>]+>|[^)]+)\)", line):
            yield number, match[1]
    # Undefined references are ordinary text in Markdown, e.g. parameters[x][y].
    # Validate defined targets even when referenced with shortcut syntax.
    yield from definitions.values()


def check(root):
    root = root.resolve()
    paths = sorted(path for folder in ("docs", "llm", "hub")
                   for path in (root / folder).rglob("*.md")
                   if "__pycache__" not in path.parts)
    parsed = {path: sections(path.read_text(encoding="utf-8")) for path in paths}
    errors = []
    snippets = relative = fragments = 0
    for path in paths:
        prose, blocks, fences = parsed[path]
        def error(line, message):
            errors.append({"file": path.relative_to(root).as_posix(),
                           "line": line, "message": message})
        for line, message in fences:
            error(line, message)
        for line, source in blocks:
            snippets += 1
            try:
                ast.parse(source)
            except SyntaxError as exc:
                error(line + (exc.lineno or 1) - 1, f"Python syntax: {exc.msg}")
        for line, target in links(prose):
            target = target.strip()
            if not target:
                continue
            target = target[1:target.index(">")] if target.startswith("<") else target.split()[0]
            try:
                parts = urlsplit(target)
            except ValueError as exc:
                error(line, f"invalid URL: {exc}")
                continue
            if parts.scheme or parts.netloc:
                continue
            relative += 1
            destination = ((root / unquote(parts.path).lstrip("/") if parts.path.startswith("/")
                            else path.parent / unquote(parts.path)).resolve() if parts.path else path)
            if not destination.is_relative_to(root):
                error(line, f"link outside repository: {target}")
            elif not destination.exists():
                error(line, f"missing file: {target}")
            elif parts.fragment and destination.suffix == ".md":
                fragments += 1
                if destination not in parsed:
                    parsed[destination] = sections(destination.read_text(encoding="utf-8"))
                if unquote(parts.fragment) not in anchors(parsed[destination][0]):
                    error(line, f"missing anchor: {target}")
    if not paths:
        errors.append({"file": ".", "line": 0, "message": "no documents discovered"})
    return {"documents": len(paths), "python_fences": snippets,
            "relative_links": relative, "anchors_checked": fragments, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = check(args.root)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
