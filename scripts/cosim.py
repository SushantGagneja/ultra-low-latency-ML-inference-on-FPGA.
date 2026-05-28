#!/usr/bin/env python3
"""End-to-end market tick to RTL BNN co-simulation."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import subprocess
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SIM_DIR = ROOT / "sim"


@dataclass
class MarketTick:
    price: float
    bid: float
    ask: float
    volume: float


@dataclass
class Indicators:
    rsi: float
    momentum: float
    volume_ratio: float
    volatility: float
    price: float
    volume: float
    prev_price: float


class FeatureState:
    def __init__(self, window: int = 64, rsi_period: int = 14):
        self.window = window
        self.rsi_period = rsi_period
        self.prices: deque[float] = deque(maxlen=window)
        self.volumes: deque[float] = deque(maxlen=window)
        self.returns: deque[float] = deque(maxlen=window)
        self.gains: deque[float] = deque(maxlen=rsi_period)
        self.losses: deque[float] = deque(maxlen=rsi_period)
        self.prev_price: float | None = None

    def update(self, tick: MarketTick) -> Indicators | None:
        price = 0.5 * (tick.bid + tick.ask) if tick.bid > 0 and tick.ask >= tick.bid else tick.price
        if price <= 0 or tick.volume < 0:
            return None

        prev = price if self.prev_price is None else self.prev_price
        delta = price - prev
        ret = ((price - prev) / prev) if self.prev_price is not None and prev > 0 else 0.0

        self.prices.append(price)
        self.volumes.append(tick.volume)
        self.returns.append(ret)
        self.gains.append(max(delta, 0.0))
        self.losses.append(max(-delta, 0.0))
        self.prev_price = price

        if len(self.prices) < self.rsi_period:
            return None

        avg_vol = sum(self.volumes) / len(self.volumes)
        volume_ratio = tick.volume / avg_vol if avg_vol > 0 else 1.0

        mean_ret = sum(self.returns) / len(self.returns)
        var = sum((r - mean_ret) ** 2 for r in self.returns) / len(self.returns)
        volatility = math.sqrt(max(var, 0.0))

        avg_gain = sum(self.gains) / len(self.gains)
        avg_loss = sum(self.losses) / len(self.losses)
        if avg_loss == 0.0 and avg_gain > 0.0:
            rsi = 100.0
        elif avg_loss > 0.0:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))
        else:
            rsi = 50.0

        return Indicators(
            rsi=rsi,
            momentum=ret,
            volume_ratio=volume_ratio,
            volatility=volatility,
            price=price,
            volume=tick.volume,
            prev_price=prev,
        )


class BipolarQuantizer:
    def __init__(self):
        self.prev: Indicators | None = None

    def quantize(self, ind: Indicators) -> int:
        spike = 0
        if ind.rsi > 70.0:
            spike |= 1 << 0
        if ind.rsi > 50.0:
            spike |= 1 << 1

        if abs(ind.momentum) > 0.001:
            if ind.momentum > 0:
                spike |= 1 << 2
            if abs(ind.momentum) > 0.005:
                spike |= 1 << 3

        if ind.volume_ratio > 1.5:
            spike |= 1 << 4
        if ind.volume_ratio > 2.0:
            spike |= 1 << 5
        if ind.volatility > 0.02:
            spike |= 1 << 6
        if ind.volatility > 0.05:
            spike |= 1 << 7

        if self.prev is not None:
            rsi_delta = ind.rsi - self.prev.rsi
            if abs(rsi_delta) > 1.0:
                if rsi_delta > 0:
                    spike |= 1 << 8
                if abs(rsi_delta) > 5.0:
                    spike |= 1 << 9

            accel = (ind.price - self.prev.price) - (self.prev.price - self.prev.prev_price)
            if abs(accel) > 10.0:
                if accel > 0:
                    spike |= 1 << 10
                if abs(accel) > 100.0:
                    spike |= 1 << 11

            if self.prev.volume > 0:
                volume_delta = (ind.volume - self.prev.volume) / self.prev.volume
                if abs(volume_delta) > 0.3:
                    if volume_delta > 0:
                        spike |= 1 << 12
                    if abs(volume_delta) > 0.7:
                        spike |= 1 << 13

            vol_delta = ind.volatility - self.prev.volatility
            if abs(vol_delta) > 0.01:
                if vol_delta > 0:
                    spike |= 1 << 14
                if abs(vol_delta) > 0.03:
                    spike |= 1 << 15

        self.prev = ind
        return spike


def load_weights(path: Path) -> tuple[list[list[int]], list[list[int]]]:
    bits = [
        int(line.strip())
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("//")
    ]
    if len(bits) != 1216:
        raise ValueError(f"Expected 1216 weight bits, got {len(bits)}")

    w1 = [[0 for _ in range(64)] for _ in range(16)]
    for j in range(64):
        for i in range(16):
            w1[i][j] = bits[j * 16 + i]

    w2 = [[0 for _ in range(3)] for _ in range(64)]
    for j in range(3):
        for i in range(64):
            w2[i][j] = bits[1024 + j * 64 + i]
    return w1, w2


def golden_inference(spike: int, w1: list[list[int]], w2: list[list[int]]) -> int:
    x = [(spike >> i) & 1 for i in range(16)]
    hidden = []
    for j in range(64):
        pop = sum(1 - (x[i] ^ w1[i][j]) for i in range(16))
        hidden.append(1 if pop >= 8 else 0)

    scores = []
    for j in range(3):
        scores.append(sum(1 - (hidden[i] ^ w2[i][j]) for i in range(64)))
    return max(range(3), key=lambda idx: scores[idx])


def random_ticks(n: int, seed: int) -> Iterable[MarketTick]:
    rng = random.Random(seed)
    price = 50000.0
    for _ in range(n + 32):
        price = max(100.0, price * (1.0 + rng.gauss(0.0, 0.0008)))
        spread = max(0.1, rng.uniform(0.5, 4.0))
        bid_qty = rng.uniform(0.01, 20.0)
        ask_qty = rng.uniform(0.01, 20.0)
        yield MarketTick(
            price=price,
            bid=price - spread * 0.5,
            ask=price + spread * 0.5,
            volume=bid_qty + ask_qty,
        )


def ticks_from_ndjson(path: Path) -> Iterable[MarketTick]:
    for raw in path.read_text().splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        payload = row.get("payload", row)
        if not isinstance(payload, dict):
            continue

        bid = payload.get("b")
        ask = payload.get("a")
        bid_qty = payload.get("B")
        ask_qty = payload.get("A")
        if bid is None or ask is None:
            continue

        bid_f = float(bid)
        ask_f = float(ask)
        bid_qty_f = float(bid_qty) if bid_qty is not None else 0.0
        ask_qty_f = float(ask_qty) if ask_qty is not None else 0.0
        yield MarketTick(
            price=0.5 * (bid_f + ask_f),
            bid=bid_f,
            ask=ask_f,
            volume=bid_qty_f + ask_qty_f,
        )


def build_spikes(ticks: Iterable[MarketTick], limit: int) -> list[int]:
    features = FeatureState()
    quantizer = BipolarQuantizer()
    spikes: list[int] = []
    for tick in ticks:
        ind = features.update(tick)
        if ind is None:
            continue
        spikes.append(quantizer.quantize(ind))
        if len(spikes) >= limit:
            break
    return spikes


def run_rtl(spikes: list[int]) -> list[tuple[int, int, int, int]]:
    SIM_DIR.mkdir(exist_ok=True)
    input_path = SIM_DIR / "cosim_input.txt"
    output_path = SIM_DIR / "cosim_output.txt"
    bin_path = SIM_DIR / "cosim.vvp"

    input_path.write_text(
        str(len(spikes)) + "\n" + "".join(f"{spike:04x}\n" for spike in spikes),
        encoding="utf-8",
    )

    compile_cmd = [
        "iverilog",
        "-Wall",
        "-g2012",
        "-I",
        "rtl",
        "-I",
        "rtl/testbench",
        "-o",
        str(bin_path),
        "rtl/testbench/cosim_tb.v",
        "rtl/bram_weights.v",
        "rtl/xnor_popcount.v",
        "rtl/bnn_core.v",
    ]
    subprocess.run(compile_cmd, cwd=ROOT, check=True)
    subprocess.run(
        ["vvp", str(bin_path.relative_to(ROOT)), "+INPUT=sim/cosim_input.txt", "+OUTPUT=sim/cosim_output.txt"],
        cwd=ROOT,
        check=True,
    )

    rows = []
    for line in output_path.read_text().splitlines():
        idx_s, spike_s, decision_s, latency_s = line.split(",")
        rows.append((int(idx_s), int(spike_s, 16), int(decision_s), int(latency_s)))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Market-derived BNN RTL co-simulation")
    parser.add_argument("--vectors", type=int, default=500)
    parser.add_argument("--replay", type=Path, help="NDJSON replay file")
    parser.add_argument("--weights", type=Path, default=ROOT / "fpga_weights" / "weights.mem")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    ticks = ticks_from_ndjson(args.replay) if args.replay else random_ticks(args.vectors, args.seed)
    spikes = build_spikes(ticks, args.vectors)
    if not spikes:
        raise RuntimeError("No valid spikes generated")

    w1, w2 = load_weights(args.weights)
    expected = [golden_inference(spike, w1, w2) for spike in spikes]
    rtl_rows = run_rtl(spikes)

    mismatches = []
    latencies = []
    for idx, spike, decision, latency in rtl_rows:
        latencies.append(latency)
        if decision != expected[idx] or spike != spikes[idx]:
            mismatches.append((idx, spike, expected[idx], decision, latency))

    match_rate = 100.0 * (len(spikes) - len(mismatches)) / len(spikes)
    print(f"vectors={len(spikes)} match_rate={match_rate:.2f}% mismatches={len(mismatches)}")
    print(
        "latency_cycles "
        f"min={min(latencies)} mean={statistics.mean(latencies):.2f} "
        f"p99={sorted(latencies)[int(0.99 * (len(latencies) - 1))]} max={max(latencies)}"
    )
    if mismatches:
        print("first mismatches: idx, spike, expected, rtl, latency")
        for row in mismatches[:10]:
            print(row)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
