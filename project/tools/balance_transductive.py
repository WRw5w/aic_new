"""Transductive balanced inference on a saved TTA logit matrix.

The test set is known to be ~class-balanced (≈n/c per class). The current
production balancer (tta_predict.fit_uniform_bias) only matches the *column*
marginal in expectation via a single additive per-class bias under softmax
(temperature=1) -- i.e. the soft, column-only half of Sinkhorn.

This tool applies the *full* doubly-constrained transport at a tunable
temperature (sharper than softmax-1 = harder, closer to an exact balanced
assignment), plus an optional fully-hard capacity-constrained assignment. Still
a single model, single inference: this is post-processing of ONE model's logit
matrix using the known uniform class prior (same nature as the accepted
balanced-logit correction, not an ensemble/vote).

Inputs: <prefix>_logits.pt produced by `tta_predict.py --save-logits`
        (dict: logits[n,c] float, names[n], class_names[c]).

Usage:
  python tools/balance_transductive.py --logits submissions/pred_results_xxx_logits.pt \
      --out-prefix submissions/pred_results_xxx --tau 1.0,0.5,0.25 [--hard]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robustft.submission import save_predictions, zip_submission


def dist_stats(preds, c):
    counts = np.bincount(preds, minlength=c)
    return f"min={counts.min()} max={counts.max()} std={counts.std():.2f}"


def soft_uniform_bias(logits, iters=200, step=1.0):
    """Replicates tta_predict.fit_uniform_bias (column-only, softmax temp=1) -> preds.
    This is the current production 'balanced' baseline, for A/B comparison."""
    n, c = logits.shape
    target = n / c
    b = torch.zeros(c)
    for _ in range(iters):
        col = torch.softmax(logits + b, dim=1).sum(0)
        b = b - step * torch.log((col / target).clamp_min(1e-8))
    return (logits + b).argmax(1).numpy()


def sinkhorn_balanced(logits, tau=1.0, iters=300):
    """Full doubly-constrained entropic OT (Sinkhorn-Knopp) onto a uniform class
    marginal. tau<1 sharpens (harder assignment); tau=1 with row-softmax ~ the
    soft baseline but with the row constraint also enforced.

    Target marginals: each row (sample) sums to 1/n, each col (class) sums to 1/c.
    Returns argmax-over-class predictions of the transport plan P.
    """
    n, c = logits.shape
    logK = (logits / tau).double()             # log kernel
    logK -= logK.max()
    log_u = torch.zeros(n, dtype=torch.double)  # row scalings (log)
    log_v = torch.zeros(c, dtype=torch.double)  # col scalings (log)
    log_r = torch.full((n,), -np.log(n), dtype=torch.double)  # target row marginal
    log_c = torch.full((c,), -np.log(c), dtype=torch.double)  # target col marginal
    for _ in range(iters):
        # column update: v = log_c - logsumexp_rows(logK + u)
        log_v = log_c - torch.logsumexp(logK + log_u[:, None], dim=0)
        # row update:    u = log_r - logsumexp_cols(logK + v)
        log_u = log_r - torch.logsumexp(logK + log_v[None, :], dim=1)
    logP = logK + log_u[:, None] + log_v[None, :]
    return logP.argmax(1).numpy()


def hard_balanced_assignment(logits, cap=None):
    """Greedy capacity-constrained assignment: each class may receive at most
    `cap` samples (default round(n/c)); each sample assigned once. Greedy by
    descending probability over all (sample,class) pairs -> exact-ish balance."""
    n, c = logits.shape
    if cap is None:
        cap = int(round(n / c))
    probs = torch.softmax(logits, dim=1).numpy()
    # candidate pairs sorted by prob desc; cap memory by taking top-k classes/sample
    k = min(c, 12)
    topk = np.argsort(-probs, axis=1)[:, :k]
    rows = np.repeat(np.arange(n), k)
    cols = topk.reshape(-1)
    vals = probs[rows, cols]
    order = np.argsort(-vals)
    assigned = np.full(n, -1, dtype=np.int64)
    load = np.zeros(c, dtype=np.int64)
    left = n
    for o in order:
        i, j = rows[o], cols[o]
        if assigned[i] >= 0 or load[j] >= cap:
            continue
        assigned[i] = j
        load[j] += 1
        left -= 1
        if left == 0:
            break
    # any sample not assigned (its top-k all full): give plain argmax
    miss = np.where(assigned < 0)[0]
    if len(miss):
        assigned[miss] = probs[miss].argmax(1)
    return assigned


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--logits", required=True, help="<prefix>_logits.pt from tta_predict --save-logits")
    p.add_argument("--out-prefix", required=True)
    p.add_argument("--tau", default="1.0,0.5,0.25", help="comma-separated Sinkhorn temperatures")
    p.add_argument("--iters", type=int, default=300)
    p.add_argument("--hard", action="store_true", help="also emit hard capacity-constrained assignment")
    args = p.parse_args()

    d = torch.load(args.logits, map_location="cpu")
    logits, names, class_names = d["logits"].float(), d["names"], d["class_names"]
    c = len(class_names)
    print(f"loaded logits {tuple(logits.shape)}, {c} classes, target ~{len(names)/c:.1f}/class")

    plain = logits.argmax(1).numpy()
    soft = soft_uniform_bias(logits)
    print(f"plain         dist: {dist_stats(plain, c)}")
    print(f"soft_balanced dist: {dist_stats(soft, c)}  (vs plain changed {int((plain!=soft).sum())})")

    outputs = []
    for tau in [float(x) for x in args.tau.split(",")]:
        sk = sinkhorn_balanced(logits, tau=tau, iters=args.iters)
        tag = f"sinkhorn_t{tau:g}"
        print(f"{tag:14s} dist: {dist_stats(sk, c)}  (vs soft changed {int((soft!=sk).sum())})")
        outputs.append((tag, sk))
    if args.hard:
        hd = hard_balanced_assignment(logits)
        print(f"hard_balanced dist: {dist_stats(hd, c)}  (vs soft changed {int((soft!=hd).sum())})")
        outputs.append(("hard_balanced", hd))

    for tag, preds in outputs:
        out_csv = Path(f"{args.out_prefix}_{tag}.csv")
        save_predictions(out_csv, names, preds.tolist(), class_names)
        z = zip_submission(out_csv)
        print(f"saved {out_csv} and {z}")


if __name__ == "__main__":
    main()
