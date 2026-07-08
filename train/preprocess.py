"""
preprocess.py
将 Final-DataPaper-Horn.txt (59 只动物，~24Hz 加速度 + 行为标签)
切分为滑动窗口 .npy 文件，用于训练 5 类行为分类 1D-CNN。

数据集信息:
  列: Time, Animal_id, Behaviour, Behaviour_num, X, Y, Z, Sequence_num
  采样率: ~24.4 Hz (41ms 间隔)
  动物数: 59 只
  类别: 1=Displacement, 2=Grazing, 3=Ruminating_Chewing, 4=Other, 5=Resting

用法:
    python preprocess.py

输出目录: data/
"""

import os
import json
import numpy as np
import pandas as pd
from collections import defaultdict
from tqdm import tqdm

# ====== 配置 ======
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
RAW_PATH = os.path.join(REPO_ROOT, "Final-DataPaper-Horn.txt")
OUT_DIR = os.path.join(HERE, "data")

# 原始 Behaviour_num 是 1-5，映射为 0-4 (为了 PyTorch CE Loss)
# 1=Displacement -> 0, 2=Grazing -> 1, 3=Ruminating_Chewing -> 2, 4=Other -> 3, 5=Resting -> 4
LABEL_NAMES = ["Displacement", "Grazing", "Ruminating_Chewing", "Other", "Resting"]
NUM_CLASSES = len(LABEL_NAMES)
NUM_TO_IDX = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}

# 采样率 ~24.4Hz，窗口用 120 步 ≈ 5 秒
WINDOW_SIZE = 120
STRIDE_DEFAULT = 60   # 50% 重叠
STRIDE_MINORITY = 24  # Displacement 类加密
MINORITY_CLASS = 0    # Displacement 是最少的类
MINORITY_THRESHOLD = 0.6  # Displacement 占比 >= 60% 即归为该类

# CSV 分块读取
CHUNK_SIZE = 500_000

# 5 折交叉验证: 按 Animal_id 划分
NUM_FOLDS = 5
SEED = 42


def get_animal_ids(raw_path: str):
    """扫描文件获取所有 Animal_id。"""
    print("扫描动物 ID ...")
    ids = set()
    for chunk in tqdm(pd.read_csv(raw_path, usecols=["Animal_id"], chunksize=CHUNK_SIZE),
                       desc="扫描"):
        ids.update(chunk["Animal_id"].unique().tolist())
    return sorted(int(x) for x in ids)


def split_animals_to_folds(animal_ids, num_folds: int, seed: int = 42):
    """把动物 ID 均匀打散到 num_folds 折。"""
    rng = np.random.default_rng(seed)
    ids = np.array(animal_ids)
    rng.shuffle(ids)
    folds = [[] for _ in range(num_folds)]
    for i, gid in enumerate(ids):
        folds[i % num_folds].append(int(gid))
    for f in folds:
        f.sort()

    cv_folds = []
    for cv_idx in range(num_folds):
        test = folds[cv_idx]
        val = folds[(cv_idx + 1) % num_folds]
        train = []
        for j in range(num_folds):
            if j != cv_idx and j != (cv_idx + 1) % num_folds:
                train.extend(folds[j])
        train.sort()
        cv_folds.append({"train": train, "val": val, "test": test})
    return cv_folds


def extract_windows(acc: np.ndarray, labels: np.ndarray,
                    window_size: int, stride: int,
                    minority_class: int = -1, minority_stride: int = 24,
                    minority_threshold: float = 0.6):
    """
    滑动窗口切分。对 minority_class 使用宽松阈值规则，其他类要求纯标签。
    """
    n = len(acc)
    X_list, y_list = [], []

    i = 0
    while i + window_size <= n:
        window_labels = labels[i : i + window_size]
        unique = np.unique(window_labels)

        if len(unique) == 1:
            cls = int(unique[0])
            X_list.append(acc[i : i + window_size])
            y_list.append(cls)
            if cls == minority_class:
                i += minority_stride
            else:
                i += stride
        elif minority_class >= 0 and minority_class in unique:
            minority_count = int(np.sum(window_labels == minority_class))
            if minority_count >= window_size * minority_threshold:
                X_list.append(acc[i : i + window_size])
                y_list.append(minority_class)
                i += minority_stride
            else:
                changes = np.where(np.diff(window_labels) != 0)[0]
                if len(changes) > 0:
                    i += int(changes[0]) + 1
                else:
                    i += stride
        else:
            changes = np.where(np.diff(window_labels) != 0)[0]
            if len(changes) > 0:
                i += int(changes[0]) + 1
            else:
                i += stride

    if X_list:
        return np.array(X_list, dtype=np.float32), np.array(y_list, dtype=np.int64)
    return np.empty((0, window_size, 3), dtype=np.float32), np.empty((0,), dtype=np.int64)


def process_animal(df_animal: pd.DataFrame, animal_id: int, out_dir: str):
    """
    处理单个动物的全部数据。按 Sequence_num 分段切窗，避免跨段窗口。
    返回 (X, y) 数组和每类窗口数统计。
    """
    per_class_windows = defaultdict(int)
    X_all, y_all = [], []

    # 按 Sequence_num 分组，每段独立切窗（保证窗口内时间连续）
    for seq_num, seg in df_animal.groupby("Sequence_num", sort=False):
        if len(seg) < WINDOW_SIZE:
            continue

        # 按时间排序（通常已经有序，但保险起见）
        seg = seg.sort_values("Time", kind="stable")

        acc = seg[["X", "Y", "Z"]].values.astype(np.float32)
        # Behaviour_num 1-5 -> 0-4
        labels = seg["Behaviour_num"].map(NUM_TO_IDX).values
        # 丢弃异常标签
        if np.any(pd.isna(labels)):
            mask = ~pd.isna(labels)
            acc = acc[mask]
            labels = labels[mask]
        labels = labels.astype(np.int64)

        if len(acc) < WINDOW_SIZE:
            continue

        X, y = extract_windows(acc, labels, WINDOW_SIZE, STRIDE_DEFAULT,
                               minority_class=MINORITY_CLASS,
                               minority_stride=STRIDE_MINORITY,
                               minority_threshold=MINORITY_THRESHOLD)
        if len(y) > 0:
            X_all.append(X)
            y_all.append(y)

    if not X_all:
        return None, None, per_class_windows

    X_all = np.concatenate(X_all, axis=0)
    y_all = np.concatenate(y_all, axis=0)

    unique, counts = np.unique(y_all, return_counts=True)
    for cls, cnt in zip(unique, counts):
        per_class_windows[int(cls)] = int(cnt)

    animal_dir = os.path.join(out_dir, f"animal_{animal_id}")
    os.makedirs(animal_dir, exist_ok=True)
    np.save(os.path.join(animal_dir, f"X_{animal_id}.npy"), X_all)
    np.save(os.path.join(animal_dir, f"y_{animal_id}.npy"), y_all)

    return X_all, y_all, per_class_windows


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"输入文件: {RAW_PATH}")
    print(f"输出目录: {OUT_DIR}")
    print(f"窗口: {WINDOW_SIZE} ({WINDOW_SIZE/24.4:.1f}s @ 24.4Hz), stride={STRIDE_DEFAULT}, "
          f"minority stride={STRIDE_MINORITY}, threshold={MINORITY_THRESHOLD}")

    animal_ids = get_animal_ids(RAW_PATH)
    print(f"发现 {len(animal_ids)} 只动物: {animal_ids}")

    print("\n按动物分组处理（分块读取 + 按 Animal_id 累积）...")
    # 分块读取，按 Animal_id 累积再处理
    animal_buffers = defaultdict(list)
    for chunk in tqdm(pd.read_csv(
        RAW_PATH,
        chunksize=CHUNK_SIZE,
        usecols=["Time", "Animal_id", "Behaviour_num", "X", "Y", "Z", "Sequence_num"],
    ), desc="读取 CSV"):
        for aid, grp in chunk.groupby("Animal_id", sort=False):
            animal_buffers[int(aid)].append(grp)

    print("\n切分窗口并保存 .npy ...")
    row_stats = defaultdict(int)
    window_stats = defaultdict(int)
    per_animal_stats = {}

    for aid in tqdm(animal_ids, desc="处理动物"):
        parts = animal_buffers.get(aid, [])
        if not parts:
            continue
        df_animal = pd.concat(parts, ignore_index=True)
        del animal_buffers[aid]

        # 统计行级别类别
        row_counts = df_animal["Behaviour_num"].value_counts()
        for b_num, cnt in row_counts.items():
            idx = NUM_TO_IDX.get(int(b_num))
            if idx is not None:
                row_stats[idx] += int(cnt)

        X, y, per_cls = process_animal(df_animal, aid, OUT_DIR)
        per_animal_stats[str(aid)] = {
            LABEL_NAMES[cls]: per_cls.get(cls, 0) for cls in range(NUM_CLASSES)
        }
        for cls, cnt in per_cls.items():
            window_stats[cls] += cnt

        n_windows = 0 if y is None else len(y)
        tqdm.write(f"  animal={aid}: 行数={len(df_animal)}, 窗口数={n_windows}")

    # 打印汇总
    print("\n" + "=" * 60)
    print("行级别类别分布:")
    total_rows = sum(row_stats.values())
    for cls in range(NUM_CLASSES):
        cnt = row_stats.get(cls, 0)
        pct = 100.0 * cnt / max(1, total_rows)
        print(f"  {LABEL_NAMES[cls]:22s}: {cnt:>10d} 行 ({pct:.1f}%)")

    total_windows = sum(window_stats.values())
    print(f"\n窗口级别类别分布 (总计: {total_windows}):")
    for cls in range(NUM_CLASSES):
        cnt = window_stats.get(cls, 0)
        pct = 100.0 * cnt / max(1, total_windows)
        print(f"  {LABEL_NAMES[cls]:22s}: {cnt:>8d} 窗口 ({pct:.1f}%)")

    # CV 折分配
    cv_folds = split_animals_to_folds(animal_ids, NUM_FOLDS, seed=SEED)
    print(f"\n{NUM_FOLDS} 折 CV 划分:")
    for cv_idx, fold in enumerate(cv_folds):
        print(f"  Fold {cv_idx}: train={len(fold['train'])}, "
              f"val={len(fold['val'])}, test={len(fold['test'])}")

    split_info = {
        "animal_ids": animal_ids,
        "num_folds": NUM_FOLDS,
        "folds": cv_folds,
        "label_names": LABEL_NAMES,
        "num_to_idx": NUM_TO_IDX,
        "window_size": WINDOW_SIZE,
        "stride_default": STRIDE_DEFAULT,
        "stride_minority": STRIDE_MINORITY,
        "minority_class": MINORITY_CLASS,
        "minority_threshold": MINORITY_THRESHOLD,
        "seed": SEED,
    }
    with open(os.path.join(OUT_DIR, "split_info.json"), "w", encoding="utf-8") as f:
        json.dump(split_info, f, ensure_ascii=False, indent=2)

    label_stats = {
        "row_counts": {LABEL_NAMES[cls]: int(row_stats.get(cls, 0)) for cls in range(NUM_CLASSES)},
        "window_counts": {LABEL_NAMES[cls]: int(window_stats.get(cls, 0)) for cls in range(NUM_CLASSES)},
        "total_rows": int(total_rows),
        "total_windows": int(total_windows),
        "per_animal_window_counts": per_animal_stats,
    }
    with open(os.path.join(OUT_DIR, "label_stats.json"), "w", encoding="utf-8") as f:
        json.dump(label_stats, f, ensure_ascii=False, indent=2)

    print(f"\nsplit_info.json 和 label_stats.json 已保存到 {OUT_DIR}")
    print("预处理完成!")


if __name__ == "__main__":
    main()
