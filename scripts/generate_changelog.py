from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

_VERSION = re.compile(r"v?\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?\Z")
_SUBJECT = re.compile(
    r"^(?P<kind>[a-z][a-z0-9-]*)(?:\([^\n)]*\))?(?P<breaking>!)?:\s*(?P<summary>.+)$"
)
_BREAKING_TRAILER = re.compile(r"^BREAKING CHANGE:\s*(.*)$", re.IGNORECASE)
_LOG_FORMAT = "%H%x1f%s%x1f%b%x1e"


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str = ""


def _category(commit: Commit) -> str:
    match = _SUBJECT.fullmatch(commit.subject)
    if match is not None and match.group("breaking"):
        return "Breaking changes"
    if any(_BREAKING_TRAILER.match(line.strip()) for line in commit.body.splitlines()):
        return "Breaking changes"
    if match is None:
        return "Other changes"
    kind = match.group("kind")
    if kind == "feat":
        return "Features"
    if kind == "fix":
        return "Fixes"
    if kind == "perf":
        return "Performance"
    return "Other changes"


def parse_git_log(output: str) -> list[Commit]:
    commits: list[Commit] = []
    for record in output.split("\x1e"):
        if not record.strip():
            continue
        fields = record.strip("\n").split("\x1f", maxsplit=2)
        if len(fields) != 3 or not fields[0] or not fields[1]:
            raise ValueError("git log output did not match the expected record format")
        commits.append(Commit(sha=fields[0], subject=fields[1], body=fields[2]))
    return commits


def render_changelog(version: str, commits: list[Commit]) -> str:
    if not _VERSION.fullmatch(version):
        raise ValueError("version must look like v1.2.3, v1.2.3a1, v1.2.3b1, or v1.2.3rc1")
    if not commits:
        raise ValueError("no non-merge commits were found in the requested range")

    groups: dict[str, list[Commit]] = {}
    for commit in commits:
        groups.setdefault(_category(commit), []).append(commit)

    ordered_categories = ("Breaking changes", "Features", "Fixes", "Performance", "Other changes")
    lines = [f"## {version}", ""]
    for category in ordered_categories:
        if category not in groups:
            continue
        lines.extend((f"### {category}", ""))
        for commit in groups[category]:
            short_sha = commit.sha[:7]
            lines.append(f"- {commit.subject} (`{short_sha}`)")
            for body_line in commit.body.splitlines():
                trailer = _BREAKING_TRAILER.match(body_line.strip())
                if trailer is not None and trailer.group(1):
                    lines.append(f"  - Breaking change: {trailer.group(1).strip()}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate(version: str, from_ref: str, to_ref: str, cwd: str | None = None) -> str:
    if from_ref.startswith("-") or to_ref.startswith("-"):
        raise ValueError("git references must not begin with '-'")
    revision_range = f"{from_ref}..{to_ref}"
    try:
        result = subprocess.run(
            ["git", "log", "--no-merges", f"--format={_LOG_FORMAT}", revision_range],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError as exc:
        raise ValueError("git is required to generate release notes") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or "git log failed"
        raise ValueError(detail) from exc
    return render_changelog(version, parse_git_log(result.stdout))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Draft human-reviewable changelog entries from a Git revision range."
    )
    parser.add_argument("--version", required=True, help="release tag, e.g. v0.1.0rc1")
    parser.add_argument("--from-ref", required=True, help="last release tag or starting commit")
    parser.add_argument("--to-ref", default="HEAD", help="ending tag or commit (default: HEAD)")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        draft = generate(arguments.version, arguments.from_ref, arguments.to_ref)
    except ValueError as exc:
        print(f"changelog generation error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(draft)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
