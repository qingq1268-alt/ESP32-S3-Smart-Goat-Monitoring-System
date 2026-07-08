"""
历史数据查看器 - 查看和分析历史行为数据
"""
import sys
from datetime import datetime

from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from behavior_monitor import UNKNOWN_BEHAVIOR, normalize_behavior_label
from data_storage import DataStorage
from ui_theme import BEHAVIOR_COLORS, COLORS, HISTORY_STYLE, apply_shadow


BEHAVIOR_ORDER = [
    "Displacement",
    "Grazing",
    "Ruminating_Chewing",
    "Other",
    "Resting",
]

BEHAVIOR_NAMES_CN = {
    "Displacement": "位移",
    "Grazing": "采食",
    "Ruminating_Chewing": "反刍",
    "Other": "其他",
    "Resting": "休息",
    UNKNOWN_BEHAVIOR: "未知",
}


def behavior_name_cn(behavior):
    normalized = normalize_behavior_label(behavior)
    return BEHAVIOR_NAMES_CN.get(normalized, "未知")


def format_hours(seconds):
    hours = max(0.0, float(seconds or 0.0)) / 3600.0
    return f"{hours:.1f}h"


class HistoryMetricCard(QtWidgets.QFrame):
    """历史页顶部指标卡片。"""

    def __init__(self, title, value="--", subtitle="", color=None):
        super().__init__()
        self.color = color or COLORS["green"]
        self.setObjectName("metricCard")
        apply_shadow(self, blur=18, y=6, alpha=14)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
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
        self.setMinimumHeight(126)
        self.setStyleSheet(f"""
            QFrame#metricCard {{
                background:#ffffff;
                border:1px solid #dfe8e3;
                border-top:3px solid {self.color};
                border-radius:8px;
            }}
        """)

    def set_value(self, value, subtitle=None):
        self.value_label.setText(str(value))
        if subtitle is not None:
            self.subtitle_label.setText(str(subtitle))


class HistoryDonutChart(QtWidgets.QWidget):
    """历史行为占比环形图。"""

    def __init__(self):
        super().__init__()
        self.values = {}
        self.setMinimumHeight(220)

    def set_data(self, values):
        self.values = {
            k: float(v)
            for k, v in values.items()
            if k != UNKNOWN_BEHAVIOR and float(v or 0.0) > 0.0
        }
        self.update()

    def paintEvent(self, _):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect().adjusted(12, 12, -12, -12)
        total = sum(self.values.values())

        if total <= 0:
            painter.setPen(QtGui.QPen(QtGui.QColor("#dfe8e3"), 2))
            painter.setBrush(QtGui.QColor("#fbfcfb"))
            side = min(rect.width(), rect.height(), 128)
            circle = QtCore.QRectF(rect.left(), rect.center().y() - side / 2, side, side)
            painter.drawEllipse(circle)
            painter.setPen(QtGui.QColor(COLORS["muted"]))
            painter.setFont(QtGui.QFont("Microsoft YaHei UI", 12, QtGui.QFont.Bold))
            painter.drawText(rect, QtCore.Qt.AlignCenter, "暂无占比数据")
            return

        side = min(rect.height(), rect.width() * 0.46, 150)
        circle = QtCore.QRectF(rect.left() + 6, rect.center().y() - side / 2, side, side)
        start_angle = 90 * 16
        for behavior in BEHAVIOR_ORDER:
            value = self.values.get(behavior, 0.0)
            if value <= 0:
                continue
            span = int(-360 * 16 * value / total)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(BEHAVIOR_COLORS[behavior]))
            painter.drawPie(circle, start_angle, span)
            start_angle += span

        inner = circle.adjusted(side * 0.24, side * 0.24, -side * 0.24, -side * 0.24)
        painter.setBrush(QtGui.QColor("#ffffff"))
        painter.drawEllipse(inner)
        painter.setPen(QtGui.QColor(COLORS["green"]))
        painter.setFont(QtGui.QFont("Microsoft YaHei UI", 17, QtGui.QFont.Black))
        painter.drawText(inner, QtCore.Qt.AlignCenter, format_hours(total))

        legend_x = int(circle.right() + 22)
        y = int(rect.top() + max(0, (rect.height() - len(BEHAVIOR_ORDER) * 28) / 2))
        painter.setFont(QtGui.QFont("Microsoft YaHei UI", 10, QtGui.QFont.Bold))
        for behavior in BEHAVIOR_ORDER:
            value = self.values.get(behavior, 0.0)
            if value <= 0:
                continue
            color = QtGui.QColor(BEHAVIOR_COLORS[behavior])
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(legend_x, y + 5, 10, 10, 3, 3)
            painter.setPen(QtGui.QColor(COLORS["ink"]))
            painter.drawText(legend_x + 18, y, 70, 22, QtCore.Qt.AlignVCenter, behavior_name_cn(behavior))
            painter.setPen(QtGui.QColor(COLORS["muted"]))
            painter.drawText(legend_x + 88, y, 70, 22, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, format_hours(value))
            y += 28


class HistoryViewer(QtWidgets.QMainWindow):
    """历史数据查看器"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("历史数据分析")
        self.resize(1380, 860)
        self.setMinimumSize(1120, 740)

        self.storage = DataStorage()
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.summary = {}

        self.init_ui()
        self.apply_styles()
        self.load_data()

    def init_ui(self):
        """初始化界面"""
        central = QtWidgets.QWidget()
        central.setObjectName("historyRoot")
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(16)

        layout.addWidget(self.create_title_bar())
        layout.addLayout(self.create_summary_row())
        layout.addLayout(self.create_content_area(), 1)

    def create_title_bar(self):
        """创建顶部品牌和操作栏"""
        frame = QtWidgets.QFrame()
        frame.setObjectName("titlebar")
        frame.setMinimumHeight(72)
        apply_shadow(frame, blur=18, y=6, alpha=12)

        h = QtWidgets.QHBoxLayout(frame)
        h.setContentsMargins(18, 0, 18, 0)
        h.setSpacing(12)

        mark = QtWidgets.QLabel("L")
        mark.setObjectName("brandMark")
        mark.setAlignment(QtCore.Qt.AlignCenter)
        mark.setFixedSize(42, 42)
        h.addWidget(mark)

        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(2)
        title = QtWidgets.QLabel("历史数据分析与健康评估")
        title.setObjectName("appTitle")
        subtitle = QtWidgets.QLabel("LXSPI Monitor · SQLite 本地历史库 · 山羊行为长期统计界面")
        subtitle.setObjectName("appSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        h.addLayout(title_box, 1)

        self.local_chip = QtWidgets.QLabel("● 本机数据")
        self.local_chip.setObjectName("onlineChip")
        self.date_chip = QtWidgets.QLabel(f"日期 {self.current_date}")
        self.date_chip.setObjectName("topChip")
        h.addWidget(self.local_chip)
        h.addWidget(self.date_chip)

        self.date_edit = QtWidgets.QDateEdit()
        self.date_edit.setDate(QtCore.QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.dateChanged.connect(self.on_date_changed)
        h.addWidget(self.date_edit)

        self.btn_prev = QtWidgets.QPushButton("前一天")
        self.btn_prev.clicked.connect(self.prev_day)
        h.addWidget(self.btn_prev)

        self.btn_next = QtWidgets.QPushButton("后一天")
        self.btn_next.clicked.connect(self.next_day)
        h.addWidget(self.btn_next)

        self.btn_today = QtWidgets.QPushButton("今天")
        self.btn_today.clicked.connect(self.go_today)
        h.addWidget(self.btn_today)

        self.btn_export = QtWidgets.QPushButton("导出 CSV")
        self.btn_export.setObjectName("primary")
        self.btn_export.clicked.connect(self.export_csv)
        h.addWidget(self.btn_export)

        return frame

    def create_summary_row(self):
        """创建顶部汇总卡片区"""
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(12)

        hero = QtWidgets.QFrame()
        hero.setObjectName("heroCard")
        hero.setMinimumHeight(140)
        apply_shadow(hero, blur=18, y=6, alpha=18)
        hero_layout = QtWidgets.QVBoxLayout(hero)
        hero_layout.setContentsMargins(20, 16, 20, 16)
        hero_layout.setSpacing(8)

        overline = QtWidgets.QLabel("DAILY HEALTH ASSESSMENT")
        overline.setObjectName("overline")
        self.hero_status = QtWidgets.QLabel("等待历史数据")
        self.hero_status.setObjectName("behavior")
        self.hero_status.setWordWrap(True)
        self.hero_hint = QtWidgets.QLabel("选择日期后自动加载本地行为记录")
        self.hero_hint.setObjectName("appSubtitle")
        self.hero_hint.setStyleSheet("color:#dcefe5;font:800 13px 'Microsoft YaHei UI';")
        self.hero_score = QtWidgets.QLabel("评分 --")
        self.hero_score.setObjectName("confidence")

        status_line = QtWidgets.QHBoxLayout()
        status_line.setSpacing(14)
        status_text = QtWidgets.QVBoxLayout()
        status_text.setSpacing(4)
        status_text.addWidget(self.hero_status)
        status_text.addWidget(self.hero_hint)
        status_line.addLayout(status_text, 1)
        status_line.addWidget(self.hero_score)

        hero_layout.addWidget(overline)
        hero_layout.addStretch(1)
        hero_layout.addLayout(status_line)
        row.addWidget(hero, 3)

        self.duration_metric = HistoryMetricCard("有效记录时长", "--", "行为累计时长", COLORS["green"])
        self.records_metric = HistoryMetricCard("行为记录数", "--", "稳定确认后写入", COLORS["blue"])
        self.alerts_metric = HistoryMetricCard("告警记录", "--", "当日健康提示", COLORS["gold"])
        self.main_behavior_metric = HistoryMetricCard("主要行为", "--", "占比最高类别", COLORS["red"])
        for card in (
            self.duration_metric,
            self.records_metric,
            self.alerts_metric,
            self.main_behavior_metric,
        ):
            row.addWidget(card, 1)
        return row

    def create_content_area(self):
        """创建主体内容区"""
        content = QtWidgets.QHBoxLayout()
        content.setSpacing(16)

        main_col = QtWidgets.QVBoxLayout()
        main_col.setSpacing(16)

        timeline_panel = self._panel("24h 行为时间线", "00:00 - 24:00")
        pg.setConfigOptions(antialias=True, background=COLORS["plot"], foreground=COLORS["ink"])
        self.timeline_plot = pg.PlotWidget()
        self.timeline_plot.setMinimumHeight(300)
        self._style_plot(self.timeline_plot, "行为", "时间")
        timeline_panel["body"].addWidget(self.timeline_plot)
        main_col.addWidget(timeline_panel["frame"], 4)

        lower = QtWidgets.QHBoxLayout()
        lower.setSpacing(16)

        duration_panel = self._panel("行为时长统计", "按小时汇总")
        self.duration_plot = pg.PlotWidget()
        self.duration_plot.setMinimumHeight(250)
        self._style_plot(self.duration_plot, "时长 (小时)", "")
        duration_panel["body"].addWidget(self.duration_plot)
        lower.addWidget(duration_panel["frame"], 1)

        trend_panel = self._panel("健康评分趋势", "最近 7 天")
        self.trend_plot = pg.PlotWidget()
        self.trend_plot.setMinimumHeight(250)
        self._style_plot(self.trend_plot, "评分", "")
        trend_panel["body"].addWidget(self.trend_plot)
        lower.addWidget(trend_panel["frame"], 1)

        main_col.addLayout(lower, 3)
        content.addLayout(main_col, 7)

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

        health_panel = self._panel("健康评分", "日评估")
        health_panel["frame"].setMinimumHeight(190)
        health_layout = QtWidgets.QHBoxLayout()
        health_layout.setSpacing(16)
        self.score_label = QtWidgets.QLabel("--")
        self.score_label.setObjectName("score")
        self.score_label.setAlignment(QtCore.Qt.AlignCenter)
        self.score_label.setFixedSize(108, 108)
        self.score_label.setStyleSheet(
            f"background:#e7f3ed;border-radius:54px;color:{COLORS['green']};"
            "font:900 32px 'Microsoft YaHei UI';"
        )
        health_text = QtWidgets.QVBoxLayout()
        health_text.setSpacing(8)
        self.score_hint = QtWidgets.QLabel("暂无评分")
        self.score_hint.setObjectName("side_title")
        self.score_desc = QtWidgets.QLabel("统计数据加载后显示健康状态摘要。")
        self.score_desc.setObjectName("overview")
        self.score_desc.setWordWrap(True)
        health_text.addStretch(1)
        health_text.addWidget(self.score_hint)
        health_text.addWidget(self.score_desc)
        health_text.addStretch(1)
        health_layout.addWidget(self.score_label)
        health_layout.addLayout(health_text, 1)
        health_panel["body"].addLayout(health_layout)
        side_col.addWidget(health_panel["frame"])

        pie_panel = self._panel("今日行为占比", "时长占比")
        pie_panel["frame"].setMinimumHeight(285)
        self.pie_chart = HistoryDonutChart()
        pie_panel["body"].addWidget(self.pie_chart)
        side_col.addWidget(pie_panel["frame"])

        detail_panel = self._panel("行为明细", "Top 5")
        detail_panel["frame"].setMinimumHeight(250)
        self.detail_table = QtWidgets.QTableWidget(0, 4)
        self.detail_table.setObjectName("detailTable")
        self.detail_table.setHorizontalHeaderLabels(["行为", "时长", "记录数", "占比"])
        self.detail_table.verticalHeader().setVisible(False)
        self.detail_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.detail_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.detail_table.setFocusPolicy(QtCore.Qt.NoFocus)
        self.detail_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        detail_panel["body"].addWidget(self.detail_table)
        side_col.addWidget(detail_panel["frame"])

        alert_panel = self._panel("告警记录", "当日")
        alert_panel["frame"].setMinimumHeight(180)
        self.alert_text = QtWidgets.QTextEdit()
        self.alert_text.setReadOnly(True)
        self.alert_text.setObjectName("alertText")
        alert_panel["body"].addWidget(self.alert_text)
        side_col.addWidget(alert_panel["frame"])

        overview_panel = self._panel("数据概览", "SQLite")
        overview_panel["frame"].setMinimumHeight(150)
        self.overview_label = QtWidgets.QLabel("加载中...")
        self.overview_label.setObjectName("overview")
        self.overview_label.setWordWrap(True)
        overview_panel["body"].addWidget(self.overview_label)
        side_col.addWidget(overview_panel["frame"])

        side_col.addStretch(1)
        side_scroll.setWidget(side_body)
        content.addWidget(side_scroll, 3)
        return content

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

    def _style_plot(self, plot, left_label, bottom_label):
        plot.setBackground("#fbfcfb")
        plot.showGrid(x=True, y=True, alpha=0.14)
        if left_label:
            plot.setLabel("left", left_label, color="#6f7d78", size="9pt")
        if bottom_label:
            plot.setLabel("bottom", bottom_label, color="#6f7d78", size="9pt")
        plot.hideButtons()
        for axis_name in ("left", "bottom"):
            axis = plot.getAxis(axis_name)
            axis.setPen("#dfe6e2")
            axis.setTextPen("#6f7d78")

    def apply_styles(self):
        """应用样式"""
        self.setStyleSheet(HISTORY_STYLE)

    def on_date_changed(self, date):
        """日期变更"""
        self.current_date = date.toString("yyyy-MM-dd")
        self.load_data()

    def prev_day(self):
        """前一天"""
        self.date_edit.setDate(self.date_edit.date().addDays(-1))

    def next_day(self):
        """后一天"""
        self.date_edit.setDate(self.date_edit.date().addDays(1))

    def go_today(self):
        """回到今天"""
        self.date_edit.setDate(QtCore.QDate.currentDate())

    def load_data(self):
        """加载并显示指定日期的数据"""
        date = self.current_date
        self.date_chip.setText(f"日期 {date}")

        behaviors = self.storage.get_behaviors_by_date(date)
        alerts = self.storage.get_alerts_by_date(date)
        stats = self.storage.get_daily_stats(date)
        self.summary = self.build_summary(behaviors, stats)

        self.update_summary_cards(behaviors, alerts, stats)
        self.update_timeline(behaviors)
        self.update_duration_chart()
        self.update_health_trend()
        self.update_detail_table()
        self.update_alerts(alerts)
        self.update_overview(behaviors, alerts, stats)
        self.pie_chart.set_data(self.summary["durations"])

    def build_summary(self, behaviors, stats):
        """统一生成历史统计摘要。"""
        durations = {}
        counts = {}

        if stats:
            for behavior, seconds in stats.get("behavior_durations", {}).items():
                normalized = normalize_behavior_label(behavior)
                if normalized != UNKNOWN_BEHAVIOR:
                    durations[normalized] = durations.get(normalized, 0.0) + float(seconds or 0.0)
            for behavior, count in stats.get("behavior_counts", {}).items():
                normalized = normalize_behavior_label(behavior)
                if normalized != UNKNOWN_BEHAVIOR:
                    counts[normalized] = counts.get(normalized, 0) + int(count or 0)
        else:
            for ts, behavior, conf, duration in behaviors:
                normalized = normalize_behavior_label(behavior)
                if normalized == UNKNOWN_BEHAVIOR:
                    continue
                counts[normalized] = counts.get(normalized, 0) + 1
                if duration:
                    durations[normalized] = durations.get(normalized, 0.0) + float(duration)

            if behaviors and sum(durations.values()) <= 0:
                durations = self.estimate_durations_from_records(behaviors)

        total_duration = float(stats.get("total_duration", 0.0)) if stats else sum(durations.values())
        if total_duration <= 0:
            total_duration = sum(durations.values())

        return {
            "durations": durations,
            "counts": counts,
            "total_duration": total_duration,
        }

    def estimate_durations_from_records(self, behaviors):
        """当数据库没有写入时长时，用相邻记录间隔做温和估算。"""
        durations = {}
        rows = sorted(behaviors, key=lambda item: item[0])
        for index, (timestamp, behavior, conf, duration) in enumerate(rows):
            normalized = normalize_behavior_label(behavior)
            if normalized == UNKNOWN_BEHAVIOR:
                continue
            if duration and duration > 0:
                seconds = float(duration)
            elif index + 1 < len(rows):
                seconds = max(1.0, min(float(rows[index + 1][0] - timestamp), 300.0))
            else:
                seconds = 60.0
            durations[normalized] = durations.get(normalized, 0.0) + seconds
        return durations

    def update_summary_cards(self, behaviors, alerts, stats):
        """刷新顶部汇总卡片。"""
        total_duration = self.summary["total_duration"]
        counts = self.summary["counts"]
        durations = self.summary["durations"]
        records = len(behaviors)
        alert_count = len(alerts)

        if durations:
            dominant = max(durations.items(), key=lambda item: item[1])[0]
            dominant_subtitle = f"累计 {format_hours(durations[dominant])}"
        elif counts:
            dominant = max(counts.items(), key=lambda item: item[1])[0]
            dominant_subtitle = f"{counts[dominant]} 条记录"
        else:
            dominant = None
            dominant_subtitle = "暂无有效类别"

        self.duration_metric.set_value(format_hours(total_duration), "行为累计时长")
        self.records_metric.set_value(f"{records}", "当日行为识别记录")
        self.alerts_metric.set_value(f"{alert_count}", "当日健康提示")
        self.main_behavior_metric.set_value(
            behavior_name_cn(dominant) if dominant else "--",
            dominant_subtitle,
        )

        if stats:
            score = float(stats.get("health_score", 0.0))
            self.hero_score.setText(f"评分 {score:.0f}")
            self.score_label.setText(f"{score:.0f}")
            if score >= 80:
                self.hero_status.setText("行为结构稳定")
                self.hero_hint.setText("采食、反刍、休息比例处于参考区间")
                self.score_hint.setText("当日状态优良")
                self.score_desc.setText("反刍和采食时长达到参考范围，休息连续性较好。")
                color = COLORS["green"]
                bg = "#e7f3ed"
            elif score >= 60:
                self.hero_status.setText("存在轻度波动")
                self.hero_hint.setText("建议结合现场饲喂和活动情况复核")
                self.score_hint.setText("建议关注")
                self.score_desc.setText("部分行为比例偏离参考区间，建议持续观察。")
                color = COLORS["gold"]
                bg = "#fff7e7"
            else:
                self.hero_status.setText("需要重点复核")
                self.hero_hint.setText("行为结构偏离明显，请结合现场情况确认")
                self.score_hint.setText("需要复核")
                self.score_desc.setText("系统检测到较明显的行为异常趋势，建议尽快复查。")
                color = COLORS["red"]
                bg = "#f8e6e3"
            self.score_label.setStyleSheet(
                f"background:{bg};border-radius:54px;color:{color};"
                "font:900 32px 'Microsoft YaHei UI';"
            )
        elif records:
            self.hero_score.setText("评分 --")
            self.score_label.setText("--")
            self.hero_status.setText("已有行为记录")
            self.hero_hint.setText("当前日期暂无每日健康评分，已按记录估算统计图")
            self.score_hint.setText("暂无评分")
            self.score_desc.setText("历史记录已加载，可查看时间线、时长统计和行为占比。")
            self.score_label.setStyleSheet(
                f"background:#e7f3ed;border-radius:54px;color:{COLORS['green']};"
                "font:900 32px 'Microsoft YaHei UI';"
            )
        else:
            self.hero_score.setText("评分 --")
            self.score_label.setText("--")
            self.hero_status.setText("暂无历史记录")
            self.hero_hint.setText("请选择有采集记录的日期，或先完成实时监听保存")
            self.score_hint.setText("暂无评分")
            self.score_desc.setText("当前日期没有行为记录和每日统计数据。")
            self.score_label.setStyleSheet(
                f"background:#eef2f0;border-radius:54px;color:{COLORS['muted']};"
                "font:900 32px 'Microsoft YaHei UI';"
            )

    def update_timeline(self, behaviors):
        """更新行为时间线"""
        self.timeline_plot.clear()
        label_map = {behavior: i for i, behavior in enumerate(BEHAVIOR_ORDER)}
        label_map[UNKNOWN_BEHAVIOR] = len(BEHAVIOR_ORDER)
        ticks = [(i, behavior_name_cn(behavior)) for behavior, i in label_map.items()]
        self.timeline_plot.getAxis("left").setTicks([ticks])
        self.timeline_plot.getAxis("bottom").setTicks([[(i, f"{i:02d}:00") for i in range(0, 25, 3)]])
        self.timeline_plot.setYRange(-0.5, len(label_map) - 0.5)
        self.timeline_plot.setXRange(0, 24)

        if not behaviors:
            empty = pg.TextItem("当天无行为记录", color="#7b8782", anchor=(0.5, 0.5))
            empty.setPos(12, 2.5)
            self.timeline_plot.addItem(empty)
            return

        spots = []
        for timestamp, behavior, conf, duration in behaviors:
            normalized = normalize_behavior_label(behavior)
            idx = label_map.get(normalized, label_map[UNKNOWN_BEHAVIOR])
            color = BEHAVIOR_COLORS.get(normalized, BEHAVIOR_COLORS["Unknown"])
            hour = (timestamp % 86400) / 3600.0
            spots.append({
                "pos": (hour, idx),
                "brush": pg.mkBrush(color),
                "pen": pg.mkPen("#ffffff", width=0.6),
                "size": 6,
                "data": normalized,
            })
        scatter = pg.ScatterPlotItem(spots=spots)
        self.timeline_plot.addItem(scatter)

    def update_duration_chart(self):
        """更新行为时长柱状图"""
        self.duration_plot.clear()
        durations = self.summary["durations"]
        values = [durations.get(behavior, 0.0) / 3600.0 for behavior in BEHAVIOR_ORDER]
        self.duration_plot.getAxis("bottom").setTicks(
            [[(i, behavior_name_cn(behavior)) for i, behavior in enumerate(BEHAVIOR_ORDER)]]
        )

        if not any(values):
            self.duration_plot.setXRange(-0.5, len(BEHAVIOR_ORDER) - 0.5)
            self.duration_plot.setYRange(0, 1)
            empty = pg.TextItem("当天无统计数据", color="#7b8782", anchor=(0.5, 0.5))
            empty.setPos((len(BEHAVIOR_ORDER) - 1) / 2, 0.5)
            self.duration_plot.addItem(empty)
            return

        x = list(range(len(BEHAVIOR_ORDER)))
        bg = pg.BarGraphItem(
            x=x,
            height=values,
            width=0.58,
            brushes=[pg.mkBrush(BEHAVIOR_COLORS[behavior]) for behavior in BEHAVIOR_ORDER],
        )
        self.duration_plot.addItem(bg)
        ymax = max(values) * 1.28
        self.duration_plot.setYRange(0, max(1.0, ymax))
        self.duration_plot.setXRange(-0.6, len(BEHAVIOR_ORDER) - 0.4)
        for idx, value in enumerate(values):
            if value <= 0:
                continue
            label = pg.TextItem(f"{value:.1f}h", color=COLORS["ink"], anchor=(0.5, 1.0))
            label.setPos(idx, value + max(ymax * 0.03, 0.05))
            self.duration_plot.addItem(label)

    def update_health_trend(self):
        """更新最近 7 天健康评分趋势。"""
        self.trend_plot.clear()
        weekly = self.storage.get_weekly_stats(self.current_date)
        if not weekly:
            self.trend_plot.setYRange(0, 100)
            empty = pg.TextItem("暂无趋势数据", color="#7b8782", anchor=(0.5, 0.5))
            empty.setPos(3, 50)
            self.trend_plot.addItem(empty)
            return

        x = list(range(len(weekly)))
        y = [float(item.get("health_score", 0.0)) for item in weekly]
        dates = [item.get("date", "")[-5:] for item in weekly]
        self.trend_plot.getAxis("bottom").setTicks([list(zip(x, dates))])
        self.trend_plot.setYRange(max(0, min(y) - 8), min(100, max(y) + 8))
        self.trend_plot.setXRange(-0.3, max(6, len(weekly) - 1) + 0.3)
        self.trend_plot.plot(
            x,
            y,
            pen=pg.mkPen(COLORS["green"], width=3),
            symbol="o",
            symbolSize=8,
            symbolBrush=pg.mkBrush("#ffffff"),
            symbolPen=pg.mkPen(COLORS["green"], width=2),
        )

    def update_detail_table(self):
        """更新右侧行为明细表。"""
        durations = self.summary["durations"]
        counts = self.summary["counts"]
        total = sum(durations.values())
        rows = [
            behavior for behavior in BEHAVIOR_ORDER
            if durations.get(behavior, 0.0) > 0 or counts.get(behavior, 0) > 0
        ]
        rows.sort(key=lambda behavior: durations.get(behavior, 0.0), reverse=True)

        self.detail_table.setRowCount(max(1, len(rows)))
        if not rows:
            item = QtWidgets.QTableWidgetItem("暂无数据")
            item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.detail_table.setItem(0, 0, item)
            for col in range(1, 4):
                self.detail_table.setItem(0, col, QtWidgets.QTableWidgetItem("--"))
            return

        for row, behavior in enumerate(rows):
            duration = durations.get(behavior, 0.0)
            count = counts.get(behavior, 0)
            ratio = duration / total * 100 if total > 0 else 0.0
            values = [
                behavior_name_cn(behavior),
                format_hours(duration),
                f"{count}",
                f"{ratio:.1f}%",
            ]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                if col == 0:
                    item.setForeground(QtGui.QColor(BEHAVIOR_COLORS[behavior]))
                    item.setFont(QtGui.QFont("Microsoft YaHei UI", 10, QtGui.QFont.Black))
                self.detail_table.setItem(row, col, item)
            self.detail_table.setRowHeight(row, 36)

    def update_alerts(self, alerts):
        """更新告警信息"""
        if not alerts:
            self.alert_text.setPlainText("当天无告警记录")
            return

        lines = []
        for alert in alerts:
            ts = datetime.fromtimestamp(alert["timestamp"]).strftime("%H:%M:%S")
            level = "观察" if alert["severity"] == "warning" else "复核"
            lines.append(f"[{ts}] {level} · {alert['message']}")
        self.alert_text.setPlainText("\n".join(lines))

    def update_overview(self, behaviors, alerts, stats):
        """更新数据概览"""
        total_duration = self.summary["total_duration"]
        if stats:
            source = "每日统计表 + 行为记录表"
            score_text = f"{stats.get('health_score', 0):.0f}"
        else:
            source = "行为记录表估算"
            score_text = "--"
        self.overview_label.setText(
            f"数据日期: {self.current_date}\n"
            f"数据来源: {source}\n"
            f"行为记录: {len(behaviors)} 条\n"
            f"告警记录: {len(alerts)} 条\n"
            f"累计时长: {format_hours(total_duration)}\n"
            f"健康评分: {score_text}"
        )

    def export_csv(self):
        """导出当日数据为CSV"""
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not dir_path:
            return
        try:
            self.storage.export_to_csv(self.current_date, dir_path)
            QtWidgets.QMessageBox.information(
                self,
                "导出成功",
                f"已导出 {self.current_date} 的数据到:\n{dir_path}",
            )
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "导出失败", f"导出出错: {e}")

    def closeEvent(self, event):
        """关闭时清理资源"""
        self.storage.close()
        super().closeEvent(event)


if __name__ == "__main__":
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QtGui.QFont("Microsoft YaHei UI", 10))
    w = HistoryViewer()
    w.show()
    sys.exit(app.exec())
