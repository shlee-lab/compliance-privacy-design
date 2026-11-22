"""Gas regions from differences between matched successful transaction receipts."""

import argparse
import csv
import gzip
import json
from collections import defaultdict
from fractions import Fraction
from functools import lru_cache
from itertools import combinations
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
OPS = ("public", "private", "enter", "return")
BASE = tuple(p + d for p in "HSR" for d in "HSR")
MODE = ("MH", "MS", "MR")
ROUTES = BASE + MODE
ALTERNATIVES = ("MH", "MR")


def alpha_bounds():
    """Outer endpoints of the two survey waves' first-payment-share intervals."""
    waves = json.loads((DATA / "calibration.json").read_text(), parse_float=Fraction)["waves"]
    return min(w["interval"][0] for w in waves.values()), max(w["interval"][1] for w in waves.values())


def paired(rows):
    panel = {}
    for row in rows:
        key = (int(row["n"]), row.get("campaign", "replay"), row["day"], int(row["payment"]), row["op"])
        route, gas, tx = row["route"], int(row["gas"]), row["tx"]
        if row["op"] not in OPS or route not in ROUTES or gas <= 0 or len(tx) != 66 or not tx.startswith("0x"):
            raise ValueError(f"Invalid receipt for {key} {route}")
        outcomes = panel.setdefault(key, {})
        if route in outcomes:
            raise ValueError(f"Duplicate receipt for {key} {route}")
        outcomes[route] = gas
    for key, outcomes in panel.items():
        expected = ROUTES if key[-1] in OPS[:2] else MODE
        if outcomes.keys() != set(expected):
            raise ValueError(f"Incomplete matched operation at {key}")
        if expected == MODE:
            outcomes.update(dict.fromkeys(BASE, 0))  # Per-payment routes have no handover transaction.
    return panel


@lru_cache(maxsize=1)
def calibration():
    with gzip.open(DATA / "calibration-receipts.csv.gz", "rt", newline="") as stream:
        return paired(csv.DictReader(stream))


def original_mean_deltas(n):
    """Measured settlement minus HH gas in each operation of the baseline campaign."""
    samples = defaultdict(list)
    for (roster, campaign, day, payment, op), gas in calibration().items():
        if roster == n and campaign == "baseline":
            for route in MODE:
                samples[route, op].append(gas[route] - gas["HH"])
    def mean(seq):
        return Fraction(sum(seq), len(seq))
    return {r: tuple(mean(samples[r, op]) for op in OPS) for r in MODE}


def operation_bounds(panel):
    groups = defaultdict(list)
    for (n, campaign, day, payment, op), row in panel.items():
        groups[n, op].append(row)
    # Maximize each paired difference on the same debit or handover.
    pairs = ([("HH", r) for r in BASE if r != "HH"] + [("MH", "MS")] +
             [(r, "HH") for r in ALTERNATIVES] + [("HH", r) for r in ALTERNATIVES])
    return {
        n: {pair: tuple(max(row[pair[0]] - row[pair[1]] for row in groups[n, op]) for op in OPS)
            for pair in pairs}
        for n in sorted({n for n, op in groups})
    }


def affine(bounds, alpha):
    """Upper cost gap = c + b*q + k*s, per accepted debit."""
    public, private, enter, back = bounds
    return public, private - public + alpha * enter, (1 - alpha) * (enter + back) / 2


def verify_reduction(bounds, alphas):
    # The domain of (q,s) is a triangle; at each vertex the gap is affine in alpha.
    vertices = ((0, 0), (Fraction(1, 2), 1), (1, 0))
    for roster in bounds.values():
        for pair in [("HH", r) for r in BASE if r != "HH"] + [("MH", "MS")]:
            for alpha in alphas:
                c, b, k = affine(roster[pair], alpha)
                for q, s in vertices:
                    gap = c + b * q + k * s
                    if pair == ("MH", "MS") and (q, s) == (0, 0):
                        if gap == 0:  # The three settlement routes share the all-public path.
                            continue
                    if gap >= 0:
                        raise ValueError(f"Observed receipts do not establish the reduction for {pair}")


def boundary(family, bounds, alphas):
    # Between consecutive line intersections all rankings are constant.
    lines = {}
    for n, roster in bounds.items():
        for r in ALTERNATIVES:
            pair = (r, "HH") if family == "settlement" else ("HH", r)
            for alpha in alphas:
                c, b, k = affine(roster[pair], alpha)
                if (family == "settlement" and b <= 0) or (family == "HH" and b >= 0):
                    raise ValueError(f"Unexpected crossing direction for {pair}")
                lines[n, r, alpha] = (-Fraction(c) / b, -k / b)
    half = Fraction(1, 2)
    edges = {(0, half), (1, -half)}
    cuts = {Fraction(0), Fraction(1)}
    for (a, b), (c, d) in combinations(set(lines.values()) | edges, 2):
        if b != d:
            s = (c - a) / (b - d)
            if 0 < s < 1:
                cuts.add(s)

    spans = []
    below_feasible = []
    ordered = sorted(cuts)
    for left, right in zip(ordered, ordered[1:]):
        s = (left + right) / 2
        def height(key):
            c, m = lines[key]
            return c + m * s
        if family == "HH":
            # HH must beat both alternatives for all rosters and both endpoints.
            winner = max(lines, key=height)
        else:
            # Each roster needs at least one alternative that beats HH at both endpoints.
            best = []
            for n in bounds:
                by_route = [min(((n, r, a) for a in alphas), key=height) for r in ALTERNATIVES]
                best.append(max(by_route, key=height))
            winner = min(best, key=height)
        if s / 2 < height(winner) < 1 - s / 2:
            if spans and spans[-1][2] == winner and spans[-1][1] == left:
                spans[-1][1] = right
            else:
                spans.append([left, right, winner])
        elif height(winner) < s / 2:
            below_feasible.append((left, right))
    for lo, hi, key in spans:
        n, route, alpha = key
        c, m = lines[key]
        yield dict(family=family, s_low=float(lo), s_high=float(hi), intercept=float(c),
                   slope=float(m), roster=n, competitor=route, alpha=float(alpha),
                   q_rule="strictly_below" if family == "settlement" else "strictly_above")
    if family == "HH" and spans:
        tip = spans[-1][1]
        if tip < 1:
            covered = sum((hi - lo for lo, hi in below_feasible if lo >= tip), Fraction(0))
            if covered != 1 - tip:
                raise ValueError("HH does not cover the full remaining feasible region")
            yield dict(family=family, s_low=float(tip), s_high=1.0, intercept="", slope="",
                       roster="", competitor="", alpha="", q_rule="all_feasible_above_s_low")


def selection_regions(live_rows):
    live = paired(live_rows)
    if {key[0] for key in live} != {2, 8}:
        raise ValueError("The binding replay requires receipts from both the 2- and 8-wallet rosters.")
    alphas = alpha_bounds()
    bounds = operation_bounds({**calibration(), **live})
    verify_reduction(bounds, alphas)
    return [row for f in ("settlement", "HH") for row in boundary(f, bounds, alphas)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipts", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with args.receipts.open(newline="") as stream:
        rows = selection_regions(csv.DictReader(stream))
    with args.output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
