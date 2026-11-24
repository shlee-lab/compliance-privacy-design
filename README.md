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

## A two-wallet gas boundary

The first row of `results/regions.csv` compares MR (SV in the figure) with HH. To check its inputs in `results/receipts.csv`, filter on `n=2` and the day, zero-based payment index and operation below. HH has no entry or return transaction.

| Day | Payment | Operation | MR gas | HH gas | Difference |
| --- | ---: | --- | ---: | ---: | ---: |
| bd_a3_010 | 0 | public | 48,880 | 367,272 | −318,392 |
| bd_a2_11 | 0 | private | 373,393 | 367,224 | 6,169 |
| bd_b5_01001 | 4 | enter | 395,928 | 0 | 395,928 |
| bd_b5_01100 | 3 | return | 398,975 | 0 | 398,975 |

With first-payment share 0.41054327, these differences give `q < 0.65364 - 0.48096s` on the first boundary segment. Here `q` is the private payment share and `s` the share of adjacent same-wallet payments that switch routes. The segment ends at `s = 0.12053` (rounded).

## Fresh proofs

```sh
./reproduce.sh --prove
```

This runs the two-wallet HH circuit used in the gas experiment. One wallet makes a private payment followed by a public payment. The script generates fresh Groth16 keys and proofs, deploys the matching verifier on Anvil, and checks the contract's spending record after each payment. The two receipts are written to `results/proof-run.json`. Fresh proof bytes can change calldata gas.
