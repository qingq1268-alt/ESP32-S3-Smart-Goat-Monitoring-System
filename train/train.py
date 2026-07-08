"""
train.py
5 折动物级交叉验证训练 GoatCNN 5 类行为分类模型。
使用 Final-DataPaper-Horn.txt 数据集（59 只动物，~24Hz）。

核心设计:
- 按 Animal_id 做 leave-goats-out 交叉验证，杜绝个体泄漏
- 每窗口独立 center（消除传感器姿态/重力基线差异）
- 4 通道输入: X, Y, Z + |a| 幅值（旋转不变量）
- 训练时随机 3D 旋转增强（对佩戴姿态鲁棒）
- FocalLoss + WeightedSampler 处理类别不均衡

用法:
    python train.py

输入: data/ (由 preprocess.py 生成)
输出: results/
"""

import warnings
warnings.filterwarnings("ignore", category=UserWarning)

import os
import glob
import json
import csv
import random
import bisect
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler
from tqdm import tqdm
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support, accuracy_score

_HAS_MPL = False
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _HAS_MPL = True
except Exception:
    pass

# ====== 配置 ======
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = os.path.join(HERE, "data")
OUT_ROOT = os.path.join(HERE, "results")

LABEL_NAMES = ["Displacement", "Grazing", "Ruminating_Chewing", "Other", "Resting"]
NUM_CLASSES = len(LABEL_NAMES)
INPUT_CHANNELS = 4  # X, Y, Z, |a|


def set_seed(seed: int = 42):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


# ====== 可视化 ======
def plot_cm(cm: np.ndarray, labels: List[str], save_path: str,
            title: str = "Confusion Matrix", normalize: bool = True, dpi: int = 200):
    if not _HAS_MPL:
        return
    ensure_dir(os.path.dirname(save_path))
    cm_show = cm.astype(np.float64)
    if normalize:
        row = cm_show.sum(axis=1, keepdims=True)
        row[row == 0] = 1.0
        cm_show = cm_show / row

    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(cm_show, interpolation="nearest", cmap=plt.cm.Blues)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    ax.set_title(title, pad=12)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_yticklabels(labels)
    ax.grid(False)
    thresh = (cm_show.max() + cm_show.min()) / 2.0
    for i in range(cm_show.shape[0]):
        for j in range(cm_show.shape[1]):
            val = cm_show[i, j]
            txt = f"{val*100:.1f}%" if normalize else f"{int(val)}"
            ax.text(j, i, txt, ha="center", va="center",
                    color="white" if val > thresh else "black", fontsize=9)
    fig.tight_layout()
    fig.savefig(save_path, dpi=dpi)
    plt.close(fig)


def plot_training_curve(csv_path: str, save_path: str,
                        title: str = "Training Dynamics", dpi: int = 200):
    if not _HAS_MPL or not os.path.exists(csv_path):
        return
    ensure_dir(os.path.dirname(save_path))
    epochs, losses, f1s = [], [], []
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            epochs.append(int(row["epoch"]))
            losses.append(float(row["train_loss"]))
            f1s.append(float(row["val_macro_f1"]))
    if not epochs:
        return
    fig, ax1 = plt.subplots(figsize=(9, 6))
    ax1.plot(epochs, losses, label="Train Loss")
    ax1.set_xlabel("Epoch")
    ax1.set_ylabel("Loss")
    ax1.grid(True, linestyle="--", alpha=0.4)
    ax2 = ax1.twinx()
    ax2.plot(epochs, f1s, "--", color="orange", label="Val Macro-F1")
    ax2.set_ylabel("Macro-F1")
    lines = ax1.get_legend_handles_labels()[0] + ax2.get_legend_handles_labels()[0]
    labs = ax1.get_legend_handles_labels()[1] + ax2.get_legend_handles_labels()[1]
    ax1.legend(lines, labs, loc="best")
    plt.title(title)
    fig.tight_layout()
    fig.savefig(save_path, dpi=dpi)
    plt.close(fig)


# ====== 数据集 ======
def list_animal_shards(data_root: str, animal_ids: List[int]) -> Tuple[List[str], List[str]]:
    x_files, y_files = [], []
    for aid in animal_ids:
        animal_dir = os.path.join(data_root, f"animal_{aid}")
        if not os.path.isdir(animal_dir):
            continue
        x_shards = sorted(glob.glob(os.path.join(animal_dir, "X_*.npy")))
        for xf in x_shards:
            yf = xf.replace("X_", "y_")
            if not os.path.exists(yf):
                continue
            x_files.append(xf)
            y_files.append(yf)
    return x_files, y_files


def compute_global_std4(x_files: List[str]) -> np.ndarray:
    """
    计算 per-window centered 后的 4 通道全局 std（mean 恒为 0）。
    """
    count = 0
    mean4 = np.zeros(4, dtype=np.float64)
    M2 = np.zeros(4, dtype=np.float64)

    for xf in tqdm(x_files, desc="Norm stats", leave=False):
        X = np.load(xf, mmap_mode="r")  # (N, W, 3)
        X = np.asarray(X, dtype=np.float64)
        X_centered = X - X.mean(axis=1, keepdims=True)
        mag = np.linalg.norm(X, axis=-1, keepdims=True)
        mag_centered = mag - mag.mean(axis=1, keepdims=True)
        X4 = np.concatenate([X_centered, mag_centered], axis=-1)
        flat = X4.reshape(-1, 4)
        # 批量 Welford
        for row in flat:
            count += 1
            delta = row - mean4
            mean4 += delta / count
            M2 += delta * (row - mean4)

    std4 = np.sqrt(M2 / max(1, count - 1) + 1e-12)
    return std4.astype(np.float32)


class PerWindowNormalizer:
    """
    每个窗口独立归一化:
      1. 拼接 |a| 幅值通道 (旋转不变量)
      2. 每窗口减去通道均值 (消除重力/姿态基线)
      3. 除以训练集全局 std (统一尺度)
    (W,3) -> (W,4)
    """

    def __init__(self, std4: np.ndarray, eps: float = 1e-6):
        self.std4 = std4.reshape(1, 4).astype(np.float32)
        self.eps = eps

    def __call__(self, x3: np.ndarray) -> np.ndarray:
        mag = np.linalg.norm(x3, axis=-1, keepdims=True).astype(np.float32)
        x4 = np.concatenate([x3, mag], axis=-1)
        x4 = x4 - x4.mean(axis=0, keepdims=True)
        x4 = x4 / (self.std4 + self.eps)
        return x4.astype(np.float32)


def random_rotation_matrix(rng: np.random.Generator) -> np.ndarray:
    """均匀分布的随机 3x3 旋转矩阵 (Arvo 1992)."""
    theta = rng.uniform(0, 2 * np.pi)
    phi = rng.uniform(0, 2 * np.pi)
    z = rng.uniform(0, 1)
    r = np.sqrt(z)
    V = np.array([np.cos(phi) * r, np.sin(phi) * r, np.sqrt(1.0 - z)])
    st, ct = np.sin(theta), np.cos(theta)
    R = np.array([[ct, st, 0.0], [-st, ct, 0.0], [0.0, 0.0, 1.0]])
    M = (2.0 * np.outer(V, V) - np.eye(3)) @ R
    return M.astype(np.float32)


class AnimalDataset(Dataset):
    def __init__(self, data_root: str, animal_ids: List[int],
                 normalizer: Optional[PerWindowNormalizer] = None,
                 augment_rotation: bool = False, rng_seed: int = 42):
        self.normalizer = normalizer
        self.augment_rotation = augment_rotation
        self.rng = np.random.default_rng(rng_seed)

        x_files, y_files = list_animal_shards(data_root, animal_ids)
        if not x_files:
            raise FileNotFoundError(f"在 {data_root} 找不到 animal_ids={animal_ids} 的数据")

        self.X_arrays, self.y_arrays, self.cum = [], [], [0]
        total = 0
        for xf, yf in zip(x_files, y_files):
            X = np.load(xf, mmap_mode="r")
            y = np.load(yf, mmap_mode="r")
            if X.shape[0] != y.shape[0]:
                raise ValueError(f"分片 shape 不一致: {xf}")
            self.X_arrays.append(X)
            self.y_arrays.append(y)
            total += int(y.shape[0])
            self.cum.append(total)

    def __len__(self):
        return self.cum[-1]

    def __getitem__(self, idx: int):
        shard = bisect.bisect_right(self.cum, idx) - 1
        off = idx - self.cum[shard]
        x = np.array(self.X_arrays[shard][off], dtype=np.float32)  # (W, 3)
        y = int(self.y_arrays[shard][off])

        if self.augment_rotation:
            R = random_rotation_matrix(self.rng)
            x = x @ R.T

        if self.normalizer is not None:
            x = self.normalizer(x)

        x = torch.from_numpy(x).transpose(0, 1).unsqueeze(-1).contiguous()
        return x, torch.tensor(y, dtype=torch.long)


def class_counts(ds: AnimalDataset, nc: int = NUM_CLASSES) -> np.ndarray:
    counts = np.zeros(nc, dtype=np.int64)
    for y_arr in ds.y_arrays:
        counts += np.bincount(np.asarray(y_arr, dtype=np.int64), minlength=nc)
    counts[counts == 0] = 1
    return counts


def make_weighted_sampler(ds: AnimalDataset, nc: int = NUM_CLASSES, power: float = 1.0):
    cc = class_counts(ds, nc)
    cw = (1.0 / cc.astype(np.float64)) ** power
    cw = cw / cw.mean()
    sw = []
    for y_arr in ds.y_arrays:
        sw.extend(cw[np.asarray(y_arr, dtype=np.int64)].tolist())
    return WeightedRandomSampler(torch.tensor(sw, dtype=torch.double),
                                 num_samples=len(sw), replacement=True), cc


def focal_alpha_effective_num(cc: np.ndarray, beta: float = 0.999) -> np.ndarray:
    eff = 1.0 - np.power(beta, cc.astype(np.float64))
    alpha = (1.0 - beta) / np.maximum(eff, 1e-12)
    alpha = alpha / alpha.mean()
    return alpha.astype(np.float32)


# ====== Focal Loss ======
class FocalLoss(nn.Module):
    def __init__(self, gamma: float = 2.0, alpha: Optional[torch.Tensor] = None):
        super().__init__()
        self.gamma = gamma
        self.alpha = alpha

    def forward(self, logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        logp = F.log_softmax(logits, dim=1)
        logp_t = logp.gather(1, target.unsqueeze(1)).squeeze(1)
        p_t = logp_t.exp()
        loss = -((1.0 - p_t) ** self.gamma) * logp_t
        if self.alpha is not None:
            alpha_t = self.alpha.to(logits.device).gather(0, target)
            loss = alpha_t * loss
        return loss.mean()


# ====== 模型 ======
class GoatCNN(nn.Module):
    """
    3-block 1D-CNN (~200K 参数)。
    输入: (B, 4, 120)  输出: (B, 5)
    """

    def __init__(self, num_classes: int = NUM_CLASSES,
                 in_channels: int = INPUT_CHANNELS, dropout: float = 0.4):
        super().__init__()
        self.conv1a = nn.Conv2d(in_channels, 64, kernel_size=(7, 1), padding=(3, 0), bias=False)
        self.bn1a = nn.BatchNorm2d(64)
        self.conv1b = nn.Conv2d(64, 64, kernel_size=(7, 1), padding=(3, 0), bias=False)
        self.bn1b = nn.BatchNorm2d(64)

        self.conv2a = nn.Conv2d(64, 128, kernel_size=(5, 1), padding=(2, 0), bias=False)
        self.bn2a = nn.BatchNorm2d(128)
        self.conv2b = nn.Conv2d(128, 128, kernel_size=(5, 1), padding=(2, 0), bias=False)
        self.bn2b = nn.BatchNorm2d(128)

        self.conv3 = nn.Conv2d(128, 128, kernel_size=(3, 1), padding=(1, 0), bias=False)
        self.bn3 = nn.BatchNorm2d(128)

        self.pool = nn.MaxPool2d(kernel_size=(2, 1))
        self.drop = nn.Dropout(dropout)
        self.gap = nn.AdaptiveAvgPool2d((1, 1))
        self.fc = nn.Linear(128, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.relu(self.bn1a(self.conv1a(x)))
        x = F.relu(self.bn1b(self.conv1b(x)))
        x = self.pool(x)

        x = F.relu(self.bn2a(self.conv2a(x)))
        x = F.relu(self.bn2b(self.conv2b(x)))
        x = self.pool(x)

        x = F.relu(self.bn3(self.conv3(x)))
        x = self.drop(x)

        x = self.gap(x).flatten(1)
        return self.fc(x)


# ====== 训练与评估 ======
@torch.no_grad()
def evaluate(model: nn.Module, loader: DataLoader, device: str, desc: str = "Eval") -> Dict:
    model.eval()
    yt, yp = [], []
    for x, y in tqdm(loader, desc=desc, leave=False):
        logits = model(x.to(device))
        pred = logits.argmax(1).cpu().numpy()
        yt.append(y.numpy())
        yp.append(pred)
    yt = np.concatenate(yt) if yt else np.array([])
    yp = np.concatenate(yp) if yp else np.array([])

    cm = confusion_matrix(yt, yp, labels=list(range(NUM_CLASSES)))
    prec, rec, f1, sup = precision_recall_fscore_support(
        yt, yp, labels=list(range(NUM_CLASSES)), zero_division=0
    )
    acc = accuracy_score(yt, yp)
    sup_sum = sup.sum()
    return {
        "accuracy": float(acc),
        "macro_f1": float(f1.mean()),
        "weighted_f1": float(np.sum(f1 * sup) / max(1, sup_sum)),
        "per_class": [
            {"name": LABEL_NAMES[i], "precision": float(prec[i]), "recall": float(rec[i]),
             "f1": float(f1[i]), "support": int(sup[i])}
            for i in range(NUM_CLASSES)
        ],
        "cm": cm,
    }


def train_one_epoch(model, loader, optim, criterion, device, epoch, clip=1.0):
    model.train()
    total, n = 0.0, 0
    pbar = tqdm(loader, desc=f"Epoch {epoch:03d}", leave=False)
    for x, y in pbar:
        x, y = x.to(device), y.to(device)
        optim.zero_grad(set_to_none=True)
        loss = criterion(model(x), y)
        loss.backward()
        if clip > 0:
            nn.utils.clip_grad_norm_(model.parameters(), clip)
        optim.step()
        bs = x.size(0)
        total += float(loss.item()) * bs
        n += bs
        pbar.set_postfix(loss=float(loss.item()))
    return total / max(1, n)


@dataclass
class Config:
    data_root: str = DATA_ROOT
    out_root: str = OUT_ROOT
    batch_size: int = 256
    epochs: int = 50
    lr: float = 1e-3
    weight_decay: float = 1e-4
    dropout: float = 0.4
    focal_gamma: float = 2.0
    alpha_beta: float = 0.999
    sampler_power: float = 1.0
    patience: int = 10
    num_workers: int = 0
    seed: int = 42
    device: str = "cuda"


def run_fold(cfg: Config, cv_idx: int, fold: Dict) -> Dict:
    device = cfg.device if cfg.device.startswith("cuda") and torch.cuda.is_available() else "cpu"

    train_animals = fold["train"]
    val_animals = fold["val"]
    test_animals = fold["test"]

    out = os.path.join(cfg.out_root, f"cv{cv_idx}")
    ensure_dir(out)

    print("\n" + "=" * 60)
    print(f"[CV {cv_idx}] train={len(train_animals)} animals, "
          f"val={len(val_animals)}, test={len(test_animals)}")

    train_x_files, _ = list_animal_shards(cfg.data_root, train_animals)
    std4 = compute_global_std4(train_x_files)
    np.savez(os.path.join(out, "norm_stats.npz"), std=std4)
    print(f"Norm std (4ch): {std4}")
    normalizer = PerWindowNormalizer(std4)

    train_ds = AnimalDataset(cfg.data_root, train_animals, normalizer,
                             augment_rotation=True, rng_seed=cfg.seed + cv_idx)
    val_ds = AnimalDataset(cfg.data_root, val_animals, normalizer, augment_rotation=False)
    test_ds = AnimalDataset(cfg.data_root, test_animals, normalizer, augment_rotation=False)
    print(f"样本数: train={len(train_ds)}, val={len(val_ds)}, test={len(test_ds)}")

    sampler, cc = make_weighted_sampler(train_ds, NUM_CLASSES, cfg.sampler_power)
    print(f"训练类别分布: {cc.tolist()}")

    train_loader = DataLoader(train_ds, batch_size=cfg.batch_size, sampler=sampler,
                              num_workers=cfg.num_workers, pin_memory=(device != "cpu"))
    val_loader = DataLoader(val_ds, batch_size=cfg.batch_size, shuffle=False,
                            num_workers=cfg.num_workers, pin_memory=(device != "cpu"))
    test_loader = DataLoader(test_ds, batch_size=cfg.batch_size, shuffle=False,
                             num_workers=cfg.num_workers, pin_memory=(device != "cpu"))

    alpha = torch.tensor(focal_alpha_effective_num(cc, cfg.alpha_beta),
                         dtype=torch.float32, device=device)
    print(f"Focal alpha: {alpha.cpu().numpy().tolist()}")

    model = GoatCNN(num_classes=NUM_CLASSES, in_channels=INPUT_CHANNELS,
                    dropout=cfg.dropout).to(device)
    num_params = sum(p.numel() for p in model.parameters())
    print(f"GoatCNN 参数量: {num_params:,}")

    criterion = FocalLoss(gamma=cfg.focal_gamma, alpha=alpha).to(device)
    optim = torch.optim.AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(optim, T_max=cfg.epochs, eta_min=1e-5)

    log_path = os.path.join(out, "train_log.csv")
    with open(log_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["epoch", "train_loss", "val_acc",
                                 "val_macro_f1", "val_weighted_f1", "lr"])

    best_f1, best_ep, bad = -1.0, -1, 0
    ckpt_path = os.path.join(out, "best_model.pt")

    for ep in range(1, cfg.epochs + 1):
        lr_now = optim.param_groups[0]["lr"]
        loss = train_one_epoch(model, train_loader, optim, criterion, device, ep)
        vr = evaluate(model, val_loader, device, desc=f"VAL ep{ep:03d}")

        with open(log_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([ep, f"{loss:.6f}", f"{vr['accuracy']:.6f}",
                                     f"{vr['macro_f1']:.6f}", f"{vr['weighted_f1']:.6f}",
                                     f"{lr_now:.7f}"])

        print(f"  ep{ep:03d} lr={lr_now:.5f} loss={loss:.4f} "
              f"macF1={vr['macro_f1']:.4f} acc={vr['accuracy']:.4f}")

        if vr["macro_f1"] > best_f1 + 1e-6:
            best_f1, best_ep, bad = vr["macro_f1"], ep, 0
            torch.save({"epoch": ep, "model_state": model.state_dict()}, ckpt_path)
        else:
            bad += 1
        if bad >= cfg.patience:
            print(f"  早停于 ep {ep} (patience={cfg.patience})")
            break
        sched.step()

    print(f"  最佳: ep={best_ep} val_macF1={best_f1:.4f}")
    plot_training_curve(log_path, os.path.join(out, "training_dynamics.png"), f"CV{cv_idx} Training")

    model.load_state_dict(torch.load(ckpt_path, map_location=device)["model_state"])
    tr = evaluate(model, test_loader, device, desc=f"TEST cv{cv_idx}")
    with open(os.path.join(out, "test_report.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in tr.items() if k != "cm"}, f, ensure_ascii=False, indent=2)

    print(f"\n  TEST: acc={tr['accuracy']:.4f} macF1={tr['macro_f1']:.4f} wF1={tr['weighted_f1']:.4f}")
    for c in tr["per_class"]:
        print(f"    {c['name']:22s} P={c['precision']:.3f} R={c['recall']:.3f} "
              f"F1={c['f1']:.3f} n={c['support']}")

    cm = tr["cm"].astype(np.int64)
    np.save(os.path.join(out, "test_cm.npy"), cm)
    plot_cm(cm, LABEL_NAMES, os.path.join(out, "cm_norm.png"),
            f"CV{cv_idx} Normalized CM", normalize=True)
    plot_cm(cm, LABEL_NAMES, os.path.join(out, "cm_count.png"),
            f"CV{cv_idx} Count CM", normalize=False)

    return {
        "cv_idx": cv_idx,
        "train_animals": train_animals,
        "val_animals": val_animals,
        "test_animals": test_animals,
        "best_epoch": best_ep,
        "val_macro_f1": float(best_f1),
        "test_accuracy": tr["accuracy"],
        "test_macro_f1": tr["macro_f1"],
        "test_weighted_f1": tr["weighted_f1"],
        "ckpt_path": ckpt_path,
        "norm_path": os.path.join(out, "norm_stats.npz"),
        "test_cm": cm,
    }


def summarize(results: List[Dict], out_root: str):
    acc = np.array([r["test_accuracy"] for r in results])
    mf1 = np.array([r["test_macro_f1"] for r in results])
    wf1 = np.array([r["test_weighted_f1"] for r in results])
    cm_sum = np.sum([r["test_cm"] for r in results], axis=0).astype(np.int64)

    best_idx = int(np.argmax(mf1))
    best = results[best_idx]

    summary = {
        "num_folds": len(results),
        "mean": {
            "accuracy": float(acc.mean()),
            "macro_f1": float(mf1.mean()),
            "weighted_f1": float(wf1.mean()),
        },
        "std": {
            "accuracy": float(acc.std(ddof=1)) if len(acc) > 1 else 0.0,
            "macro_f1": float(mf1.std(ddof=1)) if len(mf1) > 1 else 0.0,
            "weighted_f1": float(wf1.std(ddof=1)) if len(wf1) > 1 else 0.0,
        },
        "folds": [{k: v for k, v in r.items() if k != "test_cm"} for r in results],
        "label_names": LABEL_NAMES,
        "best_cv_idx": best_idx,
    }

    with open(os.path.join(out_root, "cv_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    np.save(os.path.join(out_root, "cv_cm_sum.npy"), cm_sum)
    plot_cm(cm_sum, LABEL_NAMES, os.path.join(out_root, "cv_cm_norm.png"),
            "CV Sum Normalized CM", normalize=True)
    plot_cm(cm_sum, LABEL_NAMES, os.path.join(out_root, "cv_cm_count.png"),
            "CV Sum Count CM", normalize=False)

    best_info = {
        "best_cv_idx": best_idx,
        "best_test_macro_f1": float(mf1[best_idx]),
        "ckpt_path": best["ckpt_path"],
        "norm_path": best["norm_path"],
        "train_animals": best["train_animals"],
        "val_animals": best["val_animals"],
        "test_animals": best["test_animals"],
    }
    with open(os.path.join(out_root, "best_fold_info.json"), "w", encoding="utf-8") as f:
        json.dump(best_info, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("CV 汇总")
    print("=" * 60)
    print(f"Accuracy    {acc.mean():.4f} ± {summary['std']['accuracy']:.4f}")
    print(f"Macro-F1    {mf1.mean():.4f} ± {summary['std']['macro_f1']:.4f}")
    print(f"Weighted-F1 {wf1.mean():.4f} ± {summary['std']['weighted_f1']:.4f}")
    print(f"\n最佳 fold: cv{best_idx} (test macro-F1={mf1[best_idx]:.4f})")
    print(f"checkpoint: {best['ckpt_path']}")


def main():
    cfg = Config()
    ensure_dir(cfg.out_root)
    set_seed(cfg.seed)

    split_info_path = os.path.join(cfg.data_root, "split_info.json")
    if not os.path.exists(split_info_path):
        raise FileNotFoundError(f"找不到 split_info.json,请先运行 preprocess.py")
    with open(split_info_path, "r", encoding="utf-8") as f:
        split_info = json.load(f)

    folds = split_info["folds"]
    print(f"CV 折数: {len(folds)}")
    print(f"设备: {'cuda' if cfg.device.startswith('cuda') and torch.cuda.is_available() else 'cpu'}")

    results = []
    for cv_idx, fold in enumerate(folds):
        res = run_fold(cfg, cv_idx, fold)
        results.append(res)

    summarize(results, cfg.out_root)


if __name__ == "__main__":
    main()
