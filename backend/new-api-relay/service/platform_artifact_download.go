package service

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

const (
	defaultPlatformArtifactMaxBytes = int64(512 * 1024 * 1024)
	platformArtifactCopyBufferSize  = 64 * 1024
	platformArtifactPrefixSize      = 64 * 1024
	platformArtifactMaxBoxCount     = 100_000
)

var (
	ErrPlatformArtifactDownload  = errors.New("platform artifact download failed")
	ErrPlatformArtifactSecurity  = errors.New("platform artifact security policy rejected the source")
	ErrPlatformArtifactTooLarge  = errors.New("platform artifact exceeds maximum size")
	ErrPlatformArtifactIntegrity = errors.New("platform artifact integrity check failed")
)

var platformArtifactNonPublicPrefixes = []netip.Prefix{
	netip.MustParsePrefix("0.0.0.0/8"),
	netip.MustParsePrefix("10.0.0.0/8"),
	netip.MustParsePrefix("100.64.0.0/10"),
	netip.MustParsePrefix("127.0.0.0/8"),
	netip.MustParsePrefix("169.254.0.0/16"),
	netip.MustParsePrefix("172.16.0.0/12"),
	netip.MustParsePrefix("192.0.0.0/24"),
	netip.MustParsePrefix("192.0.2.0/24"),
	netip.MustParsePrefix("192.88.99.0/24"),
	netip.MustParsePrefix("192.168.0.0/16"),
	netip.MustParsePrefix("198.18.0.0/15"),
	netip.MustParsePrefix("198.51.100.0/24"),
	netip.MustParsePrefix("203.0.113.0/24"),
	netip.MustParsePrefix("224.0.0.0/4"),
	netip.MustParsePrefix("240.0.0.0/4"),
	netip.MustParsePrefix("::/128"),
	netip.MustParsePrefix("::1/128"),
	netip.MustParsePrefix("64:ff9b:1::/48"),
	netip.MustParsePrefix("100::/64"),
	netip.MustParsePrefix("2001::/32"),
	netip.MustParsePrefix("2001:2::/48"),
	netip.MustParsePrefix("2001:10::/28"),
	netip.MustParsePrefix("2001:20::/28"),
	netip.MustParsePrefix("2001:db8::/32"),
	netip.MustParsePrefix("3fff::/20"),
	netip.MustParsePrefix("5f00::/16"),
	netip.MustParsePrefix("fc00::/7"),
	netip.MustParsePrefix("fe80::/10"),
	netip.MustParsePrefix("ff00::/8"),
}

var defaultPlatformArtifactContentTypes = map[string]struct{}{
	"image/jpeg": {},
	"image/png":  {},
	"image/webp": {},
	"video/mp4":  {},
	"video/webm": {},
}

type platformArtifactResolver interface {
	LookupIPAddr(ctx context.Context, host string) ([]net.IPAddr, error)
}

type platformArtifactDialContext func(ctx context.Context, network, address string) (net.Conn, error)

type PlatformArtifactDownloadConfig struct {
	Production          bool
	MaxBytes            int64
	Timeout             time.Duration
	SpoolDirectory      string
	AllowedContentTypes map[string]struct{}
}

type PlatformArtifactDownloadExpectation struct {
	SizeBytes *int64
	SHA256    string
}

type PlatformDownloadedArtifact struct {
	Content     *os.File
	ContentType string
	SizeBytes   int64
	SHA256      string
	temporary   string
}

type platformArtifactPrefixCapture struct {
	data []byte
}

func (capture *platformArtifactPrefixCapture) Write(payload []byte) (int, error) {
	if remaining := platformArtifactPrefixSize - len(capture.data); remaining > 0 {
		if remaining > len(payload) {
			remaining = len(payload)
		}
		capture.data = append(capture.data, payload[:remaining]...)
	}
	return len(payload), nil
}

func (artifact *PlatformDownloadedArtifact) Close() error {
	if artifact == nil {
		return nil
	}
	var closeErr error
	if artifact.Content != nil {
		closeErr = artifact.Content.Close()
		artifact.Content = nil
	}
	if artifact.temporary != "" {
		removeErr := os.Remove(artifact.temporary)
		artifact.temporary = ""
		if closeErr == nil && removeErr != nil && !os.IsNotExist(removeErr) {
			closeErr = removeErr
		}
	}
	return closeErr
}

type PlatformArtifactDownloader struct {
	config      PlatformArtifactDownloadConfig
	resolver    platformArtifactResolver
	dialContext platformArtifactDialContext
}

func NewPlatformArtifactDownloader(config PlatformArtifactDownloadConfig) (*PlatformArtifactDownloader, error) {
	netDialer := &net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}
	return newPlatformArtifactDownloader(config, net.DefaultResolver, netDialer.DialContext)
}

func NewPlatformArtifactDownloaderFromEnvironment() (*PlatformArtifactDownloader, error) {
	maxBytes := defaultPlatformArtifactMaxBytes
	if raw := strings.TrimSpace(os.Getenv("RELAY_ARTIFACT_MAX_BYTES")); raw != "" {
		parsed, err := strconv.ParseInt(raw, 10, 64)
		if err != nil || parsed < 1 {
			return nil, fmt.Errorf("%w: RELAY_ARTIFACT_MAX_BYTES is invalid", ErrPlatformArtifactDownload)
		}
		maxBytes = parsed
	}
	timeout := 60 * time.Second
	if raw := strings.TrimSpace(os.Getenv("RELAY_ARTIFACT_TIMEOUT_SECONDS")); raw != "" {
		seconds, err := strconv.ParseFloat(raw, 64)
		if err != nil || seconds < 1 {
			return nil, fmt.Errorf("%w: RELAY_ARTIFACT_TIMEOUT_SECONDS is invalid", ErrPlatformArtifactDownload)
		}
		timeout = time.Duration(seconds * float64(time.Second))
	}
	return NewPlatformArtifactDownloader(PlatformArtifactDownloadConfig{
		Production:     PlatformRelayProductionSecurityEnabled(),
		MaxBytes:       maxBytes,
		Timeout:        timeout,
		SpoolDirectory: strings.TrimSpace(os.Getenv("RELAY_ARTIFACT_SPOOL_DIRECTORY")),
	})
}

func newPlatformArtifactDownloader(
	config PlatformArtifactDownloadConfig,
	resolver platformArtifactResolver,
	dialContext platformArtifactDialContext,
) (*PlatformArtifactDownloader, error) {
	if config.MaxBytes == 0 {
		config.MaxBytes = defaultPlatformArtifactMaxBytes
	}
	if config.MaxBytes < 1 {
		return nil, fmt.Errorf("%w: maximum size must be positive", ErrPlatformArtifactDownload)
	}
	if config.Timeout == 0 {
		config.Timeout = 60 * time.Second
	}
	if config.Timeout < time.Second {
		return nil, fmt.Errorf("%w: timeout must be at least one second", ErrPlatformArtifactDownload)
	}
	if config.AllowedContentTypes == nil {
		config.AllowedContentTypes = defaultPlatformArtifactContentTypes
	}
	if len(config.AllowedContentTypes) == 0 {
		return nil, fmt.Errorf("%w: at least one MIME type is required", ErrPlatformArtifactDownload)
	}
	if config.SpoolDirectory != "" {
		spoolInfo, err := os.Stat(config.SpoolDirectory)
		if err != nil || !spoolInfo.IsDir() {
			return nil, fmt.Errorf("%w: spool directory is unavailable", ErrPlatformArtifactDownload)
		}
	}
	if resolver == nil || dialContext == nil {
		return nil, fmt.Errorf("%w: resolver and dialer are required", ErrPlatformArtifactDownload)
	}
	return &PlatformArtifactDownloader{
		config:      config,
		resolver:    resolver,
		dialContext: dialContext,
	}, nil
}

func (downloader *PlatformArtifactDownloader) Download(
	ctx context.Context,
	sourceURL string,
	expectation PlatformArtifactDownloadExpectation,
) (*PlatformDownloadedArtifact, error) {
	if downloader == nil {
		return nil, fmt.Errorf("%w: downloader is not configured", ErrPlatformArtifactDownload)
	}
	temporary, err := os.CreateTemp(downloader.config.SpoolDirectory, ".relay-artifact-*")
	if err != nil {
		return nil, fmt.Errorf("%w: could not create download spool", ErrPlatformArtifactDownload)
	}
	artifact := &PlatformDownloadedArtifact{Content: temporary, temporary: temporary.Name()}
	keep := false
	defer func() {
		if !keep {
			_ = artifact.Close()
		}
	}()

	prefix := &platformArtifactPrefixCapture{}
	contentType, sizeBytes, digest, err := downloader.downloadTo(
		ctx,
		sourceURL,
		io.MultiWriter(temporary, prefix),
	)
	if err != nil {
		return nil, err
	}
	if expectation.SizeBytes != nil && sizeBytes != *expectation.SizeBytes {
		return nil, fmt.Errorf("%w: provider size did not match", ErrPlatformArtifactIntegrity)
	}
	if expectation.SHA256 != "" {
		expected, err := normalizePlatformArtifactSHA256(expectation.SHA256)
		if err != nil {
			return nil, err
		}
		if expected != digest {
			return nil, fmt.Errorf("%w: provider digest did not match", ErrPlatformArtifactIntegrity)
		}
	}
	if err := temporary.Sync(); err != nil {
		return nil, fmt.Errorf("%w: could not flush download spool", ErrPlatformArtifactDownload)
	}
	if err := validatePlatformArtifactMedia(ctx, temporary, contentType, sizeBytes, prefix.data); err != nil {
		return nil, err
	}
	if _, err := temporary.Seek(0, io.SeekStart); err != nil {
		return nil, fmt.Errorf("%w: could not rewind download spool", ErrPlatformArtifactDownload)
	}
	artifact.ContentType = contentType
	artifact.SizeBytes = sizeBytes
	artifact.SHA256 = digest
	keep = true
	return artifact, nil
}

func (downloader *PlatformArtifactDownloader) downloadTo(
	ctx context.Context,
	sourceURL string,
	destination io.Writer,
) (string, int64, string, error) {
	parsed, port, err := downloader.validateAndResolveURL(ctx, sourceURL)
	if err != nil {
		return "", 0, "", err
	}
	addresses, err := downloader.resolvePublicAddresses(ctx, parsed.Hostname())
	if err != nil {
		return "", 0, "", err
	}

	pinnedHost := strings.TrimSuffix(strings.ToLower(parsed.Hostname()), ".")
	transport := &http.Transport{
		Proxy:                 nil,
		DisableCompression:    true,
		DisableKeepAlives:     true,
		ForceAttemptHTTP2:     true,
		TLSHandshakeTimeout:   min(downloader.config.Timeout, 10*time.Second),
		ResponseHeaderTimeout: min(downloader.config.Timeout, 30*time.Second),
		DialContext: func(dialCtx context.Context, network, address string) (net.Conn, error) {
			host, requestedPort, splitErr := net.SplitHostPort(address)
			if splitErr != nil {
				return nil, fmt.Errorf("%w: invalid dial target", ErrPlatformArtifactSecurity)
			}
			if strings.TrimSuffix(strings.ToLower(host), ".") != pinnedHost || requestedPort != strconv.Itoa(port) {
				return nil, fmt.Errorf("%w: unexpected hostname during download", ErrPlatformArtifactSecurity)
			}
			var lastErr error
			for _, address := range addresses {
				if network == "tcp4" && !address.Is4() {
					continue
				}
				if network == "tcp6" && address.Is4() {
					continue
				}
				connection, dialErr := downloader.dialContext(
					dialCtx,
					network,
					net.JoinHostPort(address.String(), requestedPort),
				)
				if dialErr == nil {
					return connection, nil
				}
				lastErr = dialErr
			}
			if lastErr != nil {
				return nil, lastErr
			}
			return nil, fmt.Errorf("%w: no usable pinned address", ErrPlatformArtifactSecurity)
		},
	}
	defer transport.CloseIdleConnections()
	client := &http.Client{
		Transport: transport,
		Timeout:   downloader.config.Timeout,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, parsed.String(), nil)
	if err != nil {
		return "", 0, "", fmt.Errorf("%w: invalid request", ErrPlatformArtifactDownload)
	}
	request.Header.Set("Accept-Encoding", "identity")
	request.Header.Set("User-Agent", "new-api-platform-artifact-transfer/1")
	response, err := client.Do(request)
	if err != nil {
		return "", 0, "", fmt.Errorf("%w: provider request failed", ErrPlatformArtifactDownload)
	}
	defer response.Body.Close()
	if response.StatusCode >= 300 && response.StatusCode < 400 {
		return "", 0, "", fmt.Errorf("%w: redirects are forbidden", ErrPlatformArtifactSecurity)
	}
	if response.StatusCode != http.StatusOK {
		return "", 0, "", fmt.Errorf("%w: provider returned status %d", ErrPlatformArtifactDownload, response.StatusCode)
	}
	if response.ContentLength > downloader.config.MaxBytes {
		return "", 0, "", ErrPlatformArtifactTooLarge
	}
	contentType, _, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil {
		return "", 0, "", fmt.Errorf("%w: provider MIME type is invalid", ErrPlatformArtifactDownload)
	}
	contentType = strings.ToLower(contentType)
	if _, allowed := downloader.config.AllowedContentTypes[contentType]; !allowed {
		return "", 0, "", fmt.Errorf("%w: provider MIME type is not allowed", ErrPlatformArtifactDownload)
	}

	digest := sha256.New()
	written, err := io.CopyBuffer(
		io.MultiWriter(destination, digest),
		io.LimitReader(response.Body, downloader.config.MaxBytes),
		make([]byte, platformArtifactCopyBufferSize),
	)
	if err != nil {
		return "", 0, "", fmt.Errorf("%w: provider stream could not be copied", ErrPlatformArtifactDownload)
	}
	var overflow [1]byte
	overflowBytes, overflowErr := io.ReadFull(response.Body, overflow[:])
	if overflowBytes > 0 {
		return "", 0, "", ErrPlatformArtifactTooLarge
	}
	if overflowErr != nil && !errors.Is(overflowErr, io.EOF) && !errors.Is(overflowErr, io.ErrUnexpectedEOF) {
		return "", 0, "", fmt.Errorf("%w: provider stream could not be completed", ErrPlatformArtifactDownload)
	}
	return contentType, written, hex.EncodeToString(digest.Sum(nil)), nil
}

func validatePlatformArtifactMedia(
	ctx context.Context,
	content io.ReaderAt,
	contentType string,
	sizeBytes int64,
	prefix []byte,
) error {
	if sizeBytes <= 0 || len(prefix) == 0 {
		return fmt.Errorf("%w: provider artifact is empty", ErrPlatformArtifactIntegrity)
	}
	detected := detectPlatformArtifactContentType(prefix)
	if detected == "" {
		return fmt.Errorf("%w: provider artifact has no supported media signature", ErrPlatformArtifactIntegrity)
	}
	if detected != contentType {
		return fmt.Errorf(
			"%w: provider artifact signature %s does not match declared MIME type %s",
			ErrPlatformArtifactIntegrity,
			detected,
			contentType,
		)
	}

	var err error
	switch contentType {
	case "image/jpeg":
		err = validatePlatformJPEG(ctx, content, sizeBytes)
	case "image/png":
		err = validatePlatformPNG(ctx, content, sizeBytes)
	case "image/webp":
		err = validatePlatformWebP(ctx, content, sizeBytes)
	case "video/mp4":
		err = validatePlatformMP4(ctx, content, sizeBytes)
	case "video/webm":
		err = validatePlatformWebM(ctx, content, sizeBytes)
	default:
		err = errors.New("unsupported media type")
	}
	if err != nil {
		return fmt.Errorf("%w: provider artifact is not a valid %s: %v", ErrPlatformArtifactIntegrity, contentType, err)
	}
	return nil
}

func detectPlatformArtifactContentType(prefix []byte) string {
	switch {
	case len(prefix) >= 3 && prefix[0] == 0xff && prefix[1] == 0xd8 && prefix[2] == 0xff:
		return "image/jpeg"
	case len(prefix) >= 8 && bytes.Equal(prefix[:8], []byte("\x89PNG\r\n\x1a\n")):
		return "image/png"
	case len(prefix) >= 12 && bytes.Equal(prefix[:4], []byte("RIFF")) && bytes.Equal(prefix[8:12], []byte("WEBP")):
		return "image/webp"
	case len(prefix) >= 4 && bytes.Equal(prefix[:4], []byte{0x1a, 0x45, 0xdf, 0xa3}):
		return "video/webm"
	case platformArtifactPrefixHasMP4(prefix):
		return "video/mp4"
	default:
		return ""
	}
}

func readPlatformArtifactAt(content io.ReaderAt, offset int64, buffer []byte) error {
	if offset < 0 || len(buffer) == 0 {
		return errors.New("invalid media read")
	}
	read, err := content.ReadAt(buffer, offset)
	if read != len(buffer) {
		return io.ErrUnexpectedEOF
	}
	if err != nil && !errors.Is(err, io.EOF) {
		return err
	}
	return nil
}

func checkPlatformArtifactValidationContext(ctx context.Context) error {
	if ctx == nil {
		return nil
	}
	return ctx.Err()
}

func validatePlatformJPEG(ctx context.Context, content io.ReaderAt, sizeBytes int64) error {
	if sizeBytes < 16 {
		return errors.New("JPEG is truncated")
	}
	var trailer [2]byte
	if err := readPlatformArtifactAt(content, sizeBytes-2, trailer[:]); err != nil || trailer != [2]byte{0xff, 0xd9} {
		return errors.New("JPEG end marker is missing")
	}

	offset := int64(2)
	sawFrame := false
	for markerCount := 0; markerCount < platformArtifactMaxBoxCount && offset < sizeBytes-2; markerCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return err
		}
		var markerPrefix [1]byte
		if err := readPlatformArtifactAt(content, offset, markerPrefix[:]); err != nil || markerPrefix[0] != 0xff {
			return errors.New("JPEG marker stream is invalid")
		}
		offset++
		var marker byte
		for {
			if err := readPlatformArtifactAt(content, offset, markerPrefix[:]); err != nil {
				return errors.New("JPEG marker is truncated")
			}
			offset++
			if markerPrefix[0] != 0xff {
				marker = markerPrefix[0]
				break
			}
		}
		switch {
		case marker == 0x00 || marker == 0xd8:
			return errors.New("JPEG marker stream is invalid")
		case marker == 0xd9:
			return errors.New("JPEG contains no scan data")
		case marker == 0x01 || (marker >= 0xd0 && marker <= 0xd7):
			continue
		}

		var lengthBytes [2]byte
		if err := readPlatformArtifactAt(content, offset, lengthBytes[:]); err != nil {
			return errors.New("JPEG segment length is truncated")
		}
		segmentLength := int64(binary.BigEndian.Uint16(lengthBytes[:]))
		if segmentLength < 2 || segmentLength > sizeBytes-offset {
			return errors.New("JPEG segment length is invalid")
		}
		segmentEnd := offset + segmentLength
		if isPlatformJPEGStartOfFrame(marker) {
			if segmentLength < 8 {
				return errors.New("JPEG frame header is truncated")
			}
			var frameHeader [5]byte
			if err := readPlatformArtifactAt(content, offset+2, frameHeader[:]); err != nil ||
				binary.BigEndian.Uint16(frameHeader[1:3]) == 0 || binary.BigEndian.Uint16(frameHeader[3:5]) == 0 {
				return errors.New("JPEG frame dimensions are invalid")
			}
			sawFrame = true
		}
		if marker == 0xda {
			if !sawFrame || segmentEnd >= sizeBytes-2 {
				return errors.New("JPEG scan data is missing")
			}
			return nil
		}
		offset = segmentEnd
	}
	return errors.New("JPEG contains no complete image scan")
}

func isPlatformJPEGStartOfFrame(marker byte) bool {
	if marker < 0xc0 || marker > 0xcf {
		return false
	}
	switch marker {
	case 0xc4, 0xc8, 0xcc:
		return false
	default:
		return true
	}
}

func validatePlatformPNG(ctx context.Context, content io.ReaderAt, sizeBytes int64) error {
	if sizeBytes < 45 {
		return errors.New("PNG is truncated")
	}
	offset := int64(8)
	sawIHDR := false
	sawImageData := false
	for chunkCount := 0; chunkCount < platformArtifactMaxBoxCount && offset < sizeBytes; chunkCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return err
		}
		var header [8]byte
		if err := readPlatformArtifactAt(content, offset, header[:]); err != nil {
			return errors.New("PNG chunk header is truncated")
		}
		chunkLength := int64(binary.BigEndian.Uint32(header[:4]))
		chunkType := string(header[4:8])
		chunkEnd := offset + 12 + chunkLength
		if chunkLength < 0 || chunkEnd < offset || chunkEnd > sizeBytes {
			return errors.New("PNG chunk length is invalid")
		}
		if !platformArtifactASCIILetters(header[4:8]) {
			return errors.New("PNG chunk type is invalid")
		}
		if !sawIHDR {
			if chunkType != "IHDR" || chunkLength != 13 {
				return errors.New("PNG IHDR is missing")
			}
			var dimensions [8]byte
			if err := readPlatformArtifactAt(content, offset+8, dimensions[:]); err != nil ||
				binary.BigEndian.Uint32(dimensions[:4]) == 0 || binary.BigEndian.Uint32(dimensions[4:]) == 0 {
				return errors.New("PNG dimensions are invalid")
			}
			sawIHDR = true
		} else if chunkType == "IHDR" {
			return errors.New("PNG contains multiple IHDR chunks")
		}
		if chunkType == "IDAT" && chunkLength > 0 {
			sawImageData = true
		}
		if chunkType == "IEND" {
			if chunkLength != 0 || !sawImageData || chunkEnd != sizeBytes {
				return errors.New("PNG terminal chunk is invalid")
			}
			return nil
		}
		offset = chunkEnd
	}
	return errors.New("PNG IEND is missing")
}

func platformArtifactASCIILetters(value []byte) bool {
	for _, character := range value {
		if (character < 'A' || character > 'Z') && (character < 'a' || character > 'z') {
			return false
		}
	}
	return true
}

func validatePlatformWebP(ctx context.Context, content io.ReaderAt, sizeBytes int64) error {
	if sizeBytes < 30 {
		return errors.New("WebP is truncated")
	}
	var header [12]byte
	if err := readPlatformArtifactAt(content, 0, header[:]); err != nil ||
		!bytes.Equal(header[:4], []byte("RIFF")) || !bytes.Equal(header[8:], []byte("WEBP")) {
		return errors.New("WebP RIFF header is invalid")
	}
	if uint64(binary.LittleEndian.Uint32(header[4:8]))+8 != uint64(sizeBytes) {
		return errors.New("WebP RIFF length is invalid")
	}
	found, err := platformWebPChunkListHasImage(ctx, content, 12, sizeBytes, true)
	if err != nil {
		return err
	}
	if !found {
		return errors.New("WebP contains no image frame")
	}
	return nil
}

func platformWebPChunkListHasImage(
	ctx context.Context,
	content io.ReaderAt,
	start int64,
	end int64,
	allowAnimation bool,
) (bool, error) {
	offset := start
	found := false
	for chunkCount := 0; chunkCount < platformArtifactMaxBoxCount && offset < end; chunkCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return false, err
		}
		var header [8]byte
		if err := readPlatformArtifactAt(content, offset, header[:]); err != nil {
			return false, errors.New("WebP chunk header is truncated")
		}
		chunkLength := int64(binary.LittleEndian.Uint32(header[4:]))
		payloadStart := offset + 8
		payloadEnd := payloadStart + chunkLength
		paddedEnd := payloadEnd + chunkLength%2
		if payloadEnd < payloadStart || paddedEnd < payloadEnd || paddedEnd > end {
			return false, errors.New("WebP chunk length is invalid")
		}
		switch string(header[:4]) {
		case "VP8 ":
			if err := validatePlatformVP8Lossy(content, payloadStart, chunkLength); err != nil {
				return false, err
			}
			found = true
		case "VP8L":
			if err := validatePlatformVP8Lossless(content, payloadStart, chunkLength); err != nil {
				return false, err
			}
			found = true
		case "ANMF":
			if !allowAnimation || chunkLength < 24 {
				return false, errors.New("WebP animation frame is invalid")
			}
			frameFound, err := platformWebPChunkListHasImage(ctx, content, payloadStart+16, payloadEnd, false)
			if err != nil {
				return false, err
			}
			found = found || frameFound
		}
		offset = paddedEnd
	}
	if offset != end {
		return false, errors.New("WebP chunk stream is incomplete")
	}
	return found, nil
}

func validatePlatformVP8Lossy(content io.ReaderAt, offset int64, length int64) error {
	if length < 10 {
		return errors.New("WebP VP8 frame is truncated")
	}
	var header [10]byte
	if err := readPlatformArtifactAt(content, offset, header[:]); err != nil ||
		!bytes.Equal(header[3:6], []byte{0x9d, 0x01, 0x2a}) ||
		binary.LittleEndian.Uint16(header[6:8])&0x3fff == 0 ||
		binary.LittleEndian.Uint16(header[8:10])&0x3fff == 0 {
		return errors.New("WebP VP8 frame header is invalid")
	}
	return nil
}

func validatePlatformVP8Lossless(content io.ReaderAt, offset int64, length int64) error {
	if length < 5 {
		return errors.New("WebP VP8L frame is truncated")
	}
	var header [5]byte
	if err := readPlatformArtifactAt(content, offset, header[:]); err != nil || header[0] != 0x2f {
		return errors.New("WebP VP8L frame header is invalid")
	}
	return nil
}

type platformISOBox struct {
	typ       [4]byte
	start     int64
	dataStart int64
	end       int64
}

func platformArtifactPrefixHasMP4(prefix []byte) bool {
	reader := bytes.NewReader(prefix)
	limit := int64(len(prefix))
	offset := int64(0)
	for boxCount := 0; boxCount < 64 && offset+8 <= limit && offset <= 4096; boxCount++ {
		box, err := readPlatformISOBox(reader, offset, limit)
		if err != nil {
			return false
		}
		if string(box.typ[:]) == "ftyp" {
			payloadSize := box.end - box.dataStart
			return box.start <= 4096 && payloadSize >= 8 && payloadSize <= 4096
		}
		if box.end <= offset {
			return false
		}
		offset = box.end
	}
	return false
}

func readPlatformISOBox(content io.ReaderAt, offset int64, limit int64) (platformISOBox, error) {
	if offset < 0 || limit-offset < 8 {
		return platformISOBox{}, errors.New("ISO BMFF box header is truncated")
	}
	var header [16]byte
	if err := readPlatformArtifactAt(content, offset, header[:8]); err != nil {
		return platformISOBox{}, errors.New("ISO BMFF box header is truncated")
	}
	copyType := [4]byte{}
	copy(copyType[:], header[4:8])
	headerSize := int64(8)
	size := uint64(binary.BigEndian.Uint32(header[:4]))
	switch size {
	case 0:
		size = uint64(limit - offset)
	case 1:
		if limit-offset < 16 || readPlatformArtifactAt(content, offset+8, header[8:16]) != nil {
			return platformISOBox{}, errors.New("ISO BMFF extended box header is truncated")
		}
		headerSize = 16
		size = binary.BigEndian.Uint64(header[8:16])
	}
	if size < uint64(headerSize) || size > uint64(limit-offset) {
		return platformISOBox{}, errors.New("ISO BMFF box length is invalid")
	}
	return platformISOBox{
		typ:       copyType,
		start:     offset,
		dataStart: offset + headerSize,
		end:       offset + int64(size),
	}, nil
}

func validatePlatformMP4(ctx context.Context, content io.ReaderAt, sizeBytes int64) error {
	if sizeBytes < 48 {
		return errors.New("MP4 is truncated")
	}
	offset := int64(0)
	sawFTYP := false
	sawMOOV := false
	sawVideoTrack := false
	sawMediaData := false
	for boxCount := 0; boxCount < platformArtifactMaxBoxCount && offset < sizeBytes; boxCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return err
		}
		box, err := readPlatformISOBox(content, offset, sizeBytes)
		if err != nil {
			return err
		}
		switch string(box.typ[:]) {
		case "ftyp":
			if sawFTYP || box.start > 4096 {
				return errors.New("MP4 ftyp placement is invalid")
			}
			if err := validatePlatformMP4FileType(content, box); err != nil {
				return err
			}
			sawFTYP = true
		case "moov":
			sawMOOV = true
			hasVideo, err := platformMP4MovieHasVideoTrack(ctx, content, box)
			if err != nil {
				return err
			}
			sawVideoTrack = sawVideoTrack || hasVideo
		case "mdat":
			if box.end-box.dataStart > 0 {
				sawMediaData = true
			}
		}
		if box.end <= offset {
			return errors.New("MP4 box stream did not advance")
		}
		offset = box.end
	}
	if offset != sizeBytes {
		return errors.New("MP4 has too many or incomplete boxes")
	}
	if !sawFTYP || !sawMOOV || !sawVideoTrack || !sawMediaData {
		return errors.New("MP4 requires ftyp, moov, a video track, and non-empty media data")
	}
	return nil
}

func validatePlatformMP4FileType(content io.ReaderAt, box platformISOBox) error {
	payloadSize := box.end - box.dataStart
	if payloadSize < 8 || payloadSize > 4096 || (payloadSize-8)%4 != 0 {
		return errors.New("MP4 ftyp box is invalid")
	}
	payload := make([]byte, payloadSize)
	if err := readPlatformArtifactAt(content, box.dataStart, payload); err != nil {
		return errors.New("MP4 ftyp box is truncated")
	}
	for offset := 0; offset < len(payload); offset += 4 {
		for _, character := range payload[offset : offset+4] {
			if character < 0x20 || character > 0x7e {
				return errors.New("MP4 brand is invalid")
			}
		}
		if offset == 0 {
			offset += 4 // Skip the binary minor-version field after the major brand.
		}
	}
	return nil
}

func platformMP4MovieHasVideoTrack(
	ctx context.Context,
	content io.ReaderAt,
	movie platformISOBox,
) (bool, error) {
	offset := movie.dataStart
	for boxCount := 0; boxCount < platformArtifactMaxBoxCount && offset < movie.end; boxCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return false, err
		}
		box, err := readPlatformISOBox(content, offset, movie.end)
		if err != nil {
			return false, err
		}
		if string(box.typ[:]) == "trak" {
			hasVideo, err := platformMP4TrackHasVideoHandler(ctx, content, box)
			if err != nil {
				return false, err
			}
			if hasVideo {
				return true, nil
			}
		}
		offset = box.end
	}
	if offset != movie.end {
		return false, errors.New("MP4 moov box is incomplete")
	}
	return false, nil
}

func platformMP4TrackHasVideoHandler(
	ctx context.Context,
	content io.ReaderAt,
	track platformISOBox,
) (bool, error) {
	offset := track.dataStart
	for boxCount := 0; boxCount < platformArtifactMaxBoxCount && offset < track.end; boxCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return false, err
		}
		box, err := readPlatformISOBox(content, offset, track.end)
		if err != nil {
			return false, err
		}
		if string(box.typ[:]) == "mdia" {
			return platformMP4MediaHasVideoHandler(ctx, content, box)
		}
		offset = box.end
	}
	if offset != track.end {
		return false, errors.New("MP4 trak box is incomplete")
	}
	return false, nil
}

func platformMP4MediaHasVideoHandler(
	ctx context.Context,
	content io.ReaderAt,
	media platformISOBox,
) (bool, error) {
	offset := media.dataStart
	for boxCount := 0; boxCount < platformArtifactMaxBoxCount && offset < media.end; boxCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return false, err
		}
		box, err := readPlatformISOBox(content, offset, media.end)
		if err != nil {
			return false, err
		}
		if string(box.typ[:]) == "hdlr" {
			if box.end-box.dataStart < 12 {
				return false, errors.New("MP4 hdlr box is truncated")
			}
			var handler [12]byte
			if err := readPlatformArtifactAt(content, box.dataStart, handler[:]); err != nil {
				return false, errors.New("MP4 hdlr box is truncated")
			}
			return bytes.Equal(handler[8:12], []byte("vide")), nil
		}
		offset = box.end
	}
	if offset != media.end {
		return false, errors.New("MP4 mdia box is incomplete")
	}
	return false, nil
}

type platformEBMLElement struct {
	id        uint64
	start     int64
	dataStart int64
	end       int64
	unknown   bool
}

func readPlatformEBMLVINT(
	content io.ReaderAt,
	offset int64,
	maxLength int,
	keepMarker bool,
) (uint64, int64, bool, error) {
	var first [1]byte
	if err := readPlatformArtifactAt(content, offset, first[:]); err != nil || first[0] == 0 {
		return 0, 0, false, errors.New("EBML variable integer is invalid")
	}
	mask := byte(0x80)
	length := 1
	for length <= maxLength && first[0]&mask == 0 {
		mask >>= 1
		length++
	}
	if length > maxLength || mask == 0 {
		return 0, 0, false, errors.New("EBML variable integer is too long")
	}
	encoded := make([]byte, length)
	if err := readPlatformArtifactAt(content, offset, encoded); err != nil {
		return 0, 0, false, errors.New("EBML variable integer is truncated")
	}
	value := uint64(encoded[0])
	if !keepMarker {
		value = uint64(encoded[0] & (mask - 1))
	}
	for _, character := range encoded[1:] {
		value = value<<8 | uint64(character)
	}
	unknown := false
	if !keepMarker {
		unknownValue := uint64(1)<<(7*length) - 1
		unknown = value == unknownValue
	}
	return value, int64(length), unknown, nil
}

func readPlatformEBMLElement(content io.ReaderAt, offset int64, limit int64) (platformEBMLElement, error) {
	if offset < 0 || offset >= limit {
		return platformEBMLElement{}, errors.New("EBML element header is truncated")
	}
	id, idLength, _, err := readPlatformEBMLVINT(content, offset, 4, true)
	if err != nil {
		return platformEBMLElement{}, err
	}
	size, sizeLength, unknown, err := readPlatformEBMLVINT(content, offset+idLength, 8, false)
	if err != nil {
		return platformEBMLElement{}, err
	}
	dataStart := offset + idLength + sizeLength
	if dataStart > limit {
		return platformEBMLElement{}, errors.New("EBML element header exceeds its container")
	}
	end := limit
	if !unknown {
		if size > uint64(limit-dataStart) {
			return platformEBMLElement{}, errors.New("EBML element length is invalid")
		}
		end = dataStart + int64(size)
	}
	return platformEBMLElement{id: id, start: offset, dataStart: dataStart, end: end, unknown: unknown}, nil
}

func validatePlatformWebM(ctx context.Context, content io.ReaderAt, sizeBytes int64) error {
	if sizeBytes < 48 {
		return errors.New("WebM is truncated")
	}
	header, err := readPlatformEBMLElement(content, 0, sizeBytes)
	if err != nil || header.id != 0x1a45dfa3 || header.unknown || header.end-header.dataStart > 1024*1024 {
		return errors.New("WebM EBML header is invalid")
	}
	docType, err := platformWebMHeaderDocType(ctx, content, header)
	if err != nil {
		return err
	}
	if docType != "webm" {
		return errors.New("WebM EBML DocType is not webm")
	}

	offset := header.end
	for elementCount := 0; elementCount < platformArtifactMaxBoxCount && offset < sizeBytes; elementCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return err
		}
		element, err := readPlatformEBMLElement(content, offset, sizeBytes)
		if err != nil {
			return err
		}
		if element.id == 0x18538067 {
			return validatePlatformWebMSegment(ctx, content, element)
		}
		if element.unknown || element.end <= offset {
			return errors.New("WebM root element is invalid")
		}
		offset = element.end
	}
	return errors.New("WebM Segment is missing")
}

func platformWebMHeaderDocType(
	ctx context.Context,
	content io.ReaderAt,
	header platformEBMLElement,
) (string, error) {
	offset := header.dataStart
	for elementCount := 0; elementCount < 256 && offset < header.end; elementCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return "", err
		}
		element, err := readPlatformEBMLElement(content, offset, header.end)
		if err != nil || element.unknown {
			return "", errors.New("WebM EBML header child is invalid")
		}
		if element.id == 0x4282 {
			length := element.end - element.dataStart
			if length < 1 || length > 16 {
				return "", errors.New("WebM DocType is invalid")
			}
			value := make([]byte, length)
			if err := readPlatformArtifactAt(content, element.dataStart, value); err != nil {
				return "", errors.New("WebM DocType is truncated")
			}
			return string(value), nil
		}
		offset = element.end
	}
	return "", errors.New("WebM DocType is missing")
}

func validatePlatformWebMSegment(
	ctx context.Context,
	content io.ReaderAt,
	segment platformEBMLElement,
) error {
	offset := segment.dataStart
	sawInfo := false
	sawVideoTrack := false
	sawMediaBlock := false
	for elementCount := 0; elementCount < platformArtifactMaxBoxCount && offset < segment.end; elementCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return err
		}
		element, err := readPlatformEBMLElement(content, offset, segment.end)
		if err != nil {
			return err
		}
		switch element.id {
		case 0x1549a966: // Info
			sawInfo = true
		case 0x1654ae6b: // Tracks
			hasVideo, err := platformWebMTracksHaveVideo(ctx, content, element)
			if err != nil {
				return err
			}
			sawVideoTrack = sawVideoTrack || hasVideo
		case 0x1f43b675: // Cluster
			hasBlock, err := platformWebMClusterHasMediaBlock(ctx, content, element)
			if err != nil {
				return err
			}
			sawMediaBlock = sawMediaBlock || hasBlock
		}
		if element.unknown {
			break
		}
		if element.end <= offset {
			return errors.New("WebM Segment element did not advance")
		}
		offset = element.end
	}
	if !sawInfo || !sawVideoTrack || !sawMediaBlock {
		return errors.New("WebM requires Info, a video TrackEntry, and media blocks")
	}
	return nil
}

func platformWebMTracksHaveVideo(
	ctx context.Context,
	content io.ReaderAt,
	tracks platformEBMLElement,
) (bool, error) {
	if tracks.unknown {
		return false, errors.New("WebM Tracks length is unknown")
	}
	offset := tracks.dataStart
	for elementCount := 0; elementCount < platformArtifactMaxBoxCount && offset < tracks.end; elementCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return false, err
		}
		element, err := readPlatformEBMLElement(content, offset, tracks.end)
		if err != nil || element.unknown {
			return false, errors.New("WebM Tracks element is invalid")
		}
		if element.id == 0xae {
			hasVideo, err := platformWebMTrackEntryIsVideo(ctx, content, element)
			if err != nil {
				return false, err
			}
			if hasVideo {
				return true, nil
			}
		}
		offset = element.end
	}
	return false, nil
}

func platformWebMTrackEntryIsVideo(
	ctx context.Context,
	content io.ReaderAt,
	entry platformEBMLElement,
) (bool, error) {
	offset := entry.dataStart
	trackType := uint64(0)
	codecID := ""
	for elementCount := 0; elementCount < 1024 && offset < entry.end; elementCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return false, err
		}
		element, err := readPlatformEBMLElement(content, offset, entry.end)
		if err != nil || element.unknown {
			return false, errors.New("WebM TrackEntry child is invalid")
		}
		switch element.id {
		case 0x83: // TrackType
			length := element.end - element.dataStart
			if length < 1 || length > 8 {
				return false, errors.New("WebM TrackType is invalid")
			}
			value := make([]byte, length)
			if err := readPlatformArtifactAt(content, element.dataStart, value); err != nil {
				return false, err
			}
			for _, character := range value {
				trackType = trackType<<8 | uint64(character)
			}
		case 0x86: // CodecID
			length := element.end - element.dataStart
			if length < 3 || length > 128 {
				return false, errors.New("WebM CodecID is invalid")
			}
			value := make([]byte, length)
			if err := readPlatformArtifactAt(content, element.dataStart, value); err != nil {
				return false, err
			}
			codecID = string(value)
		}
		offset = element.end
	}
	return trackType == 1 && strings.HasPrefix(codecID, "V_"), nil
}

func platformWebMClusterHasMediaBlock(
	ctx context.Context,
	content io.ReaderAt,
	cluster platformEBMLElement,
) (bool, error) {
	offset := cluster.dataStart
	for elementCount := 0; elementCount < platformArtifactMaxBoxCount && offset < cluster.end; elementCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return false, err
		}
		element, err := readPlatformEBMLElement(content, offset, cluster.end)
		if err != nil {
			return false, err
		}
		switch element.id {
		case 0xa3: // SimpleBlock
			return validatePlatformWebMBlock(content, element)
		case 0xa0: // BlockGroup
			hasBlock, err := platformWebMBlockGroupHasBlock(ctx, content, element)
			if err != nil || hasBlock {
				return hasBlock, err
			}
		}
		if element.unknown {
			break
		}
		offset = element.end
	}
	return false, nil
}

func platformWebMBlockGroupHasBlock(
	ctx context.Context,
	content io.ReaderAt,
	group platformEBMLElement,
) (bool, error) {
	if group.unknown {
		return false, errors.New("WebM BlockGroup length is unknown")
	}
	offset := group.dataStart
	for elementCount := 0; elementCount < 1024 && offset < group.end; elementCount++ {
		if err := checkPlatformArtifactValidationContext(ctx); err != nil {
			return false, err
		}
		element, err := readPlatformEBMLElement(content, offset, group.end)
		if err != nil || element.unknown {
			return false, errors.New("WebM BlockGroup child is invalid")
		}
		if element.id == 0xa1 {
			return validatePlatformWebMBlock(content, element)
		}
		offset = element.end
	}
	return false, nil
}

func validatePlatformWebMBlock(content io.ReaderAt, block platformEBMLElement) (bool, error) {
	length := block.end - block.dataStart
	if length < 4 {
		return false, errors.New("WebM media block is truncated")
	}
	trackNumber, vintLength, unknown, err := readPlatformEBMLVINT(content, block.dataStart, 8, false)
	if err != nil || unknown || trackNumber == 0 || vintLength+3 >= length {
		return false, errors.New("WebM media block header is invalid")
	}
	return true, nil
}

func (downloader *PlatformArtifactDownloader) validateAndResolveURL(
	ctx context.Context,
	sourceURL string,
) (*url.URL, int, error) {
	parsed, err := url.Parse(sourceURL)
	if err != nil || parsed.Hostname() == "" || parsed.User != nil || parsed.Fragment != "" {
		return nil, 0, fmt.Errorf("%w: artifact URL is invalid", ErrPlatformArtifactSecurity)
	}
	scheme := strings.ToLower(parsed.Scheme)
	if downloader.config.Production {
		if scheme != "https" {
			return nil, 0, fmt.Errorf("%w: production artifact URLs must use HTTPS", ErrPlatformArtifactSecurity)
		}
	} else if scheme != "http" && scheme != "https" {
		return nil, 0, fmt.Errorf("%w: artifact URL must use HTTP(S)", ErrPlatformArtifactSecurity)
	}
	if strings.Contains(parsed.Hostname(), "%") {
		return nil, 0, fmt.Errorf("%w: scoped addresses are forbidden", ErrPlatformArtifactSecurity)
	}
	port := 443
	if scheme == "http" {
		port = 80
	}
	if parsed.Port() != "" {
		port, err = strconv.Atoi(parsed.Port())
		if err != nil || port < 1 || port > 65535 {
			return nil, 0, fmt.Errorf("%w: artifact URL port is invalid", ErrPlatformArtifactSecurity)
		}
	}
	if downloader.config.Production && port != 443 {
		return nil, 0, fmt.Errorf("%w: production artifact URLs must use HTTPS port 443", ErrPlatformArtifactSecurity)
	}
	if err := ctx.Err(); err != nil {
		return nil, 0, err
	}
	return parsed, port, nil
}

func (downloader *PlatformArtifactDownloader) resolvePublicAddresses(
	ctx context.Context,
	host string,
) ([]netip.Addr, error) {
	if parsed, err := netip.ParseAddr(host); err == nil {
		parsed = parsed.Unmap()
		if !platformArtifactAddressIsPublic(parsed) {
			return nil, fmt.Errorf("%w: artifact URL resolved to a non-public address", ErrPlatformArtifactSecurity)
		}
		return []netip.Addr{parsed}, nil
	}
	records, err := downloader.resolver.LookupIPAddr(ctx, host)
	if err != nil {
		return nil, fmt.Errorf("%w: artifact hostname did not resolve", ErrPlatformArtifactSecurity)
	}
	addresses := make([]netip.Addr, 0, len(records))
	seen := make(map[netip.Addr]struct{}, len(records))
	for _, record := range records {
		address, ok := netip.AddrFromSlice(record.IP)
		if !ok {
			return nil, fmt.Errorf("%w: artifact hostname returned an invalid address", ErrPlatformArtifactSecurity)
		}
		address = address.Unmap()
		if !platformArtifactAddressIsPublic(address) {
			return nil, fmt.Errorf("%w: artifact URL resolved to a non-public address", ErrPlatformArtifactSecurity)
		}
		if _, duplicate := seen[address]; duplicate {
			continue
		}
		seen[address] = struct{}{}
		addresses = append(addresses, address)
	}
	if len(addresses) == 0 {
		return nil, fmt.Errorf("%w: artifact hostname did not resolve", ErrPlatformArtifactSecurity)
	}
	return addresses, nil
}

func platformArtifactAddressIsPublic(address netip.Addr) bool {
	if !address.IsValid() || !address.IsGlobalUnicast() || address.IsPrivate() || address.IsLoopback() || address.IsLinkLocalUnicast() {
		return false
	}
	for _, prefix := range platformArtifactNonPublicPrefixes {
		if prefix.Contains(address) {
			return false
		}
	}
	return true
}

func normalizePlatformArtifactSHA256(value string) (string, error) {
	normalized := strings.ToLower(strings.TrimSpace(value))
	normalized = strings.TrimPrefix(normalized, "sha256:")
	if len(normalized) != sha256.Size*2 {
		return "", fmt.Errorf("%w: SHA-256 metadata is invalid", ErrPlatformArtifactIntegrity)
	}
	decoded, err := hex.DecodeString(normalized)
	if err != nil || len(decoded) != sha256.Size {
		return "", fmt.Errorf("%w: SHA-256 metadata is invalid", ErrPlatformArtifactIntegrity)
	}
	return normalized, nil
}
