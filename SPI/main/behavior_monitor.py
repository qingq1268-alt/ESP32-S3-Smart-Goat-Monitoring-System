"""
Behavior health monitor for the PC-side visualizer.

The firmware emits one of five model labels. This module does not make
veterinary diagnoses; it turns behavior predictions into configurable
behavior-risk alerts, health scores, and UI-friendly summaries.
"""
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app_paths import resource_path, user_data_path


CANONICAL_BEHAVIORS = (
    "Displacement",
    "Grazing",
    "Ruminating_Chewing",
    "Other",
    "Resting",
)
UNKNOWN_BEHAVIOR = "Unknown"

ACTIVE_BEHAVIORS = ("Displacement", "Other")
RUMINATING_BEHAVIOR = "Ruminating_Chewing"

_BEHAVIOR_ALIASES = {
    "displacement": "Displacement",
    "walking": "Displacement",
    "walk": "Displacement",
    "running": "Displacement",
    "moving": "Displacement",
    "move": "Displacement",
    "grazing": "Grazing",
    "eating": "Grazing",
    "ruminating": "Ruminating_Chewing",
    "ruminating_chewing": "Ruminating_Chewing",
    "ruminating/chewing": "Ruminating_Chewing",
    "rumination": "Ruminating_Chewing",
    "chewing": "Ruminating_Chewing",
    "other": "Other",
    "resting": "Resting",
    "rest": "Resting",
    "static": "Resting",
    "unknown": UNKNOWN_BEHAVIOR,
    "--": UNKNOWN_BEHAVIOR,
    "": UNKNOWN_BEHAVIOR,
    "位移": "Displacement",
    "移动": "Displacement",
    "走动": "Displacement",
    "吃草": "Grazing",
    "采食": "Grazing",
    "反刍": "Ruminating_Chewing",
    "咀嚼": "Ruminating_Chewing",
    "其他": "Other",
    "静止": "Resting",
    "休息": "Resting",
}


@dataclass
class Alert:
    """UI/database-friendly alert object. Timestamp is in seconds."""

    timestamp: float
    alert_type: str
    message: str
    severity: str
    behavior: Optional[str] = None
    duration: Optional[float] = None


def normalize_behavior_label(label: Optional[str]) -> str:
    """Return the canonical model label, or Unknown for invalid input."""
    if label is None:
        return UNKNOWN_BEHAVIOR

    text = str(label).strip()
    key = text.lower().replace("-", "_").replace(" ", "_")
    normalized = _BEHAVIOR_ALIASES.get(key)
    if normalized:
        return normalized
    if text in CANONICAL_BEHAVIORS:
        return text
    return UNKNOWN_BEHAVIOR


class HealthBehaviorMonitor:
    """
    基于行为识别结果的健康风险提示模块。

    输入:
        behavior: Displacement / Grazing / Ruminating_Chewing / Other / Resting
        confidence: 模型置信度
        timestamp_ms: 毫秒时间戳

    输出:
        health_score: 0-100
        health_level: 分级结果
        new_alerts: 本次新增告警
        behavior_durations: 当日各行为累计时长
    """

    VALID_BEHAVIORS = set(CANONICAL_BEHAVIORS)

    LABEL_ALIAS = {
        "Walking": "Displacement",
        "Running": "Displacement",
        "Ruminating": "Ruminating_Chewing",
        "Rumination": "Ruminating_Chewing",
        "Ruminating/Chewing": "Ruminating_Chewing",
        "Chewing": "Ruminating_Chewing",
    }

    def __init__(self, config_path=None):
        if config_path:
            self.config_path = Path(config_path)
        else:
            external_config = user_data_path("health_config.json")
            self.config_path = (
                external_config
                if external_config.exists()
                else resource_path("health_config.json")
            )
        self.config = self._load_config()

        self.time_scale = float(self.config.get("time_scale", 1.0))
        self.min_confidence = float(self.config.get("min_confidence", 0.50))
        self.switch_confirmations = int(self.config.get("switch_confirmations", 2))
        self.alert_cooldown_sec = float(self.config.get("alert_cooldown_sec", 600))

        self.thresholds = self.config.get("thresholds_sec", {})
        self.score_cfg = self.config.get("score", {})
        self.score_levels = self.config.get("score_levels", [])

        self.current_date: Optional[str] = None
        self.current_behavior: Optional[str] = None
        self.current_start_ms: Optional[int] = None
        self.last_ts_ms: Optional[int] = None
        self.session_start_ms: Optional[int] = None  # 本次程序运行起点

        self.candidate_behavior: Optional[str] = None
        self.candidate_count = 0
        self.last_accepted_behavior: Optional[str] = None
        self.last_observed_behavior: Optional[str] = None

        self.behavior_durations = defaultdict(float)
        self.behavior_counts = defaultdict(int)

        self.switch_events = deque()
        self.recent_segments = deque()

        self.last_ruminating_ms: Optional[int] = None
        self.last_alert_time: Dict[str, int] = {}
        self.alerts_today_count = 0
        self.alerts: List[Alert] = []
        self.last_snapshot: Optional[Dict[str, Any]] = None

        # Kept for compatibility with older UI code that assigned a callback.
        self.on_alert = None

    def _load_config(self) -> Dict[str, Any]:
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)

        return {
            "time_scale": 1.0,
            "min_confidence": 0.50,
            "switch_confirmations": 2,
            "alert_cooldown_sec": 600,
            "thresholds_sec": {},
            "score": {},
            "score_levels": [],
        }

    def _scaled(self, seconds: float) -> float:
        return float(seconds) / max(self.time_scale, 1e-6)

    def _normalize_behavior(self, behavior) -> str:
        if behavior is None:
            return "Other"

        text = str(behavior).strip()
        text = self.LABEL_ALIAS.get(text, text)
        normalized = normalize_behavior_label(text)
        if normalized == UNKNOWN_BEHAVIOR:
            return "Other"
        return normalized

    def _date_str(self, timestamp_ms: int) -> str:
        return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d")

    def _seconds_since_midnight(self, timestamp_ms: int) -> int:
        dt = datetime.fromtimestamp(timestamp_ms / 1000)
        return dt.hour * 3600 + dt.minute * 60 + dt.second

    def _reset_daily_if_needed(self, timestamp_ms: int):
        date_str = self._date_str(timestamp_ms)

        if self.current_date is None:
            self.current_date = date_str
            return

        if date_str != self.current_date:
            self.current_date = date_str
            self.behavior_durations.clear()
            self.behavior_counts.clear()
            self.switch_events.clear()
            self.recent_segments.clear()
            self.last_alert_time.clear()
            self.alerts_today_count = 0
            self.alerts.clear()
            self.last_ruminating_ms = None
            self.current_behavior = None
            self.current_start_ms = None
            self.last_ts_ms = None
            self.session_start_ms = timestamp_ms  # 跨日也重置会话起点，避免无反刍误报
            self.candidate_behavior = None
            self.candidate_count = 0
            self.last_accepted_behavior = None
            self.last_observed_behavior = None

    def update(self, behavior, confidence=1.0, timestamp_ms=None) -> Dict[str, Any]:
        """主入口：每收到一次模型推理结果调用一次。"""
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)

        timestamp_ms = int(timestamp_ms)
        confidence = float(confidence)

        # 第一次收到样本时记录会话起点，用于"启动后 N 小时内不报无反刍"
        if self.session_start_ms is None:
            self.session_start_ms = timestamp_ms

        self._reset_daily_if_needed(timestamp_ms)

        behavior = self._normalize_behavior(behavior)
        self.last_observed_behavior = behavior
        self.last_accepted_behavior = None

        if confidence < self.min_confidence:
            return self._snapshot(
                timestamp_ms=timestamp_ms,
                new_alerts=[],
                ignored=True,
                ignore_reason="low_confidence",
            )

        self._update_duration(timestamp_ms)
        self._update_stable_behavior(behavior, timestamp_ms)

        if behavior == RUMINATING_BEHAVIOR:
            self.last_ruminating_ms = timestamp_ms

        self._prune_recent(timestamp_ms)
        new_alerts = self._check_alerts(timestamp_ms)

        return self._snapshot(
            timestamp_ms=timestamp_ms,
            new_alerts=new_alerts,
            ignored=False,
            ignore_reason=None,
        )

    def update_behavior(
        self,
        behavior: str,
        confidence: float,
        timestamp: Optional[float] = None,
    ) -> List[Alert]:
        """Compatibility wrapper for older UI/tests. Timestamp is seconds."""
        timestamp_ms = None
        if timestamp is not None:
            timestamp_ms = int(timestamp * 1000) if timestamp < 1_000_000_000_000 else int(timestamp)

        snapshot = self.update(behavior, confidence, timestamp_ms)
        return [self._alert_dict_to_obj(alert) for alert in snapshot["new_alerts"]]

    def _update_duration(self, timestamp_ms: int):
        if self.last_ts_ms is None:
            self.last_ts_ms = timestamp_ms
            return

        delta_sec = (timestamp_ms - self.last_ts_ms) / 1000.0
        if delta_sec < 0:
            delta_sec = 0

        # 防止电脑卡顿导致一次性累计过长。
        delta_sec = min(delta_sec, 60.0)

        if self.current_behavior is not None:
            self.behavior_durations[self.current_behavior] += delta_sec
            self.recent_segments.append((timestamp_ms, self.current_behavior, delta_sec))

        self.last_ts_ms = timestamp_ms

    def _update_stable_behavior(self, behavior: str, timestamp_ms: int):
        if self.current_behavior is None:
            self.current_behavior = behavior
            self.current_start_ms = timestamp_ms
            self.behavior_counts[behavior] += 1
            self.last_accepted_behavior = behavior
            return

        if behavior == self.current_behavior:
            self.candidate_behavior = None
            self.candidate_count = 0
            self.last_accepted_behavior = behavior
            return

        if self.candidate_behavior == behavior:
            self.candidate_count += 1
        else:
            self.candidate_behavior = behavior
            self.candidate_count = 1

        if self.candidate_count >= self.switch_confirmations:
            self.current_behavior = behavior
            self.current_start_ms = timestamp_ms
            self.behavior_counts[behavior] += 1
            self.switch_events.append(timestamp_ms)
            self.last_accepted_behavior = behavior

            self.candidate_behavior = None
            self.candidate_count = 0

    def _prune_recent(self, timestamp_ms: int):
        one_hour_ms = 3600 * 1000
        while self.switch_events and timestamp_ms - self.switch_events[0] > one_hour_ms:
            self.switch_events.popleft()

        while self.recent_segments and timestamp_ms - self.recent_segments[0][0] > one_hour_ms:
            self.recent_segments.popleft()

    def _session_duration_sec(self, timestamp_ms: int) -> float:
        if self.current_start_ms is None:
            return 0.0
        return max(0.0, (timestamp_ms - self.current_start_ms) / 1000.0)

    def _recent_duration_by_behavior(self) -> Dict[str, float]:
        durations = defaultdict(float)
        for _, behavior, sec in self.recent_segments:
            durations[behavior] += sec
        return durations

    def _check_alerts(self, timestamp_ms: int) -> List[Dict[str, Any]]:
        alerts = []
        now_dt = datetime.fromtimestamp(timestamp_ms / 1000)

        current = self.current_behavior
        session_sec = self._session_duration_sec(timestamp_ms)

        long_still_sec = self._scaled(self.thresholds.get("long_still_sec", 7200))
        intense_activity_sec = self._scaled(self.thresholds.get("intense_activity_sec", 1800))
        no_ruminating_sec = self._scaled(self.thresholds.get("no_ruminating_sec", 21600))
        insufficient_grazing_sec = self._scaled(self.thresholds.get("insufficient_grazing_sec", 14400))
        frequent_switch_count = int(self.thresholds.get("frequent_switch_count_per_hour", 20))

        # 1. 长时间静止
        if current == "Resting" and session_sec >= long_still_sec:
            alerts.append(
                self._make_alert(
                    timestamp_ms,
                    alert_type="long_stillness",
                    severity="warning",
                    behavior=current,
                    duration=session_sec,
                    message="持续静止时间过长，建议人工观察确认。",
                )
            )

        # 2. 持续高活动
        if current in ACTIVE_BEHAVIORS and session_sec >= intense_activity_sec:
            alerts.append(
                self._make_alert(
                    timestamp_ms,
                    alert_type="sustained_high_activity",
                    severity="warning",
                    behavior=current,
                    duration=session_sec,
                    message="持续活动时间较长，可能存在受惊、躁动或环境干扰，建议观察。",
                )
            )

        # 3. 行为频繁切换
        if len(self.switch_events) >= frequent_switch_count:
            alerts.append(
                self._make_alert(
                    timestamp_ms,
                    alert_type="frequent_behavior_switching",
                    severity="warning",
                    behavior=current,
                    duration=3600,
                    message=f"近 1 小时行为切换次数较高：{len(self.switch_events)} 次。",
                )
            )

        # === 三大健康风险指标 ===

        # 指标1: 昼夜活动节律异常
        # 系统统计白天与夜间活动对比例，当夜间活动持续偏多、休息减少时，提示昼夜活动节律异常
        day_elapsed_sec = self._seconds_since_midnight(timestamp_ms)

        # 夜间时段 (22:00-05:00)
        if now_dt.hour >= 22 or now_dt.hour < 5:
            recent = self._recent_duration_by_behavior()
            total_recent = sum(recent.values())

            if total_recent >= self._scaled(1800):
                night_active = recent.get("Displacement", 0.0) + recent.get("Other", 0.0)
                night_resting = recent.get("Resting", 0.0)
                night_active_ratio = night_active / max(total_recent, 1e-6)
                night_resting_ratio = night_resting / max(total_recent, 1e-6)
                night_threshold = float(self.thresholds.get("circadian_night_active_ratio", 0.35))

                if night_active_ratio >= night_threshold:
                    alerts.append(
                        self._make_alert(
                            timestamp_ms,
                            alert_type="circadian_activity_abnormal",
                            severity="warning",
                            behavior=current,
                            duration=total_recent,
                            message=f"【昼夜活动节律异常】系统统计白天与夜间活动对比例，当夜间活动持续偏多（当前{night_active_ratio*100:.1f}%）、休息减少时，提示昼夜活动节律异常。文献支撑：昼夜节律是在24小时周期内发生的身体、心理和行为变化。",
                        )
                    )

        # 指标2: 采食-反刍节律异常
        # 系统统计Grazing和Ruminating_Chewing的持续时间，当长时间未检测到反刍行为，
        # 或当日采食时间明显不足时，提示采食-反刍节律异常
        grazing_sec = self.behavior_durations.get("Grazing", 0.0)
        ruminating_sec = self.behavior_durations.get(RUMINATING_BEHAVIOR, 0.0)

        # 计算无反刍时长
        if self.last_ruminating_ms is None:
            # 还没观察到反刍：用「会话已运行多久」作为无反刍时长，避免开机就触发 6 小时阈值。
            # 这样必须真的连续运行了 6+ 小时还没看到反刍才会报警。
            if self.session_start_ms is not None:
                no_rum_sec = (timestamp_ms - self.session_start_ms) / 1000.0
            else:
                no_rum_sec = 0.0
        else:
            no_rum_sec = (timestamp_ms - self.last_ruminating_ms) / 1000.0

        # 采食-反刍节律异常综合判断（18:00后判断）
        if now_dt.hour >= 18:
            # 参考值：理想采食时长约6小时(21600秒)，理想反刍时长约4小时(14400秒)
            # 山羊反刍时长参考：280.8 min/day ≈ 16848秒
            min_ruminating_sec = self._scaled(self.thresholds.get("min_ruminating_sec", 16800))  # 280 min

            # 会话刚启动时，累积时长当然不够，需要等运行至少 4 小时再判断，避免开机即报警
            session_runtime_sec = 0.0
            if self.session_start_ms is not None:
                session_runtime_sec = (timestamp_ms - self.session_start_ms) / 1000.0
            min_session_for_alert = self._scaled(14400)  # 至少运行 4 小时才能下结论

            if session_runtime_sec >= min_session_for_alert:
                grazing_insufficient = grazing_sec < insufficient_grazing_sec
                ruminating_insufficient = ruminating_sec < min_ruminating_sec
            else:
                grazing_insufficient = False
                ruminating_insufficient = False

            if grazing_insufficient or ruminating_insufficient:
                issues = []
                if grazing_insufficient:
                    issues.append(f"采食时长{grazing_sec/60:.1f}分钟不足")
                if ruminating_insufficient:
                    issues.append(f"反刍时长{ruminating_sec/60:.1f}分钟不足")

                alerts.append(
                    self._make_alert(
                        timestamp_ms,
                        alert_type="grazing_ruminating_rhythm_abnormal",
                        severity="warning",
                        behavior="Grazing" if grazing_insufficient else RUMINATING_BEHAVIOR,
                        duration=grazing_sec + ruminating_sec,
                        message=f"【采食-反刍节律异常】系统统计Grazing和Ruminating_Chewing的持续时间，当长时间未检测到反刍行为，或当日采食时间明显不足时，提示采食-反刍节律异常。当前状态：{', '.join(issues)}。文献支撑：反刍动物的采食和反刍行为可用于评估健康与福利状况，山羊反刍时长约为280.8 min/day。",
                    )
                )

        # 长时间无反刍（严重情况，单独告警）
        if no_rum_sec >= no_ruminating_sec:
            alerts.append(
                self._make_alert(
                    timestamp_ms,
                    alert_type="no_ruminating",
                    severity="danger",
                    behavior=RUMINATING_BEHAVIOR,
                    duration=no_rum_sec,
                    message=f"长时间未检测到反刍行为（已持续{no_rum_sec/3600:.1f}小时），建议人工复核。",
                )
            )

        # 指标3: 高活动-低采食失衡
        # 系统统计Displacement、Other与Grazing的比例，当活动为主导偏高而采食行为明显偏低时，
        # 提示能量消耗偏高而采食行为可能不足
        recent = self._recent_duration_by_behavior()
        total_recent = sum(recent.values())

        if total_recent >= self._scaled(1800):
            activity_recent = recent.get("Displacement", 0.0) + recent.get("Other", 0.0)
            grazing_recent = recent.get("Grazing", 0.0)

            activity_ratio = activity_recent / max(total_recent, 1e-6)
            grazing_ratio = grazing_recent / max(total_recent, 1e-6)

            activity_threshold = float(
                self.thresholds.get("high_activity_low_grazing_activity_ratio", 0.50)
            )
            grazing_threshold = float(
                self.thresholds.get("high_activity_low_grazing_grazing_ratio", 0.10)
            )

            if activity_ratio >= activity_threshold and grazing_ratio <= grazing_threshold:
                alerts.append(
                    self._make_alert(
                        timestamp_ms,
                        alert_type="high_activity_low_grazing",
                        severity="warning",
                        behavior=current,
                        duration=total_recent,
                        message=f"【高活动-低采食失衡】系统统计Displacement、Other与Grazing的比例，当活动为主导偏高（当前{activity_ratio*100:.1f}%）而采食行为明显偏低（当前{grazing_ratio*100:.1f}%）时，提示能量消耗偏高而采食行为可能不足。文献支撑：反映行为结构失衡，需要人工观察确认。",
                    )
                )

        return [alert for alert in alerts if alert is not None]

    def _make_alert(
        self,
        timestamp_ms: int,
        alert_type: str,
        severity: str,
        behavior: Optional[str],
        duration: Optional[float],
        message: str,
    ) -> Optional[Dict[str, Any]]:
        last = self.last_alert_time.get(alert_type)
        if last is not None and (timestamp_ms - last) / 1000.0 < self.alert_cooldown_sec:
            return None

        self.last_alert_time[alert_type] = timestamp_ms
        self.alerts_today_count += 1

        alert = {
            "timestamp": timestamp_ms,
            "alert_type": alert_type,
            "severity": severity,
            "behavior": behavior,
            "duration": round(float(duration), 2) if duration is not None else None,
            "message": message,
        }
        self.alerts.append(self._alert_dict_to_obj(alert))
        return alert

    def _calculate_health_score(self, timestamp_ms: int) -> int:
        grazing = self.behavior_durations.get("Grazing", 0.0)
        ruminating = self.behavior_durations.get(RUMINATING_BEHAVIOR, 0.0)
        activity = (
            self.behavior_durations.get("Displacement", 0.0)
            + self.behavior_durations.get("Other", 0.0)
        )
        resting = self.behavior_durations.get("Resting", 0.0)

        ideal_grazing = self._scaled(self.score_cfg.get("ideal_grazing_sec", 21600))
        ideal_ruminating = self._scaled(self.score_cfg.get("ideal_ruminating_sec", 14400))
        ideal_activity = self._scaled(self.score_cfg.get("ideal_activity_sec", 7200))
        min_resting = self._scaled(self.score_cfg.get("min_resting_sec", 14400))
        max_resting = self._scaled(self.score_cfg.get("max_resting_sec", 36000))

        grazing_score = min(grazing / max(ideal_grazing, 1e-6), 1.0) * 30
        ruminating_score = min(ruminating / max(ideal_ruminating, 1e-6), 1.0) * 25

        if activity <= ideal_activity:
            activity_score = activity / max(ideal_activity, 1e-6) * 20
        else:
            over_ratio = min((activity - ideal_activity) / max(ideal_activity, 1e-6), 1.0)
            activity_score = 20 * (1.0 - 0.5 * over_ratio)

        if min_resting <= resting <= max_resting:
            resting_score = 15
        elif resting < min_resting:
            resting_score = max(0, resting / max(min_resting, 1e-6) * 15)
        else:
            over_ratio = min((resting - max_resting) / max(max_resting, 1e-6), 1.0)
            resting_score = 15 * (1.0 - over_ratio)

        penalty_per_event = self.score_cfg.get("alert_penalty_per_event", 2)
        max_penalty = self.score_cfg.get("max_alert_penalty", 10)
        alert_penalty = min(self.alerts_today_count * penalty_per_event, max_penalty)

        score = grazing_score + ruminating_score + activity_score + resting_score + 10 - alert_penalty
        return int(max(0, min(100, round(score))))

    def calculate_health_score(self, timestamp: Optional[float] = None) -> float:
        timestamp_ms = self._coerce_timestamp_ms(timestamp)
        return float(self._calculate_health_score(timestamp_ms))

    def _classify_score(self, score: int) -> Dict[str, str]:
        if not self.score_levels:
            if score >= 80:
                return {"name": "基本正常", "display": "良好", "severity": "normal"}
            if score >= 60:
                return {"name": "需关注", "display": "预警", "severity": "warning"}
            return {"name": "高风险", "display": "建议人工复核", "severity": "danger"}

        levels = sorted(self.score_levels, key=lambda x: x.get("min", 0), reverse=True)
        for level in levels:
            if score >= level.get("min", 0):
                return {
                    "name": level.get("name", ""),
                    "display": level.get("display", ""),
                    "severity": level.get("severity", ""),
                }

        return {"name": "未知", "display": "未知", "severity": "unknown"}

    def _snapshot(
        self,
        timestamp_ms: int,
        new_alerts: List[Dict[str, Any]],
        ignored: bool = False,
        ignore_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        score = self._calculate_health_score(timestamp_ms)
        level = self._classify_score(score)

        snapshot = {
            "timestamp": timestamp_ms,
            "date": self._date_str(timestamp_ms),
            "current_behavior": self.current_behavior,
            "current_duration_sec": round(self._session_duration_sec(timestamp_ms), 2),
            "behavior_durations": {k: round(v, 2) for k, v in self.behavior_durations.items()},
            "behavior_counts": dict(self.behavior_counts),
            "switch_count_last_hour": len(self.switch_events),
            "health_score": score,
            "health_level": level,
            "alerts_today_count": self.alerts_today_count,
            "new_alerts": new_alerts,
            "ignored": ignored,
            "ignore_reason": ignore_reason,
        }
        self.last_snapshot = snapshot
        return snapshot

    def get_current_duration(self, timestamp: Optional[float] = None) -> float:
        return self._session_duration_sec(self._coerce_timestamp_ms(timestamp))

    def get_behavior_summary(self, timestamp: Optional[float] = None) -> Dict[str, Any]:
        timestamp_ms = self._coerce_timestamp_ms(timestamp)
        score = self._calculate_health_score(timestamp_ms)
        durations = {k: round(v, 2) for k, v in self.behavior_durations.items()}
        total_duration = sum(durations.values())
        return {
            "current_behavior": self.current_behavior,
            "current_duration": self._session_duration_sec(timestamp_ms),
            "total_duration": total_duration,
            "behavior_durations": durations,
            "behavior_counts": dict(self.behavior_counts),
            "health_score": score,
            "health_level": self._classify_score(score),
            "recent_alerts": self.alerts[-5:],
            "pending_behavior": self.candidate_behavior,
            "pending_count": self.candidate_count,
            "switch_count_last_hour": len(self.switch_events),
        }

    def get_daily_report(self, date: Optional[str] = None):
        return None

    def get_weekly_report(self):
        return {}

    def _coerce_timestamp_ms(self, timestamp: Optional[float]) -> int:
        if timestamp is None:
            if self.last_ts_ms is not None:
                return self.last_ts_ms
            return int(time.time() * 1000)
        return int(timestamp * 1000) if timestamp < 1_000_000_000_000 else int(timestamp)

    @staticmethod
    def _alert_dict_to_obj(alert: Dict[str, Any]) -> Alert:
        timestamp_ms = alert.get("timestamp", int(time.time() * 1000))
        return Alert(
            timestamp=float(timestamp_ms) / 1000.0,
            alert_type=alert.get("alert_type", "unknown"),
            message=alert.get("message", ""),
            severity=alert.get("severity", "warning"),
            behavior=alert.get("behavior"),
            duration=alert.get("duration"),
        )


# 兼容旧代码：visualize.py 里原来写的是 BehaviorMonitor()
BehaviorMonitor = HealthBehaviorMonitor
