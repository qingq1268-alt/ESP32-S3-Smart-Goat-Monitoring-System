package com.lxspi.monitor;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.view.View;

public class DailyPieView extends View {
    private static final int[] COLORS = {
            Color.rgb(219, 79, 74),
            Color.rgb(41, 169, 109),
            Color.rgb(122, 92, 255),
            Color.rgb(219, 160, 68),
            Color.rgb(47, 128, 237),
            Color.rgb(216, 203, 184)
    };

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint centerPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final double[] hours = new double[]{0, 0, 0, 0, 0, 24};

    public DailyPieView(Context context) {
        super(context);
        centerPaint.setColor(Color.rgb(255, 250, 241));
    }

    public void setHours(double[] behaviorHours) {
        double recorded = 0.0;
        for (int i = 0; i < 5; i++) {
            hours[i] = Math.max(0.0, behaviorHours[i]);
            recorded += hours[i];
        }
        hours[5] = Math.max(0.0, 24.0 - recorded);
        invalidate();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        int w = getWidth();
        int h = getHeight();
        float cx = w / 2f;
        float cy = h / 2f;
        float r = Math.min(w, h) / 2f - 4f;
        float start = -90f;
        for (int i = 0; i < hours.length; i++) {
            float sweep = (float) (hours[i] / 24.0 * 360.0);
            paint.setColor(COLORS[i]);
            canvas.drawArc(cx - r, cy - r, cx + r, cy + r, start, sweep, true, paint);
            start += sweep;
        }
        canvas.drawCircle(cx, cy, r * 0.48f, centerPaint);
    }
}
