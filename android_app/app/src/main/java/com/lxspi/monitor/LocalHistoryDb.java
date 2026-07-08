package com.lxspi.monitor;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import java.util.ArrayList;
import java.util.List;

public class LocalHistoryDb extends SQLiteOpenHelper {
    private static final String DB_NAME = "lxspi_history.db";
    private static final int DB_VERSION = 1;

    public static class BehaviorSegment {
        public final long startMs;
        public final long endMs;
        public final String behavior;
        public final double confidence;

        public BehaviorSegment(long startMs, long endMs, String behavior, double confidence) {
            this.startMs = startMs;
            this.endMs = endMs;
            this.behavior = behavior;
            this.confidence = confidence;
        }
    }

    public static class AlertRecord {
        public final long tsMs;
        public final String severity;
        public final String title;
        public final String message;

        public AlertRecord(long tsMs, String severity, String title, String message) {
            this.tsMs = tsMs;
            this.severity = severity;
            this.title = title;
            this.message = message;
        }
    }

    public LocalHistoryDb(Context context) {
        super(context, DB_NAME, null, DB_VERSION);
    }

    @Override
    public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE behavior_segments (" +
                "id INTEGER PRIMARY KEY AUTOINCREMENT, " +
                "start_ms INTEGER NOT NULL, " +
                "end_ms INTEGER NOT NULL, " +
                "behavior TEXT NOT NULL, " +
                "confidence REAL NOT NULL)");
        db.execSQL("CREATE INDEX idx_behavior_day ON behavior_segments(start_ms, end_ms)");

        db.execSQL("CREATE TABLE alerts (" +
                "id INTEGER PRIMARY KEY AUTOINCREMENT, " +
                "ts_ms INTEGER NOT NULL, " +
                "severity TEXT NOT NULL, " +
                "title TEXT NOT NULL, " +
                "message TEXT NOT NULL)");
        db.execSQL("CREATE INDEX idx_alert_day ON alerts(ts_ms)");
    }

    @Override
    public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        db.execSQL("DROP TABLE IF EXISTS behavior_segments");
        db.execSQL("DROP TABLE IF EXISTS alerts");
        onCreate(db);
    }

    public void saveBehaviorSegment(long startMs, long endMs, String behavior, double confidence) {
        if (behavior == null || behavior.length() == 0 || endMs <= startMs) {
            return;
        }
        ContentValues values = new ContentValues();
        values.put("start_ms", startMs);
        values.put("end_ms", endMs);
        values.put("behavior", behavior);
        values.put("confidence", confidence);
        getWritableDatabase().insert("behavior_segments", null, values);
    }

    public void saveAlert(long tsMs, String severity, String title, String message) {
        ContentValues values = new ContentValues();
        values.put("ts_ms", tsMs);
        values.put("severity", severity == null ? "info" : severity);
        values.put("title", title == null ? "提示" : title);
        values.put("message", message == null ? "" : message);
        getWritableDatabase().insert("alerts", null, values);
    }

    public List<BehaviorSegment> getSegmentsForDay(long dayStartMs, long dayEndMs) {
        ArrayList<BehaviorSegment> out = new ArrayList<>();
        Cursor cursor = null;
        try {
            cursor = getReadableDatabase().query(
                    "behavior_segments",
                    new String[]{"start_ms", "end_ms", "behavior", "confidence"},
                    "start_ms < ? AND end_ms > ?",
                    new String[]{String.valueOf(dayEndMs), String.valueOf(dayStartMs)},
                    null,
                    null,
                    "start_ms ASC");
            while (cursor.moveToNext()) {
                out.add(new BehaviorSegment(
                        cursor.getLong(0),
                        cursor.getLong(1),
                        cursor.getString(2),
                        cursor.getDouble(3)));
            }
        } finally {
            if (cursor != null) {
                cursor.close();
            }
        }
        return out;
    }

    public List<AlertRecord> getAlertsForDay(long dayStartMs, long dayEndMs) {
        ArrayList<AlertRecord> out = new ArrayList<>();
        Cursor cursor = null;
        try {
            cursor = getReadableDatabase().query(
                    "alerts",
                    new String[]{"ts_ms", "severity", "title", "message"},
                    "ts_ms >= ? AND ts_ms < ?",
                    new String[]{String.valueOf(dayStartMs), String.valueOf(dayEndMs)},
                    null,
                    null,
                    "ts_ms DESC");
            while (cursor.moveToNext()) {
                out.add(new AlertRecord(
                        cursor.getLong(0),
                        cursor.getString(1),
                        cursor.getString(2),
                        cursor.getString(3)));
            }
        } finally {
            if (cursor != null) {
                cursor.close();
            }
        }
        return out;
    }
}
