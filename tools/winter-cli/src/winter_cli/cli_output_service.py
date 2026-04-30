from __future__ import annotations

from collections.abc import Iterable, Sequence


class CliOutputService:
    """Renders CLI output for handlers — space-aligned tables, formatted lines, etc."""

    def render_table(self, rows: Iterable[Sequence[str]], gap: int = 2) -> list[str]:
        """Render rows as space-aligned columns, no header, no borders.

        Each column's width is the max of any cell in that column. Cells are padded
        to their column width except the last non-empty cell of a row, so trailing
        empty cells produce no trailing whitespace and rows can have fewer cells
        than the widest row.
        """
        materialized = [list(r) for r in rows]
        if not materialized:
            return []

        n_cols = max(len(r) for r in materialized)
        padded = [r + [""] * (n_cols - len(r)) for r in materialized]
        widths = [max(len(row[i]) for row in padded) for i in range(n_cols)]
        sep = " " * gap

        lines: list[str] = []
        for row in padded:
            last = n_cols
            while last > 0 and row[last - 1] == "":
                last -= 1
            if last == 0:
                lines.append("")
                continue
            cells = [row[i].ljust(widths[i]) for i in range(last - 1)] + [row[last - 1]]
            lines.append(sep.join(cells).rstrip())
        return lines
