"""Dataset image statistics: native properties of the train/test images.

Reads *headers only* (no pixel decode) so it is fast and memory-safe on the
16GB box. For every image it records width/height/mode/format/file-size, then
aggregates:

  - per-class image counts (train) + class imbalance
  - dimension distribution (width / height / short-side / long-side / MP)
  - aspect-ratio distribution + extreme-ratio flags
  - color-mode and file-format breakdown (catches the palette/CMYK/grayscale
    oddballs that triggered the PIL warnings during training)
  - **resolution adequacy vs input size**: for each candidate --img-size, what
    fraction of images are *upscaled* (short side < target) vs already large
    enough. This is the concrete answer to "is 448 inventing pixels?".

Optionally (--pixel-stats N) decodes a random sample to report per-channel
mean/std in [0,1] (normalization sanity check).

Usage:
  python tools/dataset_stats.py                       # train, header-only
  python tools/dataset_stats.py --split both
  python tools/dataset_stats.py --pixel-stats 2000    # + sampled channel stats
  python tools/dataset_stats.py --verify              # also integrity-check (slow)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

# The palette-transparency / corrupt-EXIF warnings are informational; we detect
# real problems explicitly below, so keep the console clean.
warnings.filterwarnings("ignore", category=UserWarning, module="PIL")

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".tif", ".tiff"}
TARGET_SIZES = [224, 416, 448, 480, 512, 576]


def iter_images(split: str):
    """Yield (split, class_id, path) for every image file.

    train/ is class-foldered (0000..0499); test/ is flat.
    """
    if split == "train":
        root = config.TRAIN_DIR
        for cls_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for f in cls_dir.iterdir():
                if f.suffix.lower() in IMG_EXTS:
                    yield ("train", cls_dir.name, f)
    else:  # test
        root = config.TEST_DIR
        for f in sorted(root.iterdir()):
            if f.is_file() and f.suffix.lower() in IMG_EXTS:
                yield ("test", None, f)


def probe(item, verify: bool):
    """Header-only probe of one image. Returns a dict row."""
    split, cls, path = item
    row = {"split": split, "class": cls, "path": str(path),
           "name": path.name, "ok": True, "error": ""}
    try:
        row["bytes"] = path.stat().st_size
        with Image.open(path) as im:
            row["w"], row["h"] = im.size      # header read, no decode
            row["mode"] = im.mode
            row["format"] = im.format
            if verify:
                im.verify()                   # integrity check (slower)
    except Exception as e:                     # noqa: BLE001 - want every failure
        row["ok"] = False
        row["error"] = f"{type(e).__name__}: {e}"
        row.setdefault("bytes", -1)
        row.update({"w": 0, "h": 0, "mode": "?", "format": "?"})
    return row


def describe(arr, name):
    """One-line percentile summary of a numeric array."""
    a = np.asarray(arr, dtype=float)
    if a.size == 0:
        return f"  {name:<12}: (empty)"
    pct = np.percentile(a, [1, 5, 25, 50, 75, 95, 99])
    return (f"  {name:<12}: min {a.min():>8.1f} | p1 {pct[0]:>7.1f} | p5 {pct[1]:>7.1f} | "
            f"p25 {pct[2]:>7.1f} | med {pct[3]:>7.1f} | p75 {pct[4]:>7.1f} | "
            f"p95 {pct[5]:>7.1f} | p99 {pct[6]:>7.1f} | max {a.max():>8.1f} | mean {a.mean():>7.1f}")


def pixel_stats(rows, n_sample, seed=42):
    """Decode a random sample of readable images; return per-channel mean/std in [0,1]."""
    ok = [r for r in rows if r["ok"]]
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(ok), size=min(n_sample, len(ok)), replace=False)
    s = np.zeros(3)
    ss = np.zeros(3)
    npix = 0
    used = 0
    for i in idx:
        try:
            with Image.open(ok[i]["path"]) as im:
                a = np.asarray(im.convert("RGB"), dtype=np.float64) / 255.0
            a = a.reshape(-1, 3)
            s += a.sum(0)
            ss += (a * a).sum(0)
            npix += a.shape[0]
            used += 1
        except Exception:  # noqa: BLE001
            continue
    if npix == 0:
        return None
    mean = s / npix
    std = np.sqrt(np.maximum(ss / npix - mean ** 2, 0))
    return {"sampled": used, "mean": mean.tolist(), "std": std.tolist()}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["train", "test", "both"], default="train")
    ap.add_argument("--workers", type=int, default=16, help="thread pool size (I/O bound)")
    ap.add_argument("--verify", action="store_true",
                    help="also run PIL integrity check (catches truncation; slower)")
    ap.add_argument("--pixel-stats", type=int, default=0, metavar="N",
                    help="decode N random images to compute channel mean/std (0=off)")
    ap.add_argument("--out-dir", default="outputs/dataset_stats",
                    help="where to write per-image CSV + JSON summary")
    ap.add_argument("--limit", type=int, default=0, help="probe only first N images (debug)")
    args = ap.parse_args()

    splits = ["train", "test"] if args.split == "both" else [args.split]
    items = []
    for sp in splits:
        items.extend(iter_images(sp))
    if args.limit:
        items = items[:args.limit]
    print(f"probing {len(items)} images from split={args.split} "
          f"(verify={args.verify}, workers={args.workers}) ...", flush=True)

    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, row in enumerate(ex.map(lambda it: probe(it, args.verify), items), 1):
            rows.append(row)
            if i % 10000 == 0:
                print(f"  ... {i}/{len(items)}", flush=True)

    ok = [r for r in rows if r["ok"]]
    bad = [r for r in rows if not r["ok"]]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- per-image CSV ----
    csv_path = out_dir / f"per_image_{args.split}.csv"
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("split,class,name,w,h,mode,format,bytes,ok,error\n")
        for r in rows:
            err = r["error"].replace(",", ";").replace("\n", " ")
            f.write(f'{r["split"]},{r["class"]},{r["name"]},{r["w"]},{r["h"]},'
                    f'{r["mode"]},{r["format"]},{r["bytes"]},{r["ok"]},{err}\n')

    # ---- aggregates over readable images ----
    w = np.array([r["w"] for r in ok])
    h = np.array([r["h"] for r in ok])
    short = np.minimum(w, h)
    long_ = np.maximum(w, h)
    mp = (w * h) / 1e6
    ar = long_ / np.maximum(short, 1)            # >= 1 by construction
    mb = np.array([r["bytes"] for r in ok]) / 1e6

    summary = {"split": args.split, "total": len(rows),
               "readable": len(ok), "corrupt": len(bad)}

    print("\n" + "=" * 78)
    print(f"OVERVIEW  split={args.split}")
    print("=" * 78)
    print(f"  total images   : {len(rows)}")
    print(f"  readable       : {len(ok)}")
    print(f"  corrupt/failed : {len(bad)}")

    # per-class counts (train only)
    train_ok = [r for r in ok if r["split"] == "train"]
    if train_ok:
        per_cls = Counter(r["class"] for r in train_ok)
        counts = np.array(sorted(per_cls.values()))
        summary["per_class"] = {
            "n_classes": len(per_cls), "min": int(counts.min()),
            "max": int(counts.max()), "mean": float(counts.mean()),
            "median": float(np.median(counts)), "std": float(counts.std()),
            "imbalance_max_over_min": float(counts.max() / max(counts.min(), 1)),
        }
        most = sorted(per_cls.items(), key=lambda kv: -kv[1])[:5]
        least = sorted(per_cls.items(), key=lambda kv: kv[1])[:5]
        print("\n" + "-" * 78)
        print(f"PER-CLASS COUNTS (train, {len(per_cls)} classes)")
        print("-" * 78)
        print(f"  per class: min {counts.min()} | median {int(np.median(counts))} | "
              f"mean {counts.mean():.1f} | max {counts.max()} | std {counts.std():.1f}")
        print(f"  imbalance (max/min): {counts.max() / max(counts.min(), 1):.1f}x")
        print(f"  most  populated: {most}")
        print(f"  least populated: {least}")

    # dimensions
    summary["dims"] = {k: {"min": float(v.min()), "median": float(np.median(v)),
                           "mean": float(v.mean()), "max": float(v.max())}
                       for k, v in {"w": w, "h": h, "short": short,
                                    "long": long_, "megapixels": mp}.items()}
    print("\n" + "-" * 78)
    print("DIMENSIONS (pixels; readable images)")
    print("-" * 78)
    print(describe(w, "width"))
    print(describe(h, "height"))
    print(describe(short, "short side"))
    print(describe(long_, "long side"))
    print(describe(mp, "megapixels"))
    print(describe(ar, "aspect L/S"))
    print(describe(mb, "file MB"))

    # aspect ratio buckets
    summary["aspect"] = {"gt_1_5": int((ar > 1.5).sum()),
                         "gt_2": int((ar > 2).sum()), "gt_3": int((ar > 3).sum())}
    print(f"\n  aspect-ratio tails: >1.5 : {(ar > 1.5).sum()} "
          f"({100 * (ar > 1.5).mean():.1f}%) | >2 : {(ar > 2).sum()} "
          f"({100 * (ar > 2).mean():.1f}%) | >3 : {(ar > 3).sum()} "
          f"({100 * (ar > 3).mean():.1f}%)")

    # *** resolution adequacy vs input size (the section that matters for our discussion) ***
    print("\n" + "-" * 78)
    print("RESOLUTION ADEQUACY  (short side vs candidate --img-size)")
    print("-" * 78)
    print("  'upscaled' = native short side < target -> crop region must be enlarged")
    print(f"  {'target':>7} | {'upscaled':>18} | {'native >= target':>18}")
    summary["resolution_adequacy"] = {}
    for s in TARGET_SIZES:
        up = int((short < s).sum())
        ge = int((short >= s).sum())
        summary["resolution_adequacy"][s] = {"upscaled": up, "native_ge": ge}
        print(f"  {s:>7} | {up:>8} ({100*up/len(short):>5.1f}%) | "
              f"{ge:>8} ({100*ge/len(short):>5.1f}%)")

    # color mode + format
    modes = Counter(r["mode"] for r in ok)
    fmts = Counter(r["format"] for r in ok)
    summary["modes"] = dict(modes)
    summary["formats"] = dict(fmts)
    non_rgb = sum(v for k, v in modes.items() if k != "RGB")
    print("\n" + "-" * 78)
    print("COLOR MODE / FORMAT")
    print("-" * 78)
    print(f"  modes  : {dict(modes.most_common())}")
    print(f"  formats: {dict(fmts.most_common())}")
    print(f"  non-RGB images: {non_rgb} ({100*non_rgb/max(len(ok),1):.2f}%)  "
          f"(converted to RGB at load time)")

    # problem images
    tiny = [r for r in ok if min(r["w"], r["h"]) < 64]
    if bad or tiny:
        print("\n" + "-" * 78)
        print("PROBLEM IMAGES")
        print("-" * 78)
        print(f"  corrupt/unreadable: {len(bad)}")
        for r in bad[:10]:
            print(f"    [{r['split']}/{r['class']}] {r['name']}: {r['error']}")
        if len(bad) > 10:
            print(f"    ... +{len(bad) - 10} more (see CSV)")
        print(f"  very small (<64px short side): {len(tiny)}")
        for r in tiny[:10]:
            print(f"    [{r['split']}/{r['class']}] {r['name']}: {r['w']}x{r['h']}")

    # optional pixel stats
    if args.pixel_stats:
        print("\n" + "-" * 78)
        print(f"PIXEL STATS (random sample of {args.pixel_stats}, decoded)")
        print("-" * 78)
        ps = pixel_stats(ok, args.pixel_stats)
        if ps:
            summary["pixel_stats"] = ps
            print(f"  sampled {ps['sampled']} images")
            print(f"  channel mean (RGB, [0,1]): "
                  f"[{ps['mean'][0]:.4f}, {ps['mean'][1]:.4f}, {ps['mean'][2]:.4f}]")
            print(f"  channel std  (RGB, [0,1]): "
                  f"[{ps['std'][0]:.4f}, {ps['std'][1]:.4f}, {ps['std'][2]:.4f}]")
            print("  (CLIP norm for reference: mean [0.4815,0.4578,0.4082] "
                  "std [0.2686,0.2613,0.2758])")

    json_path = out_dir / f"summary_{args.split}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print("\n" + "=" * 78)
    print(f"per-image CSV : {csv_path}")
    print(f"JSON summary  : {json_path}")
    print("=" * 78)


if __name__ == "__main__":
    main()
