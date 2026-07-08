"""
verify_espdl.py
Quick local checks for the exported ONNX model and espdl artifact metadata.
"""

import hashlib
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

EXPORT_DIR = os.path.join(HERE, "export")
ONNX_PATH = os.path.join(EXPORT_DIR, "goat_cnn_5cls.onnx")

LABEL_NAMES = ["Displacement", "Grazing", "Ruminating_Chewing", "Other", "Resting"]
NORM_STD = np.array([0.110051081, 0.159435093, 0.168068826, 0.099703118], dtype=np.float32)
WINDOW_SIZE = 120


def parse_test_windows_h():
    h_path = os.path.join(HERE, "..", "SPI", "main", "model", "test_windows.h")
    with open(h_path, "r", encoding="utf-8") as f:
        content = f.read()

    windows = []
    labels = []

    for i in range(5):
        pattern = rf"TEST_WINDOW_{i}\[360\]\s*=\s*\{{([^}}]+)\}}"
        match = re.search(pattern, content)
        if match:
            vals_str = match.group(1)
            vals = [float(v.strip().rstrip("f")) for v in vals_str.split(",") if v.strip()]
            assert len(vals) == 360, f"Window {i} has {len(vals)} values, expected 360"
            windows.append(np.array(vals, dtype=np.float32).reshape(WINDOW_SIZE, 3))

    label_pattern = r'TEST_LABELS\[.*?\]\s*=\s*\{([^}]+)\}'
    match = re.search(label_pattern, content)
    if match:
        for item in match.group(1).split(","):
            label = item.strip().strip('"').strip()
            if label:
                labels.append(label)

    if not labels:
        labels = LABEL_NAMES.copy()

    return windows, labels


def normalize_window_like_firmware(window_xyz, norm_std):
    mag = np.sqrt(np.sum(window_xyz ** 2, axis=1, keepdims=True))
    x4 = np.concatenate([window_xyz, mag], axis=1)  # (W, 4)
    x4 = x4 - x4.mean(axis=0, keepdims=True)
    std = norm_std.copy()
    std[std == 0] = 1.0
    return (x4 / std).astype(np.float32)


def normalize_for_onnx(window_xyz, norm_std):
    x4_nwc = normalize_window_like_firmware(window_xyz, norm_std)  # (W, 4)
    return x4_nwc.T[:, :, np.newaxis][np.newaxis, :, :, :]  # (1, 4, W, 1)


def softmax(x):
    e = np.exp(x - np.max(x))
    return e / e.sum()


def main():
    windows, labels = parse_test_windows_h()
    print(f"Parsed {len(windows)} test windows, labels: {labels}\n")

    print("=" * 60)
    print("Part 1: Float ONNX inference")
    print("=" * 60)

    try:
        import onnxruntime as ort

        sess = ort.InferenceSession(ONNX_PATH)
        has_ort = True
    except ImportError:
        print("[SKIP] onnxruntime not installed")
        has_ort = False

    if has_ort:
        for i, (win, label) in enumerate(zip(windows, labels)):
            x_nchw = normalize_for_onnx(win, NORM_STD)
            logits = sess.run(None, {"input": x_nchw})[0][0]
            probs = softmax(logits)
            pred = LABEL_NAMES[np.argmax(probs)]
            print(
                f"  Window[{i}] expect={label:20s} -> pred={pred:20s} "
                f"conf={probs.max():.4f}  scores={[f'{p:.4f}' for p in probs]}"
            )
            print(f"           logits={[f'{v:.4f}' for v in logits]}")

    print()
    print("=" * 60)
    print("Part 2: Simulated INT8 quantization")
    print("=" * 60)

    input_exp = -5
    output_exp = -3
    input_scale = 2.0 ** input_exp
    output_scale = 2.0 ** output_exp

    for i, (win, label) in enumerate(zip(windows, labels)):
        x4_nwc = normalize_window_like_firmware(win, NORM_STD)
        print(f"\n  Window[{i}] expect={label}")
        print(f"    float range: min={x4_nwc.min():.4f} max={x4_nwc.max():.4f} mean={x4_nwc.mean():.4f}")

        inv_scale = 1.0 / input_scale
        x_q = np.clip(np.round(x4_nwc * inv_scale), -128, 127).astype(np.int8)
        print(f"    int8 range: min={x_q.min()} max={x_q.max()}")
        print(f"    first 8 int8 (flat): {x_q.flatten()[:8].tolist()}")

        x_unclipped = np.round(x4_nwc * inv_scale)
        n_clipped = np.sum((x_unclipped < -128) | (x_unclipped > 127))
        n_total = x_unclipped.size
        print(f"    clipped: {n_clipped}/{n_total} ({100 * n_clipped / n_total:.1f}%)")

        if has_ort:
            x_deq_nchw = x_q.astype(np.float32).T[:, :, np.newaxis][np.newaxis, :, :, :] * input_scale
            logits_q = sess.run(None, {"input": x_deq_nchw})[0][0]
            probs_q = softmax(logits_q)
            pred_q = LABEL_NAMES[np.argmax(probs_q)]
            print(
                f"    quantize->dequantize->ONNX: pred={pred_q:20s} "
                f"conf={probs_q.max():.4f}  scores={[f'{p:.4f}' for p in probs_q]}"
            )

    print()
    print("=" * 60)
    print("Part 3: Zero input")
    print("=" * 60)

    if has_ort:
        zero_nchw = np.zeros((1, 4, WINDOW_SIZE, 1), dtype=np.float32)
        logits_z = sess.run(None, {"input": zero_nchw})[0][0]
        probs_z = softmax(logits_z)
        pred_z = LABEL_NAMES[np.argmax(probs_z)]
        print(f"  Zero input -> pred={pred_z} conf={probs_z.max():.4f}")
        print(f"  logits={[f'{v:.4f}' for v in logits_z]}")
        print(f"  scores={[f'{p:.4f}' for p in probs_z]}")
        zero_q_int8 = np.clip(np.round(logits_z / output_scale), -128, 127).astype(np.int8)
        print(f"  output int8 (at exp=-3): {zero_q_int8.tolist()}")

    print()
    print("=" * 60)
    print("Part 4: espdl artifact comparison")
    print("=" * 60)

    espdl_deploy = os.path.join(HERE, "..", "SPI", "main", "model", "goat_cnn_5cls_s3_int8.espdl")
    espdl_export = os.path.join(EXPORT_DIR, "goat_cnn_5cls_s3_int8.espdl")
    espdl_test = os.path.join(EXPORT_DIR, "goat_cnn_5cls_s3_int8_test.espdl")

    for path, name in [(espdl_deploy, "deployed"), (espdl_export, "export"), (espdl_test, "test")]:
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            print(f"  {name:10s}: {len(data):>8d} bytes  md5={hashlib.md5(data).hexdigest()}  path={path}")
        else:
            print(f"  {name:10s}: NOT FOUND  path={path}")

    if os.path.exists(espdl_deploy) and os.path.exists(espdl_export):
        with open(espdl_deploy, "rb") as f1, open(espdl_export, "rb") as f2:
            if f1.read() == f2.read():
                print("\n  deployed == export: IDENTICAL")
            else:
                print("\n  deployed != export: DIFFERENT")

    print()
    print("On-device correctness should now be checked primarily with model->test(),")
    print("because export_test_values=True embeds reference tensors inside the espdl model.")


if __name__ == "__main__":
    main()
