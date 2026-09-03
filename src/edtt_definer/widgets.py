from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QFrame,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)


class CollapsibleSection(QWidget):
    """A section whose contents are hidden, rather than merely disabled."""

    toggled = Signal(bool)

    def __init__(
        self,
        title: str,
        *,
        expanded: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.base_title = title
        self.item_count = 0
        self.validation_error_count = 0

        self.header = QToolButton()
        self.header.setObjectName("collapsibleHeader")
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.setAutoRaise(True)
        self.header.toggled.connect(self._set_open)

        self.content = QFrame()
        self.content.setObjectName("collapsibleContent")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(12, 4, 4, 4)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.header)
        layout.addWidget(self.content)

        self._set_open(expanded, emit_signal=False)

    @property
    def expanded(self) -> bool:
        return self.header.isChecked()

    def add_widget(self, widget: QWidget) -> None:
        self.content_layout.addWidget(widget)

    def set_expanded(self, expanded: bool) -> None:
        self.header.setChecked(bool(expanded))

    def set_item_count(self, count: int) -> None:
        self.item_count = max(0, int(count))
        self._update_header()

    def set_validation_error_count(self, count: int) -> None:
        self.validation_error_count = max(0, int(count))
        self._update_header()

    def _set_open(self, expanded: bool, *, emit_signal: bool = True) -> None:
        self.content.setVisible(expanded)
        self.header.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._update_header()
        if emit_signal:
            self.toggled.emit(expanded)

    def _update_header(self) -> None:
        text = self.base_title
        if self.item_count:
            text += f" ({self.item_count})"
        has_hidden_errors = bool(self.validation_error_count and not self.expanded)
        if has_hidden_errors:
            suffix = "error" if self.validation_error_count == 1 else "errors"
            text += f" — {self.validation_error_count} {suffix}"
        self.header.setText(text)
        self.header.setProperty("hasValidationError", has_hidden_errors)
        self.header.style().unpolish(self.header)
        self.header.style().polish(self.header)


class DelayedTooltipFilter(QObject):
    """Shows registered explanations after a deliberate hover delay."""

    shown = Signal(object, str)

    def __init__(self, parent: QObject | None = None, *, delay_ms: int = 1800) -> None:
        super().__init__(parent)
        self.delay_ms = delay_ms
        self._pending: QWidget | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show_pending)

    def register(self, widget: QWidget, text: str) -> None:
        explanation = text.strip()
        if not explanation:
            return
        widget.setProperty("edttHelpText", explanation)
        widget.setAccessibleDescription(explanation)
        widget.setToolTip("")
        widget.installEventFilter(self)

    def register_existing(self, root: QWidget) -> None:
        for widget in [root, *root.findChildren(QWidget)]:
            if widget.toolTip().strip():
                self.register(widget, widget.toolTip())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if isinstance(watched, QWidget) and watched.property("edttHelpText"):
            if event.type() is QEvent.Type.Enter:
                self._pending = watched
                self._timer.start(self.delay_ms)
            elif event.type() is QEvent.Type.ToolTip:
                return True
            elif event.type() in {
                QEvent.Type.Leave,
                QEvent.Type.MouseButtonPress,
                QEvent.Type.Wheel,
                QEvent.Type.KeyPress,
                QEvent.Type.FocusOut,
                QEvent.Type.Hide,
            }:
                if self._pending is watched:
                    self._cancel()
                QToolTip.hideText()
        return super().eventFilter(watched, event)

    def _cancel(self) -> None:
        self._timer.stop()
        self._pending = None

    def _show_pending(self) -> None:
        widget = self._pending
        self._pending = None
        if widget is None or not widget.isVisible() or not widget.isEnabled():
            return
        text = str(widget.property("edttHelpText") or "")
        if text:
            QToolTip.showText(QCursor.pos(), text, widget)
            self.shown.emit(widget, text)
