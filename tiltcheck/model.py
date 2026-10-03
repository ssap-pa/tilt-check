"""TabPFN on your own trade history.

TabPFN is a pretrained transformer for small tables: `fit` stores your trades as
context and `predict_proba` reads them in one forward pass. There is no training
run, so every new trade you import is used on the very next check. That is the
"learns from every trade" part.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

from .features import FEATURES, add_features, encode


def _tabpfn(cat_idx: list[int]):
    import torch
    from tabpfn import TabPFNClassifier
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return TabPFNClassifier(device=device, categorical_features_indices=cat_idx, random_state=0)


@dataclass
class Evaluation:
    n_train: int
    n_test: int
    base_rate: float
    auc_tabpfn: float
    auc_logreg: float
    brier_tabpfn: float
    brier_base: float
    flagged: int            # test trades scored in the bottom fifth
    flagged_pnl: float      # what those trades made or lost
    flagged_win_rate: float
    rest_pnl: float
    rest_win_rate: float
    threshold: float


def walk_forward(trades: pd.DataFrame, test_share: float = 0.3) -> tuple[Evaluation, pd.DataFrame]:
    """Train on the older trades, score the newer ones it has never seen."""
    t = add_features(trades)
    cut = int(len(t) * (1 - test_share))
    train, test = t.iloc[:cut], t.iloc[cut:]
    Xtr, cat_idx, cats = encode(train)
    Xte, _, _ = encode(test, cats)
    ytr, yte = train["win"].to_numpy(), test["win"].to_numpy()

    clf = _tabpfn(cat_idx).fit(Xtr, ytr)
    p = clf.predict_proba(Xte)[:, 1]
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(np.nan_to_num(Xtr), ytr)
    p_lr = lr.predict_proba(np.nan_to_num(Xte))[:, 1]

    thr = float(np.quantile(p, 0.2))
    flag = p <= thr
    scored = test.assign(p_win=p, flagged=flag)
    ev = Evaluation(
        n_train=len(train), n_test=len(test), base_rate=float(ytr.mean()),
        auc_tabpfn=float(roc_auc_score(yte, p)) if len(set(yte)) > 1 else float("nan"),
        auc_logreg=float(roc_auc_score(yte, p_lr)) if len(set(yte)) > 1 else float("nan"),
        brier_tabpfn=float(brier_score_loss(yte, p)),
        brier_base=float(brier_score_loss(yte, np.full(len(yte), ytr.mean()))),
        flagged=int(flag.sum()), flagged_pnl=float(test["profit"][flag].sum()),
        flagged_win_rate=float(yte[flag].mean()) if flag.any() else float("nan"),
        rest_pnl=float(test["profit"][~flag].sum()),
        rest_win_rate=float(yte[~flag].mean()) if (~flag).any() else float("nan"),
        threshold=thr,
    )
    return ev, scored


def reliability(trades: pd.DataFrame, blocks=(0.4, 0.55, 0.7, 0.85)) -> float:
    """Mean AUC over expanding-window folds: train on everything before a block,
    score the block. Below ~0.55 the probability is noise and we say so."""
    t = add_features(trades)
    n, aucs = len(t), []
    for start in blocks:
        a, b = int(n * start), int(n * min(start + 0.15, 1.0))
        tr, te = t.iloc[:a], t.iloc[a:b]
        if len(tr) < 30 or te["win"].nunique() < 2:
            continue
        Xtr, ci, cats = encode(tr)
        Xte, _, _ = encode(te, cats)
        p = _tabpfn(ci).fit(Xtr, tr["win"].to_numpy()).predict_proba(Xte)[:, 1]
        aucs.append(roc_auc_score(te["win"], p))
    return float(np.mean(aucs)) if aucs else float("nan")


def win_probability(trades: pd.DataFrame, row: pd.DataFrame) -> float:
    """Probability that a planned trade ends green, given all history so far."""
    t = add_features(trades)
    X, cat_idx, cats = encode(t)
    clf = _tabpfn(cat_idx).fit(X, t["win"].to_numpy())
    Xr, _, _ = encode(row.assign(**{c: row[c] for c in FEATURES}), cats)
    return float(clf.predict_proba(Xr)[0, 1])
