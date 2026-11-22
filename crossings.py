"""Compute the gas crossover for two distributions over payment schedules."""

import argparse
import csv
from collections import defaultdict
from fractions import Fraction as F
from itertools import product
from math import comb
from pathlib import Path

ROUTES = tuple(a + b for a in "HSRM" for b in "HSR")
SETTLEMENT = ROUTES[-3:]

# Wallet numbers here are local to each schedule. Only payer equality matters.
PAYERS = {"a2": (0, 0), "a3": (0, 0, 0), "b2": (0, 0),
          "b3": (0, 0, 0), "b5": (0, 1, 0, 1, 0)}


def load_days(path):
    rows = defaultdict(dict)
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            key = (int(row["n"]), row["route"], row["day"])
            op = (int(row["payment"]), row["op"])
            gas = int(row["gas"])
            if op in rows[key] or gas <= 0:
                raise ValueError(f"Invalid receipt for {key}, {op}")
            rows[key][op] = gas

    days = defaultdict(list)
    for schedule, payers in PAYERS.items():
        for modes in product((0, 1), repeat=len(payers)):
            name = "bd_" + schedule + "_" + "".join(map(str, modes))
            debits = set()
            switches = set()
            last = {}
            for i, (payer, mode) in enumerate(zip(payers, modes)):
                debits.add((i, "private" if mode else "public"))
                if mode != last.get(payer, 0):
                    switches.add((i, "enter" if mode else "return"))
                last[payer] = mode
            for n in (2, 8):
                costs = []
                for route in ROUTES:
                    receipts = rows.pop((n, route, name))
                    expected = debits | switches if route in SETTLEMENT else debits
                    if set(receipts) != expected:
                        raise ValueError(f"Unexpected operations for {n}, {route}, {name}")
                    costs.append(sum(receipts.values()))
                days[n, schedule[0]].append((schedule, modes, costs))
    if rows:
        raise ValueError(f"Unexpected replay cases: {len(rows)}")
    return days


def schedule_weights(alpha):
    # Divide day mass by E[debits/day] to obtain gas per accepted debit.
    a3 = 1 / alpha - 2
    b3 = 3 / alpha - 7
    if not (0 <= a3 <= 1 and 0 <= b3 <= 1):
        raise ValueError("Demand is outside the support of these schedules")
    return {"a2": (1 - a3) * alpha, "a3": a3 * alpha,
            "b2": (1 - b3) * alpha / 3, "b3": b3 * alpha / 3,
            "b5": alpha / 3}


def path_probability(payers, modes, q, s):
    # First payment is stationary. Subsequent switches depend on this payer's last mode.
    stationary = (1 - q, q)
    previous = {}
    mass = F(1)
    for payer, mode in zip(payers, modes):
        if payer in previous:
            switch = s / (2 * stationary[previous[payer]])
            mass *= switch if mode != previous[payer] else 1 - switch
        else:
            mass *= stationary[mode]
        previous[payer] = mode
    return mass


def quartics(days, alpha, s):
    """Bernstein coefficients of q(1-q) times each route's expected gas.

    A payer has at most three debits. Its joint path probability with three
    debits is the product of two consecutive-pair probabilities divided by
    the middle debit's stationary probability. Any second payer has at most
    two debits. Multiplying by q(1-q) thus gives a polynomial of degree <= 4.
    """
    weights = schedule_weights(alpha)
    matrix = []
    for i in range(5):
        t = F(i, 4)
        q = s / 2 + t * (1 - s)
        costs = [F(0)] * len(ROUTES)
        expected_debits = F(0)
        for schedule, modes, gas in days:
            weight = weights[schedule] * path_probability(PAYERS[schedule], modes, q, s)
            expected_debits += weight * len(modes)
            for j, value in enumerate(gas):
                costs[j] += weight * value
        if expected_debits != 1:
            raise ValueError("Schedule probabilities do not normalize per debit")
        basis = [comb(4, j) * t**j * (1 - t)**(4 - j) for j in range(5)]
        matrix.append(basis + [q * (1 - q) * x for x in costs])

    # Solve the interpolation system exactly at five distinct q values.
    for i in range(5):
        pivot = matrix[i][i]
        matrix[i] = [v / pivot for v in matrix[i]]
        for j in range(5):
            if j != i:
                multiple = matrix[j][i]
                matrix[j] = [v - multiple * x for v, x in zip(matrix[j], matrix[i])]
    return {r: [row[5 + i] for row in matrix] for i, r in enumerate(ROUTES)}


def single_crossing(coefficients, lo, hi):
    if min(coefficients) > 0:
        return None
    signs = [x > 0 for x in coefficients if x]
    if coefficients[0] >= 0 or coefficients[-1] <= 0 or signs != sorted(signs):
        raise ValueError("Could not certify a single settlement crossover")
    # One Bernstein sign change bounds the number of roots by one.
    # Subdivide at the midpoint using de Casteljau's algorithm.
    for _ in range(48):
        mid = (lo + hi) / 2
        levels = [coefficients]
        while len(levels[-1]) > 1:
            line = levels[-1]
            levels.append([(a + b) / 2 for a, b in zip(line, line[1:])])
        value = levels[-1][0]
        if value == 0:
            return mid
        if value > 0:
            hi, coefficients = mid, [line[0] for line in levels]
        else:
            lo, coefficients = mid, [line[-1] for line in reversed(levels)]
    return (lo + hi) / 2


def observed(days, alpha, s):
    curves = quartics(days, alpha, s)
    crossings = []
    for route in ROUTES:
        if route == "HH":
            continue
        excess = [v - h for v, h in zip(curves[route], curves["HH"])]
        if route in SETTLEMENT:
            root = single_crossing(excess, s / 2, 1 - s / 2)
            if root is not None:
                crossings.append(root)
        elif min(excess) <= 0:
            raise ValueError(f"Could not certify HH dominance over {route}")
    # HH wins when it beats every settlement candidate.
    return max(crossings)


def predicted(means, alpha, s):
    roots = []
    for public, private, enter, leave in means.values():
        repeated_switch = (1 - alpha) * s / 2
        intercept = public + repeated_switch * (enter + leave)
        slope = private - public + alpha * enter
        if slope <= 0:
            raise ValueError("Mean cost does not increase with private share")
        roots.append(-intercept / slope)
    return max(roots)


def write_crossings(receipts, output):
    from regions import alpha_bounds, original_mean_deltas

    days = load_days(receipts)
    low, high = alpha_bounds()
    with output.open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("switch_rate", "wallets", "debits_per_active_wallet",
                         "predicted_q", "schedule_a_q", "schedule_b_q"))
        for s, n, alpha in ((F(1, 5), 2, high), (F(1, 5), 8, low),
                            (F(11, 20), 2, low), (F(11, 20), 8, high)):
            roots = [predicted(original_mean_deltas(n), alpha, s)]
            roots.extend(observed(days[n, tag], alpha, s) for tag in "ab")
            writer.writerow((f"{float(s):.2f}", n, f"{float(1 / alpha):.2f}",
                             *(f"{float(x):.12f}" for x in roots)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipts", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_crossings(args.receipts, args.output)
