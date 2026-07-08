"""
quantize.py
Run INT8 post-training quantization on the GoatCNN ONNX model and produce a
.espdl flatbuffer ready for ESP32-S3 deployment.

Pipeline:
    preprocess.py  -> data/animal_*/X_*.npy
    train.py       -> results/cv*/best.pt
    export_espdl.py-> export/goat_cnn_5cls.onnx
                      export/calibration_data.npy   (200 normalized windows, NCHW)
                      export/export_meta.json
    quantize.py    -> export/goat_cnn_5cls_s3_int8.espdl   (deployable)
                      export/goat_cnn_5cls_s3_int8.json    (PPQ debug dump)
                      export/goat_cnn_5cls_s3_int8.info    (human-readable graph)
                      SPI/main/model/goat_cnn_5cls_s3_int8.espdl  (flashed by firmware)

The firmware (goat_behavior_model.cpp) reads input/output exponents and the
4-D layout from the .espdl at runtime, so this script does not force specific
exponents -- they fall out of the calibration data. The previously deployed
model had input exp=-5 and output exp=-3 (see verify_espdl.py).
"""

import json
import os
import shutil
import sys

import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset

from esp_ppq.api import espdl_quantize_onnx

HERE = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(HERE, "export")
ESP_MODEL_DIR = os.path.join(HERE, "..", "SPI", "main", "model")

ONNX_PATH = os.path.join(EXPORT_DIR, "goat_cnn_5cls.onnx")
CALIB_PATH = os.path.join(EXPORT_DIR, "calibration_data.npy")
META_PATH = os.path.join(EXPORT_DIR, "export_meta.json")

ESPDL_PATH = os.path.join(EXPORT_DIR, "goat_cnn_5cls_s3_int8.espdl")
ESPDL_DEPLOY = os.path.abspath(os.path.join(ESP_MODEL_DIR, "goat_cnn_5cls_s3_int8.espdl"))

TARGET = "esp32s3"
NUM_OF_BITS = 8
BATCH_SIZE = 32
DEVICE = "cpu"


def require_artifact(path, hint):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing {path}\n  hint: {hint}")
    if os.path.getsize(path) == 0:
        raise RuntimeError(f"Empty file {path} (0 bytes). {hint}")
    with open(path, "rb") as f:
        head = f.read(64)
    if head and not any(head):
        raise RuntimeError(
            f"{path} looks zero-filled (first 64 bytes all 0x00).\n  hint: {hint}"
        )


def load_calibration(path, expected_shape):
    arr = np.load(path).astype(np.float32)
    if arr.ndim != 4:
        raise ValueError(f"calibration_data must be 4-D NCHW, got shape {arr.shape}")
    if list(arr.shape[1:]) != list(expected_shape[1:]):
        raise ValueError(
            f"calibration shape mismatch: got {arr.shape}, expected (N, *{expected_shape[1:]})"
        )
    print(f"Calibration tensor: shape={arr.shape}  dtype={arr.dtype}  "
          f"min={arr.min():.4f}  max={arr.max():.4f}  mean={arr.mean():.4f}")
    return arr


def collate_first(batch):
    return torch.stack([b[0] for b in batch], dim=0)


def main():
    require_artifact(ONNX_PATH, "Run export_espdl.py first to produce the float ONNX model.")
    require_artifact(CALIB_PATH, "Run export_espdl.py first to produce calibration_data.npy.")
    require_artifact(META_PATH, "export_meta.json missing -- rerun export_espdl.py.")

    with open(META_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)
    input_shape = meta["input_shape_nchw"]
    print(f"export_meta.json: labels={meta['labels']}  input_shape={input_shape}  "
          f"best_macro_f1={meta.get('best_test_macro_f1', 'n/a')}")

    calib_arr = load_calibration(CALIB_PATH, input_shape)
    calib_loader = DataLoader(
        TensorDataset(torch.from_numpy(calib_arr)),
        batch_size=BATCH_SIZE,
        shuffle=False,
        collate_fn=collate_first,
    )
    calib_steps = max(1, (len(calib_arr) + BATCH_SIZE - 1) // BATCH_SIZE)
    print(f"Calibration: {len(calib_arr)} windows, batch_size={BATCH_SIZE}, steps={calib_steps}")

    os.makedirs(EXPORT_DIR, exist_ok=True)

    print(f"\nRunning esp_ppq INT{NUM_OF_BITS} PTQ for target={TARGET} ...")
    quant_graph = espdl_quantize_onnx(
        onnx_import_file=ONNX_PATH,
        espdl_export_file=ESPDL_PATH,
        calib_dataloader=calib_loader,
        calib_steps=calib_steps,
        input_shape=input_shape,
        target=TARGET,
        num_of_bits=NUM_OF_BITS,
        device=DEVICE,
        error_report=True,
        skip_export=False,
        export_test_values=True,
        verbose=1,
    )
    print("PTQ complete.")

    if not os.path.exists(ESPDL_PATH) or os.path.getsize(ESPDL_PATH) == 0:
        raise RuntimeError(f"espdl export produced no/empty file at {ESPDL_PATH}")

    os.makedirs(os.path.dirname(ESPDL_DEPLOY), exist_ok=True)
    shutil.copyfile(ESPDL_PATH, ESPDL_DEPLOY)

    deployed_size = os.path.getsize(ESPDL_DEPLOY)
    print()
    print("=" * 60)
    print(f"  exported : {ESPDL_PATH}  ({os.path.getsize(ESPDL_PATH)} bytes)")
    print(f"  deployed : {ESPDL_DEPLOY}  ({deployed_size} bytes)")
    print(f"  graph    : {ESPDL_PATH.replace('.espdl', '.info')}")
    print(f"  metadata : {ESPDL_PATH.replace('.espdl', '.json')}")
    print("=" * 60)
    print("Next step: rebuild firmware (idf.py build flash) so the new model is embedded.")
    return quant_graph


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        print(f"[ABORT] {exc}", file=sys.stderr)
        sys.exit(2)
    except RuntimeError as exc:
        print(f"[ABORT] {exc}", file=sys.stderr)
        sys.exit(3)
