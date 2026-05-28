#!/usr/bin/env python3
"""
BNN Retraining with bookTicker Volume Contract
================================================

This script is a minimal fork of `train bnn standalone.py` that closes the
data-contract gap between Phase 1 (synthetic traded volume) and Phase 2
(bookTicker bid_qty + ask_qty as volume proxy).

Key change:
  The synthetic data generator now produces volume values that match the
  distribution of `bid_qty + ask_qty` from the Binance bookTicker stream
  for BTCUSDT, rather than the synthetic ~600-1200 range used previously.

  Typical bookTicker bid_qty + ask_qty for BTCUSDT:
    - Range: ~0.02 to ~80 BTC
    - Median: ~5 BTC
    - Distribution: heavy right tail (occasional large book updates)
    - Tick-to-tick variance: much higher than trade volume

After retraining:
  1. New weights are exported to fpga_weights/
  2. generate_test_vectors.py must be re-run
  3. `make` must pass 100/100
  4. cosim.py should confirm market-derived vectors still match
"""

import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

import sys
import json
import shutil
import subprocess
import numpy as np
import tensorflow as tf
from tensorflow import keras
from pathlib import Path
from typing import Tuple

# ---------------------------------------------------------------------------
# Import the model architecture and utilities from Phase 1
# ---------------------------------------------------------------------------
# We add the parent directory to sys.path so we can import from the
# training script directly.

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# We need the custom layers and activation, so we import them.
# The spaces in the filename require importlib.
import importlib.util
spec = importlib.util.spec_from_file_location(
    "train_bnn", ROOT / "train bnn standalone.py"
)
train_bnn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(train_bnn)

sign_with_ste   = train_bnn.sign_with_ste
BinaryDense     = train_bnn.BinaryDense
BinaryOutputDense = train_bnn.BinaryOutputDense
BipolarQuantizer = train_bnn.BipolarQuantizer
build_model     = train_bnn.build_model
train_model     = train_bnn.train_model
extract_weights = train_bnn.extract_weights
verify_fpga     = train_bnn.verify_fpga
fpga_inference_sim = train_bnn.fpga_inference_sim


# ---------------------------------------------------------------------------
# bookTicker-calibrated synthetic data generator
# ---------------------------------------------------------------------------

def generate_bookticker_data(n_samples: int = 12000) -> Tuple[np.ndarray, np.ndarray]:
    """
    Generate synthetic BTC-style tick data with volume modeled after the
    Binance bookTicker bid_qty + ask_qty distribution.

    Volume model:
      bid_qty ~ LogNormal(mu=1.0, sigma=1.2)  → median ~2.7 BTC
      ask_qty ~ LogNormal(mu=1.0, sigma=1.2)  → median ~2.7 BTC
      volume  = bid_qty + ask_qty             → median ~5.4 BTC
      Range: ~0.02 to ~150 BTC (heavy right tail)

    This matches the empirical distribution of top-of-book liquidity on
    BTCUSDT far better than the previous uniform ~600-1200 range.
    """
    print(f"Generating {n_samples} bookTicker-calibrated samples...")
    quantizer = BipolarQuantizer()
    X, y = [], []

    price    = 50_000.0
    prev_ind = None
    buy_c = sell_c = hold_c = 0

    for i in range(n_samples):
        mom  = np.random.randn() * 0.008
        price = max(100.0, price * (1 + mom))

        rsi  = 50 + 40 * np.sin(i / 80.0) + np.random.randn() * 8
        rsi  = float(np.clip(rsi, 1, 99))

        # bookTicker-calibrated volume: LogNormal produces realistic
        # top-of-book liquidity with a heavy right tail
        bid_qty = float(np.random.lognormal(mean=1.0, sigma=1.2))
        ask_qty = float(np.random.lognormal(mean=1.0, sigma=1.2))
        volume  = bid_qty + ask_qty  # This is what the ESP32 computes

        # Volume ratio uses the same rolling-window logic as temporal_features.c
        # For training, we approximate with a simple ratio to a typical value
        vrat = volume / 5.4  # 5.4 ≈ median of bid_qty + ask_qty

        volt = max(0.005, 0.025 + 0.015 * abs(np.random.randn()))

        ind = {
            'rsi': rsi, 'momentum': mom,
            'volume_ratio': vrat, 'volatility': volt,
            'price': price, 'volume': volume,
            'prev_price': prev_ind['price'] if prev_ind else price
        }

        spike = quantizer.quantize(ind, prev_ind)
        X.append(spike)

        # --- Deterministic, balanced labels ---
        if rsi > 70 and mom > 0.004:
            label = [0, 0, 1];  sell_c += 1          # SELL
        elif rsi < 30 and mom < -0.004:
            label = [1, 0, 0];  buy_c  += 1          # BUY
        else:
            label = [0, 1, 0];  hold_c += 1          # HOLD

        y.append(label)
        prev_ind = {**ind}

    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.float32)

    print(f"  Samples : {n_samples}")
    print(f"  BUY     : {buy_c}  ({100*buy_c/n_samples:.1f}%)")
    print(f"  HOLD    : {hold_c} ({100*hold_c/n_samples:.1f}%)")
    print(f"  SELL    : {sell_c} ({100*sell_c/n_samples:.1f}%)")
    print(f"  Volume range: [{X[:, 4].min():.2f}, {X[:, 4].max():.2f}]  (bit 4 activations)")
    return X, y


def main():
    print("=" * 60)
    print("BNN RETRAINING — bookTicker Volume Contract Calibration")
    print("=" * 60)

    np.random.seed(42)
    tf.random.set_seed(42)

    # --- Data (bookTicker-calibrated) ---
    X, y = generate_bookticker_data(12000)

    n      = len(X)
    tr_end = int(0.70 * n)
    val_end = int(0.85 * n)

    X_train, y_train = X[:tr_end],       y[:tr_end]
    X_val,   y_val   = X[tr_end:val_end], y[tr_end:val_end]
    X_test,  y_test  = X[val_end:],       y[val_end:]

    print(f"\n  Train : {len(X_train)}  Val : {len(X_val)}  Test : {len(X_test)}")

    # --- Model (same architecture) ---
    model   = build_model()
    history = train_model(model, X_train, y_train, X_val, y_val)

    # --- Evaluate ---
    loss, acc = model.evaluate(X_test, y_test, verbose=0)
    print(f"\n{'=' * 60}")
    print(f"  FINAL TEST ACCURACY : {acc*100:.2f}%")
    print(f"  FINAL TEST LOSS     : {loss:.4f}")
    print(f"{'=' * 60}")

    # --- Backup old weights ---
    weights_dir = ROOT / "fpga_weights"
    backup_dir  = ROOT / "fpga_weights_phase1_backup"
    if weights_dir.exists() and not backup_dir.exists():
        shutil.copytree(weights_dir, backup_dir)
        print(f"\n  Backed up original weights to {backup_dir}")

    # --- Extract new weights ---
    w1_bin, w2_bin = extract_weights(model, weights_dir)

    # Also copy weights.mem to project root (where BRAM init expects it)
    shutil.copy2(weights_dir / "weights.mem", ROOT / "weights.mem")

    # --- Verify hardware equivalence ---
    match_rate = verify_fpga(model, X_test, w1_bin, w2_bin)

    # --- Summary ---
    print(f"\n{'=' * 60}")
    print("RETRAINING SUMMARY (bookTicker Volume Contract)")
    print(f"{'=' * 60}")
    print(f"  Test accuracy    : {acc*100:.2f}%")
    print(f"  HW match rate    : {match_rate:.1f}%")
    print(f"  Total parameters : {16*64 + 64*3} bits  ({(16*64+64*3)//8} bytes)")
    print(f"  BRAM usage       : {(16*64+64*3)/1024:.3f} kbits / 32 kbits")

    if match_rate >= 99.0 and acc >= 0.70:
        print("\n  ✅ RETRAINING COMPLETE — Weights calibrated for bookTicker contract")
        print("  Next steps:")
        print("    1. python scripts/generate_test_vectors.py")
        print("    2. make")
        print("    3. python scripts/cosim.py --vectors 500")
    else:
        print("\n  ⚠  Retraining incomplete — check warnings above")

    print("=" * 60)


if __name__ == "__main__":
    main()
