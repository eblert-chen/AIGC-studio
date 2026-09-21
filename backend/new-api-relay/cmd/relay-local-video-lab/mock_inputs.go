//go:build relay_local_video_lab

package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const labMockInputMaxBytes int64 = 64 * 1024 * 1024

var labMockInputPath = regexp.MustCompile(`^/api/v1/input-assets/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/content$`)
var labMockInputSignature = regexp.MustCompile(`^[0-9a-f]{64}$`)

type labMockInputLocator struct {
	URL string `json:"url"`
}

type labMockContent struct {
	Type     string               `json:"type"`
	Text     string               `json:"text"`
	ImageURL *labMockInputLocator `json:"image_url"`
	VideoURL *labMockInputLocator `json:"video_url"`
	AudioURL *labMockInputLocator `json:"audio_url"`
}

// These local-provider observations prove a complete reference read, not model
// quality or production acceptance. Signed URLs and provider bodies are omitted.
type labMockInputEvidence struct {
	MediaType   string `json:"media_type"`
	ContentType string `json:"content_type"`
	SizeBytes   int64  `json:"size_bytes"`
	SHA256      string `json:"sha256"`
}

func newLabMockInputClient(ca []byte) (*http.Client, error) {
	roots := x509.NewCertPool()
	if len(ca) == 0 || len(ca) > 64*1024 || !roots.AppendCertsFromPEM(ca) {
		return nil, errors.New("mock references require the prepared local object CA")
	}
	transport := &http.Transport{
		Proxy: nil, DialContext: (&net.Dialer{Timeout: 5 * time.Second}).DialContext,
		TLSClientConfig:     &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12},
		TLSHandshakeTimeout: 5 * time.Second, ResponseHeaderTimeout: 5 * time.Second,
		DisableCompression: true, MaxConnsPerHost: 2, MaxIdleConnsPerHost: 2, IdleConnTimeout: 10 * time.Second,
	}
	return &http.Client{
		Transport: transport, Timeout: 10 * time.Second,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse },
	}, nil
}

func validateLabMockInputURL(raw string, now time.Time) error {
	parsed, err := url.Parse(raw)
	if err != nil || raw == "" || len(raw) > 4096 || raw != strings.TrimSpace(raw) || strings.ContainsAny(raw, "\r\n\\#") ||
		parsed.Scheme != "https" || parsed.Host != labObjectStoreHost || parsed.User != nil || parsed.RawPath != "" ||
		!labMockInputPath.MatchString(parsed.Path) || parsed.EscapedPath() != parsed.Path {
		return errors.New("mock reference must be an exact local HTTPS signed input URL")
	}
	query, err := url.ParseQuery(parsed.RawQuery)
	if err != nil || len(query) != 3 || len(query["expires"]) != 1 || len(query["disposition"]) != 1 || len(query["signature"]) != 1 ||
		(query.Get("disposition") != "inline" && query.Get("disposition") != "attachment") || !labMockInputSignature.MatchString(query.Get("signature")) {
		return errors.New("mock reference signature parameters are invalid")
	}
	expires, err := strconv.ParseInt(query.Get("expires"), 10, 64)
	if err != nil || strconv.FormatInt(expires, 10) != query.Get("expires") || expires <= now.Unix() || expires > now.Unix()+3600 {
		return errors.New("mock reference signature has an invalid expiry")
	}
	return nil
}

func (provider *labMockProvider) readReferenceInputs(ctx context.Context, content []labMockContent) ([]labMockInputEvidence, error) {
	if len(content) > 13 {
		return nil, errors.New("mock reference content exceeds the bounded contract")
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	var evidence []labMockInputEvidence
	for _, item := range content {
		var locator *labMockInputLocator
		mediaType := ""
		switch item.Type {
		case "text":
			if item.ImageURL != nil || item.VideoURL != nil || item.AudioURL != nil {
				return nil, errors.New("mock text content contains an ambiguous reference")
			}
			continue
		case "image_url":
			locator, mediaType = item.ImageURL, "image"
			if item.VideoURL != nil || item.AudioURL != nil {
				return nil, errors.New("mock image content contains an ambiguous reference")
			}
		case "video_url":
			locator, mediaType = item.VideoURL, "video"
			if item.ImageURL != nil || item.AudioURL != nil {
				return nil, errors.New("mock video content contains an ambiguous reference")
			}
		case "audio_url":
			locator, mediaType = item.AudioURL, "audio"
			if item.ImageURL != nil || item.VideoURL != nil {
				return nil, errors.New("mock audio content contains an ambiguous reference")
			}
		default:
			return nil, errors.New("mock content type is unsupported")
		}
		if locator == nil || provider.inputClient == nil || len(evidence) >= 12 {
			return nil, errors.New("mock reference reader or locator is unavailable")
		}
		observed, err := readLabMockInput(ctx, provider.inputClient, locator.URL, mediaType)
		if err != nil {
			return nil, err
		}
		provider.inputFetches.Add(1)
		provider.inputBytes.Add(observed.SizeBytes)
		evidence = append(evidence, observed)
	}
	return evidence, nil
}

func readLabMockInput(ctx context.Context, client *http.Client, rawURL, mediaType string) (labMockInputEvidence, error) {
	var evidence labMockInputEvidence
	if err := validateLabMockInputURL(rawURL, time.Now().UTC()); err != nil {
		return evidence, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return evidence, errors.New("mock reference request could not be created")
	}
	// The source validates the real Platform HMAC, expiry and active asset row.
	// No incoming API key, browser cookie, bootstrap token or Origin is forwarded.
	request.Header.Set("Accept", mediaType+"/*")
	request.Header.Set("Accept-Encoding", "identity")
	response, err := client.Do(request)
	if err != nil {
		return evidence, errors.New("mock reference HTTPS read failed")
	}
	defer response.Body.Close()
	contentType, _, mimeErr := mime.ParseMediaType(response.Header.Get("Content-Type"))
	contentType = strings.ToLower(contentType)
	if response.StatusCode != http.StatusOK || mimeErr != nil || !strings.HasPrefix(contentType, mediaType+"/") ||
		response.ContentLength == 0 || response.ContentLength > labMockInputMaxBytes ||
		(response.Header.Get("Content-Encoding") != "" && response.Header.Get("Content-Encoding") != "identity") {
		return evidence, errors.New("mock reference is missing, inactive, unauthorized or has invalid media metadata")
	}
	reader := io.LimitReader(response.Body, labMockInputMaxBytes+1)
	prefix := make([]byte, 512)
	length, err := io.ReadFull(reader, prefix)
	if length == 0 || (err != nil && !errors.Is(err, io.EOF) && !errors.Is(err, io.ErrUnexpectedEOF)) {
		return evidence, errors.New("mock reference body is empty or interrupted")
	}
	prefix = prefix[:length]
	if !labMockInputMediaMatches(prefix, contentType) {
		return evidence, errors.New("mock reference bytes do not match the declared media type")
	}
	digest := sha256.New()
	_, _ = digest.Write(prefix)
	remainder, err := io.Copy(digest, reader)
	size := int64(length) + remainder
	if err != nil || size > labMockInputMaxBytes || (response.ContentLength >= 0 && response.ContentLength != size) {
		return evidence, errors.New("mock reference body is oversized or incomplete")
	}
	return labMockInputEvidence{
		MediaType: mediaType, ContentType: contentType, SizeBytes: size, SHA256: fmt.Sprintf("%x", digest.Sum(nil)),
	}, nil
}

// Container-signature validation only: it must not be described as a codec,
// duration or dimension inspection. The real upload service owns input metadata.
func labMockInputMediaMatches(prefix []byte, contentType string) bool {
	if contentType == "image/png" || contentType == "image/jpeg" || contentType == "image/gif" || contentType == "image/webp" ||
		contentType == "video/webm" || contentType == "audio/mpeg" || contentType == "audio/ogg" {
		return http.DetectContentType(prefix) == contentType
	}
	if contentType == "video/mp4" || contentType == "audio/mp4" || contentType == "audio/x-m4a" {
		return len(prefix) >= 16 && string(prefix[4:8]) == "ftyp" && binary.BigEndian.Uint32(prefix[:4]) >= 16
	}
	if contentType == "video/quicktime" {
		return len(prefix) >= 16 && string(prefix[4:8]) == "ftyp" && bytes.Contains(prefix[8:16], []byte("qt  "))
	}
	if contentType == "audio/wav" || contentType == "audio/x-wav" {
		return len(prefix) >= 12 && string(prefix[:4]) == "RIFF" && string(prefix[8:12]) == "WAVE"
	}
	return false
}
