"""
数据存储模块 - 使用SQLite存储历史数据
"""
import sqlite3
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from pathlib import Path

from app_paths import user_data_path


class DataStorage:
    """数据存储管理器"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # 默认使用项目根目录；打包成 exe 后使用 exe 同目录。
            db_path = str(user_data_path("livestock_data.db"))
        self.db_path = db_path
        self.conn = None
        self._init_database()

    def _init_database(self):
        """初始化数据库表结构"""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = self.conn.cursor()

        # 行为记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS behavior_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                behavior TEXT NOT NULL,
                confidence REAL NOT NULL,
                duration REAL DEFAULT 0,
                date TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_behavior_date ON behavior_records(date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_behavior_timestamp ON behavior_records(timestamp)")

        # 加速度数据表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accel_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                x REAL NOT NULL,
                y REAL NOT NULL,
                z REAL NOT NULL,
                date TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_accel_date ON accel_data(date)")

        # 告警记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                alert_type TEXT NOT NULL,
                message TEXT NOT NULL,
                severity TEXT NOT NULL,
                behavior TEXT,
                duration REAL,
                date TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alert_date ON alerts(date)")

        # 每日统计表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE NOT NULL,
                behavior_durations TEXT NOT NULL,
                behavior_counts TEXT NOT NULL,
                total_duration REAL NOT NULL,
                health_score REAL NOT NULL,
                alerts_count INTEGER DEFAULT 0
            )
        """)

        self.conn.commit()

    def save_behavior(self, timestamp: float, behavior: str, confidence: float, duration: float = 0):
        """保存行为记录"""
        date = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO behavior_records (timestamp, behavior, confidence, duration, date)
            VALUES (?, ?, ?, ?, ?)
        """, (timestamp, behavior, confidence, duration, date))
        self.conn.commit()

    def save_accel(self, timestamp: float, x: float, y: float, z: float):
        """保存加速度数据"""
        date = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO accel_data (timestamp, x, y, z, date)
            VALUES (?, ?, ?, ?, ?)
        """, (timestamp, x, y, z, date))
        self.conn.commit()

    def save_alert(self, timestamp: float, alert_type: str, message: str,
                   severity: str, behavior: Optional[str] = None, duration: Optional[float] = None):
        """保存告警记录"""
        date = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO alerts (timestamp, alert_type, message, severity, behavior, duration, date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (timestamp, alert_type, message, severity, behavior, duration, date))
        self.conn.commit()

    def save_daily_stats(self, date: str, behavior_durations: Dict[str, float],
                        behavior_counts: Dict[str, int], total_duration: float,
                        health_score: float, alerts_count: int):
        """保存每日统计"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO daily_stats
            (date, behavior_durations, behavior_counts, total_duration, health_score, alerts_count)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (date, json.dumps(behavior_durations), json.dumps(behavior_counts),
              total_duration, health_score, alerts_count))
        self.conn.commit()

    def get_behaviors_by_date(self, date: str) -> List[Tuple]:
        """获取指定日期的行为记录"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT timestamp, behavior, confidence, duration
            FROM behavior_records
            WHERE date = ?
            ORDER BY timestamp
        """, (date,))
        return cursor.fetchall()

    def get_behaviors_by_range(self, start_date: str, end_date: str) -> List[Tuple]:
        """获取日期范围内的行为记录"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT timestamp, behavior, confidence, duration
            FROM behavior_records
            WHERE date BETWEEN ? AND ?
            ORDER BY timestamp
        """, (start_date, end_date))
        return cursor.fetchall()

    def get_alerts_by_date(self, date: str) -> List[Dict]:
        """获取指定日期的告警"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT timestamp, alert_type, message, severity, behavior, duration
            FROM alerts
            WHERE date = ?
            ORDER BY timestamp DESC
        """, (date,))

        alerts = []
        for row in cursor.fetchall():
            alerts.append({
                "timestamp": row[0],
                "alert_type": row[1],
                "message": row[2],
                "severity": row[3],
                "behavior": row[4],
                "duration": row[5]
            })
        return alerts

    def get_daily_stats(self, date: str) -> Optional[Dict]:
        """获取指定日期的统计数据"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT date, behavior_durations, behavior_counts, total_duration, health_score, alerts_count
            FROM daily_stats
            WHERE date = ?
        """, (date,))

        row = cursor.fetchone()
        if row:
            return {
                "date": row[0],
                "behavior_durations": json.loads(row[1]),
                "behavior_counts": json.loads(row[2]),
                "total_duration": row[3],
                "health_score": row[4],
                "alerts_count": row[5]
            }
        return None

    def get_weekly_stats(self, end_date: Optional[str] = None) -> List[Dict]:
        """获取最近7天的统计数据"""
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        end = datetime.strptime(end_date, "%Y-%m-%d")
        start = end - timedelta(days=6)
        start_date = start.strftime("%Y-%m-%d")

        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT date, behavior_durations, behavior_counts, total_duration, health_score, alerts_count
            FROM daily_stats
            WHERE date BETWEEN ? AND ?
            ORDER BY date
        """, (start_date, end_date))

        stats = []
        for row in cursor.fetchall():
            stats.append({
                "date": row[0],
                "behavior_durations": json.loads(row[1]),
                "behavior_counts": json.loads(row[2]),
                "total_duration": row[3],
                "health_score": row[4],
                "alerts_count": row[5]
            })
        return stats

    def get_behavior_trend(self, behavior: str, days: int = 7) -> List[Tuple[str, float]]:
        """获取指定行为的趋势数据（日期，总时长）"""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days-1)).strftime("%Y-%m-%d")

        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT date, behavior_durations
            FROM daily_stats
            WHERE date BETWEEN ? AND ?
            ORDER BY date
        """, (start_date, end_date))

        trend = []
        for row in cursor.fetchall():
            durations = json.loads(row[1])
            duration = durations.get(behavior, 0.0)
            trend.append((row[0], duration))
        return trend

    def get_health_score_trend(self, days: int = 7) -> List[Tuple[str, float]]:
        """获取健康评分趋势"""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days-1)).strftime("%Y-%m-%d")

        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT date, health_score
            FROM daily_stats
            WHERE date BETWEEN ? AND ?
            ORDER BY date
        """, (start_date, end_date))

        return cursor.fetchall()

    def cleanup_old_data(self, days: int = 30):
        """清理超过指定天数的原始数据（保留统计数据）"""
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        cursor = self.conn.cursor()

        cursor.execute("DELETE FROM behavior_records WHERE date < ?", (cutoff_date,))
        cursor.execute("DELETE FROM accel_data WHERE date < ?", (cutoff_date,))
        cursor.execute("DELETE FROM alerts WHERE date < ?", (cutoff_date,))

        self.conn.commit()

    def export_to_csv(self, date: str, output_dir: str = "."):
        """导出指定日期的数据为CSV"""
        import csv
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)

        # 导出行为记录
        behaviors = self.get_behaviors_by_date(date)
        with open(output_path / f"behaviors_{date}.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Timestamp", "Behavior", "Confidence", "Duration"])
            for row in behaviors:
                writer.writerow([
                    datetime.fromtimestamp(row[0]).strftime("%Y-%m-%d %H:%M:%S"),
                    row[1], row[2], row[3]
                ])

        # 导出告警记录
        alerts = self.get_alerts_by_date(date)
        with open(output_path / f"alerts_{date}.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Timestamp", "Type", "Message", "Severity", "Behavior", "Duration"])
            for alert in alerts:
                writer.writerow([
                    datetime.fromtimestamp(alert["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
                    alert["alert_type"], alert["message"], alert["severity"],
                    alert["behavior"], alert["duration"]
                ])

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
