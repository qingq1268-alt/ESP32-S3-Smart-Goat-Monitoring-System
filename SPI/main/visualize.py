import json
import socket
import sys
import time
from datetime import datetime

from PyQt5 import QtCore, QtGui, QtWidgets

# 导入模块
from behavior_monitor import (
    Alert,
    BehaviorMonitor,
    UNKNOWN_BEHAVIOR,
    normalize_behavior_label,
)
from data_storage import DataStorage
from history_viewer import HistoryViewer
from model_performance_widget import AccelerationWaveformWidget, BehaviorTimelineWidget
from ui_theme import APP_STYLE, BEHAVIOR_COLORS, COLORS, apply_shadow

DEFAULT_SOFTAP_IP = "192.168.4.1"
BROADCAST_IP = "255.255.255.255"

BEHAVIOR_NAMES_CN = {
    "Displacement": "位移",
    "Grazing": "采食",
    "Ruminating_Chewing": "反刍",
    "Other": "其他",
    "Resting": "休息",
    UNKNOWN_BEHAVIOR: "未知",
}


class UdpReceiver(QtCore.QObject):
    packet_received = QtCore.pyqtSignal(dict, str, str)  # data, raw, sender_ip
    status_changed = QtCore.pyqtSignal(str)

    def __init__(self, port=5005, host="0.0.0.0"):
        super().__init__()
        self.port = port
        self.host = host
        self.running = False
        self.sock = None

    @QtCore.pyqtSlot()
    def run(self):
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.settimeout(0.3)
        try:
            self.sock.bind((self.host, self.port))
            self.status_changed.emit(f"监听中: {self.host}:{self.port}")
        except OSError as e:
            self.status_changed.emit(f"绑定失败: {e}")
            self.running = False
            return

        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
                raw = data.decode("utf-8", errors="replace")
                self.packet_received.emit(json.loads(raw), raw, addr[0])
            except socket.timeout:
                continue
            except Exception:
                continue

        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.status_changed.emit("监听已停止")

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass


class PieChart(QtWidgets.QWidget):
    """饼图显示行为分类统计"""
    def __init__(self):
        super().__init__()
        self.data = {}  # {behavior: count}
        self.colors = {
            "Displacement": BEHAVIOR_COLORS["Displacement"],
            "Grazing": BEHAVIOR_COLORS["Grazing"],
            "Ruminating_Chewing": BEHAVIOR_COLORS["Ruminating_Chewing"],
            "Other": BEHAVIOR_COLORS["Other"],
            "Resting": BEHAVIOR_COLORS["Resting"],
            UNKNOWN_BEHAVIOR: BEHAVIOR_COLORS["Unknown"],
        }
        self.setMinimumSize(220, 230)
        self.setMaximumHeight(280)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def set_data(self, behavior_counts):
        self.data = behavior_counts
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        rect = self.rect().adjusted(20, 20, -20, -20)
        center = rect.center()
        radius = min(rect.width(), rect.height()) // 2 - 10

        total = sum(self.data.values()) if self.data else 0
        if total == 0:
            # 绘制空饼图
            p.setPen(QtGui.QPen(QtGui.QColor(COLORS["line"]), 2))
            p.setBrush(QtGui.QColor(COLORS["input"]))
            p.drawEllipse(center, radius, radius)
            p.setPen(QtGui.QColor(COLORS["muted"]))
            p.setFont(QtGui.QFont("Microsoft YaHei", 12))
            p.drawText(rect, QtCore.Qt.AlignCenter, "暂无数据")
            return

        # 绘制饼图
        start_angle = 90 * 16
        for behavior, count in self.data.items():
            angle = int(360 * 16 * count / total)
            color = self.colors.get(behavior, "#95a5a6")
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(color))
            p.drawPie(center.x() - radius, center.y() - radius,
                     radius * 2, radius * 2, start_angle, angle)
            start_angle += angle

        # 绘制中心圆
        inner_radius = radius * 0.5
        p.setBrush(QtGui.QColor(COLORS["paper"]))
        p.drawEllipse(center, int(inner_radius), int(inner_radius))

        # 绘制总数
        p.setPen(QtGui.QColor(COLORS["green"]))
        p.setFont(QtGui.QFont("Microsoft YaHei", 16, QtGui.QFont.Bold))
        p.drawText(rect, QtCore.Qt.AlignCenter, f"{total}")


class Gauge(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.v = 0.0
        self.setMinimumHeight(170)

    def set_value(self, v):
        self.v = max(0.0, min(1.0, float(v)))
        self.update()

    def color(self):
        if self.v > 0.8:
            return QtGui.QColor(COLORS["green"])
        if self.v >= 0.5:
            return QtGui.QColor(COLORS["gold"])
        return QtGui.QColor(COLORS["rust"])

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = self.rect().adjusted(16, 16, -16, -16)
        side = min(r.width(), r.height() * 2)
        a = QtCore.QRectF(r.center().x() - side / 2, r.bottom() - side, side, side)

        p.setPen(QtGui.QPen(QtGui.QColor(COLORS["line"]), 12))
        p.drawArc(a, 180 * 16, 180 * 16)
        p.setPen(QtGui.QPen(self.color(), 12))
        p.drawArc(a, 180 * 16, int(180 * self.v * 16))

        p.setPen(QtGui.QColor(COLORS["ink"]))
        p.setFont(QtGui.QFont("Microsoft YaHei", 22, QtGui.QFont.Bold))
        p.drawText(r, QtCore.Qt.AlignCenter, f"{self.v * 100:.1f}%")


class CategoryProbabilityWidget(QtWidgets.QWidget):
    """显示推理包 scores 字段中的五类概率。"""

    order = ["Displacement", "Grazing", "Ruminating_Chewing", "Other", "Resting"]

    def __init__(self):
        super().__init__()
        self.rows = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        for behavior in self.order:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(10)

            name = QtWidgets.QLabel(BEHAVIOR_NAMES_CN.get(behavior, behavior))
            name.setObjectName("probName")
            name.setFixedWidth(54)

            bar = QtWidgets.QProgressBar()
            bar.setObjectName("probBar")
            bar.setRange(0, 1000)
            bar.setTextVisible(False)
            bar.setFixedHeight(10)

            value = QtWidgets.QLabel("0.0")
            value.setObjectName("probValue")
            value.setFixedWidth(42)
            value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

            row.addWidget(name)
            row.addWidget(bar, 1)
            row.addWidget(value)
            layout.addLayout(row)
            self.rows[behavior] = (bar, value)

        layout.addStretch(1)
        self.update_scores({})

    def update_scores(self, scores, active_behavior=None, confidence=0.0):
        if not isinstance(scores, dict):
            scores = {}
        for behavior in self.order:
            raw = scores.get(behavior, 0.0)
            try:
                score = float(raw)
            except (TypeError, ValueError):
                score = 0.0
            if behavior == active_behavior and score <= 0 and confidence:
                score = confidence
            score = max(0.0, min(1.0, score))
            bar, value = self.rows[behavior]
            bar.setValue(int(score * 1000))
            value.setText(f"{score * 100:.1f}")


class MetricCard(QtWidgets.QFrame):
    def __init__(self, title, value, subtitle, accent):
        super().__init__()
        self.setObjectName("metricCard")
        self.setProperty("accentColor", accent)
        self.setMinimumHeight(120)
        apply_shadow(self, blur=16, y=5, alpha=16)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 13, 14, 13)
        layout.setSpacing(7)

        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setObjectName("metricTitle")
        self.value_label = QtWidgets.QLabel(value)
        self.value_label.setObjectName("metricValue")
        self.subtitle_label = QtWidgets.QLabel(subtitle)
        self.subtitle_label.setObjectName("metricSub")
        self.subtitle_label.setWordWrap(True)

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addStretch(1)
        layout.addWidget(self.subtitle_label)

        self.setStyleSheet(f"""
            QFrame#metricCard {{
                background:#ffffff;
                border:1px solid #dfe8e3;
                border-top:3px solid {accent};
                border-radius:8px;
            }}
        """)

    def set_value(self, value, subtitle=None):
        self.value_label.setText(str(value))
        if subtitle is not None:
            self.subtitle_label.setText(str(subtitle))


class App(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESP32 行为识别监控台")
        self.resize(1400, 880)

        self.infer_recv = None
        self.infer_th = None
        self.accel_recv = None
        self.accel_th = None
        self.pkt = 0
        self.last_t = 0.0
        self.last_accel_t = 0.0
        self.last_infer_t = 0.0
        self.blink = False
        self.last_act = None
        self.esp_id = None
        self.esp_ip = None
        self.devices = {}
        self.selected_device_key = None
        self.accel_pts = 0
        self.behavior_counts = {}
        self._last_sd_state = None

        # 初始化行为监测和数据存储
        self.behavior_monitor = BehaviorMonitor()
        self.low_conf = self.behavior_monitor.min_confidence
        self.data_storage = DataStorage()
        self.behavior_monitor.on_alert = self.on_alert_triggered

        # 告警列表
        self.alert_list = []

        self.build_ui()
        self.timers()
        self.log_operation("界面启动")

    def build_ui(self):
        c = QtWidgets.QWidget()
        self.setCentralWidget(c)
        v = QtWidgets.QVBoxLayout(c)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(16)

        # 顶部品牌栏
        titlebar = QtWidgets.QFrame()
        titlebar.setObjectName("titlebar")
        apply_shadow(titlebar, blur=18, y=5, alpha=14)
        tb = QtWidgets.QHBoxLayout(titlebar)
        tb.setContentsMargins(18, 12, 18, 12)
        tb.setSpacing(14)

        logo = QtWidgets.QLabel("L")
        logo.setObjectName("brandMark")
        logo.setAlignment(QtCore.Qt.AlignCenter)
        logo.setFixedSize(42, 42)
        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(2)
        title = QtWidgets.QLabel("山羊行为智能监测上位机")
        title.setObjectName("appTitle")
        subtitle = QtWidgets.QLabel("LXSPI Monitor · ESP32-S3 边缘推理 · PC 实时监控界面")
        subtitle.setObjectName("appSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        tb.addWidget(logo)
        tb.addLayout(title_box)
        tb.addStretch(1)

        self.time_label = QtWidgets.QLabel(time.strftime("%Y-%m-%d %H:%M:%S"))
        self.time_label.setObjectName("topChip")
        self.top_device_label = QtWidgets.QLabel("设备 ESP")
        self.top_device_label.setObjectName("topChip")
        self.listen_status_label = QtWidgets.QLabel("未监听")
        self.listen_status_label.setObjectName("onlineChip")
        self.light = QtWidgets.QLabel()
        self.light.setFixedSize(10, 10)
        tb.addWidget(self.light)
        tb.addWidget(self.listen_status_label)
        tb.addWidget(self.top_device_label)
        tb.addWidget(self.time_label)
        v.addWidget(titlebar)

        # 行为与模型指标
        hero_row = QtWidgets.QHBoxLayout()
        hero_row.setSpacing(12)
        hero_card = QtWidgets.QFrame()
        hero_card.setObjectName("heroCard")
        apply_shadow(hero_card, blur=18, y=6, alpha=18)
        hero_layout = QtWidgets.QVBoxLayout(hero_card)
        hero_layout.setContentsMargins(20, 16, 20, 16)
        hero_layout.setSpacing(10)
        overline = QtWidgets.QLabel("REAL-TIME BEHAVIOR RECOGNITION")
        overline.setObjectName("overline")
        self.act = QtWidgets.QLabel("等待数据")
        self.act.setObjectName("behavior")
        self.conf_label = QtWidgets.QLabel("置信度: --")
        self.conf_label.setObjectName("confidence")
        behavior_line = QtWidgets.QHBoxLayout()
        behavior_line.addWidget(self.act, 1)
        behavior_line.addWidget(self.conf_label)
        hero_layout.addWidget(overline)
        hero_layout.addStretch(1)
        hero_layout.addLayout(behavior_line)
        hero_row.addWidget(hero_card, 3)

        self.acc_metric = MetricCard("总体准确率", "94.6%", "5 折交叉验证平均", COLORS["green"])
        self.speed_metric = MetricCard("端侧推理", "23 ms", "ESP32-S3 INT8 模型", COLORS["gold"])
        self.f1_metric = MetricCard("宏平均 F1", "80.1%", "五类行为等权平均", COLORS["blue"])
        self.battery_metric = MetricCard("电池估算", "--", "等待设备上报", COLORS["red"])
        for card in (self.acc_metric, self.speed_metric, self.f1_metric, self.battery_metric):
            hero_row.addWidget(card, 1)
        v.addLayout(hero_row)

        content = QtWidgets.QHBoxLayout()
        content.setSpacing(16)

        main_col = QtWidgets.QVBoxLayout()
        main_col.setSpacing(16)
        wave_panel = self._panel("三轴加速度实时波形", "26 点/s")
        self.waveform_widget = AccelerationWaveformWidget()
        self.waveform_widget.setMinimumHeight(330)
        self.waveform_widget.setMaximumHeight(16777215)
        wave_panel["body"].addWidget(self.waveform_widget)
        main_col.addWidget(wave_panel["frame"], 1)

        lower = QtWidgets.QHBoxLayout()
        lower.setSpacing(16)
        timeline_panel = self._panel("最近识别时间轴", "近 10 次窗口")
        self.timeline_widget = BehaviorTimelineWidget()
        timeline_panel["body"].addWidget(self.timeline_widget)
        lower.addWidget(timeline_panel["frame"], 1)
        log_panel = self._panel("操作记录", "UDP 5005 / 6006")
        self.operation_log = QtWidgets.QTextEdit()
        self.operation_log.setReadOnly(True)
        self.operation_log.setMinimumHeight(112)
        self.operation_log.setObjectName("operationLog")
        log_panel["body"].addWidget(self.operation_log)
        lower.addWidget(log_panel["frame"], 1)
        main_col.addLayout(lower)
        content.addLayout(main_col, 7)

        insight_col = QtWidgets.QVBoxLayout()
        insight_col.setSpacing(16)
        prob_panel = self._panel("类别概率输出", "Stable")
        self.probability_widget = CategoryProbabilityWidget()
        prob_panel["body"].addWidget(self.probability_widget)
        insight_col.addWidget(prob_panel["frame"])

        control_panel = self._panel("设备控制", "命令端口")
        ctrl_grid = QtWidgets.QGridLayout()
        ctrl_grid.setHorizontalSpacing(8)
        ctrl_grid.setVerticalSpacing(8)
        self.port = QtWidgets.QLineEdit("5005")
        self.port.setFixedWidth(80)
        self.ctrl_port = QtWidgets.QLineEdit("6006")
        self.ctrl_port.setFixedWidth(80)
        ctrl_grid.addWidget(QtWidgets.QLabel("推断端口"), 0, 0)
        ctrl_grid.addWidget(self.port, 0, 1)
        ctrl_grid.addWidget(QtWidgets.QLabel("控制端口"), 0, 2)
        ctrl_grid.addWidget(self.ctrl_port, 0, 3)

        self.btn = QtWidgets.QPushButton("启动监听")
        self.btn.setObjectName("primary")
        self.btn.clicked.connect(self.toggle)
        self.dev_start = QtWidgets.QPushButton("启动采集")
        self.dev_start.setObjectName("primary")
        self.wave_on_btn = QtWidgets.QPushButton("打开波形")
        self.wave_off_btn = QtWidgets.QPushButton("关闭波形")
        self.dev_pause_record = QtWidgets.QPushButton("暂停记录")
        self.dev_pause_stream = QtWidgets.QPushButton("暂停采集")
        self.sync_btn = QtWidgets.QPushButton("时间同步")
        self.mount_btn = QtWidgets.QPushButton("重新挂载SD")
        self.history_btn = QtWidgets.QPushButton("历史数据")
        self.reset_btn = QtWidgets.QPushButton("重置设备")
        self.reset_btn.setObjectName("danger")

        buttons = [
            self.btn, self.dev_start, self.wave_on_btn, self.wave_off_btn,
            self.dev_pause_record, self.dev_pause_stream, self.sync_btn,
            self.mount_btn, self.history_btn, self.reset_btn,
        ]
        for i, button in enumerate(buttons):
            row = 1 + i // 2
            col = (i % 2) * 2
            ctrl_grid.addWidget(button, row, col, 1, 2)

        self.dev_start.clicked.connect(self.start_collection)
        self.wave_on_btn.clicked.connect(lambda: self.send_ctrl("ACCELON"))
        self.wave_off_btn.clicked.connect(lambda: self.send_ctrl("ACCELOFF"))
        self.dev_pause_record.clicked.connect(lambda: self.send_ctrl("STOPREC"))
        self.dev_pause_stream.clicked.connect(lambda: self.send_ctrl("PAUSE"))
        self.sync_btn.clicked.connect(lambda: self.send_time_sync())
        self.mount_btn.clicked.connect(lambda: self.send_ctrl("MOUNT"))
        self.history_btn.clicked.connect(self.open_history_viewer)
        self.reset_btn.clicked.connect(self.reset_device)
        control_panel["body"].addLayout(ctrl_grid)
        insight_col.addWidget(control_panel["frame"])

        alert_panel = self._panel("最近告警", "最多 5 条")
        self.alert_text = QtWidgets.QTextEdit()
        self.alert_text.setReadOnly(True)
        self.alert_text.setMinimumHeight(130)
        self.alert_text.setObjectName("alertText")
        alert_panel["body"].addWidget(self.alert_text)
        insight_col.addWidget(alert_panel["frame"], 1)
        content.addLayout(insight_col, 4)

        side_scroll = QtWidgets.QScrollArea()
        side_scroll.setObjectName("rightScroll")
        side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        side_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        side_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

        side_body = QtWidgets.QWidget()
        side_body.setObjectName("rightBody")
        side_body.setMinimumWidth(350)
        side_col = QtWidgets.QVBoxLayout(side_body)
        side_col.setContentsMargins(0, 0, 8, 0)
        side_col.setSpacing(16)

        device_panel = self._panel("设备状态", "自动发现")
        device_panel["frame"].setMinimumHeight(230)
        device_grid = QtWidgets.QGridLayout()
        device_grid.setHorizontalSpacing(10)
        device_grid.setVerticalSpacing(8)

        self.current_device_label = QtWidgets.QLabel("当前设备: ESP")
        self.current_device_label.setObjectName("deviceField")
        self.current_device_no_label = QtWidgets.QLabel("设备编号: --")
        self.current_device_no_label.setObjectName("deviceField")
        self.current_device_ip_label = QtWidgets.QLabel("设备IP: --")
        self.current_device_ip_label.setObjectName("deviceField")
        self.current_device_net_label = QtWidgets.QLabel("网络模式: --")
        self.current_device_net_label.setObjectName("deviceField")

        self.device_list = QtWidgets.QListWidget()
        self.device_list.setMaximumHeight(76)
        self.device_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.device_list.currentItemChanged.connect(self.on_device_selected)
        device_grid.addWidget(self.current_device_label, 0, 0, 1, 2)
        device_grid.addWidget(self.current_device_no_label, 1, 0)
        device_grid.addWidget(self.current_device_ip_label, 1, 1)
        device_grid.addWidget(self.current_device_net_label, 2, 0, 1, 2)
        device_grid.addWidget(self.device_list, 3, 0, 1, 2)
        device_panel["body"].addLayout(device_grid)
        side_col.addWidget(device_panel["frame"])

        pie_panel = self._panel("今日行为占比", "本次运行")
        pie_panel["frame"].setMinimumHeight(330)
        self.pie_chart = PieChart()
        self.pie_chart.setMinimumHeight(240)
        pie_panel["body"].addWidget(self.pie_chart)
        side_col.addWidget(pie_panel["frame"])

        stats_panel = self._panel("统计信息", "Live")
        stats_panel["frame"].setMinimumHeight(235)
        stats_grid = QtWidgets.QGridLayout()
        stats_grid.setHorizontalSpacing(10)
        stats_grid.setVerticalSpacing(10)
        self.rate = QtWidgets.QLabel("吞吐量: 0 pkt/s")
        self.rate.setObjectName("statBox")
        self.accel_label = QtWidgets.QLabel("加速度: 0 点/s")
        self.accel_label.setObjectName("statBox")
        self.duration_label = QtWidgets.QLabel("持续时间: 0秒")
        self.duration_label.setObjectName("statBox")
        self.health_label = QtWidgets.QLabel("健康评分: 100")
        self.health_label.setObjectName("statBox")
        self.battery_label = QtWidgets.QLabel("电池: -- %")
        self.battery_label.setObjectName("statBox")
        self.sd_label = QtWidgets.QLabel("SD卡: 未知")
        self.sd_label.setObjectName("statBox")
        stats_labels = [
            self.rate, self.accel_label, self.health_label,
            self.duration_label, self.battery_label, self.sd_label,
        ]
        for i, label in enumerate(stats_labels):
            label.setMinimumHeight(52)
            label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            stats_grid.addWidget(label, i // 2, i % 2)
        stats_panel["body"].addLayout(stats_grid)
        side_col.addWidget(stats_panel["frame"])

        risk_panel = self._panel("三大健康风险指标", "2 条提示")
        risk_panel["frame"].setMinimumHeight(215)
        risk_layout = QtWidgets.QVBoxLayout()
        risk_layout.setContentsMargins(0, 0, 0, 0)
        risk_layout.setSpacing(10)

        self.circadian_indicator = QtWidgets.QLabel("昼夜活动节律: 正常")
        self.circadian_indicator.setObjectName("risk_normal")
        self.circadian_indicator.setWordWrap(True)

        self.grazing_ruminating_indicator = QtWidgets.QLabel("采食-反刍节律: 正常")
        self.grazing_ruminating_indicator.setObjectName("risk_normal")
        self.grazing_ruminating_indicator.setWordWrap(True)

        self.activity_grazing_indicator = QtWidgets.QLabel("高活动-低采食: 正常")
        self.activity_grazing_indicator.setObjectName("risk_normal")
        self.activity_grazing_indicator.setWordWrap(True)

        for risk_label in (
            self.circadian_indicator,
            self.grazing_ruminating_indicator,
            self.activity_grazing_indicator,
        ):
            risk_label.setMinimumHeight(34)
            risk_label.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Minimum)

        risk_layout.addWidget(self.circadian_indicator)
        risk_layout.addWidget(self.grazing_ruminating_indicator)
        risk_layout.addWidget(self.activity_grazing_indicator)
        risk_panel["body"].addLayout(risk_layout)
        side_col.addWidget(risk_panel["frame"])
        side_col.addStretch(1)
        side_scroll.setWidget(side_body)
        content.addWidget(side_scroll, 3)

        v.addLayout(content, 1)

        self.red()
        self.setStyleSheet(APP_STYLE)

    def _panel(self, title, chip_text=None):
        frame = QtWidgets.QFrame()
        frame.setObjectName("panel")
        apply_shadow(frame, blur=18, y=6, alpha=14)
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QtWidgets.QFrame()
        head.setObjectName("panelHead")
        head_layout = QtWidgets.QHBoxLayout(head)
        head_layout.setContentsMargins(16, 0, 16, 0)
        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("panelTitle")
        head_layout.addWidget(title_label)
        head_layout.addStretch(1)
        if chip_text:
            chip = QtWidgets.QLabel(chip_text)
            chip.setObjectName("panelChip")
            head_layout.addWidget(chip)
        layout.addWidget(head)

        body = QtWidgets.QFrame()
        body.setObjectName("panelBody")
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(16, 14, 16, 16)
        body_layout.setSpacing(10)
        layout.addWidget(body, 1)
        return {"frame": frame, "body": body_layout}

    def timers(self):
        self.r = QtCore.QTimer(self); self.r.timeout.connect(self.refresh_rate); self.r.start(1000)
        self.b = QtCore.QTimer(self); self.b.timeout.connect(self.indicator); self.b.start(350)
        self.t = QtCore.QTimer(self); self.t.timeout.connect(self.update_time); self.t.start(1000)

    def update_time(self):
        """更新时间显示"""
        self.time_label.setText(time.strftime("%Y-%m-%d %H:%M:%S"))

    def _device_number(self, device_id):
        text = str(device_id or "").strip()
        if text.startswith("ESP-"):
            return text.split("-", 1)[1] or "--"
        if text:
            return text
        return "--"

    def _network_mode_for_ip(self, ip):
        if not ip:
            return "--"
        if str(ip).startswith("192.168.4."):
            return "SoftAP兜底"
        return "外部热点"

    def _device_display_name(self, device_id):
        number = self._device_number(device_id)
        return f"ESP-{number}" if number != "--" else "ESP"

    def _set_waiting_device_state(self, message="等待发现"):
        self.top_device_label.setText("设备 ESP")
        self.current_device_label.setText("当前设备: ESP")
        self.current_device_no_label.setText("设备编号: --")
        self.current_device_ip_label.setText(f"设备IP: {message}")
        self.current_device_net_label.setText("网络模式: --")

    def _apply_current_device_labels(self, info):
        device_id = info.get("id")
        ip = info.get("ip", "--")
        number = self._device_number(device_id)
        mode = self._network_mode_for_ip(ip)

        self.top_device_label.setText(f"设备 ESP-{number}" if number != "--" else "设备 ESP")
        self.current_device_label.setText("当前设备: ESP")
        self.current_device_no_label.setText(f"设备编号: {number}")
        self.current_device_ip_label.setText(f"设备IP: {ip}")
        self.current_device_net_label.setText(f"网络模式: {mode}")

    def _set_current_device(self, device_key):
        info = self.devices.get(device_key)
        if not info:
            return
        self.selected_device_key = device_key
        self.esp_id = info.get("id")
        self.esp_ip = info.get("ip")
        self._apply_current_device_labels(info)

    def _refresh_device_panel(self):
        if not hasattr(self, "device_list"):
            return

        self.device_list.blockSignals(True)
        self.device_list.clear()

        items = sorted(
            self.devices.items(),
            key=lambda kv: kv[1].get("last_seen", 0.0),
            reverse=True,
        )
        selected_row = -1
        for row, (key, info) in enumerate(items):
            display_name = self._device_display_name(info.get("id"))
            ip = info.get("ip", "--")
            mode = self._network_mode_for_ip(ip)
            text = f"{display_name}    {ip}    {mode}"
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, key)
            self.device_list.addItem(item)
            if key == self.selected_device_key:
                selected_row = row

        if selected_row >= 0:
            self.device_list.setCurrentRow(selected_row)

        self.device_list.blockSignals(False)

    def on_device_selected(self, current, _previous):
        if current is None:
            return
        device_key = current.data(QtCore.Qt.UserRole)
        self._set_current_device(device_key)

    def _discover_device(self, sender_ip, device_id=None):
        device_id = str(device_id or "").strip()
        if not device_id:
            device_id = None

        device_key = device_id or sender_ip
        old_ip = self.devices.get(device_key, {}).get("ip")
        is_new_device = device_key not in self.devices

        self.devices[device_key] = {
            "id": device_id,
            "ip": sender_ip,
            "last_seen": time.time(),
        }

        if is_new_device or old_ip != sender_ip:
            self.log_operation(f"发现设备 {self._device_display_name(device_id)} @ {sender_ip}")
            self.send_time_sync(sender_ip)

        if self.selected_device_key is None:
            self.selected_device_key = device_key

        if self.selected_device_key == device_key:
            self._set_current_device(device_key)

        self._refresh_device_panel()

    def log_operation(self, message):
        if hasattr(self, "operation_log"):
            stamp = time.strftime("%H:%M:%S")
            self.operation_log.append(f"[{stamp}] {message}")
            cursor = self.operation_log.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End)
            self.operation_log.setTextCursor(cursor)

    def _control_targets(self, target_ip=None):
        if target_ip:
            return [target_ip]
        if self.esp_ip:
            return [self.esp_ip]
        return [BROADCAST_IP, DEFAULT_SOFTAP_IP]

    def send_time_sync(self, target_ip=None):
        targets = self._control_targets(target_ip)
        if not targets:
            QtWidgets.QMessageBox.warning(
                self, "设备未发现",
                "尚未收到 ESP32 数据包，无法发送时间同步。\n请先启动监听并等待设备自动发现。")
            self.log_operation("时间同步失败: 未发现设备")
            return
        try:
            port = int(self.ctrl_port.text().strip())
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "控制端口错误", "请输入 1~65535 的控制端口")
            self.log_operation("时间同步失败: 控制端口无效")
            return

        try:
            unix_ms = int(time.time() * 1000)
            msg = f"SYNC:{unix_ms}"
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sent = []
            last_error = None
            for ip in targets:
                try:
                    s.sendto(msg.encode("utf-8"), (ip, port))
                    sent.append(ip)
                except OSError as e:
                    last_error = e
            s.close()
            if sent:
                dst = sent[0] if len(sent) == 1 else "广播/默认地址"
                self.log_operation(f"发送时间同步 -> {dst}:{port}")
                return True
            if last_error:
                raise last_error
        except OSError as e:
            self.log_operation(f"时间同步发送失败: {e}")
            return False

    def send_ctrl(self, cmd):
        targets = self._control_targets()
        if not targets:
            QtWidgets.QMessageBox.warning(
                self, "设备未发现",
                "尚未收到 ESP32 数据包，无法发送控制命令。\n请先启动监听并等待设备自动发现。")
            self.log_operation(f"控制命令 {cmd} 失败: 未发现设备")
            return
        try:
            port = int(self.ctrl_port.text().strip())
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "控制端口错误", "请输入 1~65535 的控制端口")
            self.log_operation(f"控制命令 {cmd} 失败: 控制端口无效")
            return

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sent = []
            last_error = None
            for ip in targets:
                try:
                    s.sendto(cmd.encode("utf-8"), (ip, port))
                    sent.append(ip)
                except OSError as e:
                    last_error = e
            s.close()
            if not sent and last_error:
                raise last_error
            name_map = {
                "START": "启动采集",
                "ACCELON": "打开波形",
                "ACCELOFF": "关闭波形",
                "STOPREC": "暂停记录",
                "PAUSE": "暂停采集",
                "MOUNT": "重新挂载SD",
                "RESET": "重置设备",
            }
            dst = sent[0] if len(sent) == 1 else "广播/默认地址"
            self.log_operation(f"发送{name_map.get(cmd, cmd)} -> {dst}:{port}")
            return True
        except OSError as e:
            self.log_operation(f"控制命令 {cmd} 发送失败: {e}")
            return False

    def start_collection(self):
        """开始采集前自动同步时间"""
        self.log_operation("点击启动采集")
        # 先同步时间
        self.send_time_sync()
        # 等待100ms让ESP32处理时间同步
        QtCore.QTimer.singleShot(100, lambda: self.send_ctrl("START"))

    def reset_device(self):
        """发送 RESET 命令让 ESP32 软重启（用于卡死自救）"""
        if not self.esp_ip:
            QtWidgets.QMessageBox.warning(
                self, "设备未发现",
            "尚未收到 ESP32 数据包，无法重置。请先启动监听并等待设备自动发现。")
            return
        ans = QtWidgets.QMessageBox.question(
            self, "确认重置设备",
            "将向 ESP32 发送 RESET 命令使其软重启，重启过程约 5 秒。\n是否继续？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if ans != QtWidgets.QMessageBox.Yes:
            self.log_operation("取消重置设备")
            return
        self.log_operation("确认重置设备")
        self.send_ctrl("RESET")
        # 重置后清空 PC 端接收状态，重启后会自动重新发现设备
        self.last_t = 0.0
        self.last_accel_t = 0.0
        self.last_infer_t = 0.0
        self.esp_id = None
        self.esp_ip = None
        self.devices.clear()
        self.selected_device_key = None
        self._refresh_device_panel()
        self._set_waiting_device_state("等待重启")
        self.red()

    def toggle(self):
        if self.infer_th and self.infer_th.isRunning():
            self.stop(); return
        try:
            infer_port = int(self.port.text().strip())
            if not (1 <= infer_port <= 65535):
                raise ValueError
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "端口错误", "请输入 1~65535 端口")
            return

        accel_port = infer_port + 2  # 5007

        # Inference receiver (port 5005)
        self.infer_recv = UdpReceiver(port=infer_port)
        self.infer_th = QtCore.QThread(self)
        self.infer_recv.moveToThread(self.infer_th)
        self.infer_th.started.connect(self.infer_recv.run)
        self.infer_recv.packet_received.connect(self.on_infer_packet)
        self.infer_recv.status_changed.connect(self.on_status)

        # Accel receiver (port 5007)
        self.accel_recv = UdpReceiver(port=accel_port)
        self.accel_th = QtCore.QThread(self)
        self.accel_recv.moveToThread(self.accel_th)
        self.accel_th.started.connect(self.accel_recv.run)
        self.accel_recv.packet_received.connect(self.on_accel_packet)
        self.accel_recv.status_changed.connect(
            lambda m: self.on_status(f"加速度: {m}"))

        self.infer_th.start()
        self.accel_th.start()
        self.btn.setText("停止监听")
        self.listen_status_label.setText("监听中")
        self._set_waiting_device_state("等待连接")
        self.log_operation(f"启动监听: 推断端口 {infer_port}, 加速度端口 {accel_port}")

    def stop(self):
        self.log_operation("停止监听")
        for recv in (self.infer_recv, self.accel_recv):
            if recv:
                recv.stop()
        for th in (self.infer_th, self.accel_th):
            if th:
                th.quit(); th.wait(500)
        self.infer_recv = self.accel_recv = None
        self.infer_th = self.accel_th = None
        self.btn.setText("启动监听")
        self.listen_status_label.setText("未监听")
        self.esp_id = None
        self.esp_ip = None
        self.devices.clear()
        self.selected_device_key = None
        self._refresh_device_panel()
        self._set_waiting_device_state()
        self.red()

    @QtCore.pyqtSlot(str)
    def on_status(self, message):
        if message:
            self.log_operation(message)

    def _update_sd_label(self, sd_info):
        if not isinstance(sd_info, dict) or not sd_info:
            return
        mounted = sd_info.get("mounted", False)
        recording = sd_info.get("recording", False)
        state = (bool(mounted), bool(recording))
        if not mounted:
            self.sd_label.setText("SD卡: 未挂载（请检查插卡/格式）")
            self.sd_label.setStyleSheet(
                f"background:#fbfcfb;border:1px solid #e6ebe8;border-radius:8px;"
                f"color:{COLORS['red']};font:800 13px 'Microsoft YaHei UI';padding:8px 10px;"
            )
        elif recording:
            self.sd_label.setText("SD卡: 记录中")
            self.sd_label.setStyleSheet(
                f"background:#fbfcfb;border:1px solid #e6ebe8;border-radius:8px;"
                f"color:{COLORS['ok']};font:800 13px 'Microsoft YaHei UI';padding:8px 10px;"
            )
        else:
            self.sd_label.setText("SD卡: 已挂载（未记录）")
            self.sd_label.setStyleSheet(
                f"background:#fbfcfb;border:1px solid #e6ebe8;border-radius:8px;"
                f"color:{COLORS['gold']};font:800 13px 'Microsoft YaHei UI';padding:8px 10px;"
            )
        if state != self._last_sd_state:
            if not mounted:
                text = "SD 状态: 未挂载"
            elif recording:
                text = "SD 状态: 记录中"
            else:
                text = "SD 状态: 已挂载，未记录"
            self.log_operation(text)
            self._last_sd_state = state
    @QtCore.pyqtSlot(dict, str, str)
    def on_infer_packet(self, d, raw, sender_ip):
        now = time.time()
        self.last_t = now
        self.last_infer_t = now
        self.pkt += 1
        self._discover_device(sender_ip, d.get("dev"))
        self._update_sd_label(d.get("sd", {}))

        raw_act = str(d.get("act", "--"))
        act = normalize_behavior_label(raw_act)
        conf = d.get("conf", 0.0)
        try:
            c = float(conf)
        except (TypeError, ValueError):
            c = 0.0

        is_countable = act != UNKNOWN_BEHAVIOR and c >= self.low_conf
        display_act = act if is_countable else UNKNOWN_BEHAVIOR
        self.act.setText(BEHAVIOR_NAMES_CN.get(display_act, display_act))
        self.timeline_widget.add_behavior(display_act, c)
        self.probability_widget.update_scores(d.get("scores", {}), display_act, c)

        # 更新电池信息
        battery_info = d.get("battery", {})
        if battery_info:
            voltage = battery_info.get("voltage", 0)
            percentage = battery_info.get("percentage", 0)
            status = battery_info.get("status", "Unknown")

            # 根据电量显示不同颜色
            if percentage >= 80:
                color = COLORS["ok"]
                icon = "电池估算"
            elif percentage >= 50:
                color = COLORS["gold"]
                icon = "电池估算"
            elif percentage >= 20:
                color = "#ff9500"
                icon = "电池估算"
            else:
                color = COLORS["red"]
                icon = "电池估算"

            self.battery_label.setText(f"{icon}: {percentage}% ({voltage}mV)")
            self.battery_label.setStyleSheet(
                f"background:#fbfcfb;border:1px solid #e6ebe8;border-radius:8px;"
                f"color:{color};font:800 13px 'Microsoft YaHei UI';padding:8px 10px;"
            )
            self.battery_metric.set_value(f"{percentage}%", f"{voltage} mV · {status}")

            # 低电量告警
            if percentage <= 15 and percentage > 0:
                alert = Alert(
                    timestamp=time.time(),
                    alert_type="low_battery",
                    message=f"电池电量过低 ({percentage}%)，请及时充电",
                    severity="warning",
                    behavior=None,
                    duration=None
                )
                self.show_alert(alert)

        # SD 状态由 _update_sd_label 统一处理

        # 更新行为监测器。低置信度和未知标签会在监测器内自动过滤。
        try:
            alerts = self.behavior_monitor.update_behavior(raw_act, c)
            stable_act = self.behavior_monitor.last_accepted_behavior
            if stable_act:
                self.behavior_counts = self.behavior_monitor.behavior_counts.copy()
                self.pie_chart.set_data(self.behavior_counts)
                self.data_storage.save_behavior(time.time(), stable_act, c)

            for alert in alerts:
                self.show_alert(alert)
                self.data_storage.save_alert(
                    alert.timestamp, alert.alert_type, alert.message,
                    alert.severity, alert.behavior, alert.duration
                )
        except Exception as e:
            print(f"行为监测错误: {e}")

        if self.last_act and display_act != self.last_act:
            pass
        self.last_act = display_act

        if c < self.low_conf:
            self.conf_label.setText(f"{c * 100:.1f}% 不计入")
            self.conf_label.setStyleSheet(
                f"color:{COLORS['green_2']};background:#fff7e7;border-radius:8px;"
                "padding:9px 16px;font:900 16px 'Microsoft YaHei UI';"
            )
        else:
            self.conf_label.setText(f"{c * 100:.1f}%")
            self.conf_label.setStyleSheet(
                f"color:{COLORS['green_2']};background:#eadfc7;border-radius:8px;"
                "padding:9px 16px;font:900 16px 'Microsoft YaHei UI';"
            )

    @QtCore.pyqtSlot(dict, str, str)
    def on_accel_packet(self, d, raw, sender_ip):
        now = time.time()
        self.last_t = now
        self.last_accel_t = now
        self.pkt += 1
        self._discover_device(sender_ip, d.get("dev"))
        self._update_sd_label(d.get("sd", {}))

        acc = d.get("acc", [])
        if isinstance(acc, list):
            for p in acc:
                if isinstance(p, (list, tuple)) and len(p) >= 3:
                    try:
                        x, y, z = float(p[0]), float(p[1]), float(p[2])
                        self.accel_pts += 1

                        # 更新波形图
                        self.waveform_widget.update_data(x, y, z)

                        # 保存加速度数据到数据库（每10个点保存一次，减少IO）
                        if self.accel_pts % 10 == 0:
                            self.data_storage.save_accel(time.time(), x, y, z)
                    except Exception:
                        pass

    def refresh_rate(self):
        self.rate.setText(f"吞吐量: {self.pkt} pkt/s")
        self.accel_label.setText(f"加速度: {self.accel_pts} 点/s")

        summary = self.behavior_monitor.get_behavior_summary()
        current_behavior = summary.get("current_behavior")
        current_duration = summary.get("current_duration", 0.0)
        if current_behavior:
            self.duration_label.setText(f"{current_behavior} 持续: {int(current_duration)}秒")
        else:
            self.duration_label.setText("持续时间: 0秒")

        health_score = summary.get("health_score", 100.0)
        self.health_label.setText(f"健康评分: {health_score:.1f}")
        self.behavior_counts = summary.get("behavior_counts", {})
        self.pie_chart.set_data(self.behavior_counts)

        # 更新三大风险指标状态
        self._update_risk_indicators()

        self.pkt = 0
        self.accel_pts = 0

    def _update_risk_indicators(self):
        """更新三大健康风险指标的显示状态"""
        summary = self.behavior_monitor.get_behavior_summary()
        behavior_durations = summary.get("behavior_durations", {})

        # 指标1: 昼夜活动节律
        circadian_status = "normal"
        circadian_text = "昼夜活动节律: 正常"

        # 检查是否有昼夜活动异常告警
        for alert in self.behavior_monitor.alerts[-5:]:
            if alert.alert_type == "circadian_activity_abnormal":
                circadian_status = "warning"
                circadian_text = "昼夜活动节律: 异常"
                break

        self.circadian_indicator.setText(circadian_text)
        self.circadian_indicator.setObjectName(f"risk_{circadian_status}")
        self.circadian_indicator.setStyle(self.circadian_indicator.style())

        # 指标2: 采食-反刍节律
        grazing_sec = behavior_durations.get("Grazing", 0.0)
        ruminating_sec = behavior_durations.get("Ruminating_Chewing", 0.0)
        grazing_ruminating_status = "normal"
        grazing_ruminating_text = (
            f"采食-反刍节律: 正常 (采食{grazing_sec/60:.0f}分 / 反刍{ruminating_sec/60:.0f}分)"
        )

        # 检查是否有采食-反刍节律异常告警
        for alert in self.behavior_monitor.alerts[-5:]:
            if alert.alert_type == "grazing_ruminating_rhythm_abnormal":
                grazing_ruminating_status = "warning"
                grazing_ruminating_text = (
                    f"采食-反刍节律: 异常 (采食{grazing_sec/60:.0f}分 / 反刍{ruminating_sec/60:.0f}分)"
                )
                break
            elif alert.alert_type == "no_ruminating":
                grazing_ruminating_status = "danger"
                grazing_ruminating_text = "采食-反刍节律: 严重异常 (长时间无反刍)"
                break

        self.grazing_ruminating_indicator.setText(grazing_ruminating_text)
        self.grazing_ruminating_indicator.setObjectName(f"risk_{grazing_ruminating_status}")
        self.grazing_ruminating_indicator.setStyle(self.grazing_ruminating_indicator.style())

        # 指标3: 高活动-低采食失衡
        activity_grazing_status = "normal"
        activity_grazing_text = "高活动-低采食: 正常"

        # 检查是否有高活动-低采食失衡告警
        for alert in self.behavior_monitor.alerts[-5:]:
            if alert.alert_type == "high_activity_low_grazing":
                activity_grazing_status = "warning"
                activity_grazing_text = "高活动-低采食: 失衡"
                break

        self.activity_grazing_indicator.setText(activity_grazing_text)
        self.activity_grazing_indicator.setObjectName(f"risk_{activity_grazing_status}")
        self.activity_grazing_indicator.setStyle(self.activity_grazing_indicator.style())

    def indicator(self):
        # 以 accel 包为主判据，infer 包为兜底判据
        now = time.time()
        if (now - self.last_accel_t) < 2.5 or (now - self.last_t) < 5.0:
            self.blink = not self.blink
            self.green(COLORS["ok"] if self.blink else "#a9dfbf")
        else:
            self.red()

    def green(self, c):
        self.light.setStyleSheet(f"background:{c};border-radius:7px;border:2px solid #f5eee4;")

    def red(self):
        self.light.setStyleSheet(f"background:{COLORS['red']};border-radius:7px;border:2px solid #f5eee4;")

    def on_alert_triggered(self, alert: Alert):
        """告警触发回调"""
        self.show_alert(alert)

    def show_alert(self, alert: Alert):
        """显示告警信息（不阻塞 UDP 主循环）"""
        timestamp = datetime.fromtimestamp(alert.timestamp).strftime("%H:%M:%S")

        # 添加到告警列表
        self.alert_list.insert(0, alert)
        if len(self.alert_list) > 10:
            self.alert_list.pop()

        # 更新告警文本框
        alert_text = ""
        for a in self.alert_list[:5]:
            ts = datetime.fromtimestamp(a.timestamp).strftime("%H:%M:%S")
            severity_icon = "⚠️" if a.severity == "warning" else "\U0001f6a8"
            alert_text += f"{severity_icon} [{ts}] {a.message}\n"

        self.alert_text.setPlainText(alert_text)

        # 严重告警使用非阻塞弹窗，避免卡住 UDP 接收
        if alert.severity == "danger":
            box = QtWidgets.QMessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Warning)
            box.setWindowTitle("严重告警")
            box.setText(f"{alert.message}\n\n时间: {timestamp}")
            box.setStandardButtons(QtWidgets.QMessageBox.Ok)
            box.setWindowModality(QtCore.Qt.NonModal)
            box.show()

    def open_history_viewer(self):
        """打开历史数据查看器"""
        self.log_operation("打开历史数据窗口")
        self.history_window = HistoryViewer()
        self.history_window.show()

    def closeEvent(self, e):
        # 保存当日统计数据
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            summary = self.behavior_monitor.get_behavior_summary()
            health_score = summary["health_score"]

            self.data_storage.save_daily_stats(
                date=today,
                behavior_durations=summary["behavior_durations"],
                behavior_counts=summary["behavior_counts"],
                total_duration=summary["total_duration"],
                health_score=health_score,
                alerts_count=len(self.behavior_monitor.alerts)
            )

            self.data_storage.close()
        except Exception as e:
            print(f"保存数据失败: {e}")

        self.stop()
        super().closeEvent(e)


if __name__ == "__main__":
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    # 设置全局字体
    font = QtGui.QFont("Microsoft YaHei UI")
    font.setPointSize(10)
    app.setFont(font)

    w = App()
    w.show()
    sys.exit(app.exec())







