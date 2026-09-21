//go:build relay_local_video_lab

package main

import (
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/common"
)

type labVideoFixture struct {
	Resolution      string `json:"resolution"`
	DurationSeconds int    `json:"duration_seconds"`
	AspectRatio     string `json:"aspect_ratio"`
	Path            string `json:"path"`
	SHA256          string `json:"sha256,omitempty"`
	Width           int    `json:"width,omitempty"`
	Height          int    `json:"height,omitempty"`
	payload         []byte
}

func labFixtureKey(resolution string, duration int, ratio string) string {
	return strings.ToLower(resolution) + "/" + strconv.Itoa(duration) + "/" + ratio
}

func loadLabVideoFixtures(manifestPath string) (map[string]labVideoFixture, error) {
	if !filepath.IsAbs(manifestPath) {
		return nil, errors.New("mock runtime requires an absolute --fixture-manifest")
	}
	manifestBytes, err := readLabPrivateFile(manifestPath, 128*1024)
	if err != nil {
		return nil, err
	}
	var manifest struct {
		SchemaVersion int               `json:"schema_version"`
		Fixtures      []labVideoFixture `json:"fixtures"`
	}
	if common.Unmarshal(manifestBytes, &manifest) != nil || manifest.SchemaVersion != 1 || len(manifest.Fixtures) < 1 || len(manifest.Fixtures) > 1024 {
		return nil, errors.New("invalid mock fixture manifest")
	}
	root, err := filepath.EvalSymlinks(filepath.Dir(manifestPath))
	if err != nil {
		return nil, errors.New("fixture directory is unavailable")
	}
	fixtures := make(map[string]labVideoFixture, len(manifest.Fixtures))
	for _, fixture := range manifest.Fixtures {
		if fixture.DurationSeconds < 1 || fixture.DurationSeconds > 20 || fixture.Path == "" || filepath.IsAbs(fixture.Path) || strings.Contains(fixture.Path, "\\") {
			return nil, errors.New("invalid fixture specification")
		}
		path, err := filepath.EvalSymlinks(filepath.Join(root, fixture.Path))
		if err != nil {
			return nil, errors.New("fixture video does not exist")
		}
		relative, err := filepath.Rel(root, path)
		if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) || filepath.IsAbs(relative) {
			return nil, errors.New("fixture path leaves its dedicated directory")
		}
		payload, err := readLabPrivateFile(path, 64*1024*1024)
		if err != nil {
			return nil, err
		}
		width, height, seconds, err := inspectLabMP4(payload)
		if err != nil {
			return nil, err
		}
		if math.Abs(seconds-float64(fixture.DurationSeconds)) > 0.08 {
			return nil, errors.New("fixture MP4 duration differs from its declared output specification")
		}
		ratio := strings.Split(fixture.AspectRatio, ":")
		if len(ratio) != 2 {
			return nil, errors.New("fixture aspect ratio is invalid")
		}
		numerator, nErr := strconv.Atoi(ratio[0])
		denominator, dErr := strconv.Atoi(ratio[1])
		if nErr != nil || dErr != nil || numerator < 1 || denominator < 1 || numerator > 100 || denominator > 100 ||
			math.Abs(float64(width*denominator-height*numerator)) > float64(denominator*4) {
			return nil, errors.New("fixture MP4 dimensions differ from its declared aspect ratio")
		}
		shortEdge := min(width, height)
		if strings.HasSuffix(fixture.Resolution, "p") {
			pixels, parseErr := strconv.Atoi(strings.TrimSuffix(fixture.Resolution, "p"))
			if parseErr != nil || pixels < 240 || pixels > 4320 || shortEdge != pixels {
				return nil, errors.New("fixture MP4 short edge differs from its declared resolution")
			}
		} else if fixture.Resolution != "2k" {
			return nil, errors.New("fixture resolution is not supported by the lab")
		}
		digest := fmt.Sprintf("%x", sha256.Sum256(payload))
		if fixture.SHA256 != "" && fixture.SHA256 != digest {
			return nil, errors.New("fixture video digest mismatch")
		}
		if (fixture.Width != 0 && fixture.Width != width) || (fixture.Height != 0 && fixture.Height != height) {
			return nil, errors.New("fixture video dimensions do not match the manifest")
		}
		fixture.Width, fixture.Height, fixture.SHA256, fixture.payload = width, height, digest, payload
		key := labFixtureKey(fixture.Resolution, fixture.DurationSeconds, fixture.AspectRatio)
		if _, duplicate := fixtures[key]; duplicate {
			return nil, errors.New("duplicate fixture output specification")
		}
		fixtures[key] = fixture
	}
	return fixtures, nil
}

type labMP4Box struct {
	kind string
	data []byte
}

func labMP4Boxes(payload []byte) ([]labMP4Box, error) {
	boxes := make([]labMP4Box, 0, 8)
	for len(payload) != 0 {
		if len(payload) < 8 || len(boxes) > 10000 {
			return nil, errors.New("fixture contains a truncated MP4 box")
		}
		size, header := uint64(binary.BigEndian.Uint32(payload[:4])), uint64(8)
		if size == 1 {
			if len(payload) < 16 {
				return nil, errors.New("fixture contains a truncated extended MP4 box")
			}
			size, header = binary.BigEndian.Uint64(payload[8:16]), 16
		}
		if size == 0 {
			size = uint64(len(payload))
		}
		if size < header || size > uint64(len(payload)) {
			return nil, errors.New("fixture contains an invalid MP4 box size")
		}
		boxes = append(boxes, labMP4Box{kind: string(payload[4:8]), data: payload[header:size]})
		payload = payload[size:]
	}
	return boxes, nil
}

func inspectLabMP4(payload []byte) (width, height int, seconds float64, err error) {
	boxes, err := labMP4Boxes(payload)
	if err != nil {
		return 0, 0, 0, err
	}
	var movie []byte
	hasType, hasMedia := false, false
	for _, box := range boxes {
		switch box.kind {
		case "ftyp":
			hasType = len(box.data) >= 8
		case "mdat":
			hasMedia = len(box.data) > 16
		case "moov":
			movie = box.data
		}
	}
	if !hasType || !hasMedia || len(movie) == 0 {
		return 0, 0, 0, errors.New("fixture must be a real muxed MP4 with media and movie metadata")
	}
	children, err := labMP4Boxes(movie)
	if err != nil {
		return 0, 0, 0, err
	}
	for _, child := range children {
		if child.kind == "mvhd" {
			seconds, err = labMP4Duration(child.data)
			if err != nil {
				return 0, 0, 0, err
			}
		}
		if child.kind != "trak" {
			continue
		}
		track, err := labMP4Boxes(child.data)
		if err != nil {
			return 0, 0, 0, err
		}
		for _, item := range track {
			if item.kind == "tkhd" && len(item.data) >= 84 {
				w := int(binary.BigEndian.Uint32(item.data[len(item.data)-8:]) >> 16)
				h := int(binary.BigEndian.Uint32(item.data[len(item.data)-4:]) >> 16)
				if w > 0 && h > 0 {
					if width != 0 {
						return 0, 0, 0, errors.New("fixture contains more than one video dimension track")
					}
					width, height = w, h
				}
			}
		}
	}
	if width < 16 || height < 16 || width > 8192 || height > 8192 || seconds <= 0 || seconds > 20 {
		return 0, 0, 0, errors.New("fixture MP4 has unsupported dimensions or duration")
	}
	return width, height, seconds, nil
}

func labMP4Duration(payload []byte) (float64, error) {
	if len(payload) < 20 {
		return 0, errors.New("fixture movie header is truncated")
	}
	var ticks uint64
	var scale uint32
	switch payload[0] {
	case 0:
		scale, ticks = binary.BigEndian.Uint32(payload[12:16]), uint64(binary.BigEndian.Uint32(payload[16:20]))
	case 1:
		if len(payload) < 32 {
			return 0, errors.New("fixture version-1 movie header is truncated")
		}
		scale, ticks = binary.BigEndian.Uint32(payload[20:24]), binary.BigEndian.Uint64(payload[24:32])
	default:
		return 0, errors.New("fixture movie header version is unsupported")
	}
	if scale == 0 || ticks > uint64(scale)*20 {
		return 0, errors.New("fixture movie duration is invalid")
	}
	return float64(ticks) / float64(scale), nil
}

func labFixtureFileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}
