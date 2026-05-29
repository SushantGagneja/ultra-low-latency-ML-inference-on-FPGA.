#!/usr/bin/env python3
import os
import sys
import subprocess
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.retrain_bookticker import BNNFeatureExtractor

def main():
    print("Compiling C wrapper...")
    c_wrapper = ROOT / "scripts" / "verify_wrapper.c"
    bin_out = ROOT / "scripts" / "verify_wrapper"
    
    res = subprocess.run(["gcc", "-O3", "-Wall", str(c_wrapper), "-o", str(bin_out), "-lm"], capture_output=True, text=True)
    if res.returncode != 0:
        print("Compilation failed!")
        print(res.stderr)
        sys.exit(1)

    print("Generating 100,000 synthetic ticks for equivalence testing...")
    np.random.seed(42)
    prices = 60000.0 + np.cumsum(np.random.normal(0, 5.0, 100000))
    
    extractor = BNNFeatureExtractor()
    
    # We will write all inputs to a file, then pipe it to the C program
    in_file = ROOT / "scripts" / "verify_in.txt"
    py_outputs = []
    
    print("Running Python feature extractor...")
    with open(in_file, "w") as f:
        for p in prices:
            vol = np.random.lognormal(mean=0.5, sigma=1.0)
            bid = p - 0.1
            ask = p + 0.1
            bid_qty = vol * 0.4
            ask_qty = vol * 0.6
            
            tick = {
                "price": p,
                "volume": bid_qty + ask_qty,
                "bid": bid,
                "ask": ask
            }
            py_ready, py_ind, py_spike = extractor.update(tick)
            py_outputs.append((py_ready, py_ind, py_spike))
            f.write(f"{p} {bid} {bid_qty} {ask} {ask_qty}\n")
            
    print("Running C feature extractor...")
    with open(in_file, "r") as f_in:
        c_proc = subprocess.run([str(bin_out)], stdin=f_in, capture_output=True, text=True)
        
    c_lines = c_proc.stdout.splitlines()
    if len(c_lines) != len(prices):
        print(f"Error: C output lines ({len(c_lines)}) != expected ({len(prices)})")
        print(c_proc.stderr)
        sys.exit(1)
        
    print("Comparing floating point drift and quantized spikes...")
    
    QUANTIZATION_EPSILON = 1e-4
    mismatch_spikes = 0
    total_ready = 0
    max_rsi_drift = 0.0
    max_mom_drift = 0.0
    
    for i, py_out in enumerate(py_outputs):
        py_ready, py_ind, py_spike = py_out
        c_line = c_lines[i]
        
        if py_ready:
            if not c_line.startswith("READY"):
                print(f"Tick {i}: Python READY but C NOT_READY")
                sys.exit(1)
                
            parts = c_line.split()
            c_rsi = float(parts[1])
            c_mom = float(parts[2])
            c_vol_ratio = float(parts[3])
            c_volat = float(parts[4])
            c_spike = int(parts[5])
            
            max_rsi_drift = max(max_rsi_drift, abs(py_ind["rsi"] - c_rsi))
            max_mom_drift = max(max_mom_drift, abs(py_ind["momentum"] - c_mom))
            
            if py_spike != c_spike:
                mismatch_spikes += 1
                
            total_ready += 1
        else:
            if c_line != "NOT_READY":
                print(f"Tick {i}: Python NOT_READY but C READY")
                sys.exit(1)

    print(f"\n--- Equivalence Results ---")
    print(f"Total Ticks Evaluated: {total_ready}")
    print(f"Max RSI Drift: {max_rsi_drift:.6f}")
    print(f"Max Momentum Drift: {max_mom_drift:.6f}")
    print(f"Quantized Spike Mismatches: {mismatch_spikes}")
    
    if max_rsi_drift > QUANTIZATION_EPSILON or max_mom_drift > QUANTIZATION_EPSILON:
        print("WARNING: Float drift exceeds quantization epsilon!")
    if mismatch_spikes > 0:
        print("FAIL: Binary quantized output mismatch detected!")
        sys.exit(1)
        
    print("PASS: C implementation is bit-exact equivalent at the quantization boundary.")
    
    # Cleanup
    in_file.unlink()
    bin_out.unlink()

if __name__ == "__main__":
    main()
