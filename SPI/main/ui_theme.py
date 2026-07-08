from PyQt5 import QtGui, QtWidgets


COLORS = {
    "bg": "#eef2f0",
    "paper": "#ffffff",
    "paper_2": "#f7faf8",
    "plot": "#f9fbf9",
    "input": "#fbfcfb",
    "ink": "#20313d",
    "muted": "#65726d",
    "line": "#dfe8e3",
    "line_soft": "#e7eee9",
    "green": "#155f4c",
    "green_2": "#0f4739",
    "gold": "#dba044",
    "rust": "#a75a3f",
    "blue": "#2f80ed",
    "red": "#db4f4a",
    "ok": "#29a96d",
    "violet": "#7a5cff",
    "unknown": "#d8cbb8",
}

BEHAVIOR_COLORS = {
    "Displacement": COLORS["red"],
    "Grazing": COLORS["ok"],
    "Ruminating_Chewing": COLORS["violet"],
    "Other": COLORS["gold"],
    "Resting": COLORS["blue"],
    "Unknown": COLORS["unknown"],
    "未知": COLORS["unknown"],
}


def apply_shadow(widget, blur=22, y=7, alpha=28):
    effect = QtWidgets.QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y)
    effect.setColor(QtGui.QColor(31, 46, 41, alpha))
    widget.setGraphicsEffect(effect)


APP_STYLE = f"""
QMainWindow{{
    background:#eef2f0;
}}
QWidget{{
    color:{COLORS["ink"]};
    font-family:'Microsoft YaHei UI','Microsoft YaHei','PingFang SC',Arial;
    letter-spacing:0px;
}}
QFrame#titlebar,QFrame#panel{{
    background:#ffffff;
    border:1px solid #dfe8e3;
    border-radius:8px;
}}
QWidget#rightBody{{
    background:transparent;
}}
QScrollArea#rightScroll{{
    background:transparent;
    border:0;
}}
QScrollArea#rightScroll > QWidget > QWidget{{
    background:transparent;
}}
QLabel#brandMark{{
    background:{COLORS["green"]};
    color:#ffffff;
    border-radius:8px;
    font:900 20px 'Microsoft YaHei UI';
}}
QLabel#appTitle{{
    color:{COLORS["ink"]};
    font:900 23px 'Microsoft YaHei UI';
}}
QLabel#appSubtitle{{
    color:{COLORS["muted"]};
    font:800 12px 'Microsoft YaHei UI';
}}
QLabel#topChip,QLabel#onlineChip,QLabel#panelChip{{
    background:#f7faf8;
    border:1px solid #dfe8e3;
    border-radius:8px;
    color:{COLORS["ink"]};
    padding:8px 13px;
    font:900 13px 'Microsoft YaHei UI';
}}
QLabel#onlineChip{{
    background:#e7f3ed;
    color:{COLORS["green"]};
    border-color:#cfe5da;
}}
QFrame#heroCard{{
    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
        stop:0 {COLORS["green"]}, stop:0.58 {COLORS["green_2"]}, stop:1 #b9aa65);
    border:1px solid #2d725e;
    border-radius:8px;
}}
QLabel#overline{{
    color:#dcefe5;
    font:900 11px 'Microsoft YaHei UI';
    letter-spacing:1px;
}}
QLabel#behavior{{
    color:#ffffff;
    font:900 42px 'Microsoft YaHei UI';
}}
QLabel#confidence{{
    color:{COLORS["green_2"]};
    background:#eadfc7;
    border-radius:8px;
    padding:9px 16px;
    font:900 16px 'Microsoft YaHei UI';
}}
QLabel#metricTitle{{
    color:{COLORS["muted"]};
    font:900 12px 'Microsoft YaHei UI';
}}
QLabel#metricValue{{
    color:{COLORS["ink"]};
    font:900 30px 'Microsoft YaHei UI';
}}
QLabel#metricSub{{
    color:{COLORS["muted"]};
    font:800 12px 'Microsoft YaHei UI';
}}
QFrame#panelHead{{
    background:#ffffff;
    border:0;
    border-bottom:1px solid #e7eee9;
    min-height:48px;
}}
QFrame#panelBody{{
    background:#ffffff;
    border:0;
}}
QLabel#panelTitle{{
    color:{COLORS["ink"]};
    font:900 15px 'Microsoft YaHei UI';
}}
QLabel#probName,QLabel#probValue{{
    color:{COLORS["muted"]};
    font:900 12px 'Microsoft YaHei UI';
}}
QProgressBar#probBar{{
    background:#e9f0ec;
    border:0;
    border-radius:5px;
}}
QProgressBar#probBar::chunk{{
    background:{COLORS["green"]};
    border-radius:5px;
}}
QLabel#deviceField,QLabel#statBox{{
    background:#fbfcfb;
    border:1px solid #e6ebe8;
    border-radius:8px;
    color:{COLORS["ink"]};
    padding:8px 10px;
    font:800 13px 'Microsoft YaHei UI';
}}
QLabel#risk_normal{{
    color:{COLORS["green_2"]};
    background:#e8f4ed;
    border-radius:8px;
    padding:10px 12px;
    font:900 13px 'Microsoft YaHei UI';
}}
QLabel#risk_warning{{
    color:#8a641d;
    background:#fff7e7;
    border-radius:8px;
    padding:10px 12px;
    font:900 13px 'Microsoft YaHei UI';
}}
QLabel#risk_danger{{
    color:{COLORS["rust"]};
    background:#f3e0d0;
    border-radius:8px;
    padding:10px 12px;
    font:900 13px 'Microsoft YaHei UI';
}}
QTextEdit#operationLog,QTextEdit#alertText,QTextEdit{{
    background:#fbfcfb;
    border:1px solid #e6ebe8;
    border-radius:8px;
    color:{COLORS["ink"]};
    padding:10px;
    font:12px 'Consolas';
    selection-background-color:#dceee7;
    selection-color:{COLORS["green_2"]};
}}
QTextEdit#alertText{{
    color:{COLORS["rust"]};
}}
QListWidget{{
    background:#fbfcfb;
    border:1px solid #e6ebe8;
    border-radius:8px;
    color:{COLORS["ink"]};
    padding:4px;
    font:12px 'Consolas';
}}
QListWidget::item{{
    padding:6px 8px;
    border-radius:6px;
}}
QListWidget::item:selected{{
    background:#e3eedf;
    color:{COLORS["green_2"]};
}}
QLineEdit,QDateEdit{{
    background:#fbfcfb;
    border:1px solid #dfe8e3;
    border-radius:8px;
    color:{COLORS["ink"]};
    padding:8px 10px;
    font:800 13px 'Consolas';
}}
QLineEdit:focus,QDateEdit:focus{{
    border:1px solid {COLORS["green"]};
    background:#ffffff;
}}
QPushButton{{
    background:#ffffff;
    border:1px solid #dfe8e3;
    border-radius:8px;
    color:{COLORS["ink"]};
    padding:8px 13px;
    min-height:22px;
    font:900 13px 'Microsoft YaHei UI';
}}
QPushButton:hover{{
    background:#f1f6f3;
    border-color:#cddbd4;
}}
QPushButton:pressed{{
    background:#e5eee9;
}}
QPushButton#primary{{
    background:{COLORS["green"]};
    border:1px solid {COLORS["green_2"]};
    color:#ffffff;
}}
QPushButton#primary:hover{{
    background:{COLORS["green_2"]};
}}
QPushButton#danger{{
    background:{COLORS["rust"]};
    border:1px solid #8f4a35;
    color:#ffffff;
}}
QPushButton#danger:hover{{
    background:#8f4a35;
}}
QScrollBar:vertical{{
    background:transparent;
    width:10px;
    margin:2px;
}}
QScrollBar::handle:vertical{{
    background:#cfd9d4;
    border-radius:5px;
    min-height:24px;
}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{
    height:0px;
}}
QToolTip{{
    background:{COLORS["ink"]};
    color:#ffffff;
    border:0;
    border-radius:6px;
    padding:6px 8px;
}}
"""

HISTORY_STYLE = APP_STYLE + f"""
QWidget#historyRoot{{
    background:#eef2f0;
}}
QLabel#side_title{{
    color:{COLORS["ink"]};
    font:900 15px 'Microsoft YaHei UI';
}}
QLabel#score{{
    color:{COLORS["green"]};
    font:900 32px 'Microsoft YaHei UI';
}}
QLabel#overview{{
    color:{COLORS["ink"]};
    font:700 13px 'Microsoft YaHei UI';
    line-height:155%;
}}
QTableWidget#detailTable{{
    background:#fbfcfb;
    border:1px solid #e6ebe8;
    border-radius:8px;
    color:{COLORS["ink"]};
    gridline-color:#e6ebe8;
    font:800 12px 'Microsoft YaHei UI';
    selection-background-color:#e7f3ed;
    selection-color:{COLORS["green_2"]};
}}
QTableWidget#detailTable::item{{
    padding:6px;
    border:0;
}}
QHeaderView::section{{
    background:#ffffff;
    border:0;
    border-bottom:1px solid #e6ebe8;
    color:{COLORS["muted"]};
    padding:8px 6px;
    font:900 12px 'Microsoft YaHei UI';
}}
QTableCornerButton::section{{
    background:#ffffff;
    border:0;
}}
QCalendarWidget QWidget{{
    alternate-background-color:#f4f7f5;
}}
"""
