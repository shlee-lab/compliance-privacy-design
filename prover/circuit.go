package main

import (
	"fmt"
	"math/big"

	native "github.com/consensys/gnark-crypto/ecc/bn254/twistededwards"
	curveid "github.com/consensys/gnark-crypto/ecc/twistededwards"
	"github.com/consensys/gnark/frontend"
	ed "github.com/consensys/gnark/std/algebra/native/twistededwards"
	"github.com/consensys/gnark/std/hash/mimc"
)

const limit = 1_000_000
const scalarBits = 251

var params = native.GetEdwardsCurve()

type cipher struct{ A, B ed.Point }
type circuit struct {
	Policy                                                                string            `gnark:"-"`
	Old, New                                                              []cipher          `gnark:",public"`
	Private, VisiblePayer, VisibleAmount, VisibleRecipient, Digest, Epoch frontend.Variable `gnark:",public"`
	Owner, SK, Amount, Recipient, Nonce, Active, Other, Move              frontend.Variable
	Noise                                                                 []frontend.Variable
	Keys                                                                  []native.PointAffine `gnark:"-"`
	Rights                                                                bool                 `gnark:"-"`
}

func shape(rights bool, keys []native.PointAffine) *circuit {
	n := len(keys)
	if rights {
		n *= 2
	}
	return &circuit{Old: make([]cipher, n), New: make([]cipher, n), Noise: make([]frontend.Variable, n), Keys: keys, Rights: rights}
}

func constant(p native.PointAffine) ed.Point {
	return ed.Point{X: p.X.BigInt(new(big.Int)), Y: p.Y.BigInt(new(big.Int))}
}
func identity() ed.Point { return ed.Point{X: 0, Y: 1} }
func sel(api frontend.API, s frontend.Variable, a, b ed.Point) ed.Point {
	return ed.Point{X: api.Select(s, a.X, b.X), Y: api.Select(s, a.Y, b.Y)}
}
func same(api frontend.API, a, b ed.Point) {
	api.AssertIsEqual(a.X, b.X)
	api.AssertIsEqual(a.Y, b.Y)
}
func selectCipher(api frontend.API, s frontend.Variable, a, b cipher) cipher {
	return cipher{sel(api, s, a.A, b.A), sel(api, s, a.B, b.B)}
}

// Fixed-base window tables depend only on public roster keys. The same bit
// decomposition is used for rG and rPK; honest encryption samples r uniformly.
func fixed(api frontend.API, c ed.Curve, base native.PointAffine, bits []frontend.Variable) ed.Point {
	acc := identity()
	for i := 0; i < len(bits); i += 2 {
		var twice, thrice native.PointAffine
		twice.Add(&base, &base)
		thrice.Add(&twice, &base)
		a, b, d := constant(base), constant(twice), constant(thrice)
		var hi frontend.Variable = 0
		if i+1 < len(bits) {
			hi = bits[i+1]
		}
		q := ed.Point{X: api.Lookup2(bits[i], hi, 0, a.X, b.X, d.X), Y: api.Lookup2(bits[i], hi, 1, a.Y, b.Y, d.Y)}
		acc = c.Add(acc, q)
		base.Add(&twice, &twice)
	}
	return acc
}

// Complete double-and-add on the admitted prime-order Edwards subgroup;
// ciphertexts enter the ledger only at genesis or via a verified update.
func productPoint(api frontend.API, c ed.Curve, p ed.Point, bits []frontend.Variable) ed.Point {
	acc := identity()
	for _, b := range bits {
		acc = c.Add(acc, sel(api, b, p, identity()))
		p = c.Double(p)
	}
	return acc
}
func opening(api frontend.API, c ed.Curve, ct cipher, bits, amount []frontend.Variable) ed.Point {
	return c.Add(productPoint(api, c, ct.A, bits), fixed(api, c, params.Base, amount))
}

func (v *circuit) Define(api frontend.API) error {
	c, err := ed.NewEdCurve(api, curveid.BN254)
	if err != nil {
		return err
	}
	api.AssertIsBoolean(v.Private)

	// Roster indices are private. Exactly-one equality selectors constrain
	// sender and recipient membership in this verifier's fixed public roster.
	owners := make([]frontend.Variable, len(v.Keys))
	var ownerTotal, recipientTotal frontend.Variable = 0, 0
	expectedKey := identity()
	for w, key := range v.Keys {
		owners[w] = api.IsZero(api.Sub(v.Owner, w))
		recipient := api.IsZero(api.Sub(v.Recipient, w))
		ownerTotal = api.Add(ownerTotal, owners[w])
		recipientTotal = api.Add(recipientTotal, recipient)
		expectedKey = sel(api, owners[w], constant(key), expectedKey)
	}
	api.AssertIsEqual(ownerTotal, 1)
	api.AssertIsEqual(recipientTotal, 1)
	api.AssertIsEqual(v.Epoch, 1)
	api.AssertIsEqual(v.VisiblePayer, api.Select(v.Private, 0, api.Add(1, v.Owner)))
	api.AssertIsEqual(v.VisibleRecipient, api.Select(v.Private, 0, api.Add(1, v.Recipient)))
	api.AssertIsEqual(v.VisibleAmount, api.Select(v.Private, 0, v.Amount))
	api.AssertIsDifferent(v.Amount, 0)
	xbits := api.ToBinary(v.Amount, 20)
	xG := fixed(api, c, params.Base, xbits)
	skbits := api.ToBinary(v.SK, scalarBits)
	same(api, fixed(api, c, params.Base, skbits), expectedKey)
	hash, err := mimc.NewMiMC(api)
	if err != nil {
		return err
	}
	hash.Write(47201, v.Epoch, v.Private, v.Owner, v.Recipient, v.Amount, v.Nonce)
	api.AssertIsEqual(hash.Sum(), v.Digest)
	fields := len(v.Old) / len(v.Keys)
	field := func(f int) cipher {
		selected := v.Old[f]
		for w := 1; w < len(v.Keys); w++ {
			selected = selectCipher(api, owners[w], v.Old[w*fields+f], selected)
		}
		return selected
	}
	active := field(0)
	var deltas []ed.Point
	abits := api.ToBinary(v.Active, 20)
	if v.Rights {
		var useRight frontend.Variable = v.Private
		if v.Policy == "public_right" {
			useRight = api.Sub(1, v.Private)
		}
		if v.Policy == "pooled_right" {
			useRight = 1
		}
		other := selectCipher(api, useRight, field(0), field(1))
		active = selectCipher(api, useRight, field(1), field(0))
		mv := fixed(api, c, params.Base, api.ToBinary(v.Move, 20))
		dActive := c.Add(mv, c.Neg(xG))
		dOther := c.Neg(mv)
		deltas = []ed.Point{sel(api, useRight, dOther, dActive), sel(api, useRight, dActive, dOther)}
		// Only a nonzero transfer of authority needs the donor opening.
		present := api.Sub(1, api.IsZero(v.Move))
		// A nonzero transfer moves the entire donor pool. From genesis, only one
		// compartment per wallet can therefore carry positive authority.
		api.AssertIsEqual(v.Move, api.Mul(present, v.Other))
		candidate := opening(api, c, other, skbits, api.ToBinary(v.Other, 20))
		same(api, sel(api, present, candidate, other.B), other.B)
		api.ToBinary(api.Mul(present, api.Sub(v.Other, v.Move)), 20)
		api.ToBinary(api.Sub(api.Add(v.Active, v.Move), v.Amount), 20)
	} else {
		api.AssertIsEqual(v.Move, 0)
		api.AssertIsEqual(v.Other, 0)
		api.ToBinary(api.Sub(limit, api.Add(v.Active, v.Amount)), 20)
		deltas = []ed.Point{xG}
	}
	same(api, opening(api, c, active, skbits, abits), active.B)
	if len(deltas) != fields {
		return fmt.Errorf("field count mismatch")
	}
	for i, ct := range v.Old {
		w, f := i/fields, i%fields
		owner := owners[w]
		bits := api.ToBinary(v.Noise[i], scalarBits)
		mask := fixed(api, c, params.Base, bits)
		shared := fixed(api, c, v.Keys[w], bits)
		same(api, c.Add(ct.A, mask), v.New[i].A)
		delta := sel(api, owner, deltas[f], identity())
		same(api, c.Add(c.Add(ct.B, shared), delta), v.New[i].B)
	}
	return nil
}
