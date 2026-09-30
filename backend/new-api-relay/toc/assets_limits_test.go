package toc

import (
	"bytes"
	"errors"
	"image"
	"image/color"
	"image/png"
	"io"
	"os"
	"strings"
	"sync"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestTOCAssetRetainedCapacityIncludesDeletedAndPreservesReplay(t *testing.T) {
	s, _ := fixture(t)
	var raw bytes.Buffer
	require.NoError(t, png.Encode(&raw, image.NewRGBA(image.Rect(0, 0, 2, 2))))
	key := uuid.NewString()
	first, err := s.Upload(2, key, "reference.png", "image", "", raw.Bytes())
	require.NoError(t, err)
	s.Assets.retainedByteLimit = first.SizeBytes
	replay, err := s.Upload(2, key, "reference.png", "image", "", raw.Bytes())
	require.NoError(t, err)
	require.Equal(t, first.ID, replay.ID)
	_, err = s.Upload(2, key, "changed.png", "image", "", raw.Bytes())
	var apiErr *Error
	require.ErrorAs(t, err, &apiErr)
	require.Equal(t, "IDEMPOTENCY_KEY_REUSED", apiErr.Code)
	// The remaining positive POINT balance is irrelevant to the storage cap.
	_, err = s.Credit(1, 2, CreditRequest{AmountPoints: 100, IdempotencyKey: uuid.NewString(), Note: "storage is not a POINT purchase"})
	require.NoError(t, err)
	_, err = s.Upload(2, uuid.NewString(), "full.png", "image", "", raw.Bytes())
	require.ErrorAs(t, err, &apiErr)
	require.Equal(t, "TOC_ASSET_STORAGE_LIMIT", apiErr.Code)
	// Soft deletion retains the same physical file and must not make capacity.
	require.NoError(t, s.DB.Model(&Asset{}).Where("id = ?", first.ID).Update("deleted", true).Error)
	path, err := s.Assets.path(first.ID)
	require.NoError(t, err)
	stat, err := os.Stat(path)
	require.NoError(t, err)
	require.Equal(t, first.SizeBytes, stat.Size())
	_, err = s.Upload(2, key, "reference.png", "image", "", raw.Bytes())
	require.ErrorAs(t, err, &apiErr)
	require.Equal(t, "TOC_ASSET_DELETED", apiErr.Code)
	_, err = s.Upload(2, uuid.NewString(), "retry.png", "image", "", raw.Bytes())
	require.ErrorAs(t, err, &apiErr)
	require.Equal(t, "TOC_ASSET_STORAGE_LIMIT", apiErr.Code)
	other, err := s.Upload(3, uuid.NewString(), "other.png", "image", "", raw.Bytes())
	require.NoError(t, err, "each user's retained storage is independent, including a zero-POINT user")
	require.NotEqual(t, first.ID, other.ID)
	var count int64
	require.NoError(t, s.DB.Model(&Asset{}).Count(&count).Error)
	require.EqualValues(t, 2, count)
	files, err := os.ReadDir(s.Assets.Root)
	require.NoError(t, err)
	require.Len(t, files, 2, "rejected uploads never write extra files")
}

func TestTOCAssetCapacityRejectsNormalizedBytesBeforeFileWrite(t *testing.T) {
	s, _ := fixture(t)
	var raw bytes.Buffer
	require.NoError(t, png.Encode(&raw, image.NewRGBA(image.Rect(0, 0, 2, 2))))
	s.Assets.retainedByteLimit = 1
	_, err := s.Upload(2, uuid.NewString(), "large-for-quota.png", "image", "", raw.Bytes())
	var apiErr *Error
	require.ErrorAs(t, err, &apiErr)
	require.Equal(t, "TOC_ASSET_STORAGE_LIMIT", apiErr.Code)
	var count int64
	require.NoError(t, s.DB.Model(&Asset{}).Count(&count).Error)
	require.Zero(t, count)
	files, err := os.ReadDir(s.Assets.Root)
	require.NoError(t, err)
	require.Empty(t, files)
}

type assetDecodeGate struct {
	started chan struct{}
	release chan struct{}
}

var assetDecodeTestRegistry sync.Map
var registerAssetDecodeTestFormat sync.Once

func assetDecodeGatedInput(t *testing.T) ([]byte, *assetDecodeGate) {
	t.Helper()
	registerAssetDecodeTestFormat.Do(func() {
		// This decoder exists only in the test binary. It pauses a real Upload
		// exactly at image.Decode without timing sleeps or production hooks.
		image.RegisterFormat("toc-concurrency-test", "TOCGATE", func(reader io.Reader) (image.Image, error) {
			raw, err := io.ReadAll(reader)
			if err != nil {
				return nil, err
			}
			entry, ok := assetDecodeTestRegistry.Load(string(raw))
			if !ok {
				return nil, errors.New("unknown isolated decode gate")
			}
			gate := entry.(*assetDecodeGate)
			gate.started <- struct{}{}
			<-gate.release
			return image.NewRGBA(image.Rect(0, 0, 2, 2)), nil
		}, func(io.Reader) (image.Config, error) {
			return image.Config{ColorModel: color.RGBAModel, Width: 2, Height: 2}, nil
		})
	})
	raw := []byte("TOCGATE" + uuid.NewString())
	gate := &assetDecodeGate{started: make(chan struct{}, 1), release: make(chan struct{})}
	assetDecodeTestRegistry.Store(string(raw), gate)
	t.Cleanup(func() { assetDecodeTestRegistry.Delete(string(raw)) })
	return raw, gate
}

func TestTOCAssetDecodeConcurrencyRejectsBeforeDecodeAndReleasesSlots(t *testing.T) {
	s, _ := fixture(t)
	var pngBytes bytes.Buffer
	require.NoError(t, png.Encode(&pngBytes, image.NewRGBA(image.Rect(0, 0, 2, 2))))
	oldKey := uuid.NewString()
	old, err := s.Upload(2, oldKey, "prior.png", "image", "", pngBytes.Bytes())
	require.NoError(t, err)
	firstRaw, firstGate := assetDecodeGatedInput(t)
	secondRaw, secondGate := assetDecodeGatedInput(t)
	firstKey, secondKey := uuid.NewString(), uuid.NewString()
	results := make(chan error, 2)
	var uploads sync.WaitGroup
	var released sync.Once
	release := func() { released.Do(func() { close(firstGate.release); close(secondGate.release) }) }
	t.Cleanup(func() { release(); uploads.Wait() })
	uploads.Add(1)
	go func() {
		defer uploads.Done()
		_, err := s.Upload(2, firstKey, "first.png", "image", "", firstRaw)
		results <- err
	}()
	select {
	case <-firstGate.started:
	case err := <-results:
		require.NoError(t, err)
		t.Fatal("first upload skipped the registered image decoder")
	}
	_, err = s.Upload(2, uuid.NewString(), "same-user.png", "image", "", pngBytes.Bytes())
	var apiErr *Error
	require.ErrorAs(t, err, &apiErr)
	require.Equal(t, "TOC_ASSET_UPLOAD_BUSY", apiErr.Code)
	uploads.Add(1)
	go func() {
		defer uploads.Done()
		_, err := s.Upload(3, secondKey, "second.png", "image", "", secondRaw)
		results <- err
	}()
	select {
	case <-secondGate.started:
	case err := <-results:
		require.NoError(t, err)
		t.Fatal("second upload skipped the registered image decoder")
	}
	_, err = s.Upload(1, uuid.NewString(), "third.png", "image", "", []byte("not an image"))
	require.ErrorAs(t, err, &apiErr)
	require.Equal(t, "TOC_ASSET_UPLOAD_BUSY", apiErr.Code, "capacity must be checked before either decode entry point")
	replayed, err := s.Upload(2, oldKey, "prior.png", "image", "", pngBytes.Bytes())
	require.NoError(t, err, "an exact committed retry needs no decoder slot")
	require.Equal(t, old.ID, replayed.ID)
	release()
	require.NoError(t, <-results)
	require.NoError(t, <-results)
	_, err = s.Upload(1, uuid.NewString(), "invalid.png", "image", "", []byte("not an image"))
	require.ErrorAs(t, err, &apiErr)
	require.Equal(t, "TOC_IMAGE_INVALID", apiErr.Code)
	_, err = s.Upload(1, uuid.NewString(), "after-failure.png", "image", "", pngBytes.Bytes())
	require.NoError(t, err, "success and decoder rejection must both return their admission slots")
}

func TestTOCAssetEncodingStopsAtByteLimit(t *testing.T) {
	var buffer assetEncodingBuffer
	_, err := io.Copy(&buffer, strings.NewReader(strings.Repeat("x", MaxAssetBytes+1)))
	require.ErrorIs(t, err, errAssetEncodingLimit)
	require.LessOrEqual(t, buffer.Len(), MaxAssetBytes)
}
