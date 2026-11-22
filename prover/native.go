package main

import (
	"crypto/rand"
	"math/big"

	"github.com/consensys/gnark-crypto/ecc/bn254/fr"
	nh "github.com/consensys/gnark-crypto/ecc/bn254/fr/mimc"
	ed "github.com/consensys/gnark-crypto/ecc/bn254/twistededwards"
)

type boardCT struct{ A, B ed.PointAffine }
type wallet struct {
	sk *big.Int
	pk ed.PointAffine
}
type board struct {
	policy string
	wallet []wallet
	rights bool
	fields int
	ct     []boardCT
	plain  [][2]int
}

func fresh(max *big.Int) *big.Int {
	for {
		v, err := rand.Int(rand.Reader, max)
		if err != nil {
			panic(err)
		}
		if v.Sign() > 0 {
			return v
		}
	}
}
func add(a, b ed.PointAffine) (out ed.PointAffine)          { out.Add(&a, &b); return }
func mul(a ed.PointAffine, s *big.Int) (out ed.PointAffine) { out.ScalarMultiplication(&a, s); return }
func small(v int) ed.PointAffine {
	n := big.NewInt(int64(v))
	n.Mod(n, &params.Order)
	return mul(params.Base, n)
}
func makeKeys(count ...int) []wallet {
	n := 2
	if len(count) > 0 {
		n = count[0]
	}
	ws := make([]wallet, n)
	for i := range ws {
		ws[i].sk = fresh(&params.Order)
		ws[i].pk = mul(params.Base, ws[i].sk)
	}
	return ws
}
func keys(ws []wallet) []ed.PointAffine {
	out := make([]ed.PointAffine, len(ws))
	for i, w := range ws {
		out[i] = w.pk
	}
	return out
}
func encrypted(pk ed.PointAffine, raw int, noise *big.Int) boardCT {
	return boardCT{mul(params.Base, noise), add(mul(pk, noise), small(raw))}
}
func newBoard(rights bool, ws []wallet) board {
	fields := 1
	if rights {
		fields = 2
	}
	s := board{wallet: ws, rights: rights, fields: fields, ct: make([]boardCT, len(ws)*fields), plain: make([][2]int, len(ws))}
	for i := range s.ct {
		w, f := i/fields, i%fields
		if rights && f == 0 {
			s.plain[w][0] = limit
		}
		s.ct[i] = encrypted(ws[w].pk, s.plain[w][f], big.NewInt(0))
	}
	return s
}
func asCT(c boardCT) cipher { return cipher{constant(c.A), constant(c.B)} }
func commitment(vals ...*big.Int) *big.Int {
	h := nh.NewMiMC()
	for _, v := range vals {
		var e fr.Element
		e.SetBigInt(v)
		b := e.Bytes()
		if _, err := h.Write(b[:]); err != nil {
			panic(err)
		}
	}
	return new(big.Int).SetBytes(h.Sum(nil))
}

var rosterSize = 2

type event struct{ mode, w, x, recipient int }

func defaultEvent(mode, w, x int) event { return event{mode, w, x, (w + 1) % rosterSize} }

func sequence(cluster bool) []event {
	hidden := []int{0, 0, 1, 1}
	if cluster {
		hidden = []int{1, 1, 0, 0}
	}
	xs := []int{7, 5, 11, 9, 7, 5, 11, 9}
	out := make([]event, 8)
	for i, x := range xs {
		w := i / 4
		if i%2 == 1 {
			w = hidden[i/2]
		}
		out[i] = defaultEvent(i%2, w, x)
	}
	return out
}
func integer(v int) *big.Int { return big.NewInt(int64(v)) }
func (s *board) prepare(ev event) (*circuit, int) {
	w, x, m := ev.w, ev.x, ev.mode
	v := shape(s.rights, keys(s.wallet))
	v.Policy = s.policy
	recip := ev.recipient
	v.Owner = w
	v.SK = s.wallet[w].sk
	v.Amount = x
	v.Recipient = recip
	v.Private = m
	v.Epoch = 1
	v.VisiblePayer = (1 - m) * (1 + w)
	v.VisibleRecipient = (1 - m) * (1 + recip)
	v.VisibleAmount = (1 - m) * x
	nonce := fresh(fr.Modulus())
	v.Nonce = nonce
	v.Digest = commitment(integer(47201), integer(1), integer(m), integer(w), integer(recip), integer(x), nonce)
	changes := [2]int{}
	move := 0
	v.Other = 0
	if s.rights {
		active := m
		if s.policy == "public_right" {
			active = 1 - m
		}
		if s.policy == "pooled_right" {
			active = 1
		}
		donor := 1 - active
		// Honest full-move policy. On regular payments the donor opening is
		// never supplied to the proof; only the active pool is debited.
		move = s.plain[w][donor]
		v.Active = s.plain[w][active]
		if move > 0 {
			v.Other = move
		}
		changes[active] = move - x
		changes[donor] = -move
	} else {
		v.Active = s.plain[w][0]
		changes[0] = x
	}
	v.Move = move
	for i, old := range s.ct {
		who, f := i/s.fields, i%s.fields
		change := 0
		if w == who {
			change = changes[f]
		}
		noise := fresh(&params.Order)
		inc := encrypted(s.wallet[who].pk, change, noise)
		next := boardCT{add(old.A, inc.A), add(old.B, inc.B)}
		v.Noise[i] = noise
		v.Old[i] = asCT(old)
		v.New[i] = asCT(next)
		s.ct[i] = next
		s.plain[who][f] += change
	}
	return v, move
}
