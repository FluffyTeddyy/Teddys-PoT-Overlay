"""Place quest locations on the full map.

    python -m tools.quest_editor

Pick a map, add a quest (its name should match the text shown in the game's
quest list), then left-click the map to add points for it. Right-click a point
to remove it. Mouse wheel zooms, drag pans. Ctrl+S saves.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QComboBox, QGraphicsEllipseItem, QGraphicsItem, QGraphicsScene,
    QGraphicsView, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from potoverlay import config as C
from potoverlay.quests import Quest, QuestDB

PALETTE = ["#ffd23f", "#3fd2ff", "#ff6b6b", "#7cff6b", "#c77dff", "#ff9f40", "#ffffff", "#ff5fd2"]


class MapView(QGraphicsView):
    clicked = Signal(QPointF)
    right_clicked = Signal(QPointF)

    def __init__(self, scene):
        super().__init__(scene)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self._press = None

    def wheelEvent(self, e):
        f = 1.2 ** (e.angleDelta().y() / 120)
        self.scale(f, f)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.right_clicked.emit(self.mapToScene(e.position().toPoint()))
            return
        self._press = e.position()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if e.button() == Qt.LeftButton and self._press is not None:
            if (e.position() - self._press).manhattanLength() < 5:
                self.clicked.emit(self.mapToScene(e.position().toPoint()))
        self._press = None


class Editor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PoT Quest Editor")
        self.resize(1400, 900)
        self.db: QuestDB | None = None
        self.dirty = False
        self.items: list[QGraphicsEllipseItem] = []

        self.scene = QGraphicsScene()
        self.view = MapView(self.scene)
        self.view.clicked.connect(self.add_point)
        self.view.right_clicked.connect(self.remove_point)

        self.map_box = QComboBox()
        self.map_box.addItems(C.list_maps())
        self.map_box.currentTextChanged.connect(self.load_map)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(lambda _: self.redraw())
        self.info = QLabel()
        self.info.setWordWrap(True)

        buttons = [
            ("New quest", self.new_quest), ("Rename", self.rename_quest), ("Aliases", self.edit_aliases),
            ("Color", self.pick_color), ("Delete quest", self.delete_quest), ("Save (Ctrl+S)", self.save),
        ]
        side = QVBoxLayout()
        side.addWidget(QLabel("Map"))
        side.addWidget(self.map_box)
        side.addWidget(QLabel("Quests"))
        side.addWidget(self.list, 1)
        for text, fn in buttons:
            b = QPushButton(text)
            b.clicked.connect(fn)
            side.addWidget(b)
        side.addWidget(self.info)
        panel = QWidget()
        panel.setLayout(side)

        split = QSplitter()
        split.addWidget(panel)
        split.addWidget(self.view)
        split.setSizes([280, 1120])
        self.setCentralWidget(split)
        QShortcut(QKeySequence.Save, self, self.save)

        if self.map_box.count():
            self.load_map(self.map_box.currentText())
        else:
            QMessageBox.information(self, "Quest editor", f"No map images found in\n{C.MAPS_DIR}")

    # ---------- data ----------
    def current(self) -> Quest | None:
        row = self.list.currentRow()
        return self.db.quests[row] if self.db and 0 <= row < len(self.db.quests) else None

    def load_map(self, name: str) -> None:
        if not name or not self.confirm_discard():
            return
        path = C.map_image_path(name)
        self.scene.clear()
        self.items.clear()
        pm = QPixmap(str(path))
        self.scene.addPixmap(pm).setZValue(-1)
        self.scene.setSceneRect(pm.rect())
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        self.db = QuestDB.load(C.quest_file(name), name)
        self.dirty = False
        self.refresh_list()

    def refresh_list(self, select: int | None = None) -> None:
        row = self.list.currentRow() if select is None else select
        self.list.blockSignals(True)
        self.list.clear()
        for q in self.db.quests:
            where = f" @ {q.poi}" if q.poi else ""
            it = QListWidgetItem(f"{q.name}{where}  ({len(q.points)})")
            if q.objectives:
                it.setToolTip("\n".join(q.objectives))
            it.setForeground(QBrush(QColor(q.color or PALETTE[0]).darker(130)))
            self.list.addItem(it)
        self.list.blockSignals(False)
        if self.db.quests:
            self.list.setCurrentRow(max(0, min(row, len(self.db.quests) - 1)))
        self.redraw()

    def redraw(self) -> None:
        for it in self.items:
            self.scene.removeItem(it)
        self.items.clear()
        cur = self.current()
        for q in self.db.quests if self.db else []:
            sel = q is cur
            if not sel and cur is not None and len(self.db.quests) > 60:
                continue  # big imported catalogs: only show the selected quest
            col = QColor(q.color or PALETTE[0])
            if not sel:
                col.setAlpha(110)
            r = 7 if sel else 5
            for x, y in q.targets if sel else []:
                ring = QGraphicsEllipseItem(-12, -12, 24, 24)
                ring.setPos(x, y)
                ring.setPen(QPen(QColor(q.color or PALETTE[0]), 3))
                ring.setFlag(QGraphicsItem.ItemIgnoresTransformations)
                ring.setZValue(3)
                self.scene.addItem(ring)
                self.items.append(ring)
            for x, y in q.points:
                it = QGraphicsEllipseItem(-r, -r, 2 * r, 2 * r)
                it.setPos(x, y)
                it.setBrush(col)
                it.setPen(QPen(QColor(0, 0, 0), 2 if sel else 1))
                it.setFlag(QGraphicsItem.ItemIgnoresTransformations)
                it.setZValue(2 if sel else 1)
                self.scene.addItem(it)
                self.items.append(it)
        name = cur.name if cur else "none"
        aliases = ", ".join(cur.aliases) if cur and cur.aliases else "-"
        extra = ""
        if cur and (cur.poi or cur.objectives):
            extra = f"\nPOI: {cur.poi or '-'}\nObjectives: {'; '.join(cur.objectives) or '-'}"
        if self.db and self.db.attribution:
            extra += f"\n\n{self.db.attribution}"
        self.info.setText(f"Selected: {name}\nAliases: {aliases}{extra}\n\nLeft-click map: add point\n"
                          f"Right-click point: remove\nWheel: zoom, drag: pan"
                          + ("\n\n* unsaved changes" if self.dirty else ""))

    def mark(self) -> None:
        self.dirty = True
        self.refresh_list()

    # ---------- actions ----------
    def add_point(self, pos: QPointF) -> None:
        q = self.current()
        if q is None:
            self.info.setText("Create or select a quest first.")
            return
        if not self.scene.sceneRect().contains(pos):
            return
        q.points.append((pos.x(), pos.y()))
        self.mark()

    def remove_point(self, pos: QPointF) -> None:
        q = self.current()
        if q is None or not q.points:
            return
        tol = 12 / max(self.view.transform().m11(), 1e-6)  # 12 screen pixels in map pixels
        d = [((x - pos.x()) ** 2 + (y - pos.y()) ** 2, i) for i, (x, y) in enumerate(q.points)]
        dist, i = min(d)
        if dist <= tol * tol:
            q.points.pop(i)
            self.mark()

    def new_quest(self) -> None:
        if self.db is None:
            return
        name, ok = QInputDialog.getText(self, "New quest", "Quest name (as shown in the game's quest list):")
        if ok and name.strip():
            color = PALETTE[len(self.db.quests) % len(PALETTE)]
            self.db.quests.append(Quest(name.strip(), [], [], color))
            self.dirty = True
            self.refresh_list(select=len(self.db.quests) - 1)

    def rename_quest(self) -> None:
        q = self.current()
        if q:
            name, ok = QInputDialog.getText(self, "Rename", "Quest name:", text=q.name)
            if ok and name.strip():
                q.name = name.strip()
                self.mark()

    def edit_aliases(self) -> None:
        q = self.current()
        if q:
            text, ok = QInputDialog.getText(
                self, "Aliases", "Other text that identifies this quest, comma separated:", text=", ".join(q.aliases))
            if ok:
                q.aliases = [a.strip() for a in text.split(",") if a.strip()]
                self.mark()

    def pick_color(self) -> None:
        q = self.current()
        if q:
            c = QColorDialog.getColor(QColor(q.color or PALETTE[0]), self)
            if c.isValid():
                q.color = c.name()
                self.mark()

    def delete_quest(self) -> None:
        q = self.current()
        if q and QMessageBox.question(self, "Delete", f"Delete quest '{q.name}'?") == QMessageBox.Yes:
            self.db.quests.remove(q)
            self.mark()

    def save(self) -> None:
        if self.db is None:
            return
        try:
            self.db.save(C.quest_file(self.db.map_name))
        except OSError as e:
            QMessageBox.warning(self, "Save failed", str(e))
            return
        self.dirty = False
        self.redraw()
        self.statusBar().showMessage(f"Saved {C.quest_file(self.db.map_name).name}", 3000)

    def confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            self.save()
            return not self.dirty
        return r == QMessageBox.Discard

    def closeEvent(self, e):
        if self.confirm_discard():
            e.accept()
        else:
            e.ignore()


def main() -> int:
    app = QApplication(sys.argv)
    w = Editor()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
