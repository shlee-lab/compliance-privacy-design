package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"strings"

	"github.com/consensys/gnark-crypto/ecc"
	"github.com/consensys/gnark-crypto/ecc/bn254/fr"
	ed "github.com/consensys/gnark-crypto/ecc/bn254/twistededwards"
	"github.com/consensys/gnark/backend"
	"github.com/consensys/gnark/backend/groth16"
	bn "github.com/consensys/gnark/backend/groth16/bn254"
	"github.com/consensys/gnark/backend/solidity"
	"github.com/consensys/gnark/frontend"
	"github.com/consensys/gnark/frontend/cs/r1cs"
)

type traceRow struct {
	Mode      int      `json:"mode"`
	Ordinary  bool     `json:"ordinary"`
	Payer     int      `json:"payer"`
	Recipient int      `json:"recipient"`
	Amount    int      `json:"amount"`
	Old       []string `json:"old_board"`
	New       []string `json:"new_board"`
	Proof     string   `json:"proof"`
	Input     string   `json:"public_input"`
}
type traceFile struct {
	Architecture string     `json:"architecture"`
	Pairs        []string   `json:"pairs"`
	Enabled      bool       `json:"enabled"`
	Scenario     string     `json:"scenario"`
	Round        int        `json:"round"`
	Genesis      []string   `json:"genesis"`
	Rows         []traceRow `json:"rows"`
}

func encodePoint(p ed.PointAffine) []string {
	bx, by := p.X.Bytes(), p.Y.Bytes()
	return []string{hex.EncodeToString(bx[:]), hex.EncodeToString(by[:])}
}
func words(s []boardCT) []string {
	var out []string
	for _, c := range s {
		out = append(out, encodePoint(c.A)...)
		out = append(out, encodePoint(c.B)...)
	}
	return out
}
func clone(a []boardCT) []boardCT                   { return append([]boardCT(nil), a...) }
func diff(a, b ed.PointAffine) (out ed.PointAffine) { out.Neg(&b); return add(a, out) }
func delta(a, b []boardCT) []boardCT {
	out := make([]boardCT, len(a))
	for i, p := range a {
		out[i] = boardCT{diff(p.A, b[i].A), diff(p.B, b[i].B)}
	}
	return out
}
func apply(a, b []boardCT) []boardCT {
	out := make([]boardCT, len(a))
	for i, p := range a {
		out[i] = boardCT{add(p.A, b[i].A), add(p.B, b[i].B)}
	}
	return out
}

// A bounded baby-step giant-step table depends only on the public generator and cap.
const jump = 1001

type opener struct {
	baby  map[ed.PointAffine]int
	giant ed.PointAffine
}

func newOpener() opener {
	o := opener{baby: make(map[ed.PointAffine]int, jump), giant: small(jump)}
	p := small(0)
	for i := 0; i < jump; i++ {
		o.baby[p] = i
		p = add(p, params.Base)
	}
	return o
}
func (o opener) decrypt(ct boardCT, sk *big.Int) int {
	pt := diff(ct.B, mul(ct.A, sk))
	for i := 0; i*jump <= limit; i++ {
		if j, found := o.baby[pt]; found && i*jump+j <= limit {
			return i*jump + j
		}
		pt = diff(pt, o.giant)
	}
	panic("no valid opening in registered cap")
}

func generate(dir string) error {
	wallets := makeKeys(2)
	relation := shape(false, keys(wallets))
	relation.Policy = "direct"
	system, err := frontend.Compile(ecc.BN254.ScalarField(), r1cs.NewBuilder, relation)
	if err != nil {
		return err
	}
	const measuredConstraints = 11290
	if system.GetNbConstraints() != measuredConstraints {
		return fmt.Errorf("original two-wallet direct relation has %d constraints; compiled %d", measuredConstraints, system.GetNbConstraints())
	}
	pk, vk, err := groth16.Setup(system)
	if err != nil {
		return err
	}
	verifier, err := os.Create(filepath.Join(dir, "direct.sol"))
	if err != nil {
		return err
	}
	err = vk.ExportSolidity(verifier)
	closeErr := verifier.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	genesis := newBoard(false, wallets)
	trace := traceFile{Architecture: "direct", Pairs: []string{"HH"}, Scenario: "bd_a2_10", Genesis: words(genesis.ct)}
	open := newOpener()
	var history [][]boardCT
	// The measured two-wallet schedule bd_a2_10 debits the same payer twice.
	payments := []event{{mode: 1, w: 0, recipient: 1, x: 6147}, {mode: 0, w: 0, recipient: 1, x: 2089}}
	for _, ev := range payments {
		source := clone(genesis.ct)
		for _, increment := range history {
			source = apply(source, increment)
		}
		view := newBoard(false, wallets)
		view.policy = "direct"
		view.ct = clone(source)
		view.plain[ev.w][0] = open.decrypt(source[ev.w], wallets[ev.w].sk)
		assignment, _ := view.prepare(ev)
		witness, err := frontend.NewWitness(assignment, ecc.BN254.ScalarField())
		if err != nil {
			return err
		}
		proof, err := groth16.Prove(system, pk, witness, solidity.WithProverTargetSolidityVerifier(backend.GROTH16))
		if err != nil {
			return err
		}
		public, err := witness.Public()
		if err != nil {
			return err
		}
		if err := groth16.Verify(proof, vk, public, solidity.WithVerifierTargetSolidityVerifier(backend.GROTH16)); err != nil {
			return err
		}
		row := traceRow{Mode: ev.mode, Old: words(source), New: words(view.ct), Proof: hex.EncodeToString(proof.(*bn.Proof).MarshalSolidity())}
		if ev.mode == 0 {
			row.Payer, row.Recipient, row.Amount = ev.w+1, ev.recipient+1, ev.x
		}
		for _, input := range public.Vector().(fr.Vector) {
			word := input.Bytes()
			row.Input += hex.EncodeToString(word[:])
		}
		if len(row.Input) != 22*64 || !strings.HasPrefix(row.Input, strings.Join(row.Old, "")+strings.Join(row.New, "")) {
			return fmt.Errorf("unexpected public input layout")
		}
		history = append(history, delta(view.ct, source))
		trace.Rows = append(trace.Rows, row)
	}
	data, err := json.Marshal(trace)
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, "trace.json"), append(data, '\n'), 0600)
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: fresh-prover OUTPUT_DIRECTORY")
		os.Exit(2)
	}
	if err := generate(os.Args[1]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
