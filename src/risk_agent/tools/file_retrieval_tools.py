"""Read-only file retrieval tools constrained to a task workspace."""

from __future__ import annotations

from risk_agent.benchmark.environment import TaskEnvironment


class FileRetrievalTools:
    def __init__(self, environment: TaskEnvironment) -> None:
        self.environment = environment

    def list_files(self, directory: str) -> list[str]:
        root = self.environment.resolve_path(directory)
        if not root.is_dir():
            raise NotADirectoryError(directory)
        return sorted(
            path.relative_to(self.environment.workspace).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        )

    def search_files(self, directory: str, query: str) -> list[dict[str, object]]:
        return self.search_paths(self.list_files(directory), query)

    def search_paths(
        self, paths: list[str], query: str
    ) -> list[dict[str, object]]:
        if not query:
            raise ValueError("query must not be empty")
        matches: list[dict[str, object]] = []
        folded_query = query.casefold()
        for relative in paths:
            if not isinstance(relative, str):
                raise ValueError("paths must contain workspace-relative strings")
            path = self.environment.resolve_path(relative)
            if not path.is_file():
                raise FileNotFoundError(relative)
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(lines, start=1):
                if folded_query in line.casefold():
                    matches.append(
                        {"path": relative, "line_number": line_number, "text": line}
                    )
        return matches

    def read_file(self, path: str, max_characters: int = 10_000) -> str:
        if not 1 <= max_characters <= 100_000:
            raise ValueError("max_characters must be between 1 and 100000")
        source = self.environment.resolve_path(path)
        if not source.is_file():
            raise FileNotFoundError(path)
        content = source.read_text(encoding="utf-8")
        if len(content) > max_characters:
            raise ValueError("File exceeds max_characters; explicit truncation is forbidden")
        return content
