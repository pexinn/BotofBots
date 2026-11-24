"""Bidirectional synchronization between keywords.txt and SQLite storage."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.database import Database


_HEADER_COMMENTS = (
    "# ==========================================",
    "# LISTA DE TERMOS MONITORADOS",
    "# ==========================================",
    "# Adicione ou remova termos conforme desejar (um por linha).",
    "# Linhas iniciadas com '#' sao ignoradas.",
    "# Sintaxe: termo -exclusao <= preco_maximo | -palavra_global",
    "",
)


class KeywordsSyncManager:
    """Keeps keywords.txt hot-reload and SQLite keyword/blacklist tables synchronized."""

    def __init__(
        self,
        db: Database,
        keywords_file: str = "keywords.txt",
        example_file: str = "keywords.example.txt",
    ) -> None:
        self.db = db
        self.keywords_file = keywords_file
        self.example_file = example_file
        self._last_mtime: float = 0.0

    def _read_lines(self, path: str) -> list[str]:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().splitlines()

    def bootstrap(self) -> None:
        """Import existing keywords.txt (or example file) into SQLite when DB has no rules yet."""
        has_kws = bool(self.db.list_keywords(only_enabled=False))
        has_bl = bool(self.db.list_blacklist())

        if os.path.exists(self.keywords_file):
            if not has_kws and not has_bl:
                lines = self._read_lines(self.keywords_file)
                self.db.replace_all_from_lines(lines)
            self._last_mtime = os.path.getmtime(self.keywords_file)
        elif not has_kws and not has_bl and os.path.exists(self.example_file):
            lines = self._read_lines(self.example_file)
            self.db.replace_all_from_lines(lines)

    def sync_from_file_if_changed(self) -> bool:
        """Reload SQLite rules if keywords.txt was edited externally on disk."""
        if not os.path.exists(self.keywords_file):
            return False
        mtime = os.path.getmtime(self.keywords_file)
        if mtime != self._last_mtime:
            lines = self._read_lines(self.keywords_file)
            self.db.replace_all_from_lines(lines)
            self._last_mtime = mtime
            return True
        return False

    def export_to_file(self) -> None:
        """Persist current SQLite keywords and global blacklist back to keywords.txt."""
        parent = os.path.dirname(os.path.abspath(self.keywords_file))
        if parent:
            os.makedirs(parent, exist_ok=True)

        blacklist = self.db.list_blacklist()
        keywords = self.db.list_keywords(only_enabled=True)

        lines: list[str] = list(_HEADER_COMMENTS)
        for word in blacklist:
            lines.append(f"-{word}")
        if blacklist and keywords:
            lines.append("")
        for kw in keywords:
            lines.append(kw["raw_line"])

        content = "\n".join(lines).rstrip() + "\n"
        with open(self.keywords_file, "w", encoding="utf-8") as fh:
            fh.write(content)
        self._last_mtime = os.path.getmtime(self.keywords_file)
