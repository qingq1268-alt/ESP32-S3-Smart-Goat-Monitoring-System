package com.lxspi.monitor;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

public class BehaviorTimelineView extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF rect = new RectF();
    private List<LocalHistoryDb.BehaviorSegment> segments = new ArrayList<>();
    private long dayStartMs = 0;
    private long dayEndMs = 0;

    public BehaviorTimelineView(Context context) {
        super(context);
        textPaint.setTextSize(sp(11));
        textPaint.setColor(Color.rgb(102, 111, 116));
    }

    public void setSegments(long dayStartMs, long dayEndMs, List<LocalHistoryDb.BehaviorSegment> segments) {
        this.dayStartMs = dayStartMs;
        this.dayEndMs = dayEndMs;
        this.segments = segments == null ? new ArrayList<LocalHistoryDb.BehaviorSegment>() : segments;
        invalidate();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        int w = getWidth();
        int h = getHeight();
        float left = dp(10);
        float right = w - dp(10);
        float top = dp(22);
        float barHeight = dp(26);
        float bottom = top + barHeight;
        float width = Math.max(1f, right - left);

        paint.setColor(Color.rgb(240, 232, 218));
        rect.set(left, top, right, bottom);
        canvas.drawRoundRect(rect, dp(8), dp(8), paint);

        if (dayEndMs > dayStartMs) {
            for (LocalHistoryDb.BehaviorSegment segment : segments) {
                long s = Math.max(dayStartMs, segment.startMs);
                long e = Math.min(dayEndMs, segment.endMs);
                if (e <= s) {
                    continue;
                }
                float x1 = left + (s - dayStartMs) * width / (float) (dayEndMs - dayStartMs);
                float x2 = left + (e - dayStartMs) * width / (float) (dayEndMs - dayStartMs);
                paint.setColor(colorFor(segment.behavior));
                rect.set(x1, top, Math.max(x1 + dp(2), x2), bottom);
                canvas.drawRoundRect(rect, dp(5), dp(5), paint);
            }
        }

        paint.setColor(Color.rgb(211, 201, 184));
        paint.setStrokeWidth(dp(1));
        for (int i = 0; i <= 4; i++) {
            float x = left + width * i / 4f;
            canvas.drawLine(x, top - dp(5), x, bottom + dp(5), paint);
            String label = String.format(Locale.US, "%02d:00", i * 6);
            float tw = textPaint.measureText(label);
            canvas.drawText(label, Math.min(Math.max(left, x - tw / 2f), right - tw), bottom + dp(22), textPaint);
        }

        if (segments.isEmpty()) {
            String empty = "暂无本机历史记录";
            float tw = textPaint.measureText(empty);
            canvas.drawText(empty, left + (width - tw) / 2f, top + barHeight / 2f + dp(4), textPaint);
        }
    }

    private int colorFor(String behavior) {
        if ("Displacement".equals(behavior)) {
            return Color.rgb(219, 79, 74);
        }
        if ("Grazing".equals(behavior)) {
            return Color.rgb(41, 169, 109);
        }
        if ("Ruminating_Chewing".equals(behavior)) {
            return Color.rgb(122, 92, 255);
        }
        if ("Other".equals(behavior)) {
            return Color.rgb(219, 160, 68);
        }
        if ("Resting".equals(behavior)) {
            return Color.rgb(47, 128, 237);
        }
        return Color.rgb(216, 203, 184);
    }

    private float dp(int value) {
        return value * getResources().getDisplayMetrics().density;
    }

    private float sp(int value) {
        return value * getResources().getDisplayMetrics().scaledDensity;
    }
}
