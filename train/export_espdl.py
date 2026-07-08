"""
export_espdl.py
Export the best trained GoatCNN checkpoint to ONNX, generate calibration data,
and refresh the firmware-side normalization header.
"""

import glob
import json
import os
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from train import GoatCNN, INPUT_CHANNELS, LABEL_NAMES, NUM_CLASSES, PerWindowNormalizer

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_ROOT = os.path.join(HERE, "results")
DATA_ROOT = os.path.join(HERE, "data")
EXPORT_DIR = os.path.join(HERE, "export")
ESP_MODEL_DIR = os.path.join(HERE, "..", "SPI", "main", "model")

WINDOW_SIZE = 120


def load_best_model():
    info_path = os.path.join(RESULTS_ROOT, "best_fold_info.json")
    with open(info_path, "r", encoding="utf-8") as f:
        info = json.load(f)

    ckpt_path = info["ckpt_path"]
    norm_path = info["norm_path"]
    print(f"Best fold: cv{info['best_cv_idx']} (macro-F1={info['best_test_macro_f1']:.4f})")
    print(f"Checkpoint: {ckpt_path}")

    model = GoatCNN(num_classes=NUM_CLASSES, in_channels=INPUT_CHANNELS, dropout=0.0)
    ckpt = torch.load(ckpt_path, map_location="cpu")
    try:
        model.load_state_dict(ckpt["model_state"])
    except RuntimeError as exc:
        raise RuntimeError(
            "The saved checkpoint is from the old Conv1d model and is not compatible with "
            "the new deployable Conv2d architecture. Please retrain first with:\n"
            "  python train.py"
        ) from exc

    model.eval()
    num_params = sum(p.numel() for p in model.parameters())
    print(f"Parameters: {num_params:,}")

    norm = np.load(norm_path)
    std4 = norm["std"]
    print(f"Norm std (4ch): {std4}")

    return model, std4, info


def export_onnx(model, onnx_path):
    dummy = torch.randn(1, INPUT_CHANNELS, WINDOW_SIZE, 1)
    torch.onnx.export(
        model,
        dummy,
        onnx_path,
        input_names=["input"],
        output_names=["output"],
        opset_version=13,
        dynamic_axes=None,
    )
    print(f"ONNX exported: {onnx_path}  input=[1,{INPUT_CHANNELS},{WINDOW_SIZE},1]")

    try:
        import onnx

        onnx.checker.check_model(onnx.load(onnx_path))
        print("  ONNX validation passed")
    except ImportError:
        print("  [Hint] Install onnx if you want local ONNX validation.")


def generate_calibration_data(data_root, std4, num_samples=200):
    x_files = sorted(glob.glob(os.path.join(data_root, "animal_*", "X_*.npy")))
    if not x_files:
        raise FileNotFoundError(f"No calibration data found under {data_root}")

    all_chunks = []
    for xf in x_files:
        X = np.load(xf, mmap_mode="r")
        all_chunks.append(X)

    total = sum(len(chunk) for chunk in all_chunks)
    rng = np.random.default_rng(42)
    indices = rng.choice(total, min(num_samples, total), replace=False)
    indices.sort()

    normalizer = PerWindowNormalizer(std4)
    samples = []
    cum = 0
    idx_ptr = 0
    for chunk in all_chunks:
        n = len(chunk)
        while idx_ptr < len(indices) and indices[idx_ptr] < cum + n:
            local = indices[idx_ptr] - cum
            x3 = np.array(chunk[local], dtype=np.float32)
            x4 = normalizer(x3)  # (W, 4)
            samples.append(x4.T[:, :, np.newaxis])  # (4, W, 1)
            idx_ptr += 1
        cum += n

    calib = np.array(samples, dtype=np.float32)  # (N, 4, W, 1)
    print(f"Calibration data shape: {calib.shape}")
    return calib


def generate_model_norm_h(std4, output_path):
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("#pragma once\n\n")
        f.write(f"#define MODEL_NUM_CLASSES {NUM_CLASSES}\n")
        f.write(f"#define MODEL_INPUT_CHANNELS {INPUT_CHANNELS}\n")
        f.write(f"#define MODEL_WINDOW_SIZE {WINDOW_SIZE}\n\n")
        f.write("/* per-window centering + global std normalization (4 channels: X, Y, Z, |a|) */\n")
        f.write(
            f"static const float NORM_STD[{INPUT_CHANNELS}] = "
            f"{{{std4[0]:.9f}f, {std4[1]:.9f}f, {std4[2]:.9f}f, {std4[3]:.9f}f}};\n\n"
        )
        f.write(f"static const char ACT_LABELS[MODEL_NUM_CLASSES][24] = {{\n")
        for i, lab in enumerate(LABEL_NAMES):
            comma = "," if i < len(LABEL_NAMES) - 1 else ""
            f.write(f'    "{lab}"{comma}\n')
        f.write("};\n")
    print(f"model_norm.h: {output_path}")


def main():
    os.makedirs(EXPORT_DIR, exist_ok=True)

    model, std4, info = load_best_model()

    onnx_path = os.path.join(EXPORT_DIR, "goat_cnn_5cls.onnx")
    export_onnx(model, onnx_path)

    calib = generate_calibration_data(DATA_ROOT, std4)
    calib_path = os.path.join(EXPORT_DIR, "calibration_data.npy")
    np.save(calib_path, calib)

    h_export = os.path.join(EXPORT_DIR, "model_norm.h")
    generate_model_norm_h(std4, h_export)

    h_esp = os.path.abspath(os.path.join(ESP_MODEL_DIR, "model_norm.h"))
    generate_model_norm_h(std4, h_esp)
    print(f"Firmware header refreshed: {h_esp}")

    meta = {
        "input_shape_nchw": [1, INPUT_CHANNELS, WINDOW_SIZE, 1],
        "labels": LABEL_NAMES,
        "norm_std": std4.tolist(),
        "model_class": "GoatCNN",
        "num_params": sum(p.numel() for p in model.parameters()),
        "source_fold": f"cv{info['best_cv_idx']}",
        "best_test_macro_f1": info["best_test_macro_f1"],
        "train_data_hz": 24.4,
        "window_size": WINDOW_SIZE,
        "window_seconds": WINDOW_SIZE / 24.4,
    }
    with open(os.path.join(EXPORT_DIR, "export_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    espdl_path = os.path.join(EXPORT_DIR, "goat_cnn_5cls_s3_int8.espdl")
    espdl_deploy = os.path.abspath(os.path.join(ESP_MODEL_DIR, "goat_cnn_5cls_s3_int8.espdl"))
    print(f"\n{'=' * 60}")
    print("INT8 quantization command:")
    print("  python quantize.py")
    print("Artifacts to deploy:")
    print(f"  {espdl_path}")
    print(f"  -> {espdl_deploy}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
