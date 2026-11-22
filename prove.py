"""Submit two newly generated proofs to the two-wallet HH quota contract."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from replay import FIXTURE, constructor, local_node


def compile_verifier(source, work):
    compiler = shutil.which("solc") or shutil.which("solcjs")
    if not compiler:
        raise RuntimeError("Solidity 0.8.30 is required")
    version = subprocess.check_output([compiler, "--version"], text=True)
    if "0.8.30+commit.73712a01" not in version:
        raise RuntimeError(f"Unexpected Solidity compiler version: {version.strip()}")
    spec = {
        "language": "Solidity",
        "sources": {"direct.sol": {"content": source}},
        "settings": {
            "evmVersion": "prague",
            "optimizer": {"enabled": True, "runs": 200},
            "outputSelection": {"direct.sol": {"Verifier": ["evm.bytecode.object"]}},
        },
    }
    # solcjs reads standard input synchronously. Use files for both native and JS compilers.
    with (work / "compiler-input.json").open("w+") as src, (work / "compiler-output.json").open("w+") as dst:
        json.dump(spec, src)
        src.seek(0)
        subprocess.run([compiler, "--standard-json"], stdin=src, stdout=dst, check=True)
        dst.seek(0)
        text = dst.read()
    # The JS compiler may print an SMT solver notice on its first line.
    result = json.loads(text[text.index("{"):])
    errors = [e["formattedMessage"] for e in result.get("errors", []) if e["severity"] == "error"]
    if errors:
        raise ValueError("\n".join(errors))
    return result["contracts"]["direct.sol"]["Verifier"]["evm"]["bytecode"]["object"]


def chain_run(node, verifier_bytecode, trace, host_bytecode):
    if trace["pairs"] != ["HH"] or trace["architecture"] != "direct" or trace["enabled"]:
        raise ValueError("Expected the two-wallet direct HH fixture")
    if [row["mode"] for row in trace["rows"]] != [1, 0]:
        raise ValueError("Expected a private payment followed by a public payment")

    wallets = node.rpc("eth_accounts", [])
    relayer = wallets[2]
    vk = node.mine(relayer, verifier_bytecode)["contractAddress"]
    host = node.mine(relayer, host_bytecode + constructor("HH", vk, wallets[:2]))["contractAddress"]

    def keccak(hex_bytes):
        return node.rpc("web3_sha3", ["0x" + hex_bytes])

    def selector(signature):
        return keccak(signature.encode().hex())[:10]

    get_head = selector("currentHead()")
    verify = selector("verifyProof(uint256[8],uint256[22])")

    def check_head(board):
        expected = keccak("".join(board))
        actual = node.rpc("eth_call", [{"to": host, "data": get_head}, "latest"])
        if actual != expected:
            raise ValueError("Contract state differs from the proof's expected state")
        return actual

    board = trace["genesis"]
    head = check_head(board)
    receipts = []
    for row in trace["rows"]:
        successor = row["new_board"]
        proof = bytes.fromhex(row["proof"])
        fields = row["public_input"]
        if (row["ordinary"] or row["old_board"] != board or len(proof) != 256
                or len(board) != 8 or len(successor) != 8 or len(fields) != 22 * 64
                or not fields.startswith("".join(board + successor))):
            raise ValueError("Proof or public inputs do not match the expected quota transition")
        data = verify[2:] + proof.hex() + fields
        receipt = node.mine(relayer, data, host)
        next_head = check_head(successor)
        if head == next_head:
            raise ValueError("Successful payment did not advance the quota state")
        receipts.append({
            "payment": "private" if row["mode"] else "public",
            "proof_sha256": hashlib.sha256(proof).hexdigest(),
            "tx": receipt["transactionHash"],
            "gas": int(receipt["gasUsed"], 16),
            "head_before": head,
            "head_after": next_head,
        })
        head, board = next_head, successor
    return {"route": "HH", "wallets": 2, "verifier": vk, "host": host, "receipts": receipts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        subprocess.run(["fresh-prover", str(work)], check=True)
        verifier = compile_verifier((work / "direct.sol").read_text(), work)
        trace = json.loads((work / "trace.json").read_text())
        with gzip.open(FIXTURE, "rt") as stream:
            host_code = json.load(stream)["2"]["contracts"]["base"]
        with local_node(2) as node:
            result = chain_run(node, verifier, trace, host_code)
    result["verifier_initcode_sha256"] = hashlib.sha256(bytes.fromhex(verifier)).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    for row in result["receipts"]:
        print(f"{row['payment']} payment verified and settled: {row['gas']:,} gas")


if __name__ == "__main__":
    main()
