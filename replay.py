"""Mine the frozen synthetic calls on a local Prague EVM and write their receipt gas."""
import argparse
import csv
import gzip
import http.client
import json
import os
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import time
from threading import Thread
from urllib.parse import urlsplit


FIXTURE = Path(__file__).resolve().parent / "data" / "replay.json.gz"
ROUTES = {"HH", "HS", "HR", "SH", "SS", "SR", "RH", "RS", "RR", "MH", "MS", "MR"}


class Node:
    def __init__(self, endpoint):
        self.connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=30)

    def rpc(self, method, params):
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        self.connection.request("POST", "/", request, {"Content-Type": "application/json"})
        response = self.connection.getresponse()
        payload = json.loads(response.read())
        if response.status != 200 or "error" in payload:
            raise RuntimeError(f"{method} failed: {payload}")
        return payload["result"]

    def mine(self, sender, data, target=None):
        tx = {"from": sender, "data": "0x" + data, "gas": hex(10_000_000)}
        if target is not None:
            tx["to"] = target
        tx_hash = self.rpc("eth_sendTransaction", [tx])
        deadline = time.monotonic() + 30
        while True:
            receipt = self.rpc("eth_getTransactionReceipt", [tx_hash])
            if receipt is not None:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"No receipt for {tx_hash}")
            time.sleep(0.002)
        if int(receipt["status"], 16) != 1 or receipt["transactionHash"] != tx_hash:
            raise RuntimeError(f"Failed to mine {tx_hash}")
        return receipt


@contextmanager
def local_node(n):
    # Each receipt is consumed immediately. Older block snapshots are unnecessary.
    cmd = ["anvil", "--prune-history", "1", "--transaction-block-keeper", "2", "--host", "127.0.0.1", "--port", "0", "--hardfork", "prague",
           "--chain-id", "31337", "--timestamp", "1700000000", "--accounts", str(n + 1)]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
        try:
            for line in process.stdout:
                if line.startswith("Listening on "):
                    endpoint = urlsplit("http://" + line.split()[-1])
                    break
            else:
                raise RuntimeError("Anvil exited before opening its local RPC port")
            def drain():
                for line in process.stdout:
                    pass
            reader = Thread(target=drain, daemon=True)
            reader.start()
            node = Node(endpoint)
            try:
                yield node
            finally:
                node.connection.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


def constructor(route, verifier, wallets):
    if route.startswith("M"):
        args = [int(verifier, 16), int(route == "MS"), *(int(w, 16) for w in wallets)]
    else:
        args = [int(verifier, 16), *("HSR".index(rule) for rule in route)]
    return "".join(format(value, "064x") for value in args)


def replay_roster(node, n, fixture, writer):
    accounts = node.rpc("eth_accounts", [])
    wallets, relayer = accounts[:n], accounts[n]
    code = fixture["contracts"]
    verifiers = {}
    seen = set()
    row_count = 0
    for case in fixture["cases"]:
        route, day, verifier = case["route"], case["day"], case["verifier"]
        if (route, day) in seen or route not in ROUTES:
            raise ValueError(f"Unexpected case: {route}, {day}")
        seen.add((route, day))
        if verifier not in verifiers:
            verifiers[verifier] = node.mine(relayer, code[verifier])["contractAddress"]
        args = constructor(route, verifiers[verifier], wallets)
        host = code["mode" if route.startswith("M") else "base"]
        target = node.mine(relayer, host + args)["contractAddress"]
        for payment, op, sender, data, archived_gas in case["events"]:
            receipt = node.mine(accounts[sender], data, target)
            gas = int(receipt["gasUsed"], 16)
            if gas != archived_gas:
                raise ValueError(f"n={n}, {route}, {day}, payment {payment}, {op}: "
                                 f"measured {gas} gas, expected {archived_gas}")
            writer.writerow([n, route, day, payment, op, gas, receipt["transactionHash"]])
            row_count += 1
    if len(seen) != 56 * len(ROUTES) or row_count != 3024:
        raise ValueError(f"Incomplete n={n} run: {len(seen)} cases, {row_count} receipts")
    return row_count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    path = parser.parse_args().output
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as stream:
        fixtures = json.load(stream)
    if set(fixtures) != {"2", "8"}:
        raise ValueError("Missing roster sizes")
    with tempfile.NamedTemporaryFile(mode="w", newline="", dir=path.parent, delete=False) as sink:
        temp = Path(sink.name)
        try:
            writer = csv.writer(sink)
            writer.writerow(["n", "route", "day", "payment", "op", "gas", "tx"])
            for size, fixture in fixtures.items():
                n = int(size)
                print(f"Replaying {len(fixture['cases'])} cases at {n} wallets...", flush=True)
                with local_node(n) as node:
                    count = replay_roster(node, n, fixture, writer)
                print(f"  {count:,} gas receipts matched.", flush=True)
            sink.flush()
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
