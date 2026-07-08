package com.lxspi.monitor;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.DhcpInfo;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.ImageView;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.Inet4Address;
import java.net.InetSocketAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Date;
import java.util.Enumeration;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

public class MainActivity extends Activity {
    private static final int INFER_PORT = 5005;
    private static final int ACCEL_PORT = 5007;
    private static final int CONTROL_PORT = 6006;
    private static final double MIN_CONFIDENCE = 0.50;
    private static final long DAY_MS = 24L * 60L * 60L * 1000L;
    private static final String SOFTAP_IP = "192.168.4.1";
    private static final String BROADCAST_IP = "255.255.255.255";
    private static final String[] BEHAVIORS = {
            "Displacement", "Grazing", "Ruminating_Chewing", "Other", "Resting"
    };

    private static final int BG = Color.rgb(242, 236, 224);
    private static final int SURFACE = Color.rgb(255, 251, 244);
    private static final int SURFACE_ALT = Color.rgb(248, 241, 229);
    private static final int TEXT = Color.rgb(32, 45, 55);
    private static final int MUTED = Color.rgb(102, 111, 116);
    private static final int GREEN = Color.rgb(24, 96, 78);
    private static final int GREEN_DARK = Color.rgb(18, 72, 58);
    private static final int GOLD = Color.rgb(218, 158, 55);
    private static final int RUST = Color.rgb(166, 88, 58);
    private static final int BORDER = Color.rgb(226, 216, 199);

    private final Handler ui = new Handler(Looper.getMainLooper());
    private volatile boolean listening = false;
    private DatagramSocket inferSocket;
    private DatagramSocket accelSocket;
    private Thread inferThread;
    private Thread accelThread;
    private WifiManager.MulticastLock multicastLock;

    private TextView behaviorText;
    private TextView confidenceText;
    private TextView statusText;
    private Button goatButton;
    private TextView ipText;
    private TextView batteryText;
    private TextView sdText;
    private TextView recordText;
    private TextView packetText;
    private TextView accelRateText;
    private TextView logText;
    private EditText targetIpEdit;
    private AccelGraphView graphView;
    private DailyPieView dailyPieView;
    private Button accelWaveButton;
    private LinearLayout detailContainer;
    private LinearLayout commandContainer;
    private LinearLayout historyContainer;
    private Button detailTabButton;
    private Button commandTabButton;
    private Button historyTabButton;
    private TextView recordedHoursText;
    private TextView dailyListText;
    private TextView adviceGateText;
    private TextView adviceTitleText;
    private TextView adviceHintText;
    private Button adviceButton;
    private TextView historyDateText;
    private TextView historyScoreText;
    private TextView historySummaryText;
    private DailyPieView historyPieView;
    private BehaviorTimelineView historyTimelineView;
    private LinearLayout historyBehaviorList;
    private LinearLayout historyAlertList;
    private LocalHistoryDb historyDb;

    private String espIp = "";
    private String deviceId = "--";
    private long packetCount = 0;
    private long accelPoints = 0;
    private long lastAnyPacketMs = 0;
    private long lastAccelPacketMs = 0;
    private int lastAccelSecond = 0;
    private long lastRateTickMs = 0;
    private boolean accelWaveEnabled = false;
    private final double[] behaviorSeconds = new double[BEHAVIORS.length];
    private String lastBehaviorForDuration = null;
    private long lastBehaviorMs = 0;
    private long historySelectedDayStartMs = 0;
    private String currentHistoryBehavior = null;
    private long currentHistoryStartMs = 0;
    private long currentHistoryLastMs = 0;
    private double currentHistoryConfidence = 0.0;
    private long lastLowBatteryAlertMs = 0;
    private long lastSdAlertMs = 0;

    private final Runnable ticker = new Runnable() {
        @Override
        public void run() {
            updateConnectionStatus();
            ui.postDelayed(this, 1000);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Window window = getWindow();
        window.setStatusBarColor(BG);
        window.setNavigationBarColor(BG);
        window.getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        historyDb = new LocalHistoryDb(this);
        historySelectedDayStartMs = startOfDay(System.currentTimeMillis());
        buildUi();
        ui.post(ticker);
    }

    @Override
    protected void onDestroy() {
        closeOpenHistorySegment(System.currentTimeMillis());
        stopListening();
        if (historyDb != null) {
            historyDb.close();
        }
        ui.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    private void buildUi() {
        LinearLayout screen = new LinearLayout(this);
        screen.setOrientation(LinearLayout.VERTICAL);
        screen.setBackgroundColor(BG);

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(false);
        scroll.setBackgroundColor(BG);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(14), topSafePadding(), dp(14), dp(14));
        scroll.addView(root, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        root.addView(buildHeroCard(), fullWidthWithBottom(10));

        detailContainer = new LinearLayout(this);
        detailContainer.setOrientation(LinearLayout.VERTICAL);
        detailContainer.addView(buildDetailGrid(), fullWidthWithBottom(10));
        detailContainer.addView(buildDailyCard(), fullWidthWithBottom(10));
        detailContainer.addView(buildAdviceCard(), fullWidthWithBottom(0));
        root.addView(detailContainer);

        commandContainer = new LinearLayout(this);
        commandContainer.setOrientation(LinearLayout.VERTICAL);
        commandContainer.setVisibility(View.GONE);
        commandContainer.addView(buildConnectionCard(), fullWidthWithBottom(10));
        commandContainer.addView(buildGraphCard(), fullWidthWithBottom(10));
        commandContainer.addView(buildCommandCard(), fullWidthWithBottom(10));
        commandContainer.addView(buildLogCard(), fullWidthWithBottom(0));
        root.addView(commandContainer);

        historyContainer = new LinearLayout(this);
        historyContainer.setOrientation(LinearLayout.VERTICAL);
        historyContainer.setVisibility(View.GONE);
        historyContainer.addView(buildHistoryPage(), fullWidthWithBottom(0));
        root.addView(historyContainer);

        screen.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        screen.addView(buildBottomNav(), new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(72)));

        setContentView(screen);
    }

    private View buildHeroCard() {
        LinearLayout card = card(true);
        card.setPadding(dp(18), dp(16), dp(18), dp(16));

        TextView title = label("山羊行为监测控制台", 22, Color.WHITE, true);
        card.addView(title);

        behaviorText = label("等待数据", 32, Color.WHITE, true);
        behaviorText.setGravity(Gravity.START);
        behaviorText.setPadding(0, dp(16), 0, 0);
        behaviorText.setSingleLine(false);
        behaviorText.setMaxLines(2);
        card.addView(behaviorText);

        confidenceText = label("置信度 --", 14, Color.rgb(221, 232, 221), false);
        confidenceText.setPadding(0, dp(2), 0, 0);
        card.addView(confidenceText);

        statusText = pill("未监听", Color.rgb(237, 226, 204), GREEN_DARK);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(12);
        card.addView(statusText, lp);
        return card;
    }

    private View buildConnectionCard() {
        LinearLayout card = card(false);
        card.addView(sectionTitle("连接设置"));

        LinearLayout row = row(8);
        TextView targetLabel = label("设备 IP", 13, MUTED, true);
        row.addView(targetLabel);
        targetIpEdit = new EditText(this);
        targetIpEdit.setSingleLine(true);
        targetIpEdit.setText(SOFTAP_IP);
        targetIpEdit.setTextColor(TEXT);
        targetIpEdit.setTextSize(15);
        targetIpEdit.setHint("192.168.4.1");
        targetIpEdit.setInputType(InputType.TYPE_CLASS_TEXT);
        targetIpEdit.setPadding(dp(12), 0, dp(12), 0);
        targetIpEdit.setBackground(round(SURFACE, BORDER, 10));
        row.addView(targetIpEdit, new LinearLayout.LayoutParams(0, dp(46), 1));
        card.addView(row);

        LinearLayout buttons = row(8);
        buttons.addView(commandButton("刷新状态", false, new View.OnClickListener() {
            @Override public void onClick(View v) { refreshStatus(); }
        }), weight());
        buttons.addView(commandButton("连接推送", true, new View.OnClickListener() {
            @Override public void onClick(View v) { startListening(); }
        }), weight());
        card.addView(buttons);

        return card;
    }

    private View buildDetailGrid() {
        LinearLayout grid = new LinearLayout(this);
        grid.setOrientation(LinearLayout.VERTICAL);

        LinearLayout line1 = row(8);
        goatButton = metricButton("羊只编号", "0001", "佩戴个体", new View.OnClickListener() {
            @Override public void onClick(View v) { showGoatDialog(); }
        });
        ipText = metricCard("设备 IP", "--", "当前源地址");
        line1.addView(goatButton, weight());
        line1.addView(ipText, weight());
        grid.addView(line1);

        LinearLayout line2 = row(8);
        batteryText = metricCard("供电状态", "--", "电池估算");
        sdText = metricCard("SD 存储", "--", "挂载 / 记录");
        line2.addView(batteryText, weight());
        line2.addView(sdText, weight());
        grid.addView(line2);

        LinearLayout line3 = row(8);
        packetText = metricCard("推理包", "0", "已接收");
        recordText = metricCard("记录状态", "未记录", "SD session");
        line3.addView(packetText, weight());
        line3.addView(recordText, weight());
        grid.addView(line3);
        return grid;
    }

    private View buildMetricsGrid() {
        LinearLayout grid = new LinearLayout(this);
        grid.setOrientation(LinearLayout.VERTICAL);

        LinearLayout line1 = row(8);
        batteryText = metricCard("供电状态", "--", "电池估算");
        packetText = metricCard("推理包", "0", "已接收");
        line1.addView(batteryText, weight());
        line1.addView(packetText, weight());
        grid.addView(line1);

        LinearLayout line2 = row(8);
        sdText = metricCard("SD 存储", "--", "挂载 / 记录");
        accelRateText = metricCard("加速度点率", "0 点/s", "实时流");
        line2.addView(sdText, weight());
        line2.addView(accelRateText, weight());
        grid.addView(line2);
        return grid;
    }

    private View buildGraphCard() {
        LinearLayout card = card(false);
        LinearLayout header = row(8);
        header.addView(sectionTitle("三轴加速度"), new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        accelRateText = pill("0 点/s", Color.rgb(224, 235, 251), Color.rgb(47, 128, 237));
        header.addView(accelRateText);
        accelWaveButton = commandButton("打开波形", true, new View.OnClickListener() {
            @Override public void onClick(View v) { toggleAccelWave(); }
        });
        LinearLayout.LayoutParams waveLp = new LinearLayout.LayoutParams(dp(92), dp(42));
        waveLp.leftMargin = dp(6);
        header.addView(accelWaveButton, waveLp);
        card.addView(header);
        graphView = new AccelGraphView(this);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(240));
        lp.topMargin = dp(8);
        graphView.setBackground(round(Color.rgb(249, 251, 249), Color.rgb(226, 232, 226), 10));
        card.addView(graphView, lp);
        return card;
    }

    private View buildDailyCard() {
        LinearLayout card = card(false);
        LinearLayout header = row(8);
        header.addView(sectionTitle("今日行为占比"), new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        TextView chip = pill("24h", Color.rgb(227, 238, 223), GREEN_DARK);
        header.addView(chip);
        card.addView(header);

        LinearLayout body = row(10);
        dailyPieView = new DailyPieView(this);
        LinearLayout.LayoutParams pieLp = new LinearLayout.LayoutParams(dp(138), dp(138));
        body.addView(dailyPieView, pieLp);

        LinearLayout stats = new LinearLayout(this);
        stats.setOrientation(LinearLayout.VERTICAL);
        recordedHoursText = label("已记录 0.0h", 18, GREEN_DARK, true);
        dailyListText = label("暂无行为统计", 12, MUTED, false);
        dailyListText.setPadding(0, dp(6), 0, 0);
        stats.addView(recordedHoursText);
        stats.addView(dailyListText);
        body.addView(stats, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        card.addView(body);
        updateDailyStatsViews();
        return card;
    }

    private View buildAdviceCard() {
        LinearLayout card = card(false);
        LinearLayout header = row(8);
        header.addView(sectionTitle("辅助建议"), new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        adviceGateText = pill("已记录 0.0h", Color.rgb(227, 238, 223), GREEN_DARK);
        header.addView(adviceGateText);
        card.addView(header);

        LinearLayout entry = new LinearLayout(this);
        entry.setOrientation(LinearLayout.HORIZONTAL);
        entry.setGravity(Gravity.CENTER_VERTICAL);
        entry.setPadding(dp(10), dp(10), dp(10), dp(10));
        entry.setBackground(round(SURFACE_ALT, 0, 10));

        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);
        adviceTitleText = label("记录满 12h 后生成建议", 13, TEXT, true);
        adviceHintText = label("当前记录时长不足，暂不显示采食不足、反刍不足等判断。", 11, MUTED, false);
        adviceHintText.setPadding(0, dp(3), 0, 0);
        copy.addView(adviceTitleText);
        copy.addView(adviceHintText);
        entry.addView(copy, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        adviceButton = commandButton("暂不可用", true, new View.OnClickListener() {
            @Override public void onClick(View v) { showAdviceDialog(); }
        });
        entry.addView(adviceButton, new LinearLayout.LayoutParams(dp(92), dp(42)));
        card.addView(entry);
        return card;
    }

    private View buildCommandCard() {
        LinearLayout card = card(false);
        card.addView(sectionTitle("控制命令"));

        LinearLayout line1 = row(8);
        line1.addView(commandButton("开始采集", true, new View.OnClickListener() {
            @Override public void onClick(View v) { startCollection(); }
        }), weight());
        line1.addView(commandButton("停止采集", false, new View.OnClickListener() {
            @Override public void onClick(View v) { pauseCollection(); }
        }), weight());
        card.addView(line1);

        LinearLayout line2 = row(8);
        line2.addView(commandButton("启动监听", true, new View.OnClickListener() {
            @Override public void onClick(View v) { startListening(); }
        }), weight());
        line2.addView(commandButton("停止监听", false, new View.OnClickListener() {
            @Override public void onClick(View v) { stopListening(); }
        }), weight());
        card.addView(line2);

        LinearLayout line3 = row(8);
        line3.addView(commandButton("暂停记录", false, new View.OnClickListener() {
            @Override public void onClick(View v) { sendCommand("STOPREC"); }
        }), weight());
        line3.addView(commandButton("重新挂载SD", false, new View.OnClickListener() {
            @Override public void onClick(View v) { sendCommand("MOUNT"); }
        }), weight());
        card.addView(line3);

        LinearLayout line4 = row(8);
        line4.addView(commandButton("重置设备", false, new View.OnClickListener() {
            @Override public void onClick(View v) { confirmReset(); }
        }), weight());
        card.addView(line4);
        return card;
    }

    private View buildLogCard() {
        LinearLayout card = card(false);
        card.addView(sectionTitle("操作记录"));
        logText = label("--", 13, MUTED, false);
        logText.setMinLines(3);
        logText.setPadding(0, dp(4), 0, 0);
        card.addView(logText);
        return card;
    }

    private View buildHistoryPage() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.addView(buildHistoryHeaderCard(), fullWidthWithBottom(10));
        page.addView(buildHistoryTimelineCard(), fullWidthWithBottom(10));
        page.addView(buildHistoryBehaviorCard(), fullWidthWithBottom(10));
        page.addView(buildHistoryAlertCard(), fullWidthWithBottom(0));
        refreshHistoryPage();
        return page;
    }

    private View buildHistoryHeaderCard() {
        LinearLayout card = card(false);

        LinearLayout dateRow = row(8);
        Button prev = commandButton("前一天", false, new View.OnClickListener() {
            @Override public void onClick(View v) { shiftHistoryDay(-1); }
        });
        Button today = commandButton("今天", true, new View.OnClickListener() {
            @Override public void onClick(View v) {
                historySelectedDayStartMs = startOfDay(System.currentTimeMillis());
                refreshHistoryPage();
            }
        });
        Button next = commandButton("后一天", false, new View.OnClickListener() {
            @Override public void onClick(View v) { shiftHistoryDay(1); }
        });
        dateRow.addView(prev, weight());
        dateRow.addView(today, weight());
        dateRow.addView(next, weight());
        card.addView(dateRow);

        historyDateText = label("--", 14, MUTED, true);
        historyDateText.setGravity(Gravity.CENTER);
        historyDateText.setPadding(0, dp(6), 0, dp(8));
        card.addView(historyDateText);

        LinearLayout overview = row(10);
        LinearLayout scoreBox = new LinearLayout(this);
        scoreBox.setOrientation(LinearLayout.VERTICAL);
        scoreBox.setGravity(Gravity.CENTER);
        scoreBox.setPadding(dp(8), dp(10), dp(8), dp(9));
        scoreBox.setBackground(round(GREEN, 0, 14));

        historyScoreText = label("96", 34, Color.WHITE, true);
        historyScoreText.setGravity(Gravity.CENTER);
        historyScoreText.setSingleLine(true);
        historyScoreText.setIncludeFontPadding(false);
        scoreBox.addView(historyScoreText, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));

        TextView scoreCaption = label("健康评分", 13, Color.rgb(225, 245, 238), true);
        scoreCaption.setGravity(Gravity.CENTER);
        scoreCaption.setSingleLine(true);
        scoreCaption.setIncludeFontPadding(false);
        scoreBox.addView(scoreCaption, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        overview.addView(scoreBox, new LinearLayout.LayoutParams(dp(118), dp(112)));

        LinearLayout summaryBox = new LinearLayout(this);
        summaryBox.setOrientation(LinearLayout.VERTICAL);
        summaryBox.setPadding(dp(12), dp(10), dp(12), dp(10));
        summaryBox.setBackground(round(SURFACE_ALT, 0, 14));
        TextView title = label("本机历史总览", 15, TEXT, true);
        historySummaryText = label("暂无历史记录", 12, MUTED, false);
        historySummaryText.setPadding(0, dp(6), 0, 0);
        summaryBox.addView(title);
        summaryBox.addView(historySummaryText);
        overview.addView(summaryBox, new LinearLayout.LayoutParams(0, dp(112), 1));
        card.addView(overview);
        return card;
    }

    private View buildHistoryTimelineCard() {
        LinearLayout card = card(false);
        LinearLayout header = row(8);
        header.addView(sectionTitle("24h 行为时间线"), new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        header.addView(pill("本机", Color.rgb(227, 238, 223), GREEN_DARK));
        card.addView(header);

        historyTimelineView = new BehaviorTimelineView(this);
        historyTimelineView.setBackground(round(Color.rgb(249, 251, 249), Color.rgb(226, 232, 226), 10));
        LinearLayout.LayoutParams timelineLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(82));
        card.addView(historyTimelineView, timelineLp);

        LinearLayout body = row(10);
        historyPieView = new DailyPieView(this);
        LinearLayout.LayoutParams pieLp = new LinearLayout.LayoutParams(dp(116), dp(116));
        pieLp.topMargin = dp(10);
        body.addView(historyPieView, pieLp);

        TextView legend = label(
                "红 位移 · 绿 采食\n紫 反刍 · 黄 其他\n蓝 休息 · 灰 未记录",
                12, MUTED, false);
        legend.setPadding(dp(6), dp(10), 0, 0);
        body.addView(legend, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        card.addView(body);
        return card;
    }

    private View buildHistoryBehaviorCard() {
        LinearLayout card = card(false);
        card.addView(sectionTitle("行为卡片"));
        historyBehaviorList = new LinearLayout(this);
        historyBehaviorList.setOrientation(LinearLayout.VERTICAL);
        card.addView(historyBehaviorList);
        return card;
    }

    private View buildHistoryAlertCard() {
        LinearLayout card = card(false);
        card.addView(sectionTitle("告警列表"));
        historyAlertList = new LinearLayout(this);
        historyAlertList.setOrientation(LinearLayout.VERTICAL);
        card.addView(historyAlertList);
        return card;
    }

    private View buildBottomNav() {
        LinearLayout nav = new LinearLayout(this);
        nav.setOrientation(LinearLayout.HORIZONTAL);
        nav.setGravity(Gravity.CENTER);
        nav.setPadding(dp(12), dp(7), dp(12), dp(9));
        nav.setBackground(round(SURFACE, BORDER, 0));

        detailTabButton = navButton("详细界面", true, new View.OnClickListener() {
            @Override public void onClick(View v) { switchTab("detail"); }
        });
        commandTabButton = navButton("控制命令", false, new View.OnClickListener() {
            @Override public void onClick(View v) { switchTab("command"); }
        });
        historyTabButton = navButton("历史", false, new View.OnClickListener() {
            @Override public void onClick(View v) { switchTab("history"); }
        });
        nav.addView(detailTabButton, weight());
        nav.addView(commandTabButton, weight());
        nav.addView(historyTabButton, weight());
        return nav;
    }

    private void switchTab(String tab) {
        boolean detail = "detail".equals(tab);
        boolean command = "command".equals(tab);
        boolean history = "history".equals(tab);
        detailContainer.setVisibility(detail ? View.VISIBLE : View.GONE);
        commandContainer.setVisibility(command ? View.VISIBLE : View.GONE);
        historyContainer.setVisibility(history ? View.VISIBLE : View.GONE);
        styleNavButton(detailTabButton, detail);
        styleNavButton(commandTabButton, command);
        styleNavButton(historyTabButton, history);
        if (history) {
            refreshHistoryPage();
        }
    }

    private void startListening() {
        if (listening) {
            log("已经在监听");
            showTransientHint("已在监听中");
            return;
        }
        listening = true;
        acquireMulticastLock();
        graphView.clear();
        accelWaveEnabled = false;
        updateAccelWaveButton();
        if (accelRateText != null) {
            accelRateText.setText("波形关闭");
        }
        inferThread = new Thread(new ReceiverLoop(INFER_PORT), "infer-udp");
        accelThread = new Thread(new ReceiverLoop(ACCEL_PORT), "accel-udp");
        inferThread.start();
        accelThread.start();
        statusText.setText("监听中 5005 / 5007");
        statusText.setTextColor(GREEN_DARK);
        sendCommand("ACCELOFF");
        sendCommand("SYNC:" + System.currentTimeMillis());
        log("启动 UDP 监听");
        showTransientHint("已启动监听");
    }

    private void stopListening() {
        sendCommand("ACCELOFF");
        closeOpenHistorySegment(System.currentTimeMillis());
        accelWaveEnabled = false;
        updateAccelWaveButton();
        listening = false;
        closeSocket(inferSocket);
        closeSocket(accelSocket);
        inferSocket = null;
        accelSocket = null;
        releaseMulticastLock();
        if (statusText != null) {
            statusText.setText("已停止");
            statusText.setTextColor(GREEN_DARK);
        }
        log("停止 UDP 监听");
        showTransientHint("已停止监听");
    }

    private class ReceiverLoop implements Runnable {
        private final int port;

        ReceiverLoop(int port) {
            this.port = port;
        }

        @Override
        public void run() {
            DatagramSocket socket = null;
            try {
                socket = new DatagramSocket(null);
                socket.setReuseAddress(true);
                socket.bind(new InetSocketAddress(port));
                if (port == INFER_PORT) {
                    inferSocket = socket;
                } else {
                    accelSocket = socket;
                }

                byte[] buf = new byte[8192];
                while (listening) {
                    DatagramPacket packet = new DatagramPacket(buf, buf.length);
                    socket.receive(packet);
                    String raw = new String(packet.getData(), 0, packet.getLength(), StandardCharsets.UTF_8);
                    String sender = packet.getAddress().getHostAddress();
                    handlePacket(raw, sender);
                }
            } catch (Exception e) {
                if (listening) {
                    log("端口 " + port + " 监听异常: " + e.getMessage());
                }
            } finally {
                closeSocket(socket);
            }
        }
    }

    private void handlePacket(String raw, String senderIp) {
        try {
            JSONObject d = new JSONObject(raw);
            String type = d.optString("type", "");
            String dev = d.optString("dev", "");
            if (dev.length() > 0) {
                deviceId = dev;
            }
            syncDeviceAddress(senderIp);
            lastAnyPacketMs = System.currentTimeMillis();

            if ("infer".equals(type)) {
                handleInfer(d);
            } else if ("accel".equals(type)) {
                handleAccel(d);
            }
        } catch (Exception e) {
            log("JSON 解析失败: " + e.getMessage());
        }
    }

    private void syncDeviceAddress(String senderIp) {
        if (senderIp == null) {
            return;
        }

        final String cleanIp = senderIp.trim();
        if (cleanIp.length() == 0) {
            return;
        }

        boolean changed = !cleanIp.equals(espIp);
        espIp = cleanIp;
        if (changed) {
            log("目标 IP 自动更新: " + cleanIp);
        }

        ui.post(new Runnable() {
            @Override
            public void run() {
                if (targetIpEdit != null) {
                    String currentTarget = targetIpEdit.getText().toString().trim();
                    if (!cleanIp.equals(currentTarget)) {
                        targetIpEdit.setText(cleanIp);
                    }
                }
                if (ipText != null) {
                    ipText.setText(twoLine("IP", blankDash(cleanIp)));
                }
            }
        });
    }

    private void handleInfer(final JSONObject d) {
        packetCount++;
        final String rawAct = d.optString("act", "--");
        final double conf = d.optDouble("conf", 0.0);
        final String displayAct = conf >= MIN_CONFIDENCE ? rawAct : "未确认";
        if (conf >= MIN_CONFIDENCE) {
            updateBehaviorDuration(rawAct, conf);
        }
        final JSONObject battery = d.optJSONObject("battery");
        final JSONObject sd = d.optJSONObject("sd");

        ui.post(new Runnable() {
            @Override
            public void run() {
                behaviorText.setText(displayAct);
                behaviorText.setTextSize(displayAct.length() > 14 ? 26 : 32);
                confidenceText.setText(String.format(Locale.US, "置信度 %.1f%%", conf * 100.0));
                goatButton.setText(twoLine("羊只编号", "0001\n佩戴个体"));
                ipText.setText(twoLine("IP", blankDash(espIp)));
                packetText.setText(twoLine("推理包", String.valueOf(packetCount)));
                updateBattery(battery);
                updateSd(sd);
                updateDailyStatsViews();
                refreshHistoryIfVisible();
                updateConnectionStatus();
            }
        });
    }

    private void handleAccel(final JSONObject d) {
        lastAccelPacketMs = System.currentTimeMillis();
        final JSONObject sd = d.optJSONObject("sd");
        final JSONArray acc = d.optJSONArray("acc");
        final int n = d.optInt("n", acc == null ? 0 : acc.length());
        final boolean stream = d.optBoolean("stream", true);
        accelPoints += Math.max(0, n);

        ui.post(new Runnable() {
            @Override
            public void run() {
                if (!accelWaveEnabled || !stream) {
                    if (accelRateText != null) {
                        accelRateText.setText("波形关闭");
                    }
                } else if (acc != null) {
                    for (int i = 0; i < acc.length(); i++) {
                        JSONArray p = acc.optJSONArray(i);
                        if (p != null && p.length() >= 3) {
                            graphView.addPoint((float) p.optDouble(0), (float) p.optDouble(1), (float) p.optDouble(2));
                        }
                    }
                }
                goatButton.setText(twoLine("羊只编号", "0001\n佩戴个体"));
                ipText.setText(twoLine("IP", blankDash(espIp)));
                updateSd(sd);
                updateConnectionStatus();
            }
        });
    }

    private void updateBattery(JSONObject battery) {
        if (battery == null) {
            return;
        }
        int pct = battery.optInt("percentage", 0);
        int mv = battery.optInt("voltage", 0);
        String state = battery.optString("status", "");
        batteryText.setText(twoLine("供电状态", pct + "%  " + mv + "mV\n" + state));
        long now = System.currentTimeMillis();
        if (pct > 0 && pct <= 15 && now - lastLowBatteryAlertMs > 60L * 60L * 1000L) {
            saveHistoryAlert(now, "warn", "电量偏低", "当前电量 " + pct + "%，建议检查供电或及时充电。");
            lastLowBatteryAlertMs = now;
        }
    }

    private void updateSd(JSONObject sd) {
        if (sd == null) {
            return;
        }
        boolean mounted = sd.optBoolean("mounted", false);
        boolean recording = sd.optBoolean("recording", false);
        sdText.setText(twoLine("SD 存储", (mounted ? "已挂载" : "未挂载") + "\n" + (recording ? "记录中" : "未记录")));
        if (recordText != null) {
            recordText.setText(twoLine("记录状态", recording ? "记录中" : "未记录"));
        }
        long now = System.currentTimeMillis();
        if (!mounted && now - lastSdAlertMs > 60L * 60L * 1000L) {
            saveHistoryAlert(now, "danger", "SD 未挂载", "设备上报 SD 卡未挂载，历史记录可能无法写入板端存储。");
            lastSdAlertMs = now;
        }
    }

    private void updateConnectionStatus() {
        long now = System.currentTimeMillis();
        boolean online = listening && ((now - lastAccelPacketMs <= 2500) || (now - lastAnyPacketMs <= 5000));
        if (listening) {
            statusText.setText(online ? "在线" : "等待数据");
            statusText.setTextColor(online ? GREEN_DARK : RUST);
        }
        if (!accelWaveEnabled) {
            if (accelRateText != null) {
                accelRateText.setText("波形关闭");
            }
        } else if (lastRateTickMs == 0) {
            lastRateTickMs = now;
            lastAccelSecond = (int) accelPoints;
        } else if (now - lastRateTickMs >= 1000) {
            int current = (int) accelPoints;
            int rate = Math.max(0, current - lastAccelSecond);
            accelRateText.setText(twoLine("加速度点率", rate + " 点/s"));
            lastAccelSecond = current;
            lastRateTickMs = now;
        }
    }

    private void updateBehaviorDuration(String behavior, double confidence) {
        int idx = behaviorIndex(behavior);
        if (idx < 0) {
            return;
        }
        long now = System.currentTimeMillis();
        if (lastBehaviorForDuration != null && lastBehaviorMs > 0) {
            int lastIdx = behaviorIndex(lastBehaviorForDuration);
            if (lastIdx >= 0) {
                double delta = Math.max(0.0, Math.min(60.0, (now - lastBehaviorMs) / 1000.0));
                behaviorSeconds[lastIdx] += delta;
            }
        }
        lastBehaviorForDuration = behavior;
        lastBehaviorMs = now;
        trackHistorySegment(behavior, confidence, now);
    }

    private int behaviorIndex(String behavior) {
        if (behavior == null) {
            return -1;
        }
        for (int i = 0; i < BEHAVIORS.length; i++) {
            if (BEHAVIORS[i].equals(behavior)) {
                return i;
            }
        }
        return -1;
    }

    private void updateDailyStatsViews() {
        if (dailyPieView == null || recordedHoursText == null || dailyListText == null) {
            return;
        }
        double[] hours = new double[BEHAVIORS.length];
        double recorded = 0.0;
        for (int i = 0; i < BEHAVIORS.length; i++) {
            hours[i] = behaviorSeconds[i] / 3600.0;
            recorded += hours[i];
        }
        dailyPieView.setHours(hours);
        recordedHoursText.setText(String.format(Locale.US, "已记录 %.1fh", recorded));
        double unrecorded = Math.max(0.0, 24.0 - recorded);
        dailyListText.setText(
                String.format(Locale.US,
                        "位移 %.1fh · 采食 %.1fh\n反刍 %.1fh · 其他 %.1fh\n休息 %.1fh · 未记录 %.1fh",
                        hours[0], hours[1], hours[2], hours[3], hours[4], unrecorded));
        if (adviceGateText != null) {
            adviceGateText.setText(String.format(Locale.US, "已记录 %.1fh", recorded));
        }
        boolean ready = recorded >= 12.0;
        if (adviceTitleText != null) {
            adviceTitleText.setText(ready ? "已满足建议生成条件" : "记录满 12h 后生成建议");
        }
        if (adviceHintText != null) {
            adviceHintText.setText(ready ? "可查看采食、反刍和活动结构建议。" : "当前记录时长不足，暂不显示采食不足、反刍不足等判断。");
        }
        if (adviceButton != null) {
            adviceButton.setText(ready ? "查看建议" : "暂不可用");
        }
    }

    private void trackHistorySegment(String behavior, double confidence, long now) {
        if (historyDb == null) {
            return;
        }
        if (currentHistoryBehavior == null) {
            currentHistoryBehavior = behavior;
            currentHistoryStartMs = now;
            currentHistoryLastMs = now;
            currentHistoryConfidence = confidence;
            return;
        }
        if (currentHistoryBehavior.equals(behavior)) {
            currentHistoryLastMs = now;
            currentHistoryConfidence = Math.max(currentHistoryConfidence, confidence);
            return;
        }
        saveHistorySegmentIfUseful(currentHistoryStartMs, boundedHistoryEnd(now), currentHistoryBehavior, currentHistoryConfidence);
        currentHistoryBehavior = behavior;
        currentHistoryStartMs = now;
        currentHistoryLastMs = now;
        currentHistoryConfidence = confidence;
    }

    private void closeOpenHistorySegment(long now) {
        if (currentHistoryBehavior != null && currentHistoryStartMs > 0) {
            long endMs = currentHistoryLastMs > currentHistoryStartMs ? boundedHistoryEnd(now) : now;
            saveHistorySegmentIfUseful(currentHistoryStartMs, endMs, currentHistoryBehavior, currentHistoryConfidence);
            currentHistoryBehavior = null;
            currentHistoryStartMs = 0;
            currentHistoryLastMs = 0;
            currentHistoryConfidence = 0.0;
        }
    }

    private long boundedHistoryEnd(long now) {
        if (currentHistoryLastMs <= 0) {
            return now;
        }
        return Math.min(now, currentHistoryLastMs + 60000L);
    }

    private void saveHistorySegmentIfUseful(long startMs, long endMs, String behavior, double confidence) {
        if (historyDb == null || endMs <= startMs || endMs - startMs < 3000L) {
            return;
        }
        historyDb.saveBehaviorSegment(startMs, endMs, behavior, confidence);
    }

    private void saveHistoryAlert(long tsMs, String severity, String title, String message) {
        if (historyDb != null) {
            historyDb.saveAlert(tsMs, severity, title, message);
            refreshHistoryIfVisible();
        }
    }

    private void shiftHistoryDay(int days) {
        historySelectedDayStartMs += days * DAY_MS;
        long today = startOfDay(System.currentTimeMillis());
        if (historySelectedDayStartMs > today) {
            historySelectedDayStartMs = today;
        }
        refreshHistoryPage();
    }

    private void refreshHistoryIfVisible() {
        if (historyContainer != null && historyContainer.getVisibility() == View.VISIBLE) {
            refreshHistoryPage();
        }
    }

    private void refreshHistoryPage() {
        if (historyDb == null || historyTimelineView == null || historyBehaviorList == null || historyAlertList == null) {
            return;
        }
        long dayStart = historySelectedDayStartMs == 0 ? startOfDay(System.currentTimeMillis()) : historySelectedDayStartMs;
        long dayEnd = dayStart + DAY_MS;
        long now = System.currentTimeMillis();
        List<LocalHistoryDb.BehaviorSegment> segments = new ArrayList<>(historyDb.getSegmentsForDay(dayStart, dayEnd));
        if (currentHistoryBehavior != null && currentHistoryStartMs < dayEnd && now > dayStart) {
            long s = Math.max(currentHistoryStartMs, dayStart);
            long e = Math.min(Math.max(currentHistoryLastMs, currentHistoryStartMs), dayEnd);
            if (e > s) {
                segments.add(new LocalHistoryDb.BehaviorSegment(s, e, currentHistoryBehavior, currentHistoryConfidence));
            }
        }
        List<LocalHistoryDb.AlertRecord> alerts = historyDb.getAlertsForDay(dayStart, dayEnd);

        double[] hours = historyHoursFor(segments, dayStart, dayEnd);
        double recorded = 0.0;
        for (double h : hours) {
            recorded += h;
        }
        int score = calculateHealthScore(hours, alerts);

        if (historyDateText != null) {
            historyDateText.setText(new SimpleDateFormat("yyyy年MM月dd日", Locale.CHINA).format(new Date(dayStart)));
        }
        if (historyScoreText != null) {
            historyScoreText.setText(String.valueOf(score));
        }
        if (historySummaryText != null) {
            historySummaryText.setText(String.format(Locale.CHINA,
                    "已记录 %.1fh，采食 %.1fh，反刍 %.1fh，告警 %d 条。",
                    recorded, hours[1], hours[2], alerts.size()));
        }
        if (historyPieView != null) {
            historyPieView.setHours(hours);
        }
        historyTimelineView.setSegments(dayStart, dayEnd, segments);
        renderHistoryBehaviorList(hours, recorded);
        renderHistoryAlertList(alerts);
    }

    private double[] historyHoursFor(List<LocalHistoryDb.BehaviorSegment> segments, long dayStart, long dayEnd) {
        double[] hours = new double[BEHAVIORS.length];
        for (LocalHistoryDb.BehaviorSegment segment : segments) {
            int idx = behaviorIndex(segment.behavior);
            if (idx < 0) {
                continue;
            }
            long s = Math.max(dayStart, segment.startMs);
            long e = Math.min(dayEnd, segment.endMs);
            if (e > s) {
                hours[idx] += (e - s) / 3600000.0;
            }
        }
        return hours;
    }

    private int calculateHealthScore(double[] hours, List<LocalHistoryDb.AlertRecord> alerts) {
        double recorded = 0.0;
        for (double h : hours) {
            recorded += h;
        }
        int score = 96;
        if (recorded < 12.0) {
            score -= Math.round((float) ((12.0 - recorded) * 2.0));
        }
        score -= alerts.size() * 6;
        if (hours[1] > 0.0 && hours[1] < 3.0) {
            score -= 6;
        }
        if (hours[2] > 0.0 && hours[2] < 3.0) {
            score -= 6;
        }
        return Math.max(45, Math.min(99, score));
    }

    private void renderHistoryBehaviorList(double[] hours, double recorded) {
        historyBehaviorList.removeAllViews();
        if (recorded <= 0.01) {
            historyBehaviorList.addView(emptyHistoryText("暂无本机行为历史。开始监听并接收到行为识别包后，这里会自动生成。"));
            return;
        }
        for (int i = 0; i < BEHAVIORS.length; i++) {
            double pct = recorded <= 0 ? 0.0 : hours[i] / recorded * 100.0;
            historyBehaviorList.addView(historyRow(
                    behaviorDisplayName(BEHAVIORS[i]),
                    String.format(Locale.US, "%.1fh · %.0f%%", hours[i], pct),
                    colorForBehavior(BEHAVIORS[i])));
        }
    }

    private void renderHistoryAlertList(List<LocalHistoryDb.AlertRecord> alerts) {
        historyAlertList.removeAllViews();
        if (alerts.isEmpty()) {
            historyAlertList.addView(emptyHistoryText("暂无告警。低电量、SD 未挂载等状态会在这里保留。"));
            return;
        }
        SimpleDateFormat fmt = new SimpleDateFormat("HH:mm", Locale.CHINA);
        for (LocalHistoryDb.AlertRecord alert : alerts) {
            historyAlertList.addView(historyRow(
                    alert.title,
                    fmt.format(new Date(alert.tsMs)) + " · " + alert.message,
                    colorForSeverity(alert.severity)));
        }
    }

    private View historyRow(String title, String detail, int color) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(10), dp(9), dp(10), dp(9));
        row.setBackground(round(SURFACE_ALT, 0, 12));

        TextView dot = label("●", 20, color, true);
        dot.setGravity(Gravity.CENTER);
        row.addView(dot, new LinearLayout.LayoutParams(dp(28), ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);
        TextView titleView = label(title, 13, TEXT, true);
        TextView detailView = label(detail, 11, MUTED, false);
        detailView.setPadding(0, dp(2), 0, 0);
        copy.addView(titleView);
        copy.addView(detailView);
        row.addView(copy, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, 0, 0, dp(8));
        row.setLayoutParams(lp);
        return row;
    }

    private TextView emptyHistoryText(String text) {
        TextView tv = label(text, 12, MUTED, false);
        tv.setPadding(dp(10), dp(10), dp(10), dp(10));
        tv.setBackground(round(SURFACE_ALT, 0, 12));
        return tv;
    }

    private int colorForBehavior(String behavior) {
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

    private int colorForSeverity(String severity) {
        if ("danger".equals(severity)) {
            return Color.rgb(219, 79, 74);
        }
        if ("warn".equals(severity)) {
            return GOLD;
        }
        return GREEN;
    }

    private String behaviorDisplayName(String behavior) {
        if ("Displacement".equals(behavior)) {
            return "位移";
        }
        if ("Grazing".equals(behavior)) {
            return "采食";
        }
        if ("Ruminating_Chewing".equals(behavior)) {
            return "反刍";
        }
        if ("Other".equals(behavior)) {
            return "其他";
        }
        if ("Resting".equals(behavior)) {
            return "休息";
        }
        return behavior;
    }

    private long startOfDay(long timeMs) {
        Calendar c = Calendar.getInstance(Locale.CHINA);
        c.setTimeInMillis(timeMs);
        c.set(Calendar.HOUR_OF_DAY, 0);
        c.set(Calendar.MINUTE, 0);
        c.set(Calendar.SECOND, 0);
        c.set(Calendar.MILLISECOND, 0);
        return c.getTimeInMillis();
    }

    private void showAdviceDialog() {
        double recorded = 0.0;
        for (double sec : behaviorSeconds) {
            recorded += sec / 3600.0;
        }
        if (recorded < 12.0) {
            new AlertDialog.Builder(this)
                    .setTitle("辅助建议")
                    .setMessage("当天有效记录满 12h 后，系统才显示采食不足、反刍不足等辅助建议。")
                    .setPositiveButton("知道了", null)
                    .show();
            return;
        }

        String msg =
                "当天采食不足：采食累计低于建议观察阈值，需结合现场饲喂情况进一步确认。\n\n" +
                "反刍时长偏低：建议复核佩戴位置和个体状态。\n\n" +
                "高活动低采食倾向：可能存在行为结构失衡，需继续观察。";
        new AlertDialog.Builder(this)
                .setTitle("辅助建议")
                .setMessage(msg)
                .setPositiveButton("关闭", null)
                .show();
    }

    private void toggleAccelWave() {
        accelWaveEnabled = !accelWaveEnabled;
        sendCommand(accelWaveEnabled ? "ACCELON" : "ACCELOFF");
        updateAccelWaveButton();
        if (!accelWaveEnabled) {
            graphView.clear();
            if (accelRateText != null) {
                accelRateText.setText("波形关闭");
            }
            showTransientHint("已关闭波形");
        } else if (accelRateText != null) {
            accelRateText.setText("等待波形");
            showTransientHint("已开启波形");
        }
    }

    private void updateAccelWaveButton() {
        if (accelWaveButton == null) {
            return;
        }
        accelWaveButton.setText(accelWaveEnabled ? "关闭波形" : "打开波形");
        accelWaveButton.setTextColor(accelWaveEnabled ? TEXT : Color.WHITE);
        accelWaveButton.setBackground(round(accelWaveEnabled ? Color.rgb(235, 229, 217) : GREEN, 0, 10));
    }

    private void refreshStatus() {
        sendCommand("SYNC:" + System.currentTimeMillis());
        showTransientHint("已刷新状态");
    }

    private void startCollection() {
        sendCommand("SYNC:" + System.currentTimeMillis());
        ui.postDelayed(new Runnable() {
            @Override public void run() { sendCommand("START"); }
        }, 150);
        showTransientHint("已开启采集");
    }

    private void pauseCollection() {
        sendCommand("PAUSE");
        closeOpenHistorySegment(System.currentTimeMillis());
        accelWaveEnabled = false;
        updateAccelWaveButton();
        graphView.clear();
        if (accelRateText != null) {
            accelRateText.setText("波形关闭");
        }
        showTransientHint("已停止采集");
    }

    private void confirmReset() {
        new AlertDialog.Builder(this)
                .setTitle("重置设备")
                .setMessage("确定向 ESP32 发送 RESET 命令吗？")
                .setPositiveButton("确定", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        sendCommand("RESET");
                        showTransientHint("已发送重置设备");
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void sendCommand(final String cmd) {
        final String targetAtSend = targetIpEdit.getText().toString().trim();
        final String knownEspIp = espIp == null ? "" : espIp.trim();
        new Thread(new Runnable() {
            @Override
            public void run() {
                DatagramSocket socket = null;
                try {
                    byte[] payload = cmd.getBytes(StandardCharsets.UTF_8);
                    socket = new DatagramSocket();
                    socket.setBroadcast(true);

                    Set<String> targets = new LinkedHashSet<>();
                    String target = targetAtSend.trim();
                    if (target.length() > 0) {
                        targets.add(target);
                    }
                    if (knownEspIp.length() > 0) {
                        targets.add(knownEspIp);
                    }
                    String wifiBroadcast = getWifiBroadcastIp();
                    if (wifiBroadcast != null && wifiBroadcast.length() > 0) {
                        targets.add(wifiBroadcast);
                    }
                    addInterfaceBroadcastIps(targets);
                    addInterfaceProbeIps(targets);
                    targets.add(BROADCAST_IP);
                    targets.add("192.168.43.255");
                    targets.add("192.168.137.255");
                    targets.add("172.20.10.15");
                    targets.add("192.168.4.255");
                    targets.add(SOFTAP_IP);

                    int sent = 0;
                    Exception lastError = null;
                    for (String host : targets) {
                        try {
                            sendTo(socket, payload, host);
                            sent++;
                        } catch (Exception sendError) {
                            lastError = sendError;
                        }
                    }
                    if (sent == 0 && lastError != null) {
                        throw lastError;
                    }
                    log("发送命令: " + cmd + " (" + sent + " 个目标)");
                } catch (Exception e) {
                    log("发送失败: " + e.getMessage());
                } finally {
                    closeSocket(socket);
                }
            }
        }, "send-command").start();
    }

    private void sendTo(DatagramSocket socket, byte[] payload, String host) throws Exception {
        InetAddress address = InetAddress.getByName(host);
        DatagramPacket packet = new DatagramPacket(payload, payload.length, address, CONTROL_PORT);
        socket.send(packet);
    }

    private String getWifiBroadcastIp() {
        try {
            WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wifi == null) {
                return null;
            }
            DhcpInfo dhcp = wifi.getDhcpInfo();
            if (dhcp == null || dhcp.ipAddress == 0 || dhcp.netmask == 0) {
                return null;
            }
            int broadcast = (dhcp.ipAddress & dhcp.netmask) | ~dhcp.netmask;
            byte[] quads = new byte[4];
            for (int i = 0; i < 4; i++) {
                quads[i] = (byte) ((broadcast >> (i * 8)) & 0xFF);
            }
            return InetAddress.getByAddress(quads).getHostAddress();
        } catch (Exception ignored) {
            return null;
        }
    }

    private void addInterfaceBroadcastIps(Set<String> targets) {
        try {
            Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
            while (interfaces.hasMoreElements()) {
                NetworkInterface nif = interfaces.nextElement();
                if (!nif.isUp() || nif.isLoopback()) {
                    continue;
                }
                for (InterfaceAddress ifaceAddr : nif.getInterfaceAddresses()) {
                    InetAddress addr = ifaceAddr.getAddress();
                    if (!(addr instanceof Inet4Address)) {
                        continue;
                    }
                    InetAddress broadcast = ifaceAddr.getBroadcast();
                    if (broadcast != null) {
                        targets.add(broadcast.getHostAddress());
                        continue;
                    }
                    String calculated = calculateBroadcastIp(addr, ifaceAddr.getNetworkPrefixLength());
                    if (calculated != null) {
                        targets.add(calculated);
                    }
                }
            }
        } catch (Exception ignored) {
        }
    }

    private void addInterfaceProbeIps(Set<String> targets) {
        try {
            Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
            while (interfaces.hasMoreElements()) {
                NetworkInterface nif = interfaces.nextElement();
                if (!nif.isUp() || nif.isLoopback()) {
                    continue;
                }
                for (InterfaceAddress ifaceAddr : nif.getInterfaceAddresses()) {
                    InetAddress addr = ifaceAddr.getAddress();
                    if (!(addr instanceof Inet4Address)) {
                        continue;
                    }
                    short prefixLength = ifaceAddr.getNetworkPrefixLength();
                    if (prefixLength < 24 || prefixLength > 30) {
                        continue;
                    }

                    long ip = ipv4ToLong(addr.getAddress());
                    long mask = (0xFFFFFFFFL << (32 - prefixLength)) & 0xFFFFFFFFL;
                    long network = ip & mask;
                    long broadcast = network | (~mask & 0xFFFFFFFFL);
                    long hostCount = broadcast - network - 1;
                    if (hostCount < 1 || hostCount > 254) {
                        continue;
                    }

                    for (long host = network + 1; host < broadcast; host++) {
                        if (host != ip) {
                            targets.add(longToIpv4(host));
                        }
                    }
                }
            }
        } catch (Exception ignored) {
        }
    }

    private String calculateBroadcastIp(InetAddress address, short prefixLength) {
        byte[] raw = address.getAddress();
        if (raw.length != 4 || prefixLength < 0 || prefixLength > 32) {
            return null;
        }
        int ip = ((raw[0] & 0xFF) << 24)
                | ((raw[1] & 0xFF) << 16)
                | ((raw[2] & 0xFF) << 8)
                | (raw[3] & 0xFF);
        int mask = prefixLength == 0 ? 0 : (int) (0xFFFFFFFFL << (32 - prefixLength));
        int broadcast = ip | ~mask;
        return String.format(Locale.US, "%d.%d.%d.%d",
                (broadcast >>> 24) & 0xFF,
                (broadcast >>> 16) & 0xFF,
                (broadcast >>> 8) & 0xFF,
                broadcast & 0xFF);
    }

    private long ipv4ToLong(byte[] raw) {
        return ((long) (raw[0] & 0xFF) << 24)
                | ((long) (raw[1] & 0xFF) << 16)
                | ((long) (raw[2] & 0xFF) << 8)
                | (raw[3] & 0xFF);
    }

    private String longToIpv4(long ip) {
        return String.format(Locale.US, "%d.%d.%d.%d",
                (ip >>> 24) & 0xFF,
                (ip >>> 16) & 0xFF,
                (ip >>> 8) & 0xFF,
                ip & 0xFF);
    }

    private void acquireMulticastLock() {
        try {
            WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wifi != null && multicastLock == null) {
                multicastLock = wifi.createMulticastLock("LXSPI-UDP");
                multicastLock.setReferenceCounted(false);
                multicastLock.acquire();
            }
        } catch (Exception ignored) {
        }
    }

    private void releaseMulticastLock() {
        try {
            if (multicastLock != null && multicastLock.isHeld()) {
                multicastLock.release();
            }
        } catch (Exception ignored) {
        } finally {
            multicastLock = null;
        }
    }

    private void closeSocket(DatagramSocket socket) {
        if (socket != null) {
            try {
                socket.close();
            } catch (Exception ignored) {
            }
        }
    }

    private void log(final String msg) {
        ui.post(new Runnable() {
            @Override
            public void run() {
                if (logText == null) {
                    return;
                }
                String ts = new SimpleDateFormat("HH:mm:ss", Locale.CHINA).format(new Date());
                String old = logText.getText().toString();
                if ("--".equals(old)) {
                    old = "";
                }
                logText.setText("[" + ts + "] " + msg + "\n" + old);
            }
        });
    }

    private void showTransientHint(final String message) {
        if (message == null || message.trim().isEmpty()) {
            return;
        }
        ui.post(new Runnable() {
            @Override
            public void run() {
                try {
                    Toast toast = Toast.makeText(MainActivity.this, message, Toast.LENGTH_SHORT);
                    toast.setGravity(Gravity.CENTER, 0, 0);
                    toast.show();
                } catch (Exception ignored) {
                }
            }
        });
    }

    private LinearLayout card(boolean hero) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(13), dp(14), dp(13));
        if (hero) {
            GradientDrawable gd = new GradientDrawable(
                    GradientDrawable.Orientation.TL_BR,
                    new int[]{GREEN, GREEN_DARK, Color.rgb(181, 169, 104)});
            gd.setCornerRadius(dp(14));
            card.setBackground(gd);
        } else {
            card.setBackground(round(SURFACE, BORDER, 14));
        }
        return card;
    }

    private TextView sectionTitle(String text) {
        TextView tv = label(text, 15, TEXT, true);
        tv.setPadding(0, 0, 0, dp(9));
        return tv;
    }

    private TextView miniValue(String label, String value) {
        TextView tv = label(twoLine(label, value), 13, TEXT, false);
        tv.setPadding(dp(12), dp(10), dp(12), dp(10));
        tv.setBackground(round(SURFACE_ALT, 0, 12));
        return tv;
    }

    private TextView metricCard(String title, String value, String footnote) {
        TextView tv = label(title + "\n" + value + "\n" + footnote, 13, TEXT, false);
        tv.setPadding(dp(12), dp(10), dp(12), dp(10));
        tv.setMinHeight(dp(92));
        tv.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
        tv.setBackground(round(SURFACE, BORDER, 12));
        return tv;
    }

    private Button metricButton(String title, String value, String footnote, View.OnClickListener listener) {
        Button b = new Button(this);
        b.setText(title + "\n" + value + "\n" + footnote);
        b.setTextSize(13);
        b.setTextColor(TEXT);
        b.setAllCaps(false);
        b.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
        b.setPadding(dp(12), dp(10), dp(12), dp(10));
        b.setMinHeight(dp(92));
        b.setBackground(round(SURFACE, BORDER, 12));
        b.setOnClickListener(listener);
        return b;
    }

    private void showGoatDialog() {
        new AlertDialog.Builder(this)
                .setTitle("羊只编号")
                .setMessage("编号：0001")
                .setPositiveButton("关闭", null)
                .show();
    }

    private TextView pill(String text, int bg, int color) {
        TextView tv = label(text, 13, color, true);
        tv.setPadding(dp(12), dp(6), dp(12), dp(6));
        tv.setGravity(Gravity.CENTER);
        tv.setBackground(round(bg, 0, 100));
        return tv;
    }

    private Button commandButton(String text, boolean primary, View.OnClickListener listener) {
        Button b = new Button(this);
        b.setText(text);
        b.setTextSize(14);
        b.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        b.setAllCaps(false);
        b.setTextColor(primary ? Color.WHITE : TEXT);
        b.setMinHeight(dp(48));
        b.setPadding(dp(4), 0, dp(4), 0);
        b.setBackground(round(primary ? GREEN : Color.rgb(235, 229, 217), 0, 10));
        b.setOnClickListener(listener);
        return b;
    }

    private Button navButton(String text, boolean active, View.OnClickListener listener) {
        Button b = new Button(this);
        b.setText(text);
        b.setTextSize(13);
        b.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        b.setAllCaps(false);
        b.setPadding(dp(10), dp(8), dp(10), dp(8));
        b.setOnClickListener(listener);
        styleNavButton(b, active);
        return b;
    }

    private void styleNavButton(Button b, boolean active) {
        b.setTextColor(active ? GREEN_DARK : MUTED);
        b.setBackground(round(active ? Color.rgb(232, 222, 202) : SURFACE, 0, 8));
    }

    private TextView label(String text, int sp, int color, boolean bold) {
        TextView tv = new TextView(this);
        tv.setText(text);
        tv.setTextSize(sp);
        tv.setTextColor(color);
        tv.setIncludeFontPadding(true);
        if (bold) {
            tv.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        }
        return tv;
    }

    private LinearLayout row(int gapDp) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(0, dp(4), 0, dp(4));
        return row;
    }

    private LinearLayout.LayoutParams weight() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        lp.setMargins(dp(4), 0, dp(4), 0);
        return lp;
    }

    private LinearLayout.LayoutParams fullWidthWithBottom(int bottomDp) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, 0, 0, dp(bottomDp));
        return lp;
    }

    private GradientDrawable round(int color, int strokeColor, int radiusDp) {
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(color);
        bg.setCornerRadius(dp(radiusDp));
        if (strokeColor != 0) {
            bg.setStroke(dp(1), strokeColor);
        }
        return bg;
    }

    private String twoLine(String label, String value) {
        return label + "\n" + value;
    }

    private String blankDash(String s) {
        return s == null || s.length() == 0 ? "--" : s;
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private int topSafePadding() {
        return Math.max(dp(34), statusBarHeight() + dp(8));
    }

    private int statusBarHeight() {
        int resourceId = getResources().getIdentifier("status_bar_height", "dimen", "android");
        if (resourceId > 0) {
            return getResources().getDimensionPixelSize(resourceId);
        }
        return 0;
    }
}
