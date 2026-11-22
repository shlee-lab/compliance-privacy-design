#!/bin/sh
set -eu

case "$#:${1:-}" in
    0:) mode=gas ;;
    1:--prove) mode=prove ;;
    *) echo 'Usage: ./reproduce.sh [--prove]' >&2; exit 2 ;;
esac

cd "$(dirname "$0")"
run_dir=$(mktemp -d)
trap 'rm -rf "$run_dir"' EXIT
export COPYFILE_DISABLE=1
if [ "$mode" = prove ]; then
    tar --format ustar -cf "$run_dir/build.tar" Dockerfile.prove \
        prover/go.mod prover/go.sum prover/circuit.go prover/native.go prover/main.go \
        prover/solc/package.json prover/solc/package-lock.json
    docker build --iidfile "$run_dir/image" -f Dockerfile.prove - < "$run_dir/build.tar"
    tar --format ustar -cf "$run_dir/input.tar" prove.py replay.py data/replay.json.gz
else
    docker build --iidfile "$run_dir/image" - < Dockerfile
    tar --format ustar -cf "$run_dir/input.tar" \
        replay.py regions.py crossings.py plot.py \
        data/replay.json.gz data/calibration-receipts.csv.gz data/calibration.json
fi

# Pass only the experiment through stdin. No host directories are mounted.
docker run --rm --init -i --network none --read-only --cap-drop ALL \
    --user 65534:65534 --tmpfs /tmp:rw,mode=1777 \
    "$(cat "$run_dir/image")" sh -eu -c '
        cd /tmp
        mkdir inputs outputs
        cd inputs
        tar -xf - --no-same-owner
        if [ "$1" = prove ]; then
            python prove.py ../outputs/proof-run.json >&2
        else
            python replay.py ../outputs/receipts.csv >&2
            python regions.py ../outputs/receipts.csv ../outputs/regions.csv >&2
            python crossings.py ../outputs/receipts.csv ../outputs/crossings.csv >&2
            python plot.py ../outputs/regions.csv ../outputs/gas-map.pdf >&2
        fi
        cd ../outputs
        tar -cf - ./*
    ' sh "$mode" < "$run_dir/input.tar" > "$run_dir/output.tar"
mkdir -p results
tar --no-same-owner -xf "$run_dir/output.tar" -C results
if [ "$mode" = prove ]; then
    echo 'Wrote results/proof-run.json.'
else
    echo 'Wrote results/gas-map.pdf, regions.csv, crossings.csv and receipts.csv.'
fi
