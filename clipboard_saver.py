"""
Clipboard Saver v10.1
- 크기: 기존 대비 70% 축소된 미니멀 위젯 모드
- 시작 프로그램 등록: 전원 아이콘 클릭 시 윈도우 레지스트리에 자동 등록/해제
- 디자인: 다크 그레이 배경 + 딥 티알(#0d9488) 포인트 (활성화 시 색상 변경)
- 안정성: 메모리 크래시 방지(타이머 기반 패스스루)
요구사항: pip install PyQt6
"""

import sys, os, datetime, ctypes, winreg, hashlib
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QHBoxLayout, QFrame,
    QSizePolicy, QLineEdit
)
from PyQt6.QtCore  import Qt, QTimer, QMimeData, QUrl, QPoint, QSize, pyqtSignal, QEvent
from PyQt6.QtGui   import (
    QPixmap, QImage, QColor, QDrag, QIcon, QPalette, QCursor,
    QPainter, QBrush, QPen, QFont
)

# ── 윈도우 API 상수 (클릭 통과 관련) ──────────────────────────────────────────
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000

# ── 색상 설정 ─────────────────────────────────────────────────────────────
BG     = "#2b2b2b"   # 메인 배경
BG2    = "#333333"   # 미리보기 배경
BG3    = "#3d3d3d"   # 버튼/입력 배경
GRAY   = "#888888"   # 보조 텍스트 (비활성 아이콘)
LINE   = "#444444"   # 구분선
TITLE  = "#1e1e1e"   # 타이틀바 배경 (다크 그레이)
ACCENT = "#0d9488"   # 포인트 색상: 딥 티알 (Deep Teal)

HISTORY_MAX = 20

SAVE_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "ClipboardSaver")
os.makedirs(SAVE_DIR, exist_ok=True)


# ── 윈도우 시작 프로그램 등록/해제 (Registry) ──────────────────────────────
def is_autostart_enabled():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, "ClipboardSaver")
        winreg.CloseKey(key)
        return True
    except OSError:
        return False

def set_autostart(enable):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
        if enable:
            cmd = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(key, "ClipboardSaver", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "ClipboardSaver")
            except OSError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print("레지스트리 접근 오류:", e)


# ── 타이틀바 색상 변경 ────────────────────────────────────────────────────────
def set_titlebar_color(hwnd, hex_color):
    try:
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        c = r | (g << 8) | (b << 16)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 35, ctypes.byref(ctypes.c_int(c)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass


# ── 앱 아이콘 ──────────────────────────────────────────────────────────────
def make_icon() -> QIcon:
    size = 64
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor(BG3)))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, 56, 56)
    p.setPen(QPen(QColor(ACCENT), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(16, 20, 32, 36, 3, 3)
    p.drawRoundedRect(24, 12, 16, 12, 2, 2)
    p.drawLine(22, 32, 42, 32)
    p.drawLine(22, 40, 42, 40)
    p.end()
    return QIcon(px)


# ── 드래그 가능한 미리보기 ────────────────────────────────────────────────────
class DraggablePreview(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.filepath = None
        self._drag_start = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))

    def update_preview_image(self):
        if self.filepath and os.path.exists(self.filepath):
            px = QPixmap(self.filepath)
            if not px.isNull():
                self.setPixmap(px.scaled(self.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_preview_image()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton): return
        if self._drag_start is None or self.filepath is None: return

        if (event.pos() - self._drag_start).manhattanLength() < 10: return
        if not os.path.exists(self.filepath): return

        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self.filepath)])
        drag.setMimeData(mime)

        px = QPixmap(self.filepath)
        if not px.isNull():
            thumb = px.scaled(100, 70, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            drag.setPixmap(thumb)
            drag.setHotSpot(QPoint(thumb.width() // 2, thumb.height() // 2))

        drag.exec(Qt.DropAction.CopyAction)
        self._drag_start = None


# ── 히스토리 아이템 (컴팩트 모드) ─────────────────────────────────────────────
class HistoryItem(QWidget):
    clicked = pyqtSignal(str, str)
    deleted = pyqtSignal(object)

    def __init__(self, pixmap: QPixmap, filepath: str, filename: str, time_str: str):
        super().__init__()
        self.filepath = filepath
        self.filename = filename
        self.setFixedWidth(112)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setStyleSheet(f"background:{BG3}; border-radius:3px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        thumb_wrap = QWidget()
        thumb_wrap.setStyleSheet(f"background:{BG3};")
        tw_l = QHBoxLayout(thumb_wrap)
        tw_l.setContentsMargins(0, 0, 0, 0)
        tw_l.setSpacing(2)

        thumb_label = QLabel()
        thumb = pixmap.scaled(98, 65, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        thumb_label.setPixmap(thumb)
        thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb_label.setStyleSheet(f"background:{BG3};")
        thumb_label.mousePressEvent = lambda e: self.clicked.emit(self.filepath, self.filename)

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(14, 14)
        del_btn.setStyleSheet(f"""
            QPushButton {{ background:{LINE}; color:{GRAY}; border:none;
                           font-size:7px; border-radius:7px; }}
            QPushButton:hover {{ background:#cc4444; color:white; }}
        """)
        del_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        del_btn.clicked.connect(lambda: self.deleted.emit(self))

        tw_l.addWidget(thumb_label)
        tw_l.addWidget(del_btn, alignment=Qt.AlignmentFlag.AlignTop)

        time_label = QLabel(time_str)
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        time_label.setStyleSheet(f"color:{GRAY}; font-size:8px; background:{BG3};")
        time_label.mousePressEvent = lambda e: self.clicked.emit(self.filepath, self.filename)

        layout.addWidget(thumb_wrap)
        layout.addWidget(time_label)


# ── 커스텀 타이틀바 (자동시작 버튼 & 컴팩트) ────────────────────────────────────
class TitleBar(QWidget):
    def __init__(self, win):
        super().__init__(win)
        self._win = win  # QWidget.parent() 메서드와 충돌하지 않도록 _win 사용
        self.setFixedHeight(28)
        self.setStyleSheet(f"background:{TITLE};")
        self._drag_pos = None

        l = QHBoxLayout(self)
        l.setContentsMargins(8, 0, 6, 0)
        l.setSpacing(4)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(make_icon().pixmap(14, 14))
        l.addWidget(icon_lbl)
        l.addStretch()

        # ⚡ 윈도우 시작 시 자동 실행 버튼 (전원 아이콘)
        self.power_btn = QPushButton()
        self.power_btn.setCheckable(True)
        self.power_btn.setFixedSize(22, 20)
        self.power_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; border:none; border-radius:3px; }}
            QPushButton:checked {{ background:{BG3}; }}
            QPushButton:hover {{ background:{LINE}; }}
        """)
        self.power_btn.setToolTip("윈도우 시작 시 자동 실행")
        self.power_btn.setChecked(is_autostart_enabled())
        self.power_btn.setIcon(self._make_power_icon(self.power_btn.isChecked()))
        self.power_btn.setIconSize(QSize(12, 12))
        self.power_btn.clicked.connect(self._toggle_power)
        l.addWidget(self.power_btn)

        # 📌 항상 위 고정핀 버튼
        self.top_btn = QPushButton()
        self.top_btn.setCheckable(True)
        self.top_btn.setFixedSize(22, 20)
        self.top_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; border:none; border-radius:3px; }}
            QPushButton:checked {{ background:{BG3}; }}
            QPushButton:hover {{ background:{LINE}; }}
        """)
        self.top_btn.setToolTip("항상 위 고정")
        self.top_btn.setChecked(True)
        self.top_btn.setIcon(self._make_pin_icon(True))
        self.top_btn.setIconSize(QSize(12, 12))
        self.top_btn.clicked.connect(self._toggle_top)
        l.addWidget(self.top_btn)

        # 창 제어 버튼들
        min_btn = QPushButton("─")
        min_btn.setFixedSize(26, 20)
        min_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{GRAY}; border:none; font-size:11px; }}
            QPushButton:hover {{ background:{BG3}; color:{ACCENT}; }}
        """)
        min_btn.clicked.connect(win.showMinimized)
        l.addWidget(min_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 20)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{GRAY}; border:none; font-size:10px; }}
            QPushButton:hover {{ background:#cc4444; color:white; }}
        """)
        close_btn.clicked.connect(win.close)
        l.addWidget(close_btn)

    def _make_pin_icon(self, active: bool) -> QIcon:
        px = QPixmap(14, 14)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(ACCENT) if active else QColor(GRAY)
        p.setPen(QPen(color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(QBrush(color) if active else Qt.BrushStyle.NoBrush)
        p.drawEllipse(3, 1, 8, 7)
        p.drawLine(7, 8, 7, 13)
        p.end()
        return QIcon(px)

    def _make_power_icon(self, active: bool) -> QIcon:
        px = QPixmap(14, 14)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(ACCENT) if active else QColor(GRAY)
        p.setPen(QPen(color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(2, 2, 10, 10, 120 * 16, 300 * 16)
        p.drawLine(7, 2, 7, 6)
        p.end()
        return QIcon(px)

    def _toggle_power(self, checked):
        self.power_btn.setIcon(self._make_power_icon(checked))
        set_autostart(checked)

    def _toggle_top(self, checked):
        self.top_btn.setIcon(self._make_pin_icon(checked))
        if checked:
            self._win.setWindowFlags(self._win.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self._win.setWindowFlags(self._win.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
        self._win.show()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            self._win.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


# ── 메인 윈도우 ───────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setWindowTitle("Clipboard Saver")
        self.setWindowIcon(make_icon())

        self.resize(350, 260)
        self.setMinimumSize(300, 220)
        self.setStyleSheet(f"QMainWindow {{ background:{BG}; }}")

        self.setWindowOpacity(1.0)
        self.is_focused = True

        self.last_image_hash = None
        self.current_filepath = None
        self.fade_step = 0
        self.fade_dir  = 1
        self.dot_visible = True
        self.save_dir = SAVE_DIR

        self._build_ui()
        self._setup_timers()
        self._move_to_bottom_right()

        self.mouse_track_timer = QTimer()
        self.mouse_track_timer.timeout.connect(self._track_mouse_for_passthrough)

    def _move_to_bottom_right(self):
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + geo.width() - self.width()
            y = geo.y() + geo.height() - self.height()
            self.move(x, y)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self.is_focused = True
                self.setWindowOpacity(1.0)
                self._set_click_through(False)
                self.mouse_track_timer.stop()
            else:
                self.is_focused = False
                self.setWindowOpacity(0.6)
                self.mouse_track_timer.start(50)
        super().changeEvent(event)

    def _set_click_through(self, enable: bool):
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

            if enable:
                new_style = ex_style | WS_EX_TRANSPARENT | WS_EX_LAYERED
            else:
                new_style = (ex_style | WS_EX_LAYERED) & ~WS_EX_TRANSPARENT

            if ex_style != new_style:
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        except Exception:
            pass

    def _track_mouse_for_passthrough(self):
        global_pos = QCursor.pos()

        if self.geometry().contains(global_pos):
            local_pos = self.mapFromGlobal(global_pos)
            if self.title_bar.geometry().contains(local_pos):
                self._set_click_through(False)
            else:
                self._set_click_through(True)
        else:
            self._set_click_through(True)

    def _build_ui(self):
        central = QWidget()
        central.setStyleSheet(f"background:{BG};")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = TitleBar(self)
        root.addWidget(self.title_bar)
        root.addWidget(self._make_header())
        root.addWidget(self._hline())

        main_w = QWidget()
        main_l = QHBoxLayout(main_w)
        main_l.setContentsMargins(12, 6, 12, 6)
        main_l.setSpacing(8)

        self.preview_container = QWidget()
        self.preview_container.setStyleSheet(f"background:{BG2}; border-radius:4px;")
        pc_l = QVBoxLayout(self.preview_container)
        pc_l.setContentsMargins(0, 0, 0, 0)
        pc_l.setSpacing(0)

        self.preview = DraggablePreview()
        self.preview.setStyleSheet(f"background:{BG2}; color:{GRAY};")
        self.preview.setText("이미지를 복사하세요\n(Ctrl+C)")
        self.preview.setFont(QFont("Courier New", 9))

        self.drag_hint = QLabel("드래그하여 저장")
        self.drag_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drag_hint.setStyleSheet(f"color:{ACCENT}; background:{BG2}; font-size:8px; padding:2px;")
        self.drag_hint.hide()

        pc_l.addWidget(self.preview)
        pc_l.addWidget(self.drag_hint)

        hist_w = QWidget()
        hist_w.setFixedWidth(120)
        hist_l = QVBoxLayout(hist_w)
        hist_l.setContentsMargins(0, 0, 0, 0)
        hist_l.setSpacing(4)

        hist_title = QLabel("HISTORY")
        hist_title.setStyleSheet(f"color:{GRAY}; font-weight:bold; font-size:8px;")
        hist_l.addWidget(hist_title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{ border:none; background:{BG}; }}
            QScrollBar:vertical {{ background:{BG3}; width:4px; border-radius:2px; }}
            QScrollBar::handle:vertical {{ background:{GRAY}; border-radius:2px; min-height:16px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0px; }}
        """)
        self.hist_inner = QWidget()
        self.hist_layout = QVBoxLayout(self.hist_inner)
        self.hist_layout.setContentsMargins(0, 0, 0, 0)
        self.hist_layout.setSpacing(4)
        self.hist_layout.addStretch()
        scroll.setWidget(self.hist_inner)
        hist_l.addWidget(scroll)

        main_l.addWidget(self.preview_container, stretch=1)
        main_l.addWidget(hist_w)
        root.addWidget(main_w, stretch=1)

        root.addWidget(self._hline())
        root.addWidget(self._make_infobar())
        root.addWidget(self._hline())
        root.addWidget(self._make_pathbar())

    def _make_header(self):
        w = QWidget(); w.setFixedHeight(26)
        l = QHBoxLayout(w); l.setContentsMargins(12, 0, 12, 0)
        lbl1 = QLabel("CLIPBOARD"); lbl1.setStyleSheet(f"color:{ACCENT}; font-weight:bold; font-size:9px;")
        lbl2 = QLabel(" SAVER"); lbl2.setStyleSheet(f"color:{ACCENT}; font-size:9px;")
        l.addWidget(lbl1); l.addWidget(lbl2); l.addStretch()

        self.status_dot = QLabel("●"); self.status_dot.setStyleSheet(f"color:{ACCENT}; font-size:10px;")
        monitor = QLabel("monitoring"); monitor.setStyleSheet(f"color:{GRAY}; font-size:7px;")
        l.addWidget(monitor); l.addWidget(self.status_dot)
        return w

    def _make_infobar(self):
        w = QWidget(); w.setFixedHeight(22)
        l = QHBoxLayout(w); l.setContentsMargins(12, 0, 12, 0)
        self.info_label = QLabel("waiting...")
        self.info_label.setStyleSheet(f"color:{GRAY}; font-size:8px;")
        l.addWidget(self.info_label); l.addStretch()
        return w

    def _make_folder_icon(self) -> QIcon:
        px = QPixmap(12, 12)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(GRAY), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawRoundedRect(1, 4, 10, 6, 1, 1)
        p.drawLine(1, 4, 1, 2)
        p.drawLine(1, 2, 4, 2)
        p.drawLine(4, 2, 5, 4)
        p.end()
        return QIcon(px)

    def _make_pathbar(self):
        w = QWidget(); w.setFixedHeight(28)
        l = QHBoxLayout(w); l.setContentsMargins(12, 4, 12, 4); l.setSpacing(4)
        lbl = QLabel("PATH"); lbl.setStyleSheet(f"color:{GRAY}; font-size:7px;"); lbl.setFixedWidth(26)

        self.path_edit = QLineEdit(self.save_dir)
        self.path_edit.setFixedHeight(18)
        self.path_edit.setStyleSheet(f"""
            QLineEdit {{ background:{BG3}; color:{GRAY}; border:1px solid {LINE};
                         border-radius:2px; padding:1px 4px; font-size:8px; }}
            QLineEdit:focus {{ border:1px solid {ACCENT}; color:{ACCENT}; }}
        """)
        self.path_edit.editingFinished.connect(self._on_path_changed)

        folder_btn = QPushButton()
        folder_btn.setFixedSize(18, 18)
        folder_btn.setToolTip("저장 폴더 열기")
        folder_btn.setIcon(self._make_folder_icon())
        folder_btn.setIconSize(QSize(12, 12))
        folder_btn.setStyleSheet(f"QPushButton {{ background:{BG3}; border:none; border-radius:2px; }} QPushButton:hover {{ background:{LINE}; }}")
        folder_btn.clicked.connect(lambda: os.startfile(self.save_dir))

        copy_btn = QPushButton("⧉")
        copy_btn.setFixedSize(18, 18)
        copy_btn.setToolTip("이미지 경로 복사")
        copy_btn.setStyleSheet(f"QPushButton {{ background:{BG3}; color:{GRAY}; border:none; font-size:11px; border-radius:2px; }} QPushButton:hover {{ background:{LINE}; color:{ACCENT}; }}")
        copy_btn.clicked.connect(self._copy_image_path)

        l.addWidget(lbl); l.addWidget(self.path_edit)
        l.addWidget(folder_btn); l.addWidget(copy_btn)
        return w

    def _hline(self):
        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color:{LINE}; background:{LINE};")
        line.setFixedHeight(1); return line

    def _copy_image_path(self):
        if self.current_filepath:
            QApplication.clipboard().setText(self.current_filepath)
            self.info_label.setText("✓ 복사됨")
            self.info_label.setStyleSheet(f"color:{ACCENT}; font-size:8px;")
            QTimer.singleShot(2000, lambda: self.info_label.setStyleSheet(f"color:{GRAY}; font-size:8px;"))

    def _on_path_changed(self):
        new_dir = self.path_edit.text().strip()
        try:
            os.makedirs(new_dir, exist_ok=True)
            self.save_dir = new_dir
            self.path_edit.setStyleSheet(f"QLineEdit {{ background:{BG3}; color:{ACCENT}; border:1px solid {ACCENT}; border-radius:2px; padding:1px 4px; font-size:8px; }}")
            QTimer.singleShot(1500, lambda: self.path_edit.setStyleSheet(f"QLineEdit {{ background:{BG3}; color:{GRAY}; border:1px solid {LINE}; border-radius:2px; padding:1px 4px; font-size:8px; }} QLineEdit:focus {{ border:1px solid {ACCENT}; color:{ACCENT}; }}"))
        except Exception:
            self.path_edit.setText(self.save_dir)

    def _setup_timers(self):
        self.clipboard = QApplication.clipboard()
        self.clipboard.dataChanged.connect(self._check_clipboard)

        self.clip_timer = QTimer()
        self.clip_timer.timeout.connect(self._check_clipboard)
        self.clip_timer.start(1000)

        self.blink_timer = QTimer()
        self.blink_timer.timeout.connect(self._blink_dot)
        self.blink_timer.start(900)

        self.fade_timer = QTimer()
        self.fade_timer.timeout.connect(self._fade_tick)

    def _image_hash(self, img: QImage) -> str:
        # 이미지 전체 픽셀 데이터를 MD5로 해싱 → 동일 크기/중심 픽셀 충돌 방지
        raw = img.bits().asarray(img.sizeInBytes())
        return hashlib.md5(bytes(raw)).hexdigest()

    def _check_clipboard(self):
        try:
            self.clipboard.blockSignals(True)
            img = self.clipboard.image()
            if img.isNull():
                self.clipboard.blockSignals(False)
                return

            image_hash = self._image_hash(img)

            if image_hash == self.last_image_hash:
                self.clipboard.blockSignals(False)
                return

            self.last_image_hash = image_hash
            self._on_new_image(img)
            self.clipboard.blockSignals(False)
        except Exception:
            self.clipboard.blockSignals(False)

    def _on_new_image(self, qimg: QImage):
        pixmap = QPixmap.fromImage(qimg)
        fname  = datetime.datetime.now().strftime("clip_%Y%m%d_%H%M%S.png")
        fpath  = os.path.join(self.save_dir, fname)

        os.makedirs(self.save_dir, exist_ok=True)
        pixmap.save(fpath, "PNG")

        self.current_filepath = fpath

        self._add_history(pixmap, fpath, fname)
        self._show_preview(pixmap, fpath)

        self.info_label.setText(f"{fname}  ·  {qimg.width()}×{qimg.height()}px")
        self.info_label.setStyleSheet(f"color:{ACCENT}; font-size:8px;")
        self._start_fade()

    def _show_preview(self, pixmap: QPixmap, filepath: str):
        self.preview.filepath = filepath
        self.preview.update_preview_image()
        self.drag_hint.show()

    def _add_history(self, pixmap: QPixmap, fpath: str, fname: str):
        item = HistoryItem(pixmap, fpath, fname, datetime.datetime.now().strftime("%H:%M:%S"))
        item.clicked.connect(self._on_history_click)
        item.deleted.connect(self._on_history_delete)
        self.hist_layout.insertWidget(0, item)

        # 최대 HISTORY_MAX개 초과 시 가장 오래된 항목 제거 (-1: stretch 아이템 제외)
        while self.hist_layout.count() - 1 > HISTORY_MAX:
            w = self.hist_layout.itemAt(self.hist_layout.count() - 2).widget()
            if w:
                w.setParent(None)
                w.deleteLater()

    def _on_history_click(self, filepath: str, filename: str):
        if not os.path.exists(filepath): return
        pixmap = QPixmap(filepath)
        self.current_filepath = filepath
        self._show_preview(pixmap, filepath)
        self.info_label.setText(f"{filename}  ·  {pixmap.width()}×{pixmap.height()}px")
        self.info_label.setStyleSheet(f"color:{ACCENT}; font-size:8px;")

    def _on_history_delete(self, item):
        try:
            if os.path.exists(item.filepath):
                os.remove(item.filepath)
        except Exception:
            pass

        if self.current_filepath == item.filepath:
            self.preview.clear()
            self.preview.filepath = None
            self.preview.setText("이미지를 복사하세요\n(Ctrl+C)")
            self.drag_hint.hide()
            self.current_filepath = None
            self.info_label.setText("waiting...")
            self.info_label.setStyleSheet(f"color:{GRAY}; font-size:8px;")

        item.setParent(None)
        item.deleteLater()

    def _start_fade(self):
        self.fade_step = 0
        self.fade_dir = 1
        self.fade_timer.start(16)

    def _fade_tick(self):
        STEPS = 12
        fac = (self.fade_step / STEPS) if self.fade_dir == 1 else (1 - self.fade_step / STEPS)
        bg2 = QColor(BG2)
        acc = QColor(ACCENT)
        r = int(bg2.red() + (acc.red() - bg2.red()) * fac)
        g = int(bg2.green() + (acc.green() - bg2.green()) * fac)
        b = int(bg2.blue() + (acc.blue() - bg2.blue()) * fac)
        self.preview_container.setStyleSheet(f"background:rgb({r},{g},{b}); border-radius:4px;")

        self.fade_step += 1
        if self.fade_dir == 1 and self.fade_step > STEPS:
            self.fade_dir = -1
            self.fade_step = 0
        elif self.fade_dir == -1 and self.fade_step > STEPS:
            self.preview_container.setStyleSheet(f"background:{BG2}; border-radius:4px;")
            self.fade_timer.stop()

    def _blink_dot(self):
        self.dot_visible = not self.dot_visible
        self.status_dot.setStyleSheet(f"color:{ACCENT if self.dot_visible else BG}; font-size:10px;")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.preview.update_preview_image()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(200, lambda: set_titlebar_color(int(self.winId()), TITLE))


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.Base, QColor(BG2))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(BG3))
    palette.setColor(QPalette.ColorRole.Text, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.Button, QColor(BG3))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(ACCENT))
    app.setPalette(palette)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
