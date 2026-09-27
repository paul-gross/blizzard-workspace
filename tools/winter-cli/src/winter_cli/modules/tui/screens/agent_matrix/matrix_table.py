from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding, BindingType
from textual.widgets import DataTable


class MatrixTable(DataTable):
    """One Agent matrix table, navigated with hjkl like the dashboard tables.

    `j` past the last row and `k` above the first row hand focus to the next /
    previous non-empty `MatrixTable` on the screen, in compose order; at the
    first or last table the cursor stays put.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("h", "cursor_left", "Left", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("l", "cursor_right", "Right", show=False),
    ]

    def on_mount(self) -> None:
        self.cursor_type = "row"

    def action_cursor_down(self) -> None:
        row, col = self.cursor_coordinate
        if row < self.row_count - 1:
            self.move_cursor(row=row + 1, column=col, animate=False)
            return
        neighbor = self._neighbor(1)
        if neighbor is not None:
            neighbor._land(0)

    def action_cursor_up(self) -> None:
        row, col = self.cursor_coordinate
        if row > 0:
            self.move_cursor(row=row - 1, column=col, animate=False)
            return
        neighbor = self._neighbor(-1)
        if neighbor is not None:
            neighbor._land(neighbor.row_count - 1)

    def _neighbor(self, step: int) -> MatrixTable | None:
        """The nearest non-empty table `step` places away in compose order, or None."""
        tables = list(self.screen.query(MatrixTable))
        i = tables.index(self) + step
        while 0 <= i < len(tables):
            if tables[i].row_count > 0:
                return tables[i]
            i += step
        return None

    def _land(self, row: int) -> None:
        self.focus()
        self.move_cursor(row=row, column=self.cursor_coordinate.column, animate=False)
        self.scroll_visible(animate=False)
