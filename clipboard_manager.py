import sys
import os
import datetime
import ctypes
import winreg
import hashlib
import time

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QHBoxLayout, QFrame, QLineEdit,
    QTextEdit, QSystemTrayIcon, QMenu, QDialog, QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer, QMimeData, QUrl, QPoint, QSize, QEvent, pyqtSignal, QThread
from PyQt6.QtGui import (
    QPixmap, QImage, QColor, QDrag, QIcon, QPalette, QCursor,
    QPainter, QBrush, QPen, QAction, QFont,
)

# ── 윈도우 API 상수 ────────────────────────────────────────────────────────────
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
VK_CAPITAL = 0x14

# ── 색상 ──────────────────────────────────────────────────────────────────────
BG = "#2b2b2b"
BG2 = "#333333"
BG3 = "#3d3d3d"
GRAY = "#888888"
LINE = "#444444"
TITLE = "#1e1e1e"
ACCENT = "#0d9488"

# ── 레이아웃 상수 ──────────────────────────────────────────────────────────────
WIN_W = 270
FIXED_H = 84
MAX_CARDS = 30
CARD_HEIGHT_IMG = 136
CARD_LINE_H = 18
CARD_TEXT_MIN = 96
CARD_TEXT_MAX = CARD_HEIGHT_IMG
CARD_SPACING = 4

# 클립보드 파일 저장 기본 디렉터리
SAVE_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "ClipboardSaver")
os.makedirs(SAVE_DIR, exist_ok=True)


# ── 백그라운드 워커 ────────────────────────────────────────────────────────────
class ClipWorker(QThread):
    """클립보드 데이터를 비동기로 파일에 저장하는 백그라운드 스레드."""

    # 저장 성공 시: 모드, 해시, 파일 경로, 파일 이름, 메타, 데이터, 썸네일데이터
    finished_new = pyqtSignal(str, str, str, str, str, object, object)
    # 저장 실패 시: 콘텐츠 해시
    finished_err = pyqtSignal(str)

    def __init__(self, mode: str, data, save_dir: str, content_hash: str):
        super().__init__()
        self.mode = mode
        self.data = data
        self.save_dir = save_dir
        self.content_hash = content_hash

    def run(self):
        """스레드 실행 본체 — 텍스트/이미지를 파일로 저장한다."""
        try:
            if self.mode == "text":
                fname = datetime.datetime.now().strftime("clip_%Y%m%d_%H%M%S.txt")
                fpath = os.path.join(self.save_dir, fname)
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(self.data)
                self.finished_new.emit(
                    "text", self.content_hash, fpath, fname,
                    f"{len(self.data)}자", self.data, None
                )
            elif self.mode == "image":
                fname = datetime.datetime.now().strftime("clip_%Y%m%d_%H%M%S.png")
                fpath = os.path.join(self.save_dir, fname)
                self.data.save(fpath, "PNG")
                
                # 백그라운드에서 썸네일을 미리 생성하여 메인 UI 스레드의 부하를 줄임 (성능 개선)
                thumb_img = self.data.scaled(
                    200, CARD_HEIGHT_IMG - 8,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                
                self.finished_new.emit(
                    "image", self.content_hash, fpath, fname,
                    f"{self.data.width()}×{self.data.height()}", self.data, thumb_img
                )
        except Exception:
            self.finished_err.emit(self.content_hash)


# ── OS 유틸리티 ────────────────────────────────────────────────────────────────
def is_autostart_enabled():
    """Windows 시작프로그램 레지스트리에 등록 여부를 반환한다."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        )
        winreg.QueryValueEx(key, "ClipboardSaver")
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def set_autostart(enable):
    """Windows 시작프로그램 레지스트리 항목을 추가하거나 제거한다."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_ALL_ACCESS,
        )
        if enable:
            winreg.SetValueEx(
                key, "ClipboardSaver", 0, winreg.REG_SZ,
                f'"{sys.executable}" "{os.path.abspath(__file__)}"',
            )
        else:
            try:
                winreg.DeleteValue(key, "ClipboardSaver")
            except OSError:
                pass
        winreg.CloseKey(key)
    except Exception:
        pass


def is_start_minimized():
    """시작 시 트레이로 최소화 설정 값을 레지스트리에서 읽어 반환한다."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\ClipboardSaver",
            0,
            winreg.KEY_READ,
        )
        val, _ = winreg.QueryValueEx(key, "StartMinimized")
        winreg.CloseKey(key)
        return bool(val)
    except OSError:
        return False


def set_start_minimized(enable):
    """시작 시 트레이 최소화 여부를 레지스트리에 저장한다."""
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\ClipboardSaver")
        winreg.SetValueEx(key, "StartMinimized", 0, winreg.REG_DWORD, int(enable))
        winreg.CloseKey(key)
    except Exception:
        pass


# 지원 단축키 목록: (표시명, VK 코드)
# Qt Key → Windows VK 매핑 (확장 가능)
from PyQt6.QtCore import Qt as _Qt

_QT_TO_VK = {
    _Qt.Key.Key_CapsLock:   (0x14, "CapsLock"),
    _Qt.Key.Key_ScrollLock: (0x91, "Scroll Lock"),
    _Qt.Key.Key_Pause:      (0x13, "Pause"),
    _Qt.Key.Key_Insert:     (0x2D, "Insert"),
    _Qt.Key.Key_Delete:     (0x2E, "Delete"),
    _Qt.Key.Key_Home:       (0x24, "Home"),
    _Qt.Key.Key_End:        (0x23, "End"),
    _Qt.Key.Key_PageUp:     (0x21, "Page Up"),
    _Qt.Key.Key_PageDown:   (0x22, "Page Down"),
    _Qt.Key.Key_F1:  (0x70, "F1"),  _Qt.Key.Key_F2:  (0x71, "F2"),
    _Qt.Key.Key_F3:  (0x72, "F3"),  _Qt.Key.Key_F4:  (0x73, "F4"),
    _Qt.Key.Key_F5:  (0x74, "F5"),  _Qt.Key.Key_F6:  (0x75, "F6"),
    _Qt.Key.Key_F7:  (0x76, "F7"),  _Qt.Key.Key_F8:  (0x77, "F8"),
    _Qt.Key.Key_F9:  (0x78, "F9"),  _Qt.Key.Key_F10: (0x79, "F10"),
    _Qt.Key.Key_F11: (0x7A, "F11"), _Qt.Key.Key_F12: (0x7B, "F12"),
    _Qt.Key.Key_F13: (0x7C, "F13"), _Qt.Key.Key_F14: (0x7D, "F14"),
    _Qt.Key.Key_F15: (0x7E, "F15"), _Qt.Key.Key_F16: (0x7F, "F16"),
}

# 일반 문자/숫자 키 자동 추가 (A-Z, 0-9)
for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _QT_TO_VK[getattr(_Qt.Key, f"Key_{_ch}")] = (ord(_ch), _ch)
for _d in "0123456789":
    _QT_TO_VK[getattr(_Qt.Key, f"Key_{_d}")] = (ord(_d), _d)

# VK 코드 → 이름 역매핑 딕셔너리
_VK_TO_NAME = {vk: name for (vk, name) in _QT_TO_VK.values()}


def qt_key_to_vk(qt_key):
    """Qt Key enum → (vk: int, name: str) or None"""
    return _QT_TO_VK.get(qt_key)


# 하위 호환 (폴링용 VK → 이름)
HOTKEY_NAMES = _VK_TO_NAME


def get_hotkey_vk() -> int:
    """레지스트리에서 팝업 단축키 VK 코드를 읽어 반환한다. 기본값은 CapsLock."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\ClipboardSaver",
            0,
            winreg.KEY_READ,
        )
        val, _ = winreg.QueryValueEx(key, "HotkeyVK")
        winreg.CloseKey(key)
        return int(val)
    except OSError:
        return 0x72  # 기본값: F3


def set_hotkey_vk(vk: int):
    """팝업 단축키 VK 코드를 레지스트리에 저장한다."""
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\ClipboardSaver")
        winreg.SetValueEx(key, "HotkeyVK", 0, winreg.REG_DWORD, vk)
        winreg.CloseKey(key)
    except Exception:
        pass


def set_titlebar_color(hwnd, hex_color):
    """DWM API를 통해 창 타이틀바 색상을 설정한다."""
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        c = r | (g << 8) | (b << 16)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 35, ctypes.byref(ctypes.c_int(c)), ctypes.sizeof(ctypes.c_int)
        )
    except Exception:
        pass


def _set_foreground_window(hwnd):
    """스레드 입력을 임시로 연결하여 창을 포그라운드로 강제 이동한다."""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        curr_tid = kernel32.GetCurrentThreadId()
        fg_hwnd = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
        if fg_tid and fg_tid != curr_tid:
            user32.AttachThreadInput(fg_tid, curr_tid, True)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(fg_tid, curr_tid, False)
        else:
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def make_icon() -> QIcon:
    """클립보드 모양의 애플리케이션 아이콘을 생성한다."""
    px = QPixmap(64, 64)
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


def make_pin_icon(size: int, active: bool) -> QIcon:
    """고정(핀) 버튼 아이콘을 생성한다. active=True 이면 흰색, 아니면 회색."""
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor("white") if active else QColor(GRAY)
    p.setPen(QPen(color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(QBrush(color) if active else Qt.BrushStyle.NoBrush)
    cx = size // 2
    p.drawEllipse(cx - size // 4, 1, size // 2, size // 2)
    p.drawLine(cx, size // 2, cx, size - 2)
    p.end()
    return QIcon(px)


def make_zoom_icon(size: int, color_hex: str) -> QPixmap:
    """돋보기 모양의 줌 인디케이터 아이콘 픽스맵을 생성한다."""
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(color_hex), max(2.0, size / 10.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    c = int(size * 0.45)
    m = int(size * 0.15)
    p.drawEllipse(m, m, c, c)
    p.drawLine(m + int(c * 0.85), m + int(c * 0.85), size - m, size - m)
    p.end()
    return px


class _ResizeGrip(QWidget):
    """메인 창 오른쪽 하단에 배치되는 크기 조절 핸들."""

    def __init__(self, win):
        super().__init__()
        self._win = win
        self._drag_start = None
        self._orig_size = None
        self.setFixedSize(14, 14)
        self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.globalPosition().toPoint()
            self._orig_size = QSize(self._win.width(), self._win.height())

    def mouseMoveEvent(self, e):
        if not self._drag_start or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        d = e.globalPosition().toPoint() - self._drag_start
        nw = max(220, self._orig_size.width() + d.x())
        nh = max(FIXED_H, self._orig_size.height() + d.y())
        screen = QApplication.primaryScreen().availableGeometry()
        self._win.resize(nw, nh)
        self._win.move(screen.right() - nw, screen.bottom() - nh)

    def mouseReleaseEvent(self, e):
        self._drag_start = None
        self._orig_size = None

    def paintEvent(self, e):
        """대각선 점선 그립 모양을 그린다."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(GRAY), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for i in range(1, 4):
            p.drawLine(14 - i * 4, 14, 14, 14 - i * 4)
        p.end()


class _DialogResizeGrip(QWidget):
    """다이얼로그 창 오른쪽 하단에 배치되는 크기 조절 핸들."""

    def __init__(self, win):
        super().__init__()
        self._win = win
        self._drag_start = None
        self._orig_size = None
        self.setFixedSize(14, 14)
        self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.globalPosition().toPoint()
            self._orig_size = QSize(self._win.width(), self._win.height())

    def mouseMoveEvent(self, e):
        if not self._drag_start or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        d = e.globalPosition().toPoint() - self._drag_start
        nw = max(300, self._orig_size.width() + d.x())
        nh = max(200, self._orig_size.height() + d.y())
        self._win.resize(nw, nh)

    def mouseReleaseEvent(self, e):
        self._drag_start = None
        self._orig_size = None

    def paintEvent(self, e):
        """대각선 점선 그립 모양을 그린다."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(GRAY), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for i in range(1, 4):
            p.drawLine(14 - i * 4, 14, 14, 14 - i * 4)
        p.end()


# ── 통합 설정 다이얼로그 ──────────────────────────────────────────────────────
class SettingsDialog(QDialog):
    """자동 실행, 시작 최소화, 팝업 단축키를 한 곳에서 관리하는 설정 다이얼로그."""

    def __init__(self, win):
        super().__init__(win)
        self._win = win
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFixedWidth(260)
        self.setStyleSheet(f"QDialog {{ background:{BG2}; border:1px solid {LINE}; }}")
        self._drag_pos = None
        self._pending_vk = get_hotkey_vk()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 타이틀바
        tb = QWidget()
        tb.setFixedHeight(26)
        tb.setStyleSheet(f"background:{TITLE};")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(8, 0, 6, 0)
        title_lbl = QLabel("설정")
        title_lbl.setStyleSheet(f"color:{ACCENT}; font-size:11px; font-weight:bold;")
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 20)
        close_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{GRAY}; border:none; }}"
            f" QPushButton:hover {{ background:#cc4444; color:white; }}"
        )
        close_btn.clicked.connect(self.reject)
        tbl.addWidget(title_lbl)
        tbl.addStretch()
        tbl.addWidget(close_btn)

        # 타이틀바 드래그 이동 이벤트 연결
        tb.mousePressEvent = (
            lambda e: setattr(self, "_drag_pos", e.globalPosition().toPoint() - self.frameGeometry().topLeft())
            if e.button() == Qt.MouseButton.LeftButton
            else None
        )
        tb.mouseMoveEvent = (
            lambda e: self.move(e.globalPosition().toPoint() - self._drag_pos)
            if e.buttons() == Qt.MouseButton.LeftButton and self._drag_pos
            else None
        )
        tb.mouseReleaseEvent = lambda e: setattr(self, "_drag_pos", None)
        root.addWidget(tb)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(16, 14, 16, 16)
        bl.setSpacing(12)

        # ── 섹션: 시작 설정 ──
        sec1 = QLabel("시작 설정")
        sec1.setStyleSheet(f"color:{ACCENT}; font-size:10px; font-weight:bold;")
        bl.addWidget(sec1)

        chk_style = (
            f"QCheckBox {{ color:{ACCENT}; font-size:11px; }}"
            f" QCheckBox::indicator {{ width:14px; height:14px; border:1px solid {LINE};"
            f" border-radius:3px; background:{BG3}; }}"
            f" QCheckBox::indicator:checked {{ background:{ACCENT}; border-color:{ACCENT}; }}"
        )

        self.chk_autostart = QCheckBox("Windows 시작 시 자동 실행")
        self.chk_autostart.setStyleSheet(chk_style)
        self.chk_autostart.setChecked(is_autostart_enabled())

        self.chk_tray = QCheckBox("시작 시 트레이로 최소화")
        self.chk_tray.setStyleSheet(chk_style)
        self.chk_tray.setChecked(is_start_minimized())

        bl.addWidget(self.chk_autostart)
        bl.addWidget(self.chk_tray)

        # 구분선
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(f"color:{LINE}; background:{LINE};")
        div.setFixedHeight(1)
        bl.addWidget(div)

        # ── 섹션: 팝업 단축키 ──
        sec2 = QLabel("팝업 단축키")
        sec2.setStyleSheet(f"color:{ACCENT}; font-size:10px; font-weight:bold;")
        bl.addWidget(sec2)

        cur_name = HOTKEY_NAMES.get(self._pending_vk, f"VK 0x{self._pending_vk:02X}")
        self._listening = False
        self._listen_style_normal = (
            f"QPushButton {{ background:{BG3}; color:{ACCENT}; border:1px solid {LINE};"
            f" border-radius:4px; font-size:11px; }}"
            f" QPushButton:hover {{ border-color:{ACCENT}; }}"
        )
        self._listen_style_active = (
            f"QPushButton {{ background:{BG3}; color:#f59e0b; border:1px solid #f59e0b;"
            f" border-radius:4px; font-size:11px; }}"
        )
        self.listen_btn = QPushButton(f"현재: {cur_name}  —  클릭 후 키 입력")
        self.listen_btn.setFixedHeight(30)
        self.listen_btn.setStyleSheet(self._listen_style_normal)
        self.listen_btn.clicked.connect(self._start_listening)

        hint = QLabel("클릭 후 아무 키나 누르세요 (단독 키 또는 조합 없이)")
        hint.setStyleSheet(f"color:{GRAY}; font-size:10px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        bl.addWidget(self.listen_btn)
        bl.addWidget(hint)

        # 구분선
        div2 = QFrame()
        div2.setFrameShape(QFrame.Shape.HLine)
        div2.setStyleSheet(f"color:{LINE}; background:{LINE};")
        div2.setFixedHeight(1)
        bl.addWidget(div2)

        # ── 확인 버튼 ──
        ok_btn = QPushButton("적용")
        ok_btn.setFixedHeight(30)
        ok_btn.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:white; border:none; border-radius:4px;"
            f" font-size:11px; font-weight:bold; }}"
            f" QPushButton:hover {{ background:#0f766e; }}"
        )
        ok_btn.clicked.connect(self._apply)
        bl.addWidget(ok_btn)

        root.addWidget(body)

    def _start_listening(self):
        """단축키 입력 대기 모드를 활성화한다."""
        self._listening = True
        self.listen_btn.setText("키 입력 대기 중...  (아무 키나 누르세요)")
        self.listen_btn.setStyleSheet(self._listen_style_active)
        self.grabKeyboard()

    def keyPressEvent(self, e):
        """단축키 대기 중 누른 키를 캡처하여 VK 코드로 저장한다."""
        if not self._listening:
            super().keyPressEvent(e)
            return
        qt_key = Qt.Key(e.key())
        # 단독 modifier는 무시
        if qt_key in (
            Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt,
            Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
        ):
            return
        self.releaseKeyboard()
        self._listening = False
        result = qt_key_to_vk(qt_key)
        if result:
            vk, name = result
        else:
            # 매핑에 없는 키는 Qt key 값 자체를 VK로 저장 (GetAsyncKeyState 미지원일 수 있음)
            vk = e.nativeVirtualKey() or e.key()
            name = e.text().upper() or f"VK 0x{vk:02X}"
        self._pending_vk = vk
        self.listen_btn.setText(f"선택됨: {name}  —  클릭 후 키 입력")
        self.listen_btn.setStyleSheet(self._listen_style_normal)

    def _apply(self):
        """설정 값을 레지스트리에 저장하고 다이얼로그를 닫는다."""
        set_autostart(self.chk_autostart.isChecked())
        set_start_minimized(self.chk_tray.isChecked())
        set_hotkey_vk(self._pending_vk)
        self._win._on_hotkey_changed(self._pending_vk)
        self.accept()

    def changeEvent(self, e):
        """포커스를 잃으면 자동으로 닫힌다 (키 입력 대기 중 제외)."""
        if (
            e.type() == QEvent.Type.ActivationChange
            and not self.isActiveWindow()
            and not self._listening
        ):
            QTimer.singleShot(150, lambda: self.reject() if not self.isActiveWindow() else None)
        super().changeEvent(e)

    def showEvent(self, e):
        """창이 표시될 때 타이틀바 색상을 다크 테마로 적용한다."""
        super().showEvent(e)
        QTimer.singleShot(100, lambda: set_titlebar_color(int(self.winId()), TITLE))


# ── 다이얼로그 커스텀 타이틀바 ────────────────────────────────────────────────
class DialogTitleBar(QWidget):
    """편집/그리기 다이얼로그에 사용되는 커스텀 타이틀바 위젯."""

    def __init__(self, win, title=""):
        super().__init__(win)
        self._win = win
        self.setFixedHeight(28)
        self.setStyleSheet(f"background:{TITLE};")
        self._drag_pos = None

        l = QHBoxLayout(self)
        l.setContentsMargins(8, 0, 6, 0)
        l.setSpacing(8)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(make_icon().pixmap(14, 14))
        l.addWidget(icon_lbl)

        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet(f"color:{GRAY}; font-size:10px; font-weight:bold;")
        l.addWidget(self.title_lbl)
        l.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 20)
        close_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{GRAY}; border:none; }}"
            f" QPushButton:hover {{ background:#cc4444; color:white; }}"
        )
        close_btn.clicked.connect(win.reject)
        l.addWidget(close_btn)

    def set_title(self, title):
        """타이틀 텍스트를 동적으로 변경한다."""
        self.title_lbl.setText(title)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            self._win.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None


# ── 텍스트 에디터 (Ctrl+휠 크기 조절 지원) ──────────────────────────────────
class ZoomTextEdit(QTextEdit):
    """마우스 휠로 폰트 크기를 조절할 수 있는 코드 편집기 위젯."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setPlainText(text)  # HTML 자동 인식으로 인한 줄바꿈 증발 방지
        self.font_size = 13
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)  # 다시 자동 줄바꿈 허용
        self._apply_style()

    def _apply_style(self):
        """현재 폰트 크기에 맞춰 스타일시트와 폰트를 재적용한다."""
        # 폰트 강제 적용 및 탭 간격 4칸으로 설정 (VS Code 스타일)
        font = QFont("Consolas", self.font_size)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)

        self.setStyleSheet(f"""
            QTextEdit {{
                background: #1e1e1e;
                color: #d4d4d4;
                font-family: Consolas, Menlo, 'D2Coding', monospace;
                font-size: {self.font_size}px;
                border: 1px solid {LINE};
                border-radius: 4px;
                padding: 8px;
            }}
            QScrollBar:vertical {{ background: {BG3}; width: 10px; border-radius: 5px; }}
            QScrollBar::handle:vertical {{ background: {GRAY}; border-radius: 5px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)

    def wheelEvent(self, e):
        """마우스 휠로 폰트 크기를 증감시킨다."""
        if True:
            delta = e.angleDelta().y()
            if delta > 0:
                self.font_size = min(72, self.font_size + 2)
            elif delta < 0:
                self.font_size = max(6, self.font_size - 2)
            self._apply_style()
            e.accept()
        else:
            super().wheelEvent(e)


# ── 이미지 그리기 캔버스 (Ctrl+휠 크기 조절 지원) ──────────────────────────
class DrawingCanvas(QLabel):
    """이미지 위에 자유 곡선을 그릴 수 있는 캔버스 위젯."""

    zoom_requested = pyqtSignal(float, QPoint, QPoint)
    history_changed = pyqtSignal()  # undo/redo 가능 여부가 바뀔 때 알림

    def __init__(self, pixmap):
        super().__init__()
        self.original_pixmap = pixmap.copy()    # 원본 (Clear용)
        self.base_pixmap = pixmap.copy()
        self.setPixmap(self.base_pixmap)
        self.setScaledContents(True)
        self.scale_factor = 1.0
        self.setFixedSize(self.base_pixmap.size())
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.last_pos = None
        self._undo_stack: list[QPixmap] = []    # 스트로크 전 스냅샷
        self._redo_stack: list[QPixmap] = []    # undo 후 redo용 스냅샷

    def can_undo(self):
        return bool(self._undo_stack)

    def can_redo(self):
        return bool(self._redo_stack)

    def undo(self):
        """마지막 스트로크를 되돌린다."""
        if not self._undo_stack:
            return
        self._redo_stack.append(self.base_pixmap.copy())
        self.base_pixmap = self._undo_stack.pop()
        self.setPixmap(self.base_pixmap)
        self.history_changed.emit()

    def redo(self):
        """되돌린 스트로크를 다시 적용한다."""
        if not self._redo_stack:
            return
        self._undo_stack.append(self.base_pixmap.copy())
        self.base_pixmap = self._redo_stack.pop()
        self.setPixmap(self.base_pixmap)
        self.history_changed.emit()

    def clear_drawing(self):
        """그린 내용을 모두 지우고 원본 이미지로 복원한다."""
        self._undo_stack.append(self.base_pixmap.copy())
        self._redo_stack.clear()
        self.base_pixmap = self.original_pixmap.copy()
        self.setPixmap(self.base_pixmap)
        self.history_changed.emit()

    def wheelEvent(self, e):
        """마우스 휠로 줌 요청 시그널을 발생시킨다."""
        if True:
            delta = e.angleDelta().y()
            scale_mult = 1.15 if delta > 0 else (1 / 1.15)
            self.zoom_requested.emit(scale_mult, e.position().toPoint(), e.globalPosition().toPoint())
            e.accept()
        else:
            super().wheelEvent(e)

    def _get_real_pos(self, pos):
        # 확대/축소된 화면 좌표를 원본 이미지 비율에 맞춰 변환
        return QPoint(int(pos.x() / self.scale_factor), int(pos.y() / self.scale_factor))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            # 스트로크 시작 전 스냅샷 저장
            self._undo_stack.append(self.base_pixmap.copy())
            self._redo_stack.clear()
            self.last_pos = self._get_real_pos(e.position().toPoint())

    def mouseMoveEvent(self, e):
        if not self.last_pos or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        real_curr = self._get_real_pos(e.position().toPoint())
        p = QPainter(self.base_pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(
            QColor("#ef4444"), 4,
            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin,
        ))
        p.drawLine(self.last_pos, real_curr)
        p.end()
        self.last_pos = real_curr
        self.setPixmap(self.base_pixmap)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.last_pos is not None:
            self.last_pos = None
            self.history_changed.emit()


# ── 수정 다이얼로그 ─────────────────────────────────────────────────────────────
class TextEditDialog(QDialog):
    """클립보드 텍스트를 직접 편집하고 저장하는 다이얼로그."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(f"background:{BG}; color:{GRAY};")
        self.resize(1200, 600)  # 가로 폭을 기존 800에서 1200(50% 증가)으로 확대
        self.save_mode = "overwrite"

        # 포커스 감지용 폴링 타이머 추가
        self._just_shown = True
        self._focus_poll = QTimer(self)
        self._focus_poll.timeout.connect(self._poll_focus)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(DialogTitleBar(self, "텍스트 수정 (Ctrl+휠 크기조절)"))

        content = QWidget()
        l = QVBoxLayout(content)
        l.setContentsMargins(10, 10, 10, 10)

        self.editor = ZoomTextEdit(text)  # 줌 기능이 탑재된 에디터로 변경
        l.addWidget(self.editor)

        btn_row = QHBoxLayout()
        btn_ow = QPushButton("원본 덮어쓰기")
        btn_ow.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_ow.setStyleSheet(
            f"QPushButton {{ background:{BG3}; color:white; padding:8px; border-radius:4px; }}"
            f" QPushButton:hover {{ background:{LINE}; }}"
        )
        btn_ow.clicked.connect(self._on_overwrite)

        btn_new = QPushButton("새 클립보드로 생성")
        btn_new.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_new.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:white; padding:8px; border-radius:4px;"
            f" font-weight:bold; }}"
            f" QPushButton:hover {{ background:#0f766e; }}"
        )
        btn_new.clicked.connect(self._on_save_new)

        btn_row.addStretch()
        btn_row.addWidget(btn_ow)
        btn_row.addWidget(btn_new)
        l.addLayout(btn_row)

        bot_w = QWidget()
        bot_l = QHBoxLayout(bot_w)
        bot_l.setContentsMargins(0, 0, 0, 0)
        bot_l.addStretch()
        bot_l.addWidget(_DialogResizeGrip(self))
        l.addWidget(bot_w)

        root.addWidget(content, stretch=1)

    def _on_overwrite(self):
        self.save_mode = "overwrite"
        self.accept()

    def _on_save_new(self):
        self.save_mode = "new"
        self.accept()

    def get_text(self):
        return self.editor.toPlainText()

    def showEvent(self, event):
        """창이 뜰 때 강제 포커스 획득 및 타이머 시작."""
        super().showEvent(event)
        _set_foreground_window(int(self.winId()))
        self.activateWindow()
        QTimer.singleShot(200, self._on_shown_settle)

    def _on_shown_settle(self):
        """표시 직후 안정화 후 포커스 폴링을 시작한다."""
        self._just_shown = False
        self._focus_poll.start(100)

    def _poll_focus(self):
        """100ms 마다 포커스 잃었는지 확인 후 잃었으면 다이얼로그를 닫는다."""
        if self._just_shown:
            return
        if not self.isActiveWindow():
            active = QApplication.activeWindow()
            if active:
                # 텍스트 에디터 우클릭 시 나오는 QMenu 등은 예외 처리하여 창이 안 닫히게 보호
                if (
                    self.isAncestorOf(active)
                    or isinstance(active, QMenu)
                    or (active.windowFlags() & Qt.WindowType.Popup)
                ):
                    return
            self.reject()


class DrawingDialog(QDialog):
    """이미지 위에 자유 곡선을 그리고 새 클립보드로 저장하는 다이얼로그."""

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(f"background:{BG}; color:{GRAY};")

        # 포커스 감지용 폴링 타이머 추가
        self._just_shown = True
        self._focus_poll = QTimer(self)
        self._focus_poll.timeout.connect(self._poll_focus)

        # 원본 이미지 크기에 맞춰 동적으로 다이얼로그 초기 창 크기 설정
        screen = QApplication.primaryScreen().availableGeometry()
        max_w = int(screen.width() * 0.85)
        max_h = int(screen.height() * 0.85)  # 화면의 85% 제한
        target_w = max(350, min(pixmap.width() + 30, max_w))      # 여백(30) 추가
        target_h = max(250, min(pixmap.height() + 120, max_h))    # 타이틀바, 버튼 여백(120) 추가
        self.resize(target_w, target_h)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.title_bar = DialogTitleBar(self, "간단 그리기 (휠=줌)")
        root.addWidget(self.title_bar)

        content = QWidget()
        l = QVBoxLayout(content)
        l.setContentsMargins(10, 10, 10, 10)

        self.canvas = DrawingCanvas(pixmap)
        self.canvas.zoom_requested.connect(self._on_zoom_requested)
        self.canvas.history_changed.connect(self._update_history_btns)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.canvas)
        self.scroll_area.setStyleSheet(f"""
            QScrollArea {{ border:1px solid {LINE}; background:{BG2}; }}
            QScrollBar:vertical {{ background:{BG3}; width:12px; }}
            QScrollBar:horizontal {{ background:{BG3}; height:12px; }}
            QScrollBar::handle {{ background:{GRAY}; border-radius:4px; }}
        """)
        l.addWidget(self.scroll_area, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        # ── 왼쪽: Undo / Redo / Clear ──
        self.undo_btn = QPushButton("↩ Undo")
        self.undo_btn.setFixedHeight(30)
        self.redo_btn = QPushButton("↪ Redo")
        self.redo_btn.setFixedHeight(30)
        self.clear_btn = QPushButton("🗑 Clear")
        self.clear_btn.setFixedHeight(30)

        _dis = (
            f"QPushButton {{ background:{BG3}; color:{GRAY}; border:none; border-radius:4px;"
            f" padding:0 10px; font-size:11px; }}"
        )
        _ena = (
            f"QPushButton {{ background:{BG3}; color:{ACCENT}; border:1px solid {LINE};"
            f" border-radius:4px; padding:0 10px; font-size:11px; }}"
            f" QPushButton:hover {{ border-color:{ACCENT}; }}"
        )
        _clr = (
            f"QPushButton {{ background:{BG3}; color:#ef4444; border:1px solid {LINE};"
            f" border-radius:4px; padding:0 10px; font-size:11px; }}"
            f" QPushButton:hover {{ background:#7f1d1d; color:white; }}"
        )

        self.undo_btn.setStyleSheet(_dis)
        self.redo_btn.setStyleSheet(_dis)
        self.clear_btn.setStyleSheet(_clr)
        self.undo_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.canvas.undo)
        self.redo_btn.clicked.connect(self.canvas.redo)
        self.clear_btn.clicked.connect(self.canvas.clear_drawing)
        self._ena_style = _ena
        self._dis_style = _dis

        btn_row.addWidget(self.undo_btn)
        btn_row.addWidget(self.redo_btn)
        btn_row.addWidget(self.clear_btn)

        # ── 중앙: 줌 인디케이터 ──
        btn_row.addStretch()
        self.zoom_container = QWidget()
        zl = QHBoxLayout(self.zoom_container)
        zl.setContentsMargins(0, 0, 0, 0)
        zl.setSpacing(6)
        self.zoom_icon = QLabel()
        self.zoom_icon.setPixmap(make_zoom_icon(22, ACCENT))
        self.zoom_lbl = QLabel("100%")
        self.zoom_lbl.setStyleSheet(f"color:{ACCENT}; font-size:20px; font-weight:bold;")
        zl.addWidget(self.zoom_icon)
        zl.addWidget(self.zoom_lbl)
        btn_row.addWidget(self.zoom_container)
        btn_row.addStretch()

        # ── 오른쪽: 완료 버튼 ──
        btn = QPushButton("그리기 완료 (새 클립보드로 생성)")
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:white; padding:8px 16px;"
            f" border-radius:4px; font-weight:bold; }}"
            f" QPushButton:hover {{ background:#0f766e; }}"
        )
        btn.clicked.connect(self.accept)
        btn_row.addWidget(btn)

        l.addLayout(btn_row)

        bot_w = QWidget()
        bot_l = QHBoxLayout(bot_w)
        bot_l.setContentsMargins(0, 0, 0, 0)
        bot_l.addStretch()
        bot_l.addWidget(_DialogResizeGrip(self))
        l.addWidget(bot_w)

        root.addWidget(content, stretch=1)

    def _update_history_btns(self):
        """undo/redo 가능 여부에 따라 버튼 활성화 상태를 갱신한다."""
        self.undo_btn.setEnabled(self.canvas.can_undo())
        self.redo_btn.setEnabled(self.canvas.can_redo())
        self.undo_btn.setStyleSheet(self._ena_style if self.canvas.can_undo() else self._dis_style)
        self.redo_btn.setStyleSheet(self._ena_style if self.canvas.can_redo() else self._dis_style)

    def _on_zoom_requested(self, scale_mult, canvas_pos, global_pos):
        """휠 줌 요청을 처리하여 캔버스 크기와 스크롤 위치를 보정한다."""
        old_scale = self.canvas.scale_factor
        new_scale = max(0.1, min(old_scale * scale_mult, 10.0))
        if old_scale == new_scale:
            return

        zoom_ratio = new_scale / old_scale
        self.canvas.scale_factor = new_scale

        nw = int(self.canvas.base_pixmap.width() * new_scale)
        nh = int(self.canvas.base_pixmap.height() * new_scale)

        self.canvas.setFixedSize(nw, nh)
        self.zoom_lbl.setText(f"{int(new_scale * 100)}%")

        # 이미지 크기가 줄어들면 윈도우 창도 함께 조여지도록(Shrink-to-fit) 크기 조절
        screen = QApplication.primaryScreen().availableGeometry()
        max_w = int(screen.width() * 0.85)
        max_h = int(screen.height() * 0.85)
        target_w = max(350, min(nw + 30, max_w))
        target_h = max(250, min(nh + 120, max_h))

        self.resize(target_w, target_h)

        # 크기 변환 후 캔버스 상에서 마우스가 위치해야 할 새로운 좌표
        new_canvas_pos = QPoint(int(canvas_pos.x() * zoom_ratio), int(canvas_pos.y() * zoom_ratio))

        def adjust_positions():
            h_bar = self.scroll_area.horizontalScrollBar()
            v_bar = self.scroll_area.verticalScrollBar()

            current_canvas_global = self.canvas.mapToGlobal(QPoint(0, 0))
            current_pixel_global = current_canvas_global + new_canvas_pos

            # 마우스 포인터 중심에서 얼마나 벗어났는지(에러) 계산
            err_x = current_pixel_global.x() - global_pos.x()
            err_y = current_pixel_global.y() - global_pos.y()

            # 1차 보정: 스크롤바를 이동시켜 마우스 위치 중앙 유지
            if h_bar.maximum() > 0:
                old_h = h_bar.value()
                h_bar.setValue(old_h + err_x)
                err_x -= (h_bar.value() - old_h)  # 스크롤 가능한 범위를 초과한 잔여 오차

            if v_bar.maximum() > 0:
                old_v = v_bar.value()
                v_bar.setValue(old_v + err_y)
                err_y -= (v_bar.value() - old_v)

            # 2차 보정: 스크롤바가 한계에 도달했거나 창과 이미지가 타이트하게 붙어 스크롤이 불가능할 경우,
            # 윈도우 창 자체를 이동시켜 마우스 포인터 위치를 중심(고정)으로 보정
            if err_x != 0 or err_y != 0:
                self.move(self.x() - err_x, self.y() - err_y)

        # 레이아웃과 캔버스 리사이징이 렌더링에 완전히 반영된 직후 스크롤/창 위치 보정
        QTimer.singleShot(0, adjust_positions)

    @property
    def pixmap(self):
        # 줌 아웃/인 된 상태와 상관없이 항상 원본 비율의 이미지를 내뱉음
        return self.canvas.base_pixmap

    def showEvent(self, event):
        """창이 뜰 때 강제 포커스 획득 및 타이머 시작."""
        super().showEvent(event)
        _set_foreground_window(int(self.winId()))
        self.activateWindow()
        QTimer.singleShot(200, self._on_shown_settle)

    def _on_shown_settle(self):
        """표시 직후 안정화 후 포커스 폴링을 시작한다."""
        self._just_shown = False
        self._focus_poll.start(100)

    def _poll_focus(self):
        """100ms 마다 포커스 잃었는지 확인 후 잃었으면 다이얼로그를 닫는다."""
        if self._just_shown:
            return
        if not self.isActiveWindow():
            active = QApplication.activeWindow()
            if active:
                if (
                    self.isAncestorOf(active)
                    or isinstance(active, QMenu)
                    or (active.windowFlags() & Qt.WindowType.Popup)
                ):
                    return
            self.reject()


# ── 클립보드 카드 ──────────────────────────────────────────────────────────────
class ClipCard(QFrame):
    """저장된 클립보드 항목 하나를 나타내는 카드 위젯."""

    deleted = pyqtSignal(object)
    pinned_changed = pyqtSignal(object, bool)
    request_copy = pyqtSignal(object)

    def __init__(
        self,
        mode: str,
        filepath: str,
        filename: str,
        time_str: str,
        pixmap: QPixmap = None,
        text_snippet: str = "",
        card_height: int = CARD_HEIGHT_IMG,
        meta: str = "",
        thumb_px: QPixmap = None,
    ):
        super().__init__()
        self.setObjectName("clipCard")
        self.filepath = filepath
        self.mode = mode
        self.content_hash = ""
        self.pinned = False
        self._drag_start = None
        self.setFixedHeight(card_height)
        
        # ID 선택자를 사용하여 자식 위젯으로 스타일이 상속되지 않도록 제한합니다.
        self._base_style = f"#clipCard {{ background:{BG3}; border-radius:4px; border:2px solid transparent; }}"
        self._highlight_style = f"#clipCard {{ background:{BG3}; border-radius:4px; border:2px solid {ACCENT}; }}"
        self.setStyleSheet(self._base_style)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)
        MEDIA_H = card_height - 8

        if mode == "image" and pixmap:
            self.media = QLabel()
            # 메인 스레드에서의 무거운 리사이징 연산 제거, 워커에서 넘겨준 썸네일 우선 사용
            if thumb_px:
                thumb = thumb_px
            else:
                thumb = pixmap.scaled(
                    200, MEDIA_H,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            self.media.setPixmap(thumb)
            self.media.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.media.setStyleSheet(f"background:{BG2}; border-radius:2px;")
        else:
            self.media = QTextEdit()
            self.media.setPlainText(text_snippet)  # HTML 자동 인식 방지 (줄바꿈 유지)
            self.media.setReadOnly(True)
            self.media.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)  # 가로 스크롤 방지, 자동 줄바꿈
            self.media.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # 가로 스크롤 숨김
            self.media.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.media.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

            # VS Code 스타일 폰트 적용 및 탭 거리 4칸 정밀 설정
            font = QFont("Consolas", 11)
            font.setStyleHint(QFont.StyleHint.Monospace)
            self.media.setFont(font)
            self.media.setTabStopDistance(self.media.fontMetrics().horizontalAdvance(" ") * 4)

            self.media.setStyleSheet(f"""
                QTextEdit {{
                    background: #1e1e1e;
                    color: #d4d4d4;
                    font-family: Consolas, Menlo, 'D2Coding', monospace;
                    padding: 8px 6px;
                    border: none;
                    border-radius: 2px;
                }}
            """)
            self.media.installEventFilter(self)
        self.media.setFixedHeight(MEDIA_H)

        badge = QLabel("IMG" if mode == "image" else "TXT", self.media)
        badge.setStyleSheet(
            f"background:{ACCENT if mode == 'image' else '#6b7280'}; color:white;"
            f" font-size:10px; font-weight:bold; padding:1px 4px; border-radius:2px; border:none;"
        )
        badge.adjustSize()
        # 카드의 하단(MEDIA_H) 기준으로 라벨 위치(Y축) 계산
        badge_y = MEDIA_H - badge.height() - 4
        badge.move(4, badge_y)
        badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        badge.show()

        if meta:
            meta_lbl = QLabel(meta, self.media)
            meta_lbl.setStyleSheet(
                f"background:{ACCENT if mode == 'image' else '#6b7280'}; color:white;"
                f" font-size:10px; padding:1px 4px; border-radius:2px; border:none;"
            )
            meta_lbl.adjustSize()
            # 첫 번째 뱃지 바로 우측에 가로로 이어서 배치
            meta_lbl.move(badge.x() + badge.width() + 4, badge_y)
            meta_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            meta_lbl.show()

        row.addWidget(self.media, stretch=1)

        # 버튼 크기를 카드 높이에 따라 동적으로 결정
        BTN = max(20, min(26, (card_height - 16) // 4))
        self._btn_size = BTN
        btn_col = QWidget()
        btn_col.setStyleSheet(f"background:{BG3};")
        btn_v = QVBoxLayout(btn_col)
        btn_v.setContentsMargins(0, 0, 0, 0)
        btn_v.setSpacing(4)

        copy_btn = QPushButton("⧉")
        copy_btn.setFixedSize(BTN, BTN)
        copy_btn.setToolTip("클립보드에 복사")
        copy_btn.setStyleSheet(
            f"QPushButton {{ background:{LINE}; color:{GRAY}; border:none; font-size:{BTN - 8}px;"
            f" border-radius:4px; }}"
            f" QPushButton:hover {{ background:{ACCENT}; color:white; }}"
        )
        copy_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        copy_btn.clicked.connect(lambda: self.request_copy.emit(self))

        self.edit_btn = QPushButton("✎")
        self.edit_btn.setFixedSize(BTN, BTN)
        self.edit_btn.setToolTip("수정하기")
        self.edit_btn.setStyleSheet(
            f"QPushButton {{ background:{LINE}; color:{GRAY}; border:none; font-size:{BTN - 6}px;"
            f" border-radius:4px; }}"
            f" QPushButton:hover {{ background:{ACCENT}; color:white; }}"
        )
        self.edit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.edit_btn.clicked.connect(self._on_edit_clicked)

        self.pin_btn = QPushButton()
        self.pin_btn.setFixedSize(BTN, BTN)
        self.pin_btn.setCheckable(True)
        self.pin_btn.setIcon(make_pin_icon(BTN - 6, False))
        self.pin_btn.setIconSize(QSize(BTN - 6, BTN - 6))
        self.pin_btn.setStyleSheet(
            f"QPushButton {{ background:{LINE}; border:none; border-radius:4px; }}"
            f" QPushButton:checked {{ background:{ACCENT}; }}"
        )
        self.pin_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.pin_btn.toggled.connect(
            lambda c: (
                setattr(self, "pinned", c),
                self.pin_btn.setIcon(make_pin_icon(self._btn_size - 6, c)),
                self.pinned_changed.emit(self, c),
            )
        )

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(BTN, BTN)
        del_btn.setToolTip("삭제")
        del_btn.setStyleSheet(
            f"QPushButton {{ background:{LINE}; color:{GRAY}; border:none; font-size:{BTN - 10}px;"
            f" border-radius:4px; }}"
            f" QPushButton:hover {{ background:#cc4444; color:white; }}"
        )
        del_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        del_btn.clicked.connect(lambda: self.deleted.emit(self) if not self.pinned else None)

        btn_v.addWidget(copy_btn)
        btn_v.addWidget(self.edit_btn)
        btn_v.addWidget(self.pin_btn)
        btn_v.addWidget(del_btn)
        btn_v.addStretch()
        row.addWidget(btn_col, alignment=Qt.AlignmentFlag.AlignTop)

    def _on_edit_clicked(self):
        """편집 버튼 클릭 시 이미지는 DrawingDialog, 텍스트는 TextEditDialog를 연다."""
        if self.mode == "image":
            px = QPixmap(self.filepath)
            dlg = DrawingDialog(px)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                QApplication.clipboard().setPixmap(dlg.pixmap)
        else:
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    old_text = f.read()
            except Exception:
                old_text = ""

            dlg = TextEditDialog(old_text)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                new_text = dlg.get_text()
                if dlg.save_mode == "new":
                    QApplication.clipboard().setText(new_text)
                else:
                    with open(self.filepath, "w", encoding="utf-8") as f:
                        f.write(new_text)
                    al = new_text.splitlines()
                    ch = max(CARD_TEXT_MIN, min(CARD_TEXT_MAX, 8 + len(al) * CARD_LINE_H + 8))
                    sn = "\n".join(al[:(ch - 16) // CARD_LINE_H])
                    self.media.setPlainText(sn)  # HTML 자동 인식 방지

    def eventFilter(self, obj, event):
        """텍스트 위젯의 마우스 이벤트를 카드 수준으로 중계한다."""
        t = event.type()
        if t == QEvent.Type.MouseButtonPress:
            self.mousePressEvent(event)
        elif t == QEvent.Type.MouseMove:
            self.mouseMoveEvent(event)
        elif t == QEvent.Type.MouseButtonRelease:
            self.mouseReleaseEvent(event)
        return False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton) or not self._drag_start:
            return
        if (
            (event.globalPosition().toPoint() - self._drag_start).manhattanLength() < 10
            or not os.path.exists(self.filepath)
        ):
            return
        drag = QDrag(self)
        mime = QMimeData()
        if self.mode == "text":
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    mime.setText(f.read())
            except Exception:
                pass
        else:
            mime.setUrls([QUrl.fromLocalFile(self.filepath)])
        drag.setMimeData(mime)
        if self.mode == "image":
            px = QPixmap(self.filepath)
            if not px.isNull():
                thumb = px.scaled(
                    80, 56,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                drag.setPixmap(thumb)
                drag.setHotSpot(QPoint(thumb.width() // 2, thumb.height() // 2))
        drag.exec(Qt.DropAction.CopyAction)
        self._drag_start = None

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start:
            # 드래그가 아닌 제자리 클릭으로 판단되면 복사 + 붙여넣기 실행
            if (event.globalPosition().toPoint() - self._drag_start).manhattanLength() < 10:
                self.request_copy.emit(self)
        self._drag_start = None

# ── 토스트 알림 팝업 ──────────────────────────────────────────────────────────
class ToastItem(QWidget):
    """개별 토스트 알림 카드. 드래그로 내용을 복사할 수 있다."""

    TOAST_W = 260
    MARGIN   = 8
    GAP      = 6

    closed = pyqtSignal(object)   # 닫힐 때 자신을 매니저에 알림

    def __init__(self, mode: str, text: str = "", pixmap: QPixmap = None):
        super().__init__()
        self.mode    = mode
        self._text   = text
        self._pixmap = pixmap
        self._drag_start = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.ToolTip  # Tool 대신 ToolTip을 사용하여 Windows OS의 강제 최소 높이 제약 회피
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(self.TOAST_W)
        self.setStyleSheet(f"""
            ToastItem {{
                background:{BG3};
                border:1px solid {LINE};
                border-radius:6px;
            }}
        """)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_timeout)

        self._build_ui(mode, text, pixmap)
        
        # High-DPI 배율 환경에서 setFixedHeight 사용 시 발생하는 경고를 피하기 위해
        # 강제 고정 대신 1회에 한해서만 내용물에 맞춰 크기를 조절합니다.
        self.adjustSize()

    def _build_ui(self, mode, text, pixmap):
        root = QVBoxLayout(self)
        root.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)  # 레이아웃 수준에서 크기 고정 강제
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(6)

        # 헤더
        hl = QHBoxLayout()
        hl.setContentsMargins(0, 0, 0, 0)
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{ACCENT}; font-size:8px;")
        lbl = QLabel("클립보드 복사됨")
        lbl.setStyleSheet(f"color:{ACCENT}; font-size:9px; font-weight:bold;")
        hint = QLabel("드래그하여 붙여넣기")
        hint.setStyleSheet(f"color:{GRAY}; font-size:9px;")
        hl.addWidget(dot)
        hl.addWidget(lbl)
        hl.addStretch()
        hl.addWidget(hint)
        root.addLayout(hl)

        # 구분선
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"background:{LINE}; max-height:1px;")
        root.addWidget(line)

        if mode == "text":
            lines = text.splitlines()
            preview = "\n".join(lines[:5])
            if len(lines) > 5 or len(preview) > 220:
                preview = preview[:220].rstrip() + " …"
            content = QLabel(preview)
            content.setWordWrap(True)
            content.setStyleSheet("""
                color:#d4d4d4;
                font-family: Consolas, monospace;
                font-size:11px;
                background:#1e1e1e;
                border-radius:3px;
                padding:6px;
            """)
            content.setMaximumHeight(90)
            content.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            root.addWidget(content)
        else:
            thumb = pixmap.scaled(
                238, 80,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            content = QLabel()
            content.setPixmap(thumb)
            content.setAlignment(Qt.AlignmentFlag.AlignCenter)
            content.setStyleSheet("background:#1e1e1e; border-radius:3px; padding:4px;")
            content.setFixedHeight(thumb.height() + 8)
            root.addWidget(content)

    # ── 드래그 복사 ──────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.globalPosition().toPoint()

    def mouseMoveEvent(self, e):
        if not self._drag_start or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        if (e.globalPosition().toPoint() - self._drag_start).manhattanLength() < 8:
            return
        drag = QDrag(self)
        mime = QMimeData()
        if self.mode == "text":
            mime.setText(self._text)
        else:
            mime.setImageData(self._pixmap.toImage())
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)
        self._drag_start = None

    def mouseReleaseEvent(self, e):
        self._drag_start = None

    # ── 타이머 / 닫기 ────────────────────────────────────────────────────────────
    def start_timer(self, ms: int = 3000):
        self._hide_timer.start(ms)

    def _on_timeout(self):
        self.hide()
        self.closed.emit(self)


class ToastManager:
    """여러 ToastItem을 우하단에 위로 쌓아 표시하는 관리자."""

    MARGIN = 8
    GAP    = 6

    def __init__(self):
        self._items: list[ToastItem] = []

    def show_text(self, text: str):
        self._add(ToastItem("text", text=text))

    def show_image(self, pixmap: QPixmap):
        self._add(ToastItem("image", pixmap=pixmap))

    def _add(self, item: ToastItem):
        item.closed.connect(self._on_closed)
        self._items.append(item)
        self._reposition()
        item.show()
        item.raise_()
        item.start_timer(7000)

    def _on_closed(self, item: ToastItem):
        if item in self._items:
            self._items.remove(item)
        item.deleteLater()
        self._reposition()

    def _reposition(self):
        """쌓인 순서대로 우하단에서 위쪽으로 배치한다."""
        screen = QApplication.primaryScreen().availableGeometry()
        y = screen.bottom() - self.MARGIN
        for item in reversed(self._items):
            # item.adjustSize()  <-- 반복적인 사이즈 재계산을 방지하기 위해 삭제 (이미 고정됨)
            y -= item.height()
            x = screen.right() - item.width() - self.MARGIN
            item.move(x, y)
            y -= self.GAP


# ── 콘텐츠 팝업 ───────────────────────────────────────────────────────────────
class ContentPopup(QWidget):
    """단축키를 누를 때 커서 근처에 표시되는 클립보드 히스토리 팝업 창."""

    # 팝업에서 발생한 카드 변경을 MainWindow에 알리는 신호
    card_deleted_signal = pyqtSignal(str)   # filepath
    card_pinned_signal  = pyqtSignal(str, bool)  # filepath, is_pinned

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus  # 팝업이 포커스를 빼앗지 않음
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)  # 활성화 없이 표시
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(f"background:{BG};")
        self.cards: list[ClipCard] = []
        self.auto_close = True
        self._anchor_x = 0
        self._anchor_y = 0
        self._drag_pos = None
        self._is_manually_moved = False  # 사용자가 직접 창을 옮겼는지 추적
        self._just_shown = False
        self._focus_poll = QTimer()
        self._focus_poll.timeout.connect(self._poll_focus)
        self._build_ui()

    def _build_ui(self):
        """팝업 UI(핀 버튼 헤더 + 카드 스크롤 영역)를 구성한다."""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(28)
        header.setStyleSheet(f"background:{TITLE};")

        # 팝업 상단(헤더)을 드래그하여 이동할 수 있도록 이벤트 연결
        header.mousePressEvent = self._header_mouse_press
        header.mouseMoveEvent = self._header_mouse_move
        header.mouseReleaseEvent = self._header_mouse_release

        hl = QHBoxLayout(header)
        hl.setContentsMargins(10, 0, 6, 0)
        hl.addStretch()

        self.ac_btn = QPushButton()
        self.ac_btn.setCheckable(True)
        self.ac_btn.setChecked(not self.auto_close)
        self.ac_btn.setFixedSize(22, 20)
        self.ac_btn.setIcon(make_pin_icon(14, self.ac_btn.isChecked()))
        self.ac_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; border:none; border-radius:3px; }}"
            f" QPushButton:checked {{ background:{BG3}; }}"
            f" QPushButton:hover {{ background:{LINE}; }}"
        )
        self.ac_btn.toggled.connect(self._on_pin_toggled)
        self.ac_btn.setToolTip("자동 닫기 해제 (화면 고정)")
        hl.addWidget(self.ac_btn)
        root.addWidget(header)

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet(f"background:{BG};")
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(8, 6, 8, 6)
        self.cards_layout.setSpacing(CARD_SPACING)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            f"QScrollArea {{ border:none; background:{BG}; }}"
            f" QScrollBar:vertical {{ background:{BG3}; width:4px; }}"
            f" QScrollBar::handle:vertical {{ background:{GRAY}; border-radius:2px; min-height:16px; }}"
        )
        self.scroll_area.setWidget(self.cards_container)
        root.addWidget(self.scroll_area)

    def _header_mouse_press(self, e):
        """헤더 드래그 시작"""
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _header_mouse_move(self, e):
        """헤더 드래그 중 창 이동"""
        if e.buttons() == Qt.MouseButton.LeftButton and getattr(self, "_drag_pos", None):
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def _header_mouse_release(self, e):
        """헤더 드래그 완료 시 수동 이동 상태 기록"""
        if getattr(self, "_drag_pos", None):
            self._drag_pos = None
            self._is_manually_moved = True

    def _on_pin_toggled(self, checked):
        """핀 버튼 토글 시 자동 닫기 여부를 전환한다."""
        self.auto_close = not checked
        self.ac_btn.setIcon(make_pin_icon(14, checked))
        
        # 핀이 해제되어 자동 닫기가 켜지고 창이 표시 중이라면 폴링 타이머를 다시 시작한다.
        if self.auto_close and self.isVisible():
            self._focus_poll.start(80)
        else:
            self._focus_poll.stop()

    def changeEvent(self, event):
        """WindowDoesNotAcceptFocus 플래그로 포커스를 받지 않으므로 ActivationChange 미사용."""
        super().changeEvent(event)

    def _maybe_hide(self):
        """활성 창이 다이얼로그가 아닌 경우에만 팝업을 숨긴다."""
        top_window = QApplication.activeWindow()
        if isinstance(top_window, QDialog):
            return
        if not self._just_shown:
            self._focus_poll.stop()
            self.hide()

    def _poll_focus(self):
        """팝업 영역 밖을 클릭했는지 감지하여 자동으로 숨긴다.

        WindowDoesNotAcceptFocus 플래그로 포커스를 받지 않으므로,
        activeWindow 대신 마우스 버튼 상태 + 커서 위치로 닫기 여부를 판단한다.
        """
        if not self.isVisible() or not self.auto_close:
            self._focus_poll.stop()
            return
        if self._just_shown:
            return
        # 마우스 버튼이 눌렸고 팝업 영역 밖이면 닫기
        left_btn = bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)  # VK_LBUTTON
        if left_btn and not self.geometry().contains(QCursor.pos()):
            self._focus_poll.stop()
            self.hide()

    def add_card(
        self,
        mode,
        fpath,
        fname,
        pixmap=None,
        text_snippet="",
        card_height=CARD_HEIGHT_IMG,
        time_str=None,
        _resize=True,
        meta="",
        thumb_px=None,
    ) -> ClipCard:
        """새 클립 카드를 팝업에 추가하고 필요 시 크기를 재조정한다."""
        ts = time_str or datetime.datetime.now().strftime("%H:%M:%S")
        card = ClipCard(mode, fpath, fname, ts, pixmap, text_snippet, card_height, meta, thumb_px)
        card.deleted.connect(self._on_card_delete)
        card.pinned_changed.connect(self._on_pin_changed)
        pinned = sum(1 for c in self.cards if c.pinned)
        self.cards_layout.insertWidget(pinned, card)
        self.cards.insert(pinned, card)
        self._trim_cards()
        if _resize and self.isVisible():
            self._resize_popup()
        return card

    def get_card_by_path(self, filepath):
        return next((c for c in self.cards if c.filepath == filepath), None)

    def move_card_to_top(self, card):
        """카드를 고정 카드 바로 아래(최상단 비고정 위치)로 이동시킨다."""
        if card not in self.cards:
            return
        self.cards.remove(card)
        self.cards_layout.removeWidget(card)
        pinned = sum(1 for c in self.cards if c.pinned)
        self.cards_layout.insertWidget(pinned, card)
        self.cards.insert(pinned, card)

    def _on_pin_changed(self, card, is_pinned):
        """카드가 고정되면 목록 최상단으로 이동시키고 MainWindow에 알린다."""
        self.card_pinned_signal.emit(card.filepath, is_pinned)
        if not is_pinned:
            return
        self.cards.remove(card)
        self.cards_layout.removeWidget(card)
        self.cards_layout.insertWidget(0, card)
        self.cards.insert(0, card)

    def _on_card_delete(self, card):
        """카드 삭제 요청 처리 후 팝업 크기를 재조정하고 MainWindow에 알린다."""
        self.card_deleted_signal.emit(card.filepath)
        if card in self.cards:
            self.cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        QTimer.singleShot(0, self._resize_popup)

    def _trim_cards(self):
        """MAX_CARDS를 초과하면 고정되지 않은 가장 오래된 카드를 제거한다."""
        while len(self.cards) > MAX_CARDS:
            for i in range(len(self.cards) - 1, -1, -1):
                if not self.cards[i].pinned:
                    c = self.cards.pop(i)
                    self.cards_layout.removeWidget(c)
                    c.setParent(None)
                    c.deleteLater()
                    break

    def show_at_cursor(self):
        """현재 커서 위치를 기준으로 팝업을 표시한다. 기존 포커스는 유지된다."""
        # 팝업을 열기 전 포커스를 가진 창의 HWND를 저장 → 붙여넣기 대상으로 사용
        self._prev_hwnd = ctypes.windll.user32.GetForegroundWindow()
        pos = QCursor.pos()
        
        # 커서가 위치한 모니터 화면의 영역을 가져옴 (멀티 모니터 지원)
        screen_obj = QApplication.screenAt(pos) or QApplication.primaryScreen()
        screen = screen_obj.availableGeometry()
        
        self._anchor_x = max(screen.left(), min(pos.x(), screen.right() - WIN_W))
        self._anchor_y = pos.y()
        self._is_manually_moved = False  # 새 커서 위치에서 열리므로 수동 이동 플래그 초기화
        self._resize_popup()
        self._focus_poll.stop()
        self._just_shown = True
        self.show()
        self.raise_()
        # activateWindow() 제거 — 포커스를 빼앗지 않음
        QTimer.singleShot(200, self._on_shown_settle)

    def _on_shown_settle(self):
        """표시 직후 안정화 후 포커스 폴링을 시작한다."""
        self._just_shown = False
        if self.isVisible() and self.auto_close:
            self._focus_poll.start(80)

    def _resize_popup(self):
        """카드 수와 높이에 따라 팝업 창 크기를 동적으로 재계산한다.
        최대 3개의 카드 높이까지만 창이 커지고, 그 이상은 스크롤되도록 제한한다."""
        n = len(self.cards)
        visible_count = min(n, 3)
        
        if visible_count > 0:
            visible_cards_h = sum(self.cards[i].height() for i in range(visible_count))
            content_h = 12 + visible_cards_h + (visible_count - 1) * CARD_SPACING
        else:
            content_h = 40
            
        ideal_h = content_h + 32
        
        if self._is_manually_moved:
            # 사용자가 수동으로 창을 옮긴 상태라면, 이동된 위치를 유지하며 크기만 변경
            pos = self.pos()
            screen_obj = QApplication.screenAt(pos) or QApplication.primaryScreen()
            screen = screen_obj.availableGeometry()
            
            popup_h = min(ideal_h, screen.height() - 20)
            self.resize(WIN_W, popup_h)
            
            # 내용물이 늘어나 창이 화면 아래로 벗어나면 위로 끌어올림
            if self.y() + popup_h > screen.bottom() - 10:
                self.move(self.x(), screen.bottom() - popup_h - 10)
        else:
            # 앵커 좌표(최초 생성된 위치)가 위치한 모니터 화면의 영역을 가져옴
            pos = QPoint(self._anchor_x, self._anchor_y)
            screen_obj = QApplication.screenAt(pos) or QApplication.primaryScreen()
            screen = screen_obj.availableGeometry()

            space_below = screen.bottom() - self._anchor_y - 10
            space_above = self._anchor_y - screen.top() - 10

            if space_below >= min(ideal_h, 100) or space_below >= space_above:
                # 커서 아래에 표시
                popup_h = min(ideal_h, max(60, space_below))
                popup_y = self._anchor_y
            else:
                # 커서 위에 표시 (아래 공간이 부족할 때)
                popup_h = min(ideal_h, max(60, space_above))
                popup_y = self._anchor_y - popup_h

            self.resize(WIN_W, popup_h)
            self.move(self._anchor_x, popup_y)


# ── 커스텀 타이틀바 ────────────────────────────────────────────────────────────
class TitleBar(QWidget):
    """메인 창 상단에 위치하는 커스텀 타이틀바 위젯."""

    def __init__(self, win):
        super().__init__(win)
        self._win = win
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

        btn_style = (
            f"QPushButton {{ background:transparent; border:none; border-radius:3px; }}"
            f" QPushButton:hover {{ background:{LINE}; }}"
        )

        gear_btn = QPushButton()
        gear_btn.setFixedSize(22, 20)
        gear_btn.setStyleSheet(btn_style)
        gear_btn.setIcon(self._make_gear_icon())
        gear_btn.setToolTip("설정")
        gear_btn.clicked.connect(self._open_settings)

        win_min_btn = QPushButton("─")
        win_min_btn.setFixedSize(26, 20)
        win_min_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{GRAY}; border:none; font-size:11px; }}"
            f" QPushButton:hover {{ background:{BG3}; color:{ACCENT}; }}"
        )
        win_min_btn.clicked.connect(win._minimize_to_tray)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 20)
        close_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{GRAY}; border:none; }}"
            f" QPushButton:hover {{ background:#cc4444; color:white; }}"
        )
        close_btn.clicked.connect(QApplication.quit)

        l.addWidget(gear_btn)
        l.addWidget(win_min_btn)
        l.addWidget(close_btn)

    def _make_gear_icon(self):
        """설정 버튼에 사용할 기어 모양 아이콘을 생성한다."""
        import math
        px = QPixmap(14, 14)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(GRAY)
        p.setPen(QPen(c, 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(4, 4, 6, 6)
        for i in range(6):
            a = math.radians(i * 60)
            cx = 7 + math.cos(a) * 4.5
            cy = 7 + math.sin(a) * 4.5
            p.drawEllipse(int(cx) - 1, int(cy) - 1, 2, 2)
        p.end()
        return QIcon(px)

    def _open_settings(self):
        """설정 다이얼로그를 메인 창 위에 중앙 정렬하여 표시한다."""
        dlg = SettingsDialog(self._win)
        dlg.adjustSize()
        geo = self._win.geometry()
        x = geo.left() + (geo.width() - dlg.width()) // 2
        y = geo.top() - dlg.sizeHint().height() - 4
        screen = QApplication.primaryScreen().availableGeometry()
        y = max(screen.top(), y)
        dlg.move(x, y)
        dlg.exec()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            self._win.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None




# ── 메인 창 ───────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    """클립보드 매니저의 메인 창 — 카드 목록, 트레이, 단축키 폴링을 관리한다."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setWindowTitle("Clipboard Manager")
        self.setWindowIcon(make_icon())
        self.setStyleSheet(f"QMainWindow {{ background:{BG}; }}")
        self.setMinimumSize(220, FIXED_H)

        self.save_dir = SAVE_DIR
        self.current_filepath = None
        self.dot_visible = True
        self.cards: list[ClipCard] = []

        self._content_hashes = {}       # 콘텐츠 해시 → ClipCard 매핑 (중복 감지용)
        self._workers = []              # 실행 중인 ClipWorker 목록
        self._hotkey_vk = get_hotkey_vk()
        self._internal_copy_time = 0   # 내부 복사 시각 (클립보드 루프 방지용)

        self.popup = ContentPopup()
        self.popup.card_deleted_signal.connect(self._on_popup_card_deleted)
        self.popup.card_pinned_signal.connect(self._on_popup_card_pinned)
        self.toast = ToastManager()
        self.resize(WIN_W, FIXED_H)

        self._build_ui()
        self._setup_timers()
        self._setup_tray()
        self._resize_window()

    def _resize_window(self):
        """카드 합산 높이에 맞춰 메인 창을 재조정하고 오른쪽 하단에 배치한다.
        최대 3개의 카드 높이까지만 창이 커지고, 그 이상은 스크롤되도록 제한한다."""
        n = len(self.cards)
        visible_count = min(n, 3)
        
        if visible_count > 0:
            visible_cards_h = sum(self.cards[i].height() for i in range(visible_count))
            scroll_h = 12 + visible_cards_h + (visible_count - 1) * CARD_SPACING
        else:
            scroll_h = 20

        new_h = min(
            FIXED_H + scroll_h,
            QApplication.primaryScreen().availableGeometry().height() - 40,
        )
        w = max(self.width(), 220)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(w, new_h)
        self.move(screen.right() - w, screen.bottom() - new_h)

    def _build_ui(self):
        """메인 창의 전체 UI 레이아웃(타이틀바, 헤더, 카드 영역, 경로 바)을 구성한다."""
        central = QWidget()
        central.setStyleSheet(f"background:{BG};")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.info_label = QLabel()

        root.addWidget(TitleBar(self))

        hw = QWidget()
        hw.setFixedHeight(26)
        hl = QHBoxLayout(hw)
        hl.setContentsMargins(12, 0, 6, 0)
        hl.setSpacing(4)

        lbl1 = QLabel("CLIPBOARD")
        lbl1.setStyleSheet(f"color:{ACCENT}; font-weight:bold; font-size:10px;")
        lbl2 = QLabel(" MANAGER")
        lbl2.setStyleSheet(f"color:{ACCENT}; font-size:10px;")
        hl.addWidget(lbl1)
        hl.addWidget(lbl2)
        hl.addStretch()

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"color:{ACCENT}; font-size:10px;")
        monitor = QLabel("monitoring")
        monitor.setStyleSheet(f"color:{GRAY}; font-size:10px;")
        hl.addWidget(monitor)
        hl.addWidget(self.status_dot)

        clear_btn = QPushButton()
        clear_btn.setFixedSize(18, 18)
        clear_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; border:none; border-radius:3px; }}"
            f" QPushButton:hover {{ background:#b91c1c; }}"
        )
        clear_btn.setToolTip("Clear All")
        clear_btn.setIcon(self._make_trash_icon())
        clear_btn.setIconSize(QSize(13, 13))
        clear_btn.clicked.connect(self._clear_all)
        hl.addWidget(clear_btn)

        root.addWidget(hw)
        self._build_ui_cards(root)

    def _make_trash_icon(self):
        """휴지통 모양의 Clear All 버튼 아이콘 생성한다."""
        px = QPixmap(13, 13)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(GRAY), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawRoundedRect(2, 4, 9, 8, 1, 1)
        p.drawLine(1, 3, 12, 3)
        p.drawLine(4, 3, 5, 1)
        p.drawLine(5, 1, 8, 1)
        p.drawLine(8, 1, 9, 3)
        p.drawLine(5, 6, 5, 10)
        p.drawLine(8, 6, 8, 10)
        p.end()
        return QIcon(px)

    def _build_ui_cards(self, root):
        """카드 스크롤 영역과 하단 경로 표시 바를 구성한다."""
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color:{LINE}; background:{LINE};")
        line.setFixedHeight(1)
        root.addWidget(line)

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet(f"background:{BG};")
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(8, 6, 8, 6)
        self.cards_layout.setSpacing(CARD_SPACING)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            f"QScrollArea {{ border:none; background:{BG}; }}"
            f" QScrollBar:vertical {{ background:{BG3}; width:4px; }}"
            f" QScrollBar::handle:vertical {{ background:{GRAY}; border-radius:2px; min-height:16px; }}"
        )
        self.scroll_area.setWidget(self.cards_container)
        root.addWidget(self.scroll_area, stretch=1)

        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setStyleSheet(f"color:{LINE}; background:{LINE};")
        line2.setFixedHeight(1)
        root.addWidget(line2)

        pw = QWidget()
        pw.setFixedHeight(28)
        pl = QHBoxLayout(pw)
        pl.setContentsMargins(12, 4, 12, 4)
        pl.setSpacing(4)

        plbl = QLabel("PATH")
        plbl.setStyleSheet(f"color:{GRAY}; font-size:10px;")
        plbl.setFixedWidth(32)

        self.path_edit = QLineEdit(self.save_dir)
        self.path_edit.setFixedHeight(18)
        self.path_edit.setStyleSheet(
            f"QLineEdit {{ background:{BG3}; color:{GRAY}; border:1px solid {LINE};"
            f" border-radius:2px; padding:1px 4px; font-size:10px; }}"
            f" QLineEdit:focus {{ border:1px solid {ACCENT}; color:{ACCENT}; }}"
        )
        self.path_edit.editingFinished.connect(self._on_path_changed)

        # 폴더 열기 버튼 아이콘 직접 드로잉
        folder_btn = QPushButton()
        folder_btn.setFixedSize(18, 18)
        fpx = QPixmap(12, 12)
        fpx.fill(Qt.GlobalColor.transparent)
        fp = QPainter(fpx)
        fp.setPen(QPen(QColor(GRAY), 1.2))
        fp.drawRoundedRect(1, 4, 10, 6, 1, 1)
        fp.drawLine(1, 4, 1, 2)
        fp.drawLine(1, 2, 4, 2)
        fp.drawLine(4, 2, 5, 4)
        fp.end()
        folder_btn.setIcon(QIcon(fpx))
        folder_btn.setStyleSheet(
            f"QPushButton {{ background:{BG3}; border:none; border-radius:2px; }}"
            f" QPushButton:hover {{ background:{LINE}; }}"
        )
        folder_btn.clicked.connect(lambda: os.startfile(self.save_dir))

        pl.addWidget(plbl)
        pl.addWidget(self.path_edit)
        pl.addWidget(folder_btn)
        pl.addWidget(_ResizeGrip(self))
        root.addWidget(pw)

    def _on_path_changed(self):
        """저장 경로가 변경되면 디렉터리를 생성하고 self.save_dir을 업데이트한다."""
        new_dir = self.path_edit.text().strip()
        try:
            os.makedirs(new_dir, exist_ok=True)
            self.save_dir = new_dir
        except Exception:
            self.path_edit.setText(self.save_dir)

    def _setup_tray(self):
        """시스템 트레이 아이콘과 컨텍스트 메뉴를 초기화한다."""
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        show_act = QAction("열기", self)
        show_act.triggered.connect(
            lambda: (self.show(), self.raise_(), self.activateWindow(), self._resize_window())
        )
        settings_act = QAction("설정", self)
        settings_act.triggered.connect(self._open_tray_settings)
        quit_act = QAction("종료", self)
        quit_act.triggered.connect(QApplication.quit)
        menu.addAction(show_act)
        menu.addAction(settings_act)
        menu.addSeparator()
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: show_act.trigger() if r == QSystemTrayIcon.ActivationReason.DoubleClick else None
        )
        self.tray.show()

    def _open_tray_settings(self):
        """트레이 메뉴에서 설정 다이얼로그를 화면 오른쪽 하단에 열어 준다."""
        dlg = SettingsDialog(self)
        dlg.adjustSize()
        screen = QApplication.primaryScreen().availableGeometry()
        dlg.move(screen.right() - dlg.width() - 10, screen.bottom() - dlg.sizeHint().height() - 10)
        dlg.exec()

    def _setup_timers(self):
        """클립보드 감시 디바운스 타이머, 상태 점 깜빡임, 단축키 폴링 타이머를 초기화한다."""
        self.clipboard = QApplication.clipboard()

        # 클립보드 연속 발생(중복 캡처) 방지를 위한 디바운스 타이머
        self.clip_debounce = QTimer()
        self.clip_debounce.setSingleShot(True)
        self.clip_debounce.timeout.connect(self._check_clipboard)

        # 이벤트가 발생할 때마다 300ms 타이머 재시작 (마지막 이벤트 후 1번만 실행됨)
        self.clipboard.dataChanged.connect(lambda: self.clip_debounce.start(300))

        self.blink_timer = QTimer()
        self.blink_timer.timeout.connect(self._blink_dot)
        self.blink_timer.start(900)

        self._caps_was_down = False
        self._caps_toggle_state = bool(ctypes.windll.user32.GetKeyState(0x14) & 0x0001)
        self._caps_poll = QTimer()
        self._caps_poll.timeout.connect(self._poll_hotkey)
        self._caps_poll.start(50)

    def _blink_dot(self):
        """상태 표시 점을 교대로 켜고 끈다."""
        self.dot_visible = not self.dot_visible
        self.status_dot.setStyleSheet(
            f"color:{ACCENT if self.dot_visible else BG}; font-size:10px;"
        )

    def _on_hotkey_changed(self, vk: int):
        """단축키 변경 시 VK 코드를 갱신하고 상태를 초기화한다."""
        self._hotkey_vk = vk
        self._caps_was_down = False
        self._caps_toggle_state = bool(ctypes.windll.user32.GetKeyState(vk) & 0x0001)

    def _poll_hotkey(self):
        """50ms 마다 단축키 상태를 폴링하여 팝업을 토글한다.

        CapsLock(0x14): 토글 상태 변화만 감지 (물리 누름과 중복 방지)
        일반 키: 물리 누름 엣지 + 빠른 입력(0x0001 비트) 감지
        """
        vk = self._hotkey_vk

        if vk == 0x14:
            # CapsLock 전용 — 토글 ON/OFF 변화만으로 정확히 1회 감지
            toggle = bool(ctypes.windll.user32.GetKeyState(vk) & 0x0001)
            if toggle != self._caps_toggle_state:
                self._caps_toggle_state = toggle
                self.popup.hide() if self.popup.isVisible() else self.popup.show_at_cursor()
        else:
            # 일반 키: 물리 누름 엣지 감지
            state = ctypes.windll.user32.GetAsyncKeyState(vk)
            currently_down = bool(state & 0x8000)
            pressed_since_last = bool(state & 0x0001)

            if (currently_down and not self._caps_was_down) or (pressed_since_last and not currently_down):
                self.popup.hide() if self.popup.isVisible() else self.popup.show_at_cursor()

            self._caps_was_down = currently_down

    def _on_card_copy_requested(self, card: ClipCard):
        """카드의 복사 요청을 처리하여 클립보드에 설정하고 즉시 붙여넣기한다."""
        self._internal_copy_time = time.time()
        
        # 클릭된 항목(동일한 파일 경로)만 강조 표시를 유지하고, 나머지 카드의 강조 효과는 해제합니다.
        for c in self.cards + self.popup.cards:
            if c.filepath == card.filepath:
                c.setStyleSheet(c._highlight_style)
            else:
                c.setStyleSheet(c._base_style)

        if card.mode == "image":
            px = QPixmap(card.filepath)
            if not px.isNull():
                self.clipboard.setPixmap(px)
        else:
            try:
                with open(card.filepath, "r", encoding="utf-8") as f:
                    self.clipboard.setText(f.read())
            except Exception:
                pass
                
        # 팝업에서 클릭한 경우: 팝업을 닫고 Ctrl+V 전송
        if self.popup.isVisible():
            self.popup.hide()
            QTimer.singleShot(80, self._send_paste)

    def _send_paste(self):
        """현재 포커스 창에 Ctrl+V를 keybd_event로 전송한다."""
        k = ctypes.windll.user32.keybd_event
        k(0x11, 0, 0, 0)   # Ctrl 누름
        k(0x56, 0, 0, 0)   # V 누름
        k(0x56, 0, 2, 0)   # V 뗌
        k(0x11, 0, 2, 0)   # Ctrl 뗌

    def _check_clipboard(self):
        """클립보드 변경을 감지하여 이미지/텍스트를 비동기 워커에 전달한다."""
        # 디바운스 대기 시간을 고려하여 내부 복사 무시 시간을 0.5초에서 1.0초로 연장
        if time.time() - self._internal_copy_time < 1.0:
            return

        try:
            self.clipboard.blockSignals(True)
            img = self.clipboard.image()

            if not img.isNull():
                # 메인 스레드에서 즉시 해시를 계산하여 중복 감지 레이스 컨디션 완벽 차단
                # (memoryview를 활용하여 불필요한 배열 복사 비용을 없애 성능 개선)
                b = img.constBits()
                b.setsize(img.sizeInBytes())
                h = hashlib.md5(memoryview(b)).hexdigest()

                # OS 캡처 도구의 지연된 중복 이벤트는 UI 업데이트 없이 즉시 무시 (1.5초 이내)
                if (
                    h == getattr(self, "_last_hash", "")
                    and time.time() - getattr(self, "_last_hash_time", 0) < 1.5
                ):
                    return
                self._last_hash = h
                self._last_hash_time = time.time()

                if h in self._content_hashes:
                    card = self._content_hashes[h]
                    if card:  # None이면 아직 워커에서 이미지 파일 저장(인코딩) 중인 상태
                        self._move_to_top(card)
                        pop_card = self.popup.get_card_by_path(card.filepath)
                        if pop_card:
                            self.popup.move_card_to_top(pop_card)
                    return

                self._content_hashes[h] = None  # 처리 중 상태로 딕셔너리에 먼저 선점 등록
                worker = ClipWorker("image", img.copy(), self.save_dir, h)
                self._start_worker(worker)
            else:
                text = self.clipboard.text()
                if text and text.strip():
                    h = hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()

                    if (
                        h == getattr(self, "_last_hash", "")
                        and time.time() - getattr(self, "_last_hash_time", 0) < 1.5
                    ):
                        return
                    self._last_hash = h
                    self._last_hash_time = time.time()

                    if h in self._content_hashes:
                        card = self._content_hashes[h]
                        if card:
                            self._move_to_top(card)
                            pop_card = self.popup.get_card_by_path(card.filepath)
                            if pop_card:
                                self.popup.move_card_to_top(pop_card)
                        return

                    self._content_hashes[h] = None
                    worker = ClipWorker("text", text, self.save_dir, h)
                    self._start_worker(worker)

        except Exception:
            pass
        finally:
            self.clipboard.blockSignals(False)

    def _start_worker(self, worker):
        """ClipWorker를 시작하고 완료/에러 시그널을 연결한다."""
        worker.finished_new.connect(self._on_worker_new)
        worker.finished_err.connect(lambda h: self._content_hashes.pop(h, None))
        self._workers.append(worker)
        worker.finished.connect(
            lambda w=worker: self._workers.remove(w) if w in self._workers else None
        )
        worker.start()

    def _on_worker_new(self, mode, h, fpath, fname, meta, data, thumb_data):
        """워커가 파일 저장을 완료하면 메인 창과 팝업에 카드를 추가하고 토스트를 띄운다."""
        self.current_filepath = fpath
        if mode == "image":
            px = QPixmap.fromImage(data)
            thumb_px = QPixmap.fromImage(thumb_data) if thumb_data else None
            self._add_card("image", fpath, fname, pixmap=px, card_height=CARD_HEIGHT_IMG, content_hash=h, meta=meta, thumb_px=thumb_px)
            pop_card = self.popup.add_card("image", fpath, fname, pixmap=px, card_height=CARD_HEIGHT_IMG, meta=meta, thumb_px=thumb_px)
            self.toast.show_image(px)
        else:
            al = data.splitlines()
            ch = max(CARD_TEXT_MIN, min(CARD_TEXT_MAX, 8 + len(al) * CARD_LINE_H + 8))
            sn = "\n".join(al[:(ch - 16) // CARD_LINE_H])
            self._add_card("text", fpath, fname, text_snippet=sn, card_height=ch, content_hash=h, meta=meta)
            pop_card = self.popup.add_card("text", fpath, fname, text_snippet=sn, card_height=ch, meta=meta)
            self.toast.show_text(data)

        pop_card.request_copy.connect(self._on_card_copy_requested)

    def _add_card(
        self,
        mode,
        fpath,
        fname,
        pixmap=None,
        text_snippet="",
        card_height=CARD_HEIGHT_IMG,
        time_str=None,
        content_hash="",
        _resize=True,
        meta="",
        thumb_px=None,
    ) -> ClipCard:
        """메인 창 카드 목록에 새 ClipCard를 추가하고 레이아웃을 갱신한다."""
        ts = time_str or datetime.datetime.now().strftime("%H:%M:%S")
        card = ClipCard(mode, fpath, fname, ts, pixmap, text_snippet, card_height, meta, thumb_px)
        card.content_hash = content_hash
        card.deleted.connect(self._on_card_delete)
        card.pinned_changed.connect(self._on_pin_changed)
        card.request_copy.connect(self._on_card_copy_requested)

        pinned = sum(1 for c in self.cards if c.pinned)
        self.cards_layout.insertWidget(pinned, card)
        self.cards.insert(pinned, card)
        if content_hash:
            self._content_hashes[content_hash] = card
        self._trim_cards()
        if _resize:
            self._resize_window()
        return card

    def _on_pin_changed(self, card, is_pinned):
        """카드가 고정되면 목록 최상단으로 이동시키고 팝업 카드에도 반영한다."""
        if is_pinned:
            self._move_to_top(card)
        # 팝업의 동일 카드 핀 상태 동기화
        pop_card = next((c for c in self.popup.cards if c.filepath == card.filepath), None)
        if pop_card and pop_card.pinned != is_pinned:
            pop_card.pinned = is_pinned
            pop_card.pin_btn.setChecked(is_pinned)

    def _move_to_top(self, card):
        """카드를 고정 카드 바로 아래 최상단으로 이동시키고 창을 재조정한다."""
        if card not in self.cards:
            return
        self.cards.remove(card)
        self.cards_layout.removeWidget(card)
        pinned = sum(1 for c in self.cards if c.pinned)
        self.cards_layout.insertWidget(pinned, card)
        self.cards.insert(pinned, card)
        self._resize_window()

    def _on_card_delete(self, card):
        """카드 삭제 처리 후 해시 맵, 팝업, 창 크기를 갱신한다."""
        if card.content_hash in self._content_hashes:
            del self._content_hashes[card.content_hash]
        if self.current_filepath == card.filepath:
            self.current_filepath = None
        if card in self.cards:
            self.cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        # 팝업의 동일 카드도 제거
        self._remove_popup_card_by_filepath(card.filepath)
        QTimer.singleShot(0, self._resize_window)

    def _remove_popup_card_by_filepath(self, filepath: str):
        """팝업에서 filepath에 해당하는 카드를 찾아 제거한다."""
        pop_card = next((c for c in self.popup.cards if c.filepath == filepath), None)
        if pop_card:
            self.popup.cards.remove(pop_card)
            self.popup.cards_layout.removeWidget(pop_card)
            pop_card.setParent(None)
            pop_card.deleteLater()
            if self.popup.isVisible():
                QTimer.singleShot(0, self.popup._resize_popup)

    def _on_popup_card_deleted(self, filepath: str):
        """팝업에서 카드가 삭제되면 MainWindow의 동일 카드도 제거한다."""
        card = next((c for c in self.cards if c.filepath == filepath), None)
        if card:
            if card.content_hash in self._content_hashes:
                del self._content_hashes[card.content_hash]
            if self.current_filepath == filepath:
                self.current_filepath = None
            self.cards.remove(card)
            self.cards_layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
            QTimer.singleShot(0, self._resize_window)

    def _on_popup_card_pinned(self, filepath: str, is_pinned: bool):
        """팝업에서 핀 상태가 변경되면 MainWindow의 카드에도 반영한다."""
        card = next((c for c in self.cards if c.filepath == filepath), None)
        if card and card.pinned != is_pinned:
            card.pinned = is_pinned
            card.pin_btn.setChecked(is_pinned)
            if is_pinned:
                self._move_to_top(card)

    def _clear_all(self):
        """고정되지 않은 모든 카드를 메인 창과 팝업에서 제거한다."""
        to_remove = [c for c in self.cards if not c.pinned]
        for card in to_remove:
            if card.content_hash in self._content_hashes:
                del self._content_hashes[card.content_hash]
            self.cards.remove(card)
            self.cards_layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
            self._remove_popup_card_by_filepath(card.filepath)

        self._resize_window()

    def _trim_cards(self):
        """MAX_CARDS를 초과하면 고정되지 않은 가장 오래된 카드를 제거한다."""
        while len(self.cards) > MAX_CARDS:
            for i in range(len(self.cards) - 1, -1, -1):
                if not self.cards[i].pinned:
                    c = self.cards.pop(i)
                    if c.content_hash in self._content_hashes:
                        del self._content_hashes[c.content_hash]
                    self.cards_layout.removeWidget(c)
                    c.setParent(None)
                    c.deleteLater()
                    break

    def showEvent(self, event):
        """창이 표시될 때 타이틀바 색상을 다크 테마로 적용한다."""
        super().showEvent(event)
        QTimer.singleShot(200, lambda: set_titlebar_color(int(self.winId()), TITLE))

    def _minimize_to_tray(self):
        """창을 숨기고 트레이 알림을 표시한다."""
        self.hide()
        self.tray.showMessage(
            "Clipboard Manager",
            "트레이에서 실행 중입니다.",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )


def main():
    """애플리케이션 진입점 — QApplication을 초기화하고 메인 창을 실행한다."""
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")

    # 다크 팔레트 설정
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
    # 시작 최소화 설정에 따라 트레이로 바로 숨김 처리
    QTimer.singleShot(0, lambda: (win.show(), win.hide()))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()