"""Backup — JSON + markdown export, optional remote SQLite copy."""

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .db import MemoryDB


class BackupManager:
    """Export memory to JSON/markdown for git, optionally copy SQLite to a remote host."""

    def __init__(self, db: MemoryDB, export_dir: str | Path, remote_backup_host: str | None = None):
        self.db = db
        self.export_dir = Path(export_dir)
        self.remote_host = remote_backup_host

    def export_json(self) -> Path:
        """Full JSON export of all cells and scenes."""
        self.export_dir.mkdir(parents=True, exist_ok=True)

        cells = self.db.all_cells()
        scenes = self.db.list_scenes()

        export = {
            "exported_at": datetime.utcnow().isoformat(),
            "stats": self.db.stats(),
            "scenes": scenes,
            "cells": cells,
        }

        path = self.export_dir / "memory-export.json"
        path.write_text(json.dumps(export, indent=2, default=str))
        return path

    def export_markdown(self) -> int:
        """Export scenes as markdown files for human-readable git backup."""
        self.export_dir.mkdir(parents=True, exist_ok=True)

        scenes = self.db.list_scenes()

        # Scene index
        index_lines = [f"# Memory Scenes ({datetime.utcnow().strftime('%Y-%m-%d')})\n"]
        for s in scenes:
            index_lines.append(
                f"- **{s['scene']}** ({s['cell_count']} cells) — {s['summary'][:80]}"
            )
        (self.export_dir / "scenes-index.md").write_text("\n".join(index_lines) + "\n")

        # Individual scene files
        for scene_info in scenes:
            scene_name = scene_info["scene"]
            safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", scene_name)
            _, cells = self.db.get_scene(scene_name)

            lines = [f"# {scene_name}\n"]
            lines.append(f"**Summary:** {scene_info['summary']}\n")
            lines.append(f"**Cells:** {scene_info['cell_count']} | **Updated:** {scene_info['updated_at']}\n")
            lines.append("---\n")

            for cell in cells:
                lines.append(
                    f"- [{cell['cell_type']}] (sal: {cell['salience']:.2f}, accessed: {cell['access_count']}x) "
                    f"{cell['content']}"
                )

            (self.export_dir / f"scene-{safe_name}.md").write_text("\n".join(lines) + "\n")

        return len(scenes)

    def backup_sqlite(self, remote_path: str = "~/backups/memory.db") -> bool:
        """Copy SQLite DB to a remote host via scp. Requires remote_backup_host."""
        if not self.remote_host:
            return False
        try:
            subprocess.run(
                ["ssh", self.remote_host, f"mkdir -p $(dirname {remote_path})"],
                capture_output=True,
                timeout=10,
            )
            result = subprocess.run(
                ["scp", str(self.db.db_path), f"{self.remote_host}:{remote_path}"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        except Exception:
            return False

    def restore_from_json(self, json_path: str | Path) -> int:
        """Import cells from a JSON export file."""
        data = json.loads(Path(json_path).read_text())
        count = 0
        for cell in data.get("cells", []):
            self.db.insert_cell(cell)
            count += 1
        return count
