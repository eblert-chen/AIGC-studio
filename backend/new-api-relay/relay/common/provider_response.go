package common

import (
	"errors"
	"io"
)

// ProviderTaskResponseBodyLimit bounds status/submission documents before any
// adapter parses provider-controlled bytes. Task APIs are metadata endpoints;
// generated artifacts must be transferred separately rather than embedded in
// an unbounded JSON response.
const ProviderTaskResponseBodyLimit = 16 << 20

var (
	ErrProviderTaskResponseBodyMissing  = errors.New("provider task response body is missing")
	ErrProviderTaskResponseBodyTooLarge = errors.New("provider task response body exceeds the limit")
)

func ReadProviderTaskResponseBody(body io.Reader) ([]byte, error) {
	if body == nil {
		return nil, ErrProviderTaskResponseBodyMissing
	}
	contents, err := io.ReadAll(io.LimitReader(body, ProviderTaskResponseBodyLimit+1))
	if err != nil {
		return nil, err
	}
	if len(contents) > ProviderTaskResponseBodyLimit {
		return nil, ErrProviderTaskResponseBodyTooLarge
	}
	return contents, nil
}
