//
// (C) Copyright 2022 Intel Corporation.
//
// SPDX-License-Identifier: BSD-2-Clause-Patent
//

package scm

import (
	"encoding/xml"
	"math"
	"testing"

	"github.com/dustin/go-humanize"
	"github.com/google/go-cmp/cmp"
	"github.com/pkg/errors"

	"github.com/daos-stack/daos/src/control/common/test"
	"github.com/daos-stack/daos/src/control/lib/ipmctl"
	"github.com/daos-stack/daos/src/control/logging"
	"github.com/daos-stack/daos/src/control/server/storage"
)

// TestIpmctl_checkIpmctl verified that bad versions trigger an error.
func TestIpmctl_checkIpmctl(t *testing.T) {
	preTxt := "Intel(R) Optane(TM) Persistent Memory Command Line Interface Version "

	for name, tc := range map[string]struct {
		verOut  string
		badVers []semVer
		expErr  error
	}{
		"no bad versions": {
			verOut:  "02.00.00.3816",
			badVers: []semVer{},
		},
		"good version": {
			verOut:  "02.00.00.3825",
			badVers: badIpmctlVers,
		},
		"bad version": {
			verOut:  "02.00.00.3816",
			badVers: badIpmctlVers,
			expErr:  FaultIpmctlBadVersion("02.00.00.3816"),
		},
		"no version": {
			expErr: errors.New("could not read ipmctl version"),
		},
	} {
		t.Run(name, func(t *testing.T) {
			log, buf := logging.NewTestLogger(t.Name())
			defer test.ShowBufferOnFailure(t, buf)

			mockRun := func(_ string) (string, error) {
				return preTxt + tc.verOut, nil
			}

			cr, err := newCmdRunner(log, nil, mockRun, nil)
			if err != nil {
				t.Fatal(err)
			}
			test.CmpErr(t, tc.expErr, cr.checkIpmctl(tc.badVers))
		})
	}
}

const testXMLRegions = `<?xml version="1.0"?>
 <RegionList>
  <Region>
   <SocketID>0x0000</SocketID>
   <PersistentMemoryType>AppDirect</PersistentMemoryType>
   <Capacity>1008.000 GiB</Capacity>
   <FreeCapacity>0.000 GiB</FreeCapacity>
   <HealthState>Healthy</HealthState>
   <DimmID>0x0001, 0x0011, 0x0101, 0x0111, 0x0201, 0x0211, 0x0301, 0x0311</DimmID>
   <RegionID>0x0001</RegionID>
   <ISetID>0xb8c12120c7bd1110</ISetID>
  </Region>
 </RegionList>
`

func mockXMLRegions(t *testing.T, variant string) string {
	t.Helper()

	var rl RegionList
	if err := xml.Unmarshal([]byte(testXMLRegions), &rl); err != nil {
		t.Fatal(err)
	}

	switch variant {
	case "sock-zero", "no-free":
	case "sock-one":
		rl.Regions[0].ID = 2
		rl.Regions[0].SocketID = 1
	case "unhealthy":
		rl.Regions[0].Health = regionHealth(ipmctl.RegionHealthError)
	case "not-interleaved":
		rl.Regions[0].PersistentMemoryType = regionType(ipmctl.RegionTypeNotInterleaved)
	case "unknown-memtype":
		rl.Regions[0].PersistentMemoryType = regionType(math.MaxInt32)
	case "part-free":
		rl.Regions[0].FreeCapacity = rl.Regions[0].Capacity / 2
	case "full-free":
		rl.Regions[0].FreeCapacity = rl.Regions[0].Capacity
	case "dual-sock", "dual-sock-no-free":
		rl.Regions = append(rl.Regions, rl.Regions[0])
		rl.Regions[1].ID = 2
		rl.Regions[1].SocketID = 1
	case "dual-sock-full-free":
		rl.Regions[0].FreeCapacity = rl.Regions[0].Capacity
		rl.Regions = append(rl.Regions, rl.Regions[0])
		rl.Regions[1].ID = 2
		rl.Regions[1].SocketID = 1
	case "same-sock":
		rl.Regions = append(rl.Regions, rl.Regions[0])
	case "unhealthy-2nd-sock":
		rl.Regions = append(rl.Regions, rl.Regions[0])
		rl.Regions[1].ID = 2
		rl.Regions[1].SocketID = 1
		rl.Regions[1].Health = regionHealth(ipmctl.RegionHealthError)
	case "full-free-2nd-sock":
		rl.Regions = append(rl.Regions, rl.Regions[0])
		rl.Regions[1].ID = 2
		rl.Regions[1].SocketID = 1
		rl.Regions[1].FreeCapacity = rl.Regions[1].Capacity
	default:
		t.Fatalf("unknown variant %q", variant)
	}

	out, err := xml.Marshal(&rl)
	if err != nil {
		t.Fatal(err)
	}

	return string(out)
}

func TestIpmctl_getRegions(t *testing.T) {
	expRegionMap := socketRegionMap{
		0: {
			XMLName: xml.Name{
				Local: "Region",
			},
			ID:                   1,
			SocketID:             0,
			PersistentMemoryType: regionType(ipmctl.RegionTypeAppDirect),
			Capacity:             humanize.GiByte * 1008,
			FreeCapacity:         0,
			Health:               regionHealth(ipmctl.RegionHealthNormal),
		},
		1: {
			XMLName: xml.Name{
				Local: "Region",
			},
			ID:                   2,
			SocketID:             1,
			PersistentMemoryType: regionType(ipmctl.RegionTypeAppDirect),
			Capacity:             humanize.GiByte * 1008,
			FreeCapacity:         0,
			Health:               regionHealth(ipmctl.RegionHealthNormal),
		},
	}

	for name, tc := range map[string]struct {
		cmdOut    string
		cmdErr    error
		expErr    error
		expMapErr error
		expResult socketRegionMap
	}{
		"invalid xml": {
			cmdOut: `text that is invalid xml`,
			expErr: errors.New("parse show region cmd"),
		},
		"no permissions": {
			cmdOut: outNoCLIPerms,
			expErr: errors.New("insufficient permissions"),
		},
		"no modules": {
			cmdOut: outNoPMemModules,
			expErr: errNoPMemModules,
		},
		"no regions": {
			cmdOut:    outNoPMemRegions,
			expResult: socketRegionMap{},
		},
		"two regions; one per socket": {
			cmdOut:    mockXMLRegions(t, "dual-sock"),
			expResult: expRegionMap,
		},
		"two regions; same socket": {
			cmdOut:    mockXMLRegions(t, "same-sock"),
			expMapErr: errors.New("multiple regions"),
		},
		"two regions; socket 0 selected": {
			cmdOut: mockXMLRegions(t, "sock-zero"),
			expResult: socketRegionMap{
				0: expRegionMap[0],
			},
		},
		"two regions; socket 1 selected": {
			cmdOut: mockXMLRegions(t, "sock-one"),
			expResult: socketRegionMap{
				1: expRegionMap[1],
			},
		},
	} {
		t.Run(name, func(t *testing.T) {
			log, buf := logging.NewTestLogger(t.Name())
			defer test.ShowBufferOnFailure(t, buf)

			mockRun := func(inCmd string) (string, error) {
				if inCmd == cmdShowIpmctlVersion {
					return verStr, nil
				}
				return tc.cmdOut, tc.cmdErr
			}

			cr, err := newCmdRunner(log, nil, mockRun, nil)
			if err != nil {
				t.Fatal(err)
			}

			gotRegions, gotErr := cr.getRegions(sockAny)
			test.CmpErr(t, tc.expErr, gotErr)
			if tc.expErr != nil {
				return
			}

			gotRegionMap, gotMapErr := mapRegionsToSocket(gotRegions)
			test.CmpErr(t, tc.expMapErr, gotMapErr)
			if tc.expMapErr != nil {
				return
			}

			if diff := cmp.Diff(tc.expResult, gotRegionMap); diff != "" {
				t.Errorf("unexpected result of xml parsing (-want, +got):\n%s\n", diff)
			}
		})
	}
}

func TestIpmctl_getRegionState(t *testing.T) {
	for name, tc := range map[string]struct {
		region   Region
		expState storage.ScmState
	}{
		"not interleaved": {
			region: Region{
				PersistentMemoryType: regionType(ipmctl.RegionTypeNotInterleaved),
			},
			expState: storage.ScmNotInterleaved,
		},
		"unknown memory type": {
			region:   Region{},
			expState: storage.ScmUnknownMode,
		},
		"unhealthy": {
			region: Region{
				PersistentMemoryType: regionType(ipmctl.RegionTypeAppDirect),
				Health:               regionHealth(ipmctl.RegionHealthError),
			},
			expState: storage.ScmNotHealthy,
		},
		"full free capacity": {
			region: Region{
				PersistentMemoryType: regionType(ipmctl.RegionTypeAppDirect),
				Capacity:             regionCapacity(humanize.TiByte * 2),
				FreeCapacity:         regionCapacity(humanize.TiByte * 2),
			},
			expState: storage.ScmFreeCap,
		},
		"no free capacity": {
			region: Region{
				PersistentMemoryType: regionType(ipmctl.RegionTypeAppDirect),
				Capacity:             regionCapacity(humanize.TiByte * 2),
				FreeCapacity:         regionCapacity(0),
			},
			expState: storage.ScmNoFreeCap,
		},
		"partial free capacity": {
			region: Region{
				PersistentMemoryType: regionType(ipmctl.RegionTypeAppDirect),
				Capacity:             regionCapacity(humanize.TiByte * 2),
				FreeCapacity:         regionCapacity(humanize.TiByte),
			},
			expState: storage.ScmPartFreeCap,
		},
	} {
		t.Run(name, func(t *testing.T) {
			if diff := cmp.Diff(tc.expState, getRegionState(tc.region)); diff != "" {
				t.Errorf("unexpected result of xml parsing (-want, +got):\n%s\n", diff)
			}
		})
	}
}

// TestIpmctl_getPMemState verifies the appropriate PMem state is returned for either a specific
// socket region or all regions when either a specific socket is requested or a state is specific to
// a particular socket.
func TestIpmctl_getPMemState(t *testing.T) {
	for name, tc := range map[string]struct {
		runOut   []string
		runErr   []error
		expErr   error
		expState storage.ScmState
		expSock0 bool
		expSock1 bool
	}{
		"single region with uncorrectable error": {
			runOut: []string{
				verStr, mockXMLRegions(t, "unhealthy"),
			},
			expSock0: true,
			expState: storage.ScmNotHealthy,
		},
		"single region with free capacity": {
			runOut: []string{
				verStr, mockXMLRegions(t, "full-free"),
			},
			expSock0: true,
			expState: storage.ScmFreeCap,
		},
		"single region with no free capacity": {
			runOut: []string{
				verStr, mockXMLRegions(t, "no-free"),
			},
			expSock0: true,
			expState: storage.ScmNoFreeCap,
		},
		"second region has uncorrectable error": {
			runOut: []string{
				verStr, mockXMLRegions(t, "unhealthy-2nd-sock"),
			},
			expSock1: true,
			expState: storage.ScmNotHealthy,
		},
		"second region has free capacity": {
			runOut: []string{
				verStr, mockXMLRegions(t, "full-free-2nd-sock"),
			},
			expState: storage.ScmFreeCap,
		},
		"two regions with no free capacity": {
			runOut: []string{
				verStr, mockXMLRegions(t, "dual-sock"),
			},
			expState: storage.ScmNoFreeCap,
		},
	} {
		t.Run(name, func(t *testing.T) {
			log, buf := logging.NewTestLogger(t.Name())
			defer test.ShowBufferOnFailure(t, buf)

			callIdx := 0

			mockRun := func(in string) (string, error) {
				out := ""
				if len(tc.runOut) > callIdx {
					out = tc.runOut[callIdx]
				}

				var err error = nil
				if len(tc.runErr) > callIdx {
					err = tc.runErr[callIdx]
				}

				callIdx++

				return out, err
			}

			cr, err := newCmdRunner(log, nil, mockRun, nil)
			if err != nil {
				t.Fatal(err)
			}

			regions, err := cr.getRegions(sockAny)
			if err != nil {
				t.Fatal(err)
			}

			resp, err := getPMemState(log, regions)
			test.CmpErr(t, tc.expErr, err)
			if tc.expErr != nil {
				return
			}

			expResp := &storage.ScmSocketState{
				State: tc.expState,
			}

			if tc.expSock0 {
				s := uint(0)
				expResp.SocketID = &s
			} else if tc.expSock1 {
				s := uint(1)
				expResp.SocketID = &s
			}

			t.Logf("socket state: %+v", expResp)

			if diff := cmp.Diff(expResp, resp); diff != "" {
				t.Fatalf("unexpected scm state (-want, +got):\n%s\n", diff)
			}
		})
	}
}
