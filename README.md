# Shared transfer limits

A proof of concept for comparing the gas cost of enforcing one spending limit across public and private transfers. It runs the same payments through twelve contract designs on a local Anvil chain.

With Docker running, execute this command from the repository root.

```sh
./reproduce.sh
```

Open `results/gas-map.pdf` to see which design uses less gas as the share of private payments and the frequency of switching change. The right panel allows the wallet to disclose a mode switch and its settlement amount. Hatching marks patterns for which the gas measurements leave the choice open.

Read each label as public route, then private route. H reconstructs spending from history, S reads an encrypted running total, and R spends a reserved allowance. M settles when the wallet returns to public mode.

| Output in `results/` | Contents |
| --- | --- |
| `regions.csv` | The equations for the edges of the hatched region |
| `crossings.csv` | Predicted and measured crossovers for one payer (A) and a mix of single-payer and interleaved two-payer days (B) |
| `receipts.csv` | Transaction hash and gas used for each replayed quota operation |

The replay uses synthetic payments and precomputed proofs at two and eight wallets. It checks each receipt against the original gas measurement. The map combines those receipts with archived measurements for two through eight wallets. Gas covers limit checks and mode switches. Deployment, token transfers and proof generation are outside the measurement.

## Fresh proofs

```sh
./reproduce.sh --prove
```

This runs the two-wallet HH circuit used in the gas experiment. One wallet makes a private payment followed by a public payment. The script generates fresh Groth16 keys and proofs, deploys the matching verifier on Anvil, and checks the contract's spending record after each payment. The two receipts are written to `results/proof-run.json`. Fresh proof bytes can change calldata gas.
