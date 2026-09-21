package common

import (
	"bytes"
	"errors"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReadProviderTaskResponseBodyEnforcesExactLimit(t *testing.T) {
	exact := bytes.Repeat([]byte{'a'}, ProviderTaskResponseBodyLimit)
	contents, err := ReadProviderTaskResponseBody(bytes.NewReader(exact))
	require.NoError(t, err)
	require.Len(t, contents, ProviderTaskResponseBodyLimit)

	oversized := bytes.Repeat([]byte{'b'}, ProviderTaskResponseBodyLimit+1)
	contents, err = ReadProviderTaskResponseBody(bytes.NewReader(oversized))
	require.ErrorIs(t, err, ErrProviderTaskResponseBodyTooLarge)
	require.Nil(t, contents)

	contents, err = ReadProviderTaskResponseBody(nil)
	require.ErrorIs(t, err, ErrProviderTaskResponseBodyMissing)
	require.Nil(t, contents)

	failing := &providerResponseFailingReader{}
	contents, err = ReadProviderTaskResponseBody(failing)
	require.Error(t, err)
	require.False(t, errors.Is(err, ErrProviderTaskResponseBodyTooLarge))
	require.Nil(t, contents)
}

type providerResponseFailingReader struct{}

func (*providerResponseFailingReader) Read([]byte) (int, error) {
	return 0, errors.New("read failed")
}
