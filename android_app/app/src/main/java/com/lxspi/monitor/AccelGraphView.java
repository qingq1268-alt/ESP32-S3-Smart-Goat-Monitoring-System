package com.lxspi.monitor;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.view.View;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

public class AccelGraphView extends View {
    private static final int MAX_POINTS = 220;

    private final ArrayDeque<Float> xs = new ArrayDeque<>();
    private final ArrayDeque<Float> ys = new ArrayDeque<>();
    private final ArrayDeque<Float> zs = new ArrayDeque<>();

    private final Paint gridPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint xPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint yPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint zPaint = new Paint(Paint.ANTI_ALIAS_FLAG);

    public AccelGraphView(Context context) {
        super(context);
        gridPaint.setColor(Color.rgb(220, 226, 235));
        gridPaint.setStrokeWidth(1f);

        textPaint.setColor(Color.rgb(80, 95, 115));
        textPaint.setTextSize(28f);

        xPaint.setColor(Color.rgb(220, 76, 76));
        xPaint.setStrokeWidth(4f);
        xPaint.setStyle(Paint.Style.STROKE);

        yPaint.setColor(Color.rgb(39, 174, 96));
        yPaint.setStrokeWidth(4f);
        yPaint.setStyle(Paint.Style.STROKE);

        zPaint.setColor(Color.rgb(52, 113, 235));
        zPaint.setStrokeWidth(4f);
        zPaint.setStyle(Paint.Style.STROKE);
    }

    public void addPoint(float x, float y, float z) {
        append(xs, x);
        append(ys, y);
        append(zs, z);
        invalidate();
    }

    public void clear() {
        xs.clear();
        ys.clear();
        zs.clear();
        invalidate();
    }

    private void append(ArrayDeque<Float> q, float value) {
        if (q.size() >= MAX_POINTS) {
            q.removeFirst();
        }
        q.addLast(value);
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        int w = getWidth();
        int h = getHeight();
        int left = 56;
        int right = w - 16;
        int top = 24;
        int bottom = h - 44;
        int graphW = Math.max(1, right - left);
        int graphH = Math.max(1, bottom - top);

        canvas.drawColor(Color.rgb(248, 250, 252));
        for (int i = 0; i <= 4; i++) {
            float y = top + graphH * i / 4f;
            canvas.drawLine(left, y, right, y, gridPaint);
        }
        for (int i = 0; i <= 4; i++) {
            float x = left + graphW * i / 4f;
            canvas.drawLine(x, top, x, bottom, gridPaint);
        }
        canvas.drawText("2g", 8, top + 10, textPaint);
        canvas.drawText("0", 22, top + graphH / 2f + 10, textPaint);
        canvas.drawText("-2g", 4, bottom + 8, textPaint);

        canvas.drawText("X", left, h - 12, xPaint);
        canvas.drawText("Y", left + 54, h - 12, yPaint);
        canvas.drawText("Z", left + 108, h - 12, zPaint);

        drawSeries(canvas, xs, left, top, graphW, graphH, xPaint);
        drawSeries(canvas, ys, left, top, graphW, graphH, yPaint);
        drawSeries(canvas, zs, left, top, graphW, graphH, zPaint);
    }

    private void drawSeries(Canvas canvas, ArrayDeque<Float> q, int left, int top, int graphW, int graphH, Paint paint) {
        if (q.size() < 2) {
            return;
        }

        List<Float> values = new ArrayList<>(q);
        float prevX = left;
        float prevY = mapY(values.get(0), top, graphH);
        int last = values.size() - 1;
        for (int i = 1; i < values.size(); i++) {
            float x = left + graphW * i / (float) last;
            float y = mapY(values.get(i), top, graphH);
            canvas.drawLine(prevX, prevY, x, y, paint);
            prevX = x;
            prevY = y;
        }
    }

    private float mapY(float value, int top, int graphH) {
        float clipped = Math.max(-2f, Math.min(2f, value));
        return top + (2f - clipped) / 4f * graphH;
    }
}
