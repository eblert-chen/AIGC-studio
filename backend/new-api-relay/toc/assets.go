package toc

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	_ "golang.org/x/image/webp"
	"image"
	_ "image/jpeg"
	"image/png"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"gorm.io/gorm"
)

const MaxAssetBytes = 20 * 1024 * 1024

const MaxRetainedAssetBytes int64 = 200 * 1024 * 1024

const maxAssetDecodeConcurrency = 2

type assetUploadAdmission struct {
	sync.Mutex
	users map[int]bool
}

var assetDecodeAdmission = assetUploadAdmission{users: make(map[int]bool)}

// HTTP reception and decoding have separate permits so an admitted request
// can enter Upload without reacquiring its own permit. Both are process-wide.
var assetReceiveAdmission = assetUploadAdmission{users: make(map[int]bool)}

var errAssetEncodingLimit = errors.New("normalized asset exceeds the byte limit")

type assetEncodingBuffer struct{ buffer bytes.Buffer }

func (buffer *assetEncodingBuffer) Len() int      { return buffer.buffer.Len() }
func (buffer *assetEncodingBuffer) Bytes() []byte { return buffer.buffer.Bytes() }

func (buffer *assetEncodingBuffer) Write(data []byte) (int, error) {
	if len(data) > MaxAssetBytes-buffer.Len() {
		return 0, errAssetEncodingLimit
	}
	return buffer.buffer.Write(data)
}

func acquireAssetDecode(user int) (func(), error) {
	return assetDecodeAdmission.acquire(user)
}

func (admission *assetUploadAdmission) acquire(user int) (func(), error) {
	admission.Lock()
	defer admission.Unlock()
	if admission.users[user] || len(admission.users) >= maxAssetDecodeConcurrency {
		return nil, fail(429, "TOC_ASSET_UPLOAD_BUSY", "图片处理繁忙，请稍后使用原上传标识重试")
	}
	admission.users[user] = true
	return func() {
		admission.Lock()
		delete(admission.users, user)
		admission.Unlock()
	}, nil
}

type AssetStore struct {
	Root, PublicBase, RelayBase string
	secret                      []byte
	retainedByteLimit           int64
}

func NewAssetStore(root, publicBase, secret string) (*AssetStore, error) {
	u, err := url.Parse(publicBase)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") || (u.Scheme != "https" && !(u.Scheme == "http" && (u.Hostname() == "127.0.0.1" || u.Hostname() == "localhost"))) {
		return nil, fmt.Errorf("TOC public base requires HTTPS or exact loopback origin")
	}
	if len(secret) < 32 || strings.TrimSpace(root) == "" {
		return nil, fmt.Errorf("TOC private asset root and signing secret required")
	}
	absolute, err := filepath.Abs(root)
	if err != nil {
		return nil, err
	}
	if err = os.MkdirAll(absolute, 0700); err != nil {
		return nil, err
	}
	resolved, err := filepath.EvalSymlinks(absolute)
	if err != nil {
		return nil, err
	}
	if resolved != absolute {
		return nil, fmt.Errorf("TOC asset root may not be a symbolic link")
	}
	return &AssetStore{Root: absolute, PublicBase: strings.TrimRight(publicBase, "/"), RelayBase: strings.TrimRight(publicBase, "/"), secret: []byte(secret), retainedByteLimit: MaxRetainedAssetBytes}, nil
}

func (store *AssetStore) SetRelayBase(base string) error {
	u, err := url.Parse(base)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return fmt.Errorf("TOC relay asset base requires an HTTPS origin")
	}
	store.RelayBase = strings.TrimRight(base, "/")
	return nil
}
func (store *AssetStore) path(id string) (string, error) {
	if _, err := uuid.Parse(id); err != nil {
		return "", fmt.Errorf("invalid asset identity")
	}
	return filepath.Join(store.Root, id+".png"), nil
}
func (store *AssetStore) signature(asset Asset, expires int64) string {
	mac := hmac.New(sha256.New, store.secret)
	fmt.Fprintf(mac, "toc-input-v1\n%s\n%d\n%s\n%d", asset.ID, asset.UserID, asset.SHA256, expires)
	return hex.EncodeToString(mac.Sum(nil))
}
func (store *AssetStore) InputURL(asset Asset, expiry time.Time) (string, error) {
	return store.signedURL(asset, expiry, store.RelayBase)
}
func (store *AssetStore) PublicURL(asset Asset, expiry time.Time) (string, error) {
	return store.signedURL(asset, expiry, store.PublicBase)
}
func (store *AssetStore) signedURL(asset Asset, expiry time.Time, base string) (string, error) {
	if _, err := store.read(asset); err != nil {
		return "", err
	}
	expires := expiry.Unix()
	if expires <= time.Now().Unix() || expires > time.Now().Add(time.Hour).Unix() {
		return "", fmt.Errorf("TOC asset lease outside allowed window")
	}
	return fmt.Sprintf("%s/api/v1/toc-media/%s?expires=%d&signature=%s", base, asset.ID, expires, store.signature(asset, expires)), nil
}
func (store *AssetStore) read(asset Asset) ([]byte, error) {
	path, err := store.path(asset.ID)
	if err != nil {
		return nil, err
	}
	stat, err := os.Lstat(path)
	if err != nil || !stat.Mode().IsRegular() || stat.Size() != asset.SizeBytes || stat.Size() > MaxAssetBytes {
		return nil, fail(404, "TOC_ASSET_UNAVAILABLE", "素材文件不可用")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256(data)
	if hex.EncodeToString(sum[:]) != asset.SHA256 {
		return nil, fail(409, "TOC_ASSET_INTEGRITY", "素材完整性校验失败")
	}
	return data, nil
}
func (s *Service) Upload(user int, key, filename, media, normalization string, raw []byte) (Asset, error) {
	var result Asset
	if s.Assets == nil || s.Assets.retainedByteLimit <= 0 || s.Assets.retainedByteLimit > MaxRetainedAssetBytes {
		return result, fail(503, "TOC_ASSETS_UNAVAILABLE", "素材存储尚未配置")
	}
	if user <= 0 || !validKey(key) || (media != "" && media != "image") || (normalization != "" && normalization != "image_reference_v1") || len(raw) == 0 || len(raw) > MaxAssetBytes {
		return result, fail(422, "TOC_IMAGE_INVALID", "首版仅接受20MB内的普通图片素材")
	}
	rawSum := sha256.Sum256(raw)
	sha, err := digest(struct{ Hash, Media, Normalization, Name string }{hex.EncodeToString(rawSum[:]), media, normalization, filename})
	if err != nil {
		return result, err
	}
	// Exact retries are reads, including while decoding is saturated or the
	// user's retained storage is full. Repeat this check under the write lock.
	if found, err := findAssetUploadReplay(s.DB, user, key, sha, &result); found || err != nil {
		return result, err
	}
	release, err := acquireAssetDecode(user)
	if err != nil {
		return result, err
	}
	defer release()
	if err := s.checkAssetCapacity(s.DB, user, 1); err != nil {
		return result, err
	}
	config, _, err := image.DecodeConfig(bytes.NewReader(raw))
	if err != nil || config.Width < 1 || config.Height < 1 || config.Width > 8192 || config.Height > 8192 || int64(config.Width)*int64(config.Height) > 32000000 {
		return result, fail(422, "TOC_IMAGE_INVALID", "图片格式或像素尺寸不受支持")
	}
	img, _, err := image.Decode(bytes.NewReader(raw))
	if err != nil {
		return result, fail(422, "TOC_IMAGE_INVALID", "图片内容无法解码")
	}
	var normalized assetEncodingBuffer
	if err = png.Encode(&normalized, img); err != nil {
		if errors.Is(err, errAssetEncodingLimit) {
			return result, fail(422, "TOC_IMAGE_TOO_LARGE", "规范化后的图片超过20MB")
		}
		return result, err
	}
	data := normalized.Bytes()
	if len(data) > MaxAssetBytes {
		return result, fail(422, "TOC_IMAGE_TOO_LARGE", "规范化后的图片超过20MB")
	}
	sum := sha256.Sum256(data)
	err = s.DB.Transaction(func(tx *gorm.DB) error {
		if _, err := lockWallet(tx, user); err != nil {
			return err
		}
		if found, err := findAssetUploadReplay(tx, user, key, sha, &result); found || err != nil {
			return err
		}
		// The wallet row is used only to serialize this user's asset writers.
		// Storage admission never reads or spends POINT. Deleted rows remain in
		// the sum because their physical files are deliberately retained.
		if err := s.checkAssetCapacity(tx, user, int64(len(data))); err != nil {
			return err
		}
		result = Asset{ID: uuid.NewString(), UserID: user, IdempotencyKey: key, RequestSHA256: sha, Filename: filepath.Base(strings.ReplaceAll(filename, "\\", "/")), ContentType: "image/png", SizeBytes: int64(len(data)), SHA256: hex.EncodeToString(sum[:]), CreatedAt: time.Now().UTC()}
		if len(result.Filename) > 160 {
			result.Filename = "reference.png"
		}
		path, err := s.Assets.path(result.ID)
		if err != nil {
			return err
		}
		file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return err
		}
		_, writeErr := file.Write(data)
		closeErr := file.Close()
		if writeErr != nil {
			_ = os.Remove(path)
			return writeErr
		}
		if closeErr != nil {
			_ = os.Remove(path)
			return closeErr
		}
		if err = tx.Create(&result).Error; err != nil {
			_ = os.Remove(path)
			return err
		}
		return nil
	})
	return result, err
}

func findAssetUploadReplay(db *gorm.DB, user int, key, sha string, result *Asset) (bool, error) {
	err := db.First(result, "user_id = ? AND idempotency_key = ?", user, key).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if result.RequestSHA256 != sha {
		return true, fail(409, "IDEMPOTENCY_KEY_REUSED", "同一上传标识对应不同文件")
	}
	if result.Deleted {
		return true, fail(409, "TOC_ASSET_DELETED", "原上传素材已删除，请使用新的上传标识")
	}
	return true, nil
}

func (s *Service) checkAssetCapacity(db *gorm.DB, user int, additional int64) error {
	var retained int64
	if err := db.Model(&Asset{}).Where("user_id = ?", user).
		Select("COALESCE(SUM(size_bytes), 0)").Scan(&retained).Error; err != nil {
		return err
	}
	if retained < 0 || additional < 1 || additional > s.Assets.retainedByteLimit || retained > s.Assets.retainedByteLimit-additional {
		return fail(413, "TOC_ASSET_STORAGE_LIMIT", "素材保留空间已满（上限200MB，已删除文件仍计入），暂不能继续上传")
	}
	return nil
}
func assetResponse(asset Asset) map[string]any {
	return map[string]any{"id": asset.ID, "asset_id": asset.ID, "workspace_id": workspace(asset.UserID), "user_id": fmt.Sprint(asset.UserID), "filename": asset.Filename, "media_type": "image", "content_type": asset.ContentType, "size_bytes": asset.SizeBytes, "sha256": asset.SHA256, "created_at": asset.CreatedAt, "billing_authority": "relay_toc"}
}
func (s *Service) AssetAccess(user int, id string) (map[string]any, error) {
	var asset Asset
	if err := s.DB.First(&asset, "id = ? AND user_id = ? AND deleted = ?", id, user, false).Error; err != nil {
		return nil, fail(404, "TOC_ASSET_NOT_FOUND", "素材不存在")
	}
	link, err := s.Assets.PublicURL(asset, time.Now().UTC().Add(5*time.Minute))
	if err != nil {
		return nil, err
	}
	return map[string]any{"url": link, "expires_seconds": 300}, nil
}
func (s *Service) ReadSignedAsset(id, expiresText, signature string) ([]byte, error) {
	var asset Asset
	if err := s.DB.First(&asset, "id = ? AND deleted = ?", id, false).Error; err != nil {
		return nil, fail(404, "TOC_ASSET_NOT_FOUND", "素材不存在")
	}
	expires, err := strconv.ParseInt(expiresText, 10, 64)
	if err != nil || expires < time.Now().Unix() || expires > time.Now().Add(time.Hour).Unix() {
		return nil, fail(403, "TOC_ASSET_LEASE_EXPIRED", "素材读取链接已过期")
	}
	provided, err := hex.DecodeString(signature)
	expected, _ := hex.DecodeString(s.Assets.signature(asset, expires))
	if err != nil || !hmac.Equal(provided, expected) {
		return nil, fail(403, "TOC_ASSET_LEASE_INVALID", "素材读取链接无效")
	}
	return s.Assets.read(asset)
}
