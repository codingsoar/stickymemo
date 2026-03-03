# -*- coding: utf-8 -*-
"""
StickyMemo - PySide6 버전
현대적 UI + 한글 완벽 지원
"""

import sys
import os
import json
import uuid
import re
from datetime import datetime
import winreg
import ctypes
from ctypes import windll, c_int, c_long, byref

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QLabel, QFrame, QDialog, QScrollArea, QMenu,
    QSystemTrayIcon, QSizeGrip, QSlider, QMessageBox, QLineEdit,
    QStyle
)
from PySide6.QtCore import Qt, QPoint, QTimer, Signal, QSize, QRect
from PySide6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QFont, QAction,
    QTextCharFormat, QTextCursor, QBrush, QPen, QCursor,
    QImage, QTextImageFormat
)

# --- 전역 설정 ---
SAVE_DIR = os.path.join(os.path.expanduser("~"), "Documents", "StickyM")
SAVE_FILE = os.path.join(SAVE_DIR, "notes_db.json")
SLOTS_FILE = os.path.join(SAVE_DIR, "slots_db.json")
IMAGES_DIR = os.path.join(SAVE_DIR, "images")
os.makedirs(SAVE_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)

# 메모 색상 팔레트
NOTE_COLORS = [
    {"name": "노랑", "bg": "#FEF9C3", "border": "#FACC15", "text": "#713F12"},
    {"name": "핑크", "bg": "#FCE7F3", "border": "#EC4899", "text": "#831843"},
    {"name": "파랑", "bg": "#DBEAFE", "border": "#3B82F6", "text": "#1E3A8A"},
    {"name": "초록", "bg": "#DCFCE7", "border": "#22C55E", "text": "#14532D"},
    {"name": "보라", "bg": "#EDE9FE", "border": "#8B5CF6", "text": "#4C1D95"},
    {"name": "오렌지", "bg": "#FFEDD5", "border": "#F97316", "text": "#7C2D12"},
]



def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def html_to_plain(html_content):
    """HTML 콘텐츠에서 plain text만 추출 (Qt 엔진 사용)"""
    if not html_content:
        return ""
    from PySide6.QtGui import QTextDocument
    doc = QTextDocument()
    doc.setHtml(html_content)
    return doc.toPlainText().strip()


class ImageTextEdit(QTextEdit):
    """이미지 붙여넣기를 지원하는 커스텀 QTextEdit"""
    
    def canInsertFromMimeData(self, source):
        """이미지 MIME 데이터 허용"""
        if source.hasImage():
            return True
        if source.hasUrls():
            for url in source.urls():
                path = url.toLocalFile().lower()
                if path.endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp')):
                    return True
        return super().canInsertFromMimeData(source)
    
    def insertFromMimeData(self, source):
        """클립보드에서 이미지가 있으면 이미지를 삽입"""
        image = None
        
        # 1. 클립보드에서 직접 이미지 가져오기 (스크린샷, 복사된 이미지)
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage):
                pass  # 이미 QImage
            elif image is not None:
                image = QImage(image)
            
            # 여전히 실패하면 클립보드에서 직접 가져오기
            if image is None or (isinstance(image, QImage) and image.isNull()):
                clipboard = QApplication.clipboard()
                image = clipboard.image()
        
        # 2. 파일 URL로 이미지 붙여넣기 (탐색기에서 복사한 파일)
        if (image is None or (isinstance(image, QImage) and image.isNull())) and source.hasUrls():
            for url in source.urls():
                path = url.toLocalFile()
                if path.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp')):
                    image = QImage(path)
                    if not image.isNull():
                        break
        
        # 이미지가 유효하면 삽입
        if image is not None and isinstance(image, QImage) and not image.isNull():
            self._insert_image(image)
            return
        
        # 이미지가 아니면 기본 동작 (텍스트 붙여넣기)
        super().insertFromMimeData(source)
    
    def _insert_image(self, image):
        """QImage를 파일로 저장하고 문서에 삽입"""
        # 고유 파일명으로 이미지 저장
        img_name = f"img_{uuid.uuid4().hex[:8]}.png"
        img_path = os.path.join(IMAGES_DIR, img_name)
        image.save(img_path, "PNG")
        
        # 이미지가 너무 크면 위젯 너비에 맞게 축소 (표시용)
        max_width = self.viewport().width() - 20
        display_w = image.width()
        display_h = image.height()
        if display_w > max_width:
            ratio = max_width / display_w
            display_w = max_width
            display_h = int(display_h * ratio)
        
        # 문서에 이미지 리소스 등록 후 삽입
        cursor = self.textCursor()
        doc = self.document()
        img_url = f"file:///{img_path.replace(os.sep, '/')}"
        doc.addResource(1, img_url, image)
        
        img_fmt = QTextImageFormat()
        img_fmt.setName(img_url)
        img_fmt.setWidth(display_w)
        img_fmt.setHeight(display_h)
        cursor.insertImage(img_fmt)


class SlotWidget(QWidget):
    """화면에 표시되는 슬롯 (편집 모드에서만 조작 가능)"""
    
    removed = Signal(object)
    added = Signal(object, str) # (ref_widget, direction)
    
    def __init__(self, rect=None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        if rect:
            self.setGeometry(rect)
        else:
            self.setGeometry(200, 200, 380, 400)
            
        self.is_editing = True
        self._drag_pos = None
        
        # 리사이즈 그립
        self.grip = QSizeGrip(self)
        self.grip.setStyleSheet("background: transparent;")
        self.grip.setFixedSize(20, 20)
        
        # 버튼들 생성
        self._setup_buttons()

    def _setup_buttons(self):
        # 스타일
        add_style = """
            QPushButton { 
                background: rgba(0, 120, 215, 0.6); 
                border-radius: 15px; 
                color: white; 
                border: none; 
                font-weight: bold;
                font-size: 16px;
            }
            QPushButton:hover { background: rgba(0, 120, 215, 0.9); }
        """
        del_style = """
            QPushButton { 
                background: rgba(255, 0, 0, 0.6); 
                border-radius: 12px; 
                color: white; 
                border: none; 
            }
            QPushButton:hover { background: rgba(255, 0, 0, 0.9); }
        """
        
        # 4방향 추가 버튼
        self.btn_top = QPushButton("➕", self)
        self.btn_bottom = QPushButton("➕", self)
        self.btn_left = QPushButton("➕", self)
        self.btn_right = QPushButton("➕", self)
        
        for btn, direction in [
            (self.btn_top, "top"), 
            (self.btn_bottom, "bottom"),
            (self.btn_left, "left"),
            (self.btn_right, "right")
        ]:
            btn.setFixedSize(30, 30)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(add_style)
            # 클로저 문제 방지를 위해 기본값 인자 사용
            btn.clicked.connect(lambda _, d=direction: self.added.emit(self, d))
            
        # 삭제 버튼 (우상단)
        self.del_btn = QPushButton("✕", self)
        self.del_btn.setFixedSize(24, 24)
        self.del_btn.setCursor(Qt.PointingHandCursor)
        self.del_btn.setStyleSheet(del_style)
        self.del_btn.clicked.connect(lambda: self.removed.emit(self))
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 점선 테두리
        pen = QPen(QColor("#0078D7"))
        pen.setWidth(3)
        pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        
        # 반투명 배경
        painter.setBrush(QColor(0, 120, 215, 60))
        painter.drawRect(self.rect().adjusted(2, 2, -2, -2))
        
        # 안내 텍스트 (버튼에 가리지 않게 중앙)
        painter.setPen(Qt.white)
        painter.setFont(QFont("맑은 고딕", 10))
        painter.drawText(self.rect(), Qt.AlignCenter, "드래그 이동\n가장자리 [+]로 추가")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            
    def mouseMoveEvent(self, event):
        if self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            
    def resizeEvent(self, event):
        w, h = self.width(), self.height()
        
        # 그립 위치
        self.grip.move(w - 20, h - 20)
        
        # 버튼 위치 재조정
        # Top: 중앙 상단, 약간 여유
        self.btn_top.move(w//2 - 15, 5)
        
        # Bottom: 중앙 하단
        self.btn_bottom.move(w//2 - 15, h - 35)
        
        # Left: 좌측 중앙
        self.btn_left.move(5, h//2 - 15)
        
        # Right: 우측 중앙
        self.btn_right.move(w - 35, h//2 - 15)
        
        # Delete: 우상단 모서리
        self.del_btn.move(w - 29, 5)
        
        super().resizeEvent(event)


class SlotManager:
    """슬롯 데이터 관리 및 스냅 로직"""
    
    def __init__(self):
        self.slots_data = [] # list of rect tuples (x, y, w, h)
        self.slot_widgets = []
        self.is_editing = False
        self.load_slots()
        
    def load_slots(self):
        try:
            if os.path.exists(SLOTS_FILE):
                with open(SLOTS_FILE, "r") as f:
                    self.slots_data = json.load(f)
        except:
            self.slots_data = []

    def save_slots(self):
        # 위젯이 떠있다면 위젯 위치로 업데이트
        if self.is_editing:
            self.slots_data = []
            for w in self.slot_widgets:
                geo = w.geometry()
                self.slots_data.append((geo.x(), geo.y(), geo.width(), geo.height()))
                
        try:
            with open(SLOTS_FILE, "w") as f:
                json.dump(self.slots_data, f)
        except Exception as e:
            print(f"슬롯 저장 실패: {e}")

    def toggle_edit_mode(self):
        self.is_editing = not self.is_editing
        
        if self.is_editing:
            # 편집 모드 진입: 위젯 생성
            for rect_data in self.slots_data:
                self._create_widget(QRect(*rect_data))
                
            if not self.slot_widgets:
                # 슬롯이 하나도 없으면 하나 생성
                self.add_slot(None, None)
        else:
            # 편집 모드 종료: 저장 및 위젯 제거
            self.save_slots()
            for w in self.slot_widgets:
                w.close()
                w.deleteLater()
            self.slot_widgets = []
            
        return self.is_editing

    def add_slot(self, ref_widget=None, direction=None):
        # 참조 위젯이 있으면 그 방향에 생성
        if ref_widget and isinstance(ref_widget, QWidget) and direction:
            geo = ref_widget.geometry()
            margin = 10 # 슬롯 간 간격
            
            x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()
            
            if direction == "right":
                x = geo.right() + margin
            elif direction == "left":
                x = geo.left() - w - margin
            elif direction == "bottom":
                y = geo.bottom() + margin
            elif direction == "top":
                y = geo.top() - h - margin
                
            rect = QRect(x, y, w, h)
        else:
             x, y = 200, 200
             rect = QRect(x, y, 380, 400)
             
        self._create_widget(rect)
        
    def _create_widget(self, rect):
        w = SlotWidget(rect)
        w.removed.connect(self._remove_widget)
        w.added.connect(self.add_slot)
        self.slot_widgets.append(w)
        w.show()
        w.raise_()
        w.activateWindow()
        
    def _remove_widget(self, widget):
        if widget in self.slot_widgets:
            self.slot_widgets.remove(widget)
            widget.close()
            widget.deleteLater()
            

    def snap_rect(self, current_rect):
        """주어진 rect가 슬롯 근처라면 흡착된 rect 반환, 아니면 None"""
        # 스냅 대상 후보군 선정
        targets = []
        if self.is_editing:
            # 편집 모드일 때는 현재 떠있는 위젯들의 위치 사용
            for w in self.slot_widgets:
                targets.append(w.geometry())
        else:
            # 평소에는 저장된 데이터 사용
            for (x, y, w, h) in self.slots_data:
                targets.append(QRect(x, y, w, h))
        
        # 1. 교차(겹침) 체크 - 가장 강력한 스냅
        best_match = None
        max_inter_area = 0
        
        for target_rect in targets:
            # 겹치는 영역 계산
            intersection = current_rect.intersected(target_rect)
            if not intersection.isEmpty():
                area = intersection.width() * intersection.height()
                if area > max_inter_area:
                    max_inter_area = area
                    best_match = target_rect

        if best_match:
            return best_match

        # 2. 거리 체크 (기존 로직 보완) - 근처에 갔을 때
        SNAP_DIST = 80
        for target_rect in targets:
            dist = (target_rect.topLeft() - current_rect.topLeft()).manhattanLength()
            if dist < SNAP_DIST:
                return target_rect
                
        return None



class StickyNoteWindow(QWidget):
    """개별 스티커 메모 창"""
    
    closed = Signal(object)
    save_requested = Signal()
    
    FONT_SIZES = [9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32]
    
    def __init__(self, app, data=None):
        super().__init__()
        self.app = app
        
        # 데이터 초기화
        if data:
            self.uuid = data.get("uuid", str(uuid.uuid4()))
            self.color_index = data.get("color_index", 0)
            self.is_pinned = data.get("pinned", False)
            self.is_locked = data.get("locked", False)
            self.is_minimized = data.get("minimized", False)
            self.is_desktop = data.get("desktop", False)
            self.alpha = data.get("alpha", 0.95)
            self.font_size = data.get("font_size", 12)
            self.created_at = data.get("created_at", datetime.now().isoformat())
            self.updated_at = data.get("updated_at", datetime.now().isoformat())
            initial_html = data.get("content_html", "")
            initial_plain = data.get("content", "")
            geo = data.get("geometry", "380x400+150+150")
        else:
            self.uuid = str(uuid.uuid4())
            self.color_index = 0
            self.is_pinned = False
            self.is_locked = False
            self.is_minimized = False
            self.is_desktop = False
            self.alpha = 0.95
            self.font_size = 12
            self.created_at = datetime.now().isoformat()
            self.updated_at = datetime.now().isoformat()
            initial_html = ""
            initial_plain = ""
            offset = len(app.notes) * 30
            geo = f"380x400+{150 + offset}+{120 + offset}"
        
        self.color = NOTE_COLORS[self.color_index % len(NOTE_COLORS)]
        self.restored_geometry = geo
        self._drag_pos = None
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._do_save)
        
        self._setup_ui()
        self._apply_geometry(geo)
        # HTML이 있으면 HTML로 로드 (서식+이미지 보존), 없으면 plain text
        if initial_html:
            self.text_edit.setHtml(initial_html)
            self._reload_images()
        elif initial_plain.startswith("<!DOCTYPE") or initial_plain.startswith("<html"):
            # 이전 버전 하위 호환: content에 HTML이 저장된 경우
            self.text_edit.setHtml(initial_plain)
            self._reload_images()
        else:
            self.text_edit.setPlainText(initial_plain)
        self._update_title()
        self.setWindowOpacity(self.alpha)
        
        if self.is_pinned:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            
        # 개별 데스크탑 모드 상태 적용
        if self.is_desktop:
            self.set_desktop_mode(True)
        
    def _setup_ui(self):
        """UI 구성"""
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(200, 100)
        
        # 메인 레이아웃 (마진 제거)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 컨테이너
        self.container = QFrame()
        self.container.setStyleSheet(f"""
            QFrame {{
                background-color: {self.color['bg']};
                border: 2px solid {self.color['border']};
                border-radius: 8px;
            }}
        """)
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        
        # 헤더 (제목바)
        self.header = QFrame()
        self.header.setFixedHeight(32)
        self.header.setStyleSheet(f"""
            QFrame {{
                background-color: {self.color['border']};
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                border: none;
            }}
        """)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(10, 0, 5, 0)
        
        # 제목 라벨
        self.title_label = QLabel("새 메모")
        self.title_label.setStyleSheet("color: white; font-weight: bold; border: none;")
        header_layout.addWidget(self.title_label, 1)
        
        # 헤더 버튼들
        btn_style = """
            QPushButton {
                background: transparent;
                color: white;
                border: none;
                font-size: 14px;
                padding: 2px 6px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.2);
                border-radius: 3px;
            }
        """
        
        # 잠금 버튼
        self.lock_btn = QPushButton("🔓" if not self.is_locked else "🔒")
        self.lock_btn.setStyleSheet(btn_style)
        self.lock_btn.clicked.connect(self.toggle_lock)
        header_layout.addWidget(self.lock_btn)
        
        # 데스크톱 위젯 모드 버튼
        self.desktop_btn = QPushButton("🖥️")
        self.desktop_btn.setStyleSheet(btn_style)
        self.desktop_btn.setToolTip("데스크톱 위젯 모드")
        self.desktop_btn.clicked.connect(self.toggle_desktop)
        header_layout.addWidget(self.desktop_btn)
        
        # 최소화 버튼
        self.min_btn = QPushButton("─")
        self.min_btn.setStyleSheet(btn_style)
        self.min_btn.clicked.connect(self.toggle_minimize)
        header_layout.addWidget(self.min_btn)
        
        # 닫기 버튼
        self.close_btn = QPushButton("✕")
        self.close_btn.setStyleSheet(btn_style + "QPushButton:hover { background-color: #E94560; }")
        self.close_btn.clicked.connect(self.close)
        header_layout.addWidget(self.close_btn)
        
        container_layout.addWidget(self.header)
        
        # 툴바
        self.toolbar = QFrame()
        self.toolbar.setFixedHeight(36)
        self.toolbar.setStyleSheet(f"background-color: {self.color['bg']}; border: none;")
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(5, 2, 5, 2)
        
        tool_btn_style = f"""
            QPushButton {{
                background: transparent;
                color: {self.color['text']};
                border: none;
                font-size: 14px;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                background-color: rgba(0,0,0,0.1);
                border-radius: 4px;
            }}
        """
        
        # 새 메모
        self.add_btn = QPushButton("➕")
        self.add_btn.setStyleSheet(tool_btn_style)
        self.add_btn.clicked.connect(self.app.create_new_note)
        toolbar_layout.addWidget(self.add_btn)
        
        # 고정
        self.pin_btn = QPushButton("📌" if not self.is_pinned else "📍")
        self.pin_btn.setStyleSheet(tool_btn_style)
        self.pin_btn.clicked.connect(self.toggle_pin)
        toolbar_layout.addWidget(self.pin_btn)
        
        # 색상
        self.color_btn = QPushButton("🎨")
        self.color_btn.setStyleSheet(tool_btn_style)
        self.color_btn.clicked.connect(self.show_color_picker)
        toolbar_layout.addWidget(self.color_btn)
        
        # 체크리스트
        self.check_btn = QPushButton("✔")
        self.check_btn.setStyleSheet(tool_btn_style)
        self.check_btn.clicked.connect(self.toggle_checklist)
        toolbar_layout.addWidget(self.check_btn)
        
        # 굵게
        self.bold_btn = QPushButton("B")
        self.bold_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {self.color['text']};
                border: none;
                font-size: 14px;
                padding: 4px 8px;
                font-weight: 900;
            }}
            QPushButton:hover {{
                background-color: rgba(0,0,0,0.1);
                border-radius: 4px;
            }}
        """)
        self.bold_btn.clicked.connect(self.toggle_bold)
        toolbar_layout.addWidget(self.bold_btn)
        
        toolbar_layout.addStretch()
        
        # 폰트 크기
        self.size_down = QPushButton("-")
        self.size_down.setStyleSheet(tool_btn_style)
        self.size_down.clicked.connect(self.decrease_font)
        toolbar_layout.addWidget(self.size_down)
        
        self.size_label = QLabel(str(self.font_size))
        self.size_label.setStyleSheet(f"color: {self.color['text']}; border: none;")
        toolbar_layout.addWidget(self.size_label)
        
        self.size_up = QPushButton("+")
        self.size_up.setStyleSheet(tool_btn_style)
        self.size_up.clicked.connect(self.increase_font)
        toolbar_layout.addWidget(self.size_up)
        
        # 투명도
        self.alpha_slider = QSlider(Qt.Horizontal)
        self.alpha_slider.setRange(30, 100)
        self.alpha_slider.setValue(int(self.alpha * 100))
        self.alpha_slider.setFixedWidth(60)
        self.alpha_slider.valueChanged.connect(self.on_alpha_change)
        toolbar_layout.addWidget(self.alpha_slider)
        
        container_layout.addWidget(self.toolbar)
        
        # 텍스트 영역
        self.text_edit = ImageTextEdit()
        self.text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {self.color['bg']};
                color: {self.color['text']};
                border: none;
                padding: 10px;
                font-size: {self.font_size}pt;
            }}
        """)
        self.text_edit.setFont(QFont("맑은 고딕", self.font_size))
        self.text_edit.textChanged.connect(self._on_text_changed)
        self.text_edit.cursorPositionChanged.connect(self._on_cursor_moved)
        self.text_edit.setContextMenuPolicy(Qt.CustomContextMenu)
        self.text_edit.customContextMenuRequested.connect(self.show_context_menu)
        container_layout.addWidget(self.text_edit, 1)
        
        main_layout.addWidget(self.container)
        
        # 크기 조절 그립 - 오버레이로 배치 (텍스트 영역 위에)
        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(16, 16)
        self.size_grip.setStyleSheet("background: transparent;")
        self.size_grip.raise_()  # 최상위로 올림
        
        # 이벤트
        self.header.mousePressEvent = self._header_mouse_press
        self.header.mouseMoveEvent = self._header_mouse_move
        self.header.mouseReleaseEvent = self._header_mouse_release
        self.header.mouseDoubleClickEvent = lambda e: self.toggle_minimize()
    
    def _apply_geometry(self, geo_str):
        """지오메트리 문자열 적용"""
        try:
            size_part, x, y = geo_str.rsplit('+', 2)
            w, h = size_part.split('x')
            self.setGeometry(int(x), int(y), int(w), int(h))
        except:
            self.setGeometry(150, 150, 280, 320)
    
    def _header_mouse_press(self, event):
        if event.button() == Qt.LeftButton and not self.is_pinned:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
    
    def _header_mouse_move(self, event):
        if self._drag_pos and not self.is_pinned:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            
    def _header_mouse_release(self, event):
        # 드래그 종료 시 스냅 체크
        if self._drag_pos:
            self._drag_pos = None
            if hasattr(self.app, 'slot_manager'):
                snap_rect = self.app.slot_manager.snap_rect(self.geometry())
                if snap_rect:
                    # 자석 효과 애니메이션 (선택 사항, 일단 즉시 이동)
                    self.setGeometry(snap_rect)
                    self.schedule_save()
        
    def _on_text_changed(self):
        self._update_title()
        self.updated_at = datetime.now().isoformat()
        self.schedule_save()
    
    def _on_cursor_moved(self):
        """커서 위치의 글자 크기를 size_label에 표시"""
        cursor = self.text_edit.textCursor()
        fmt = cursor.charFormat()
        size = fmt.font().pointSize()
        if size <= 0:
            size = self.font_size
        self.size_label.setText(str(size))
    
    def _update_title(self):
        text = self.text_edit.toPlainText()
        first_line = text.split('\n')[0].strip()[:25]
        self.title_label.setText(first_line if first_line else "새 메모")
    
    def schedule_save(self):
        self._save_timer.start(500)
    
    def _do_save(self):
        self.save_requested.emit()
    
    def resizeEvent(self, event):
        """창 크기 변경 시 그립 위치 업데이트"""
        super().resizeEvent(event)
        # 그립을 우하단에 배치
        self.size_grip.move(self.width() - 18, self.height() - 18)
    
    def toggle_minimize(self):
        self.is_minimized = not self.is_minimized
        if self.is_minimized:
            self.toolbar.hide()
            self.text_edit.hide()
            self.size_grip.hide()  # 그립도 숨김
            
            geo = self.geometry()
            self.restored_geometry = f"{geo.width()}x{geo.height()}+{geo.x()}+{geo.y()}"
            self.setFixedHeight(36) # 헤더 높이 32 + 여유분 4 (보더 등)
            self.min_btn.setText("🗖")
            
            # 최소화 시 컨테이너 스타일 변경 (하단 둥글게 처리하여 깔끔하게)
            self.container.setStyleSheet(f"""
                QFrame {{
                    background-color: {self.color['border']}; /* 헤더 색상과 일치 */
                    border: 2px solid {self.color['border']};
                    border-radius: 8px;
                }}
            """)
        else:
            self.setMinimumHeight(100)
            self.setMaximumHeight(16777215)
            self._apply_geometry(self.restored_geometry)
            self.toolbar.show()
            self.text_edit.show()
            self.size_grip.show()  # 그립 다시 표시
            self.min_btn.setText("─")
            # 스타일 복구
            self._apply_color(self.color_index)
            
        self.schedule_save()
    
    def toggle_pin(self):
        self.is_pinned = not self.is_pinned
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.is_pinned)
        self.show()
        self.pin_btn.setText("📍" if self.is_pinned else "📌")
        self.schedule_save()
    
    def toggle_lock(self):
        self.is_locked = not self.is_locked
        self.lock_btn.setText("🔒" if self.is_locked else "🔓")
        self.schedule_save()
    
    def show_color_picker(self):
        """컬러 피커 팝업 - 색상 시각적 표시"""
        from PySide6.QtWidgets import QWidgetAction
        
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2D2D3D;
                padding: 8px;
                border-radius: 8px;
            }
        """)
        
        # 색상 버튼들을 담을 위젯
        color_widget = QWidget()
        color_layout = QHBoxLayout(color_widget)
        color_layout.setContentsMargins(5, 5, 5, 5)
        color_layout.setSpacing(8)
        
        for i, color in enumerate(NOTE_COLORS):
            btn = QPushButton()
            btn.setFixedSize(36, 36)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(color['name'])
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color['bg']};
                    border: 3px solid {color['border']};
                    border-radius: 8px;
                }}
                QPushButton:hover {{
                    border: 3px solid white;
                }}
            """)
            btn.clicked.connect(lambda checked, idx=i, m=menu: (self._apply_color(idx), m.close()))
            color_layout.addWidget(btn)
        
        widget_action = QWidgetAction(menu)
        widget_action.setDefaultWidget(color_widget)
        menu.addAction(widget_action)
        
        menu.exec(QCursor.pos())
    
    def _apply_color(self, idx):
        self.color_index = idx
        self.color = NOTE_COLORS[idx]
        
        self.container.setStyleSheet(f"""
            QFrame {{
                background-color: {self.color['bg']};
                border: 2px solid {self.color['border']};
                border-radius: 8px;
            }}
        """)
        self.header.setStyleSheet(f"""
            QFrame {{
                background-color: {self.color['border']};
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                border: none;
            }}
        """)
        self.toolbar.setStyleSheet(f"background-color: {self.color['bg']}; border: none;")
        self.text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {self.color['bg']};
                color: {self.color['text']};
                border: none;
                padding: 10px;
                font-size: {self.font_size}pt;
            }}
        """)

        self.schedule_save()
    
    def toggle_bold(self):
        """선택 텍스트 굵게"""
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            fmt = cursor.charFormat()
            fmt.setFontWeight(QFont.Normal if fmt.fontWeight() == QFont.Bold else QFont.Bold)
            cursor.mergeCharFormat(fmt)
    
    def toggle_checklist(self):
        """체크리스트 토글"""
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.StartOfLine)
        cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
        line = cursor.selectedText()
        
        if line.startswith("✔ "):
            cursor.insertText("[ ] " + line[2:])
        elif line.startswith("[ ] "):
            cursor.insertText("✔ " + line[4:])
        else:
            cursor.insertText("[ ] " + line)
        self.schedule_save()
    
    def _get_selection_font_size(self):
        """선택 영역의 현재 폰트 크기를 반환"""
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            # 선택 시작 위치의 폰트 크기를 기준으로 사용
            fmt = cursor.charFormat()
            size = fmt.font().pointSize()
            if size <= 0:
                size = self.font_size
            return size
        return self.font_size

    def increase_font(self):
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            current_size = self._get_selection_font_size()
            new_size = min(current_size + 1, 72)
            if new_size != current_size:
                fmt = QTextCharFormat()
                fmt.setFontPointSize(new_size)
                cursor.mergeCharFormat(fmt)
                self.size_label.setText(str(new_size))
                self.schedule_save()
        else:
            self.font_size = min(self.font_size + 1, 72)
            self._apply_font()
    
    def decrease_font(self):
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            current_size = self._get_selection_font_size()
            new_size = max(current_size - 1, 1)
            if new_size != current_size:
                fmt = QTextCharFormat()
                fmt.setFontPointSize(new_size)
                cursor.mergeCharFormat(fmt)
                self.size_label.setText(str(new_size))
                self.schedule_save()
        else:
            self.font_size = max(self.font_size - 1, 1)
            self._apply_font()
    
    def _apply_font(self):
        self.size_label.setText(str(self.font_size))
        self.text_edit.setFont(QFont("맑은 고딕", self.font_size))
        self.schedule_save()
    
    def on_alpha_change(self, value):
        self.alpha = value / 100.0
        self.setWindowOpacity(self.alpha)
        self.schedule_save()
    
    def show_context_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("잘라내기", self.text_edit.cut)
        menu.addAction("복사", self.text_edit.copy)
        menu.addAction("붙여넣기", self.text_edit.paste)
        menu.addSeparator()
        menu.addAction("실행취소", self.text_edit.undo)
        menu.addAction("다시실행", self.text_edit.redo)
        menu.exec(self.text_edit.mapToGlobal(pos))
    
    def to_dict(self):
        geo = self.geometry()
        if not self.is_minimized:
            self.restored_geometry = f"{geo.width()}x{geo.height()}+{geo.x()}+{geo.y()}"
        return {
            "uuid": self.uuid,
            "geometry": self.restored_geometry,
            "content": self.text_edit.toPlainText(),
            "content_html": self.text_edit.toHtml(),
            "pinned": self.is_pinned,
            "locked": self.is_locked,
            "desktop": self.is_desktop,
            "color_index": self.color_index,
            "minimized": self.is_minimized,
            "alpha": self.alpha,
            "font_size": self.font_size,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }
    
    def _reload_images(self):
        """HTML에 포함된 이미지 파일들을 문서 리소스로 재등록"""
        doc = self.text_edit.document()
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    if fmt.isImageFormat():
                        img_fmt = fmt.toImageFormat()
                        img_url = img_fmt.name()
                        # file:/// URL에서 로컬 경로 추출
                        if img_url.startswith("file:///"):
                            local_path = img_url[8:]  # file:/// 제거
                        else:
                            local_path = img_url
                        
                        if os.path.exists(local_path):
                            image = QImage(local_path)
                            if not image.isNull():
                                doc.addResource(1, img_url, image)
                it += 1
            block = block.next()
    
    def closeEvent(self, event):
        self.closed.emit(self)
        event.accept()
    
    def toggle_desktop(self):
        """데스크톱 위젯 모드 토글"""
        self.is_desktop = not self.is_desktop
        self.set_desktop_mode(self.is_desktop)
        self.schedule_save()

    def set_desktop_mode(self, enabled):
        """데스크탑 위젯 모드 전환"""
        
        # Windows API 상수
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x80000
        WS_EX_TRANSPARENT = 0x20
        
        hwnd = self.winId()
        
        self.header.setVisible(not enabled)
        self.toolbar.setVisible(not enabled)
        
        if enabled:
            self.size_grip.hide()
        elif not self.is_minimized:
            self.size_grip.show()
        
        # Qt 속성도 설정 (보조적)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, enabled)
        
        if enabled:
            # 바탕화면 모드: 테두리 제거, 읽기 전용
            self.container.setStyleSheet(f"background-color: {self.color['bg']}; border: none; border-radius: 8px;")
            self.text_edit.setReadOnly(True)
            self.text_edit.setTextInteractionFlags(Qt.NoTextInteraction)
            
            # Win32 API로 클릭 투과 설정
            ex_style = windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_TRANSPARENT | WS_EX_LAYERED)
            
        else:
            # 일반 모드: 스타일 복구, 편집 가능
            self._apply_color(self.color_index)
            self.text_edit.setReadOnly(False)
            self.text_edit.setTextInteractionFlags(Qt.TextEditorInteraction)
            
            # Win32 API 클릭 투과 해제
            ex_style = windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # 투과 스타일 제거
            windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style & ~WS_EX_TRANSPARENT)
            
        # 윈도우 스타일 강제 갱신 (SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER)
        user32 = windll.user32
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004)


class ClickableFrame(QFrame):
    clicked = Signal()
    
    def __init__(self, uuid, parent=None):
        super().__init__(parent)
        self.uuid = uuid
    
    def mouseReleaseEvent(self, event):
        # 클릭된 위치의 자식 위젯 확인
        child = self.childAt(event.position().toPoint())
        # QLineEdit(제목)이나 QPushButton(삭제) 위라면 클릭 이벤트 무시
        if isinstance(child, (QLineEdit, QPushButton)):
            super().mouseReleaseEvent(event)
            return
            
        # QLineEdit 내부 컴포넌트일 경우도 대비 (일반적으로 QWidget)
        if child and (child.metaObject().className() == "QWidget" or "LineEdit" in child.metaObject().className()):
             # 부모가 QLineEdit인지 등을 확인해야 하지만, 
             # 간단히 포커스 정책 등을 볼 수도 있음.
             # 여기서는 안전하게 자식이 있으면 무시하는게 나을 수도 있지만,
             # 라벨(QLabel)은 클릭해서 열기가 되어야 함.
             # QLabel은 보통 상호작용 안함.
             pass

        if event.button() == Qt.LeftButton:
            # 제목 수정 중이면 열기 동작 방지
            curr_focus = QApplication.focusWidget()
            if isinstance(curr_focus, QLineEdit) and self.isAncestorOf(curr_focus):
                super().mouseReleaseEvent(event)
                return

            self.clicked.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class NoteListDialog(QDialog):
    """저장된 메모 목록 - 모던 UI"""
    
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle("📋 저장된 메모 목록")
        self.setMinimumSize(400, 450)
        self.resize(480, 550)
        
        # 모던 다크 테마
        self.setStyleSheet("""
            QDialog {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1A1A2E, stop:1 #16213E);
                border-radius: 12px;
            }
            QLabel {
                color: #E4E4E7;
                background: transparent;
            }
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #3B82F6, stop:1 #2563EB);
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #60A5FA, stop:1 #3B82F6);
            }
            QPushButton:pressed {
                background: #1D4ED8;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: #1E293B;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #475569;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #64748B;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 헤더
        header = QHBoxLayout()
        title = QLabel("📋 저장된 메모")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: white;")
        header.addWidget(title)
        header.addStretch()
        
        # 메모 개수 표시
        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: #94A3B8; font-size: 12px;")
        header.addWidget(self.count_label)
        
        new_btn = QPushButton("➕ 새 메모")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.clicked.connect(self._on_new)
        header.addWidget(new_btn)
        layout.addLayout(header)
        
        # 스크롤 영역
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        
        self.list_widget = QWidget()
        self.list_widget.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setAlignment(Qt.AlignTop)
        self.list_layout.setSpacing(10)
        scroll.setWidget(self.list_widget)
        layout.addWidget(scroll)
        
        self._refresh()
    
    def _refresh(self):
        # 기존 위젯 제거
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        count = len(self.app.all_notes_data)
        self.count_label.setText(f"{count}개의 메모")
        
        if count == 0:
            empty_label = QLabel("📝 저장된 메모가 없습니다")
            empty_label.setStyleSheet("color: #64748B; font-size: 14px; padding: 40px;")
            empty_label.setAlignment(Qt.AlignCenter)
            self.list_layout.addWidget(empty_label)
            return
        
        for data in self.app.all_notes_data:
            self._add_row(data)
    
    def _add_row(self, data):
        uuid = data.get("uuid")
        row = ClickableFrame(uuid, self)
        row.setCursor(Qt.PointingHandCursor)
        row.setStyleSheet("""
            QFrame {
                background: rgba(30, 41, 59, 0.8);
                border: 1px solid rgba(71, 85, 105, 0.5);
                border-radius: 10px;
            }
            QFrame:hover {
                background: rgba(51, 65, 85, 0.9);
                border: 1px solid rgba(100, 116, 139, 0.7);
            }
        """)
        row.clicked.connect(lambda: self._on_open(uuid))
        
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(15, 12, 15, 12)
        row_layout.setSpacing(15)
        
        # 1. 상태 아이콘 (잠금/열림)
        is_open = any(n.uuid == data.get("uuid") for n in self.app.notes)
        is_locked = data.get("locked", False)
        color_idx = data.get("color_index", 0)
        color = NOTE_COLORS[color_idx % len(NOTE_COLORS)]
        
        icon_text = "📖" if is_open else "📄"
        if is_locked:
            icon_text += "🔒"
            
        icon_label = QLabel(icon_text)
        icon_label.setStyleSheet("font-size: 20px; background: transparent; border: none;")
        row_layout.addWidget(icon_label)
        
        # 2. 중앙 정보 (제목 수정 + 미리보기)
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)
        
        content = data.get("content", "")
        # 이전 버전 하위 호환: content에 HTML이 들어있으면 변환
        if content.startswith("<") or content.startswith("<!DOCTYPE"):
            content = html_to_plain(content)
        lines = content.split('\n')
        lines = [l.strip() for l in lines if l.strip()]
        current_title = lines[0] if lines else ""
        preview = lines[1][:50] if len(lines) > 1 else "내용 없음"
        
        # 제목 (수정 가능)
        title_edit = QLineEdit(current_title)
        title_edit.setPlaceholderText("제목 없음")
        title_edit.setStyleSheet(f"""
            QLineEdit {{
                background: transparent;
                border: none;
                font-weight: bold;
                font-size: 14px;
                color: {color['border']};
                selection-background-color: {color['border']};
            }}
            QLineEdit:focus {{
                border-bottom: 2px solid {color['border']};
            }}
        """)
        # 제목 변경 시 저장
        title_edit.editingFinished.connect(lambda: self._update_title(data, title_edit.text()))
        info_layout.addWidget(title_edit)
        
        # 미리보기 (클릭 불가능하게 하여 부모 프레임 클릭 유도)
        preview_label = QLabel(preview)
        preview_label.setStyleSheet("color: #94A3B8; font-size: 11px; background: transparent; border: none;")
        preview_label.setAttribute(Qt.WA_TransparentForMouseEvents) 
        info_layout.addWidget(preview_label)
        
        row_layout.addLayout(info_layout, 1)
        
        # 3. 삭제 버튼 (휴지통 -> X)
        del_btn = QPushButton("❌")
        del_btn.setFixedSize(48, 36)
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setToolTip("삭제")
        del_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 14px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: rgba(239, 68, 68, 0.2);
            }
            QPushButton:pressed {
                background: rgba(239, 68, 68, 0.4);
            }
        """)
        # clicked 시그널 사용 (ClickableFrame에서 필터링됨)
        del_btn.clicked.connect(lambda: self._on_delete(data.get("uuid")))
        row_layout.addWidget(del_btn)
        
        self.list_layout.addWidget(row)
    
    def _update_title(self, data, new_title):
        """제목 수정 처리 - 열려있는 메모의 텍스트 에디터를 직접 수정"""
        uid = data.get("uuid")
        
        # 열려있는 메모의 첫 줄 수정
        for note in self.app.notes:
            if note.uuid == uid:
                note.text_edit.blockSignals(True)
                doc = note.text_edit.document()
                cursor = QTextCursor(doc)
                cursor.movePosition(QTextCursor.Start)
                cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
                cursor.insertText(new_title)
                note.text_edit.blockSignals(False)
                note._update_title()
                # to_dict()로 최신 HTML 가져오기
                data["content"] = note.text_edit.toHtml()
                data["updated_at"] = datetime.now().isoformat()
                self.app.save_notes()
                return
        
        # 닫힌 메모: 메모를 열어서 수정
        self.app.show_or_open_note(uid)
        for note in self.app.notes:
            if note.uuid == uid:
                note.text_edit.blockSignals(True)
                doc = note.text_edit.document()
                cursor = QTextCursor(doc)
                cursor.movePosition(QTextCursor.Start)
                cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
                cursor.insertText(new_title)
                note.text_edit.blockSignals(False)
                note._update_title()
                break
        
        data["updated_at"] = datetime.now().isoformat()
        self.app.save_notes()
    
    def _on_open(self, uid):
        self.app.show_or_open_note(uid)
        self._refresh()
    
    def _on_delete(self, uid):
        self.app.delete_note_by_uuid(uid)
        self._refresh()
    
    def _on_new(self):
        self.app.create_new_note()
        self._refresh()


class SearchDialog(QDialog):
    """검색 다이얼로그"""
    
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle("🔍 메모 검색")
        self.setFixedSize(400, 350)
        self.setStyleSheet("""
            QDialog { background-color: #1A1A2E; }
            QLabel { color: white; }
            QLineEdit {
                background-color: #233554;
                color: white;
                border: 1px solid #3B82F6;
                border-radius: 4px;
                padding: 8px;
            }
        """)
        
        layout = QVBoxLayout(self)
        
        # 검색창
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("검색어 입력...")
        self.search_input.textChanged.connect(self._do_search)
        layout.addWidget(self.search_input)
        
        # 결과 영역
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background-color: #16213E;")
        
        self.result_widget = QWidget()
        self.result_layout = QVBoxLayout(self.result_widget)
        self.result_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self.result_widget)
        layout.addWidget(scroll)
    
    def _do_search(self, query):
        # 기존 결과 제거
        while self.result_layout.count():
            item = self.result_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        if not query.strip():
            return
        
        query_lower = query.lower()
        # 모든 저장된 메모 검색
        for data in self.app.all_notes_data:
            content = data.get("content", "")
            # 이전 버전 하위 호환
            if content.startswith("<") or content.startswith("<!DOCTYPE"):
                content = html_to_plain(content)
            if query_lower in content.lower():
                lines = [l.strip() for l in content.split('\n') if l.strip()]
                title = lines[0][:30] if lines else "(내용 없음)"
                if not title.strip(): title = "(내용 없음)"
                
                # 열려있는지 확인
                is_open = any(n.uuid == data.get("uuid") for n in self.app.notes)
                status = "📖" if is_open else "📄"
                
                btn_text = f"{status} {title}"
                
                row = QPushButton(btn_text)
                row.setCursor(Qt.PointingHandCursor)
                row.setStyleSheet("""
                    QPushButton {
                        background-color: #233554;
                        color: white;
                        text-align: left;
                        padding: 10px;
                        border-radius: 4px;
                        border: 1px solid #3B82F6;
                    }
                    QPushButton:hover {
                        background-color: #3B82F6;
                    }
                """)
                # 데이터 클릭 시 해당 메모 열기
                row.clicked.connect(lambda _, uid=data.get("uuid"): self._on_result_click(uid))
                self.result_layout.addWidget(row)
    
    def _on_result_click(self, uid):
        self.app.show_or_open_note(uid)
        # 검색 창 닫지 않음 (연속 검색 위해) 또는 닫으려면 self.close() 추가


class StickyMemoApp:
    """메인 앱"""
    
    REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
    REG_KEY = "StickyMemo"
    
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        
        self.slot_manager = SlotManager()  # 슬롯 매니저
        
        self.notes = []
        self.all_notes_data = []
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_notes)
        
        self._load_notes()
        self._setup_tray()
        
        # 시작 프로그램 자동 등록 (강제)
        self.register_autostart()
    
    def _setup_tray(self):
        """시스템 트레이 설정"""
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(self._create_icon())
        self.tray.setToolTip("StickyMemo")
        
        menu = QMenu()
        menu.addAction("📝 새 메모", self.create_new_note)
        menu.addAction("📋 저장된 메모 목록", self.show_note_list)
        menu.addAction("👁 모든 메모 보기", self.show_all_notes)
        menu.addAction("🔍 메모 검색", self.show_search)
        menu.addSeparator()
        
        # 슬롯 편집 모드
        self.slot_action = menu.addAction("🧲 슬롯 편집 모드")
        self.slot_action.setCheckable(True)
        self.slot_action.triggered.connect(self._toggle_slot_edit)
        
        # 데스크탑 위젯 모드
        self.desktop_action = menu.addAction("🖥️ 데스크탑 위젯 모드")
        self.desktop_action.setCheckable(True)
        self.desktop_action.triggered.connect(self._toggle_desktop_mode)
        
        menu.addSeparator()
        
        menu.addSeparator()
        menu.addAction("❌ 종료", self.quit)
        
        self.tray.setContextMenu(menu)
        self.tray.show()
        
    def _toggle_slot_edit(self, checked):
        state = self.slot_manager.toggle_edit_mode()
        self.slot_action.setChecked(state)
        
    def _toggle_desktop_mode(self, checked):
        """트레이에서 전체 메모 데스크탑 모드 토글"""
        for note in self.notes:
            note.is_desktop = checked
            note.set_desktop_mode(checked)
            note.schedule_save()
    
    def _create_icon(self):
        """트레이 아이콘 생성"""
        # 이미지 파일이 있으면 사용
        icon_path = resource_path("StickyMemo.png")
        if os.path.exists(icon_path):
            return QIcon(icon_path)
            
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(QColor("#FFE066")))
        painter.setPen(QPen(QColor("#FFB800"), 2))
        painter.drawRoundedRect(4, 4, 56, 56, 8, 8)
        
        painter.setPen(QPen(QColor("#E0D4A8"), 1))
        for y in [20, 32, 44]:
            painter.drawLine(12, y, 52, y)
        painter.end()
        
        return QIcon(pixmap)
    
    def _load_notes(self):
        try:
            with open(SAVE_FILE, "r", encoding="utf-8") as f:
                self.all_notes_data = json.load(f)
        except:
            self.all_notes_data = []
        
        for data in self.all_notes_data:
            note = StickyNoteWindow(self, data)
            note.closed.connect(self._on_note_closed)
            note.save_requested.connect(self.schedule_save)
            self.notes.append(note)
            note.show()
    
    def create_new_note(self):
        note = StickyNoteWindow(self)
        note.closed.connect(self._on_note_closed)
        note.save_requested.connect(self.schedule_save)
        self.notes.append(note)
        self.all_notes_data.append(note.to_dict())
        note.show()
        self.save_notes()
    
    def _on_note_closed(self, note):
        # 데이터 업데이트
        for i, data in enumerate(self.all_notes_data):
            if data.get("uuid") == note.uuid:
                self.all_notes_data[i] = note.to_dict()
                break
        
        if note in self.notes:
            self.notes.remove(note)
        self.save_notes()
    
    def schedule_save(self):
        self._save_timer.start(500)
    
    def save_notes(self):
        # 열린 메모 업데이트
        for note in self.notes:
            for i, data in enumerate(self.all_notes_data):
                if data.get("uuid") == note.uuid:
                    self.all_notes_data[i] = note.to_dict()
                    break
        
        try:
            with open(SAVE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.all_notes_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"저장 오류: {e}")
            
        self.refresh_list_dialog()
    
    def show_or_open_note(self, uid):
        for note in self.notes:
            if note.uuid == uid:
                note.show()
                note.raise_()
                note.activateWindow()
                return
        
        for data in self.all_notes_data:
            if data.get("uuid") == uid:
                note = StickyNoteWindow(self, data)
                note.closed.connect(self._on_note_closed)
                note.save_requested.connect(self.schedule_save)
                self.notes.append(note)
                note.show()
                return
    
    def delete_note_by_uuid(self, uid):
        # 잠금 확인
        for data in self.all_notes_data:
            if data.get("uuid") == uid and data.get("locked"):
                reply = QMessageBox.warning(
                    None, "🔒 잠긴 메모",
                    "이 메모는 잠겨 있습니다.\n정말 삭제하시겠습니까?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return
                break
        
        # 열린 창 닫기
        for note in self.notes[:]:
            if note.uuid == uid:
                self.notes.remove(note)
                note.close()
                break
        
        self.all_notes_data = [d for d in self.all_notes_data if d.get("uuid") != uid]
        self.save_notes()
    
    def show_note_list(self):
        # 이미 열려있으면 활성화만
        if hasattr(self, 'note_list_dialog') and self.note_list_dialog.isVisible():
            self.note_list_dialog.raise_()
            self.note_list_dialog.activateWindow()
            return

        self.note_list_dialog = NoteListDialog(self)
        self.note_list_dialog.show()
    
    def show_search(self):
        if hasattr(self, 'search_dialog') and self.search_dialog.isVisible():
            self.search_dialog.raise_()
            self.search_dialog.activateWindow()
            return
            
        self.search_dialog = SearchDialog(self)
        self.search_dialog.show()
    
    def refresh_list_dialog(self):
        """열려있는 목록 창 갱신"""
        if hasattr(self, 'note_list_dialog') and self.note_list_dialog.isVisible():
            self.note_list_dialog._refresh()
    
    def show_all_notes(self):
        """모든 메모 보이기 (닫힌 메모도 포함)"""
        # 먼저 현재 열린 메모들 보이기
        for note in self.notes:
            if note.is_minimized:
                note.toggle_minimize()
            note.show()
            note.raise_()
            
        # 닫혀있는 메모들도 모두 열기
        opened_uuids = [n.uuid for n in self.notes]
        for data in self.all_notes_data:
            if data.get("uuid") not in opened_uuids:
                self.show_or_open_note(data.get("uuid"))
                
    def show_search(self):
        if hasattr(self, 'search_dialog') and self.search_dialog.isVisible():
            self.search_dialog.raise_()
            self.search_dialog.activateWindow()
            return
            
        self.search_dialog = SearchDialog(self)
        self.search_dialog.show()
    
    def _is_autostart(self):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.REG_PATH, 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, self.REG_KEY)
            winreg.CloseKey(key)
            return True
        except:
            return False
    
    def register_autostart(self):
        """시작 프로그램 강제 등록"""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.REG_PATH, 0, winreg.KEY_SET_VALUE)
            exe_path = sys.executable if not getattr(sys, 'frozen', False) else sys.argv[0]
            winreg.SetValueEx(key, self.REG_KEY, 0, winreg.REG_SZ, f'"{exe_path}"')
            winreg.CloseKey(key)
        except Exception as e:
            print(f"자동 시작 등록 오류: {e}")
    
    def quit(self):
        self.save_notes()
        self.tray.hide()
        self.app.quit()
    
    def run(self):
        return self.app.exec()


if __name__ == "__main__":
    app = StickyMemoApp()
    sys.exit(app.run())
