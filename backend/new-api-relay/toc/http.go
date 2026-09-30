package toc

import (
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

func reply(c *gin.Context, data any, err error) {
	if err == nil {
		c.JSON(200, data)
		return
	}
	var expected *Error
	if errors.As(err, &expected) {
		c.JSON(expected.Status, gin.H{"code": expected.Code, "detail": expected.Message})
		return
	}
	c.JSON(503, gin.H{"code": "TOC_UNAVAILABLE", "detail": "服务暂时不可用，请保留原请求后重试"})
}
func body(c *gin.Context, target any) bool {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 128*1024)
	raw, err := io.ReadAll(c.Request.Body)
	if err != nil || strictJSON(raw, target) != nil {
		reply(c, nil, fail(422, "TOC_REQUEST_INVALID", "请求格式或字段无效"))
		return false
	}
	return true
}
func owner(c *gin.Context) int { return c.GetInt("id") }
func pathUser(c *gin.Context) (int, bool) {
	id, err := strconv.Atoi(c.Param("user_id"))
	if err != nil || id <= 0 {
		reply(c, nil, fail(422, "TOC_USER_INVALID", "用户标识无效"))
		return 0, false
	}
	return id, true
}
func personalCapabilities() map[string]bool {
	return map[string]bool{"generation": true, "models": true, "tasks": true, "artworks": true, "assets": true, "artifact_access": true, "publishing": false, "task_cancel": false}
}
func (s *Service) me(user int) (map[string]any, error) {
	var u model.User
	if err := s.DB.First(&u, "id = ?", user).Error; err != nil {
		return nil, err
	}
	return map[string]any{"workspace_id": workspace(user), "user": map[string]any{"id": strconv.Itoa(u.Id), "email": u.Email, "display_name": u.DisplayName}, "user_id": strconv.Itoa(u.Id), "display_name": u.DisplayName, "email": u.Email, "capabilities": personalCapabilities(), "billing_unit": "POINT", "billing_version": 2, "billing_scope": "personal", "billing_authority": "relay_toc", "backend_mode": "relay_toc", "credit_kind": "test_credit"}, nil
}

// Browser mutation endpoints require a live Relay session identity and an
// exact configured origin. PATs and arbitrary Origin/forwarded-host values do
// not become a CSRF bypass; no bearer credential is ever placed into a URL.
func mutationGuard() gin.HandlerFunc {
	return func(c *gin.Context) {
		if c.Request.Method == http.MethodGet || c.Request.Method == http.MethodHead {
			c.Next()
			return
		}
		expected := strings.TrimRight(os.Getenv("TOC_CLIENT_ORIGIN"), "/")
		if expected == "" {
			expected = strings.TrimRight(os.Getenv("TOC_PUBLIC_BASE_URL"), "/")
		}
		values := c.Request.Header.Values("Origin")
		_, session := middleware.GetSessionAuthIdentity(c)
		apiOrigin := strings.TrimRight(os.Getenv("TOC_API_PUBLIC_BASE_URL"), "/")
		allowed := len(values) == 1 && ((expected != "" && values[0] == expected) || (apiOrigin != "" && values[0] == apiOrigin))
		if !session || !allowed {
			c.AbortWithStatusJSON(403, gin.H{"code": "TOC_ORIGIN_FORBIDDEN", "detail": "需要同源的有效登录会话"})
			return
		}
		c.Next()
	}
}
func RegisterRoutes(engine *gin.Engine, s *Service) {
	if os.Getenv("RELAY_RUNTIME_PROFILE") != "toc" || s == nil {
		return
	}
	// The native New API profile is an operator/account console. It is not the
	// personal TOC product surface; send direct visits back to the Studio entry
	// so ordinary users cannot mistake the inherited console for their home.
	if clientOrigin := strings.TrimRight(os.Getenv("TOC_CLIENT_ORIGIN"), "/"); clientOrigin != "" {
		// The Relay process still embeds the upstream New API web console. In
		// TOC mode it is an implementation detail, not a customer entry point:
		// intercept the browser root before the embedded static handler can serve
		// the inherited landing/profile shell.
		engine.Use(func(c *gin.Context) {
			if (c.Request.Method == http.MethodGet || c.Request.Method == http.MethodHead) && c.Request.URL.Path == "/" {
				c.Redirect(http.StatusSeeOther, clientOrigin+"/creation")
				c.Abort()
				return
			}
			c.Next()
		})
		engine.GET("/profile", func(c *gin.Context) {
			c.Redirect(http.StatusSeeOther, clientOrigin+"/creation")
		})
	}
	api := engine.Group("/api/v1", middleware.GlobalAPIRateLimit())
	personal := api.Group("/personal", middleware.UserAuth(), middleware.DisableCache(), mutationGuard())
	personal.GET("/me", func(c *gin.Context) { data, err := s.me(owner(c)); reply(c, data, err) })
	personal.GET("/wallet", func(c *gin.Context) { data, err := s.Wallet(owner(c)); reply(c, data, err) })
	personal.GET("/models", func(c *gin.Context) { data, err := s.Models(owner(c), false); reply(c, data, err) })
	personal.GET("/model-catalog", func(c *gin.Context) { data, err := s.Models(owner(c), true); reply(c, data, err) })
	api.GET("/session/surfaces", middleware.UserAuth(), middleware.DisableCache(), func(c *gin.Context) {
		data, err := s.me(owner(c))
		if err != nil {
			reply(c, nil, err)
			return
		}
		reply(c, gin.H{"user": data["user"], "account_type": "personal", "active_product_context": "personal", "available_product_contexts": []string{"personal"}, "backend_mode": "relay_toc", "billing_authority": "relay_toc", "personal": gin.H{"workspace_id": workspace(owner(c)), "label": "个人工作区 · 测试积分", "capabilities": personalCapabilities()}, "company": nil, "platform": nil}, nil)
	})
	personal.POST("/tasks", func(c *gin.Context) {
		var input CreateTaskRequest
		if !body(c, &input) {
			return
		}
		if c.GetHeader("Idempotency-Key") != input.IdempotencyKey {
			reply(c, nil, fail(422, "TOC_IDEMPOTENCY_REQUIRED", "请求体与幂等请求头必须一致"))
			return
		}
		task, err := s.CreateTask(c.Request.Context(), owner(c), input)
		if err != nil {
			reply(c, nil, err)
			return
		}
		data, err := s.Task(c.Request.Context(), owner(c), task.ID)
		reply(c, data, err)
	})
	personal.GET("/tasks/:task_id", func(c *gin.Context) {
		data, err := s.Task(c.Request.Context(), owner(c), c.Param("task_id"))
		reply(c, data, err)
	})
	personal.GET("/tasks", func(c *gin.Context) { s.taskList(c, false) })
	personal.GET("/artworks", func(c *gin.Context) { s.taskList(c, true) })
	for _, action := range []string{"preview", "download"} {
		personal.GET("/tasks/:task_id/artifacts/:asset_id/"+action, func(c *gin.Context) {
			var task Task
			if err := s.DB.First(&task, "id = ? AND user_id = ?", c.Param("task_id"), owner(c)).Error; err != nil {
				reply(c, nil, fail(404, "TOC_TASK_NOT_FOUND", "任务不存在"))
				return
			}
			data, err := s.Executor.Download(c.Request.Context(), task, c.Param("asset_id"))
			reply(c, data, err)
		})
	}
	personal.POST("/assets", func(c *gin.Context) {
		// Reserve bounded memory before multipart parsing or reading file bytes.
		// Keep this permit through normalization and persistence; Upload also
		// protects decoding for callers that do not enter through HTTP.
		release, err := assetReceiveAdmission.acquire(owner(c))
		if err != nil {
			c.Header("Retry-After", "1")
			reply(c, nil, err)
			return
		}
		defer release()
		c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, MaxAssetBytes+1024*1024)
		if err := c.Request.ParseMultipartForm(MaxAssetBytes + 1024*1024); err != nil {
			reply(c, nil, fail(422, "TOC_UPLOAD_INVALID", "上传文件过大或格式无效"))
			return
		}
		defer c.Request.MultipartForm.RemoveAll()
		file, header, err := c.Request.FormFile("file")
		if err != nil {
			reply(c, nil, fail(422, "TOC_FILE_REQUIRED", "请选择图片文件"))
			return
		}
		defer file.Close()
		raw, err := io.ReadAll(io.LimitReader(file, MaxAssetBytes+1))
		if err != nil {
			reply(c, nil, err)
			return
		}
		asset, err := s.Upload(owner(c), c.GetHeader("Idempotency-Key"), header.Filename, c.PostForm("media_type"), c.PostForm("normalization_profile"), raw)
		reply(c, assetResponse(asset), err)
	})
	personal.GET("/assets", func(c *gin.Context) {
		var rows []Asset
		err := s.DB.Where("user_id = ? AND deleted = ?", owner(c), false).Order("created_at DESC,id").Limit(200).Find(&rows).Error
		items := []map[string]any{}
		for _, row := range rows {
			items = append(items, assetResponse(row))
		}
		reply(c, gin.H{"items": items, "total": len(items), "page": 1, "page_size": 200}, err)
	})
	for _, action := range []string{"preview", "download"} {
		personal.GET("/assets/:asset_id/"+action, func(c *gin.Context) { data, err := s.AssetAccess(owner(c), c.Param("asset_id")); reply(c, data, err) })
	}
	personal.DELETE("/assets/:asset_id", func(c *gin.Context) {
		err := s.DB.Transaction(func(tx *gorm.DB) error {
			if _, err := lockWallet(tx, owner(c)); err != nil {
				return err
			}
			var asset Asset
			if err := tx.First(&asset, "id = ? AND user_id = ?", c.Param("asset_id"), owner(c)).Error; err != nil {
				return fail(404, "TOC_ASSET_NOT_FOUND", "素材不存在")
			}
			var held int64
			if err := tx.Model(&Task{}).Where("user_id = ? AND settlement_state = ? AND request_json LIKE ?", owner(c), "held", "%"+asset.ID+"%").Count(&held).Error; err != nil {
				return err
			}
			if held > 0 {
				return fail(409, "TOC_ASSET_IN_USE", "任务仍在使用此素材，请先完成状态核对")
			}
			return tx.Model(&asset).Update("deleted", true).Error
		})
		reply(c, gin.H{"deleted": err == nil}, err)
	})
	engine.GET("/api/v1/toc-media/:asset_id", middleware.DisableCache(), func(c *gin.Context) {
		data, err := s.ReadSignedAsset(c.Param("asset_id"), c.Query("expires"), c.Query("signature"))
		if err != nil {
			reply(c, nil, err)
			return
		}
		c.Header("X-Content-Type-Options", "nosniff")
		c.Header("Content-Disposition", "inline; filename=reference.png")
		c.Data(200, "image/png", data)
	})
	admin := api.Group("/toc-admin", middleware.RootAuth(), middleware.DisableCache(), mutationGuard())
	admin.GET("/config", func(c *gin.Context) {
		reply(c, gin.H{"runtime_profile": "toc", "billing_authority": "relay_toc", "credit_kind": "test_credit", "paid_credit_purchase": false}, nil)
	})
	admin.GET("/catalog", func(c *gin.Context) {
		var rows []CatalogModel
		err := s.DB.Order("created_at,id").Find(&rows).Error
		if rows == nil {
			rows = []CatalogModel{}
		}
		reply(c, rows, err)
	})
	admin.GET("/relay-models", func(c *gin.Context) {
		data, err := s.Executor.Evidence(s.DB, "seedream-5")
		reply(c, []ModelEvidence{data}, err)
	})
	admin.PUT("/catalog/:slug", func(c *gin.Context) {
		var input CatalogRequest
		if !body(c, &input) {
			return
		}
		data, err := s.SaveCatalog(c.Request.Context(), owner(c), c.Param("slug"), input)
		reply(c, data, err)
	})
	admin.GET("/users/:user_id/grants", func(c *gin.Context) {
		user, ok := pathUser(c)
		if !ok {
			return
		}
		var rows []Grant
		err := s.DB.Where("user_id = ?", user).Find(&rows).Error
		if rows == nil {
			rows = []Grant{}
		}
		reply(c, rows, err)
	})
	admin.PUT("/users/:user_id/grants", func(c *gin.Context) {
		user, ok := pathUser(c)
		if !ok {
			return
		}
		var input GrantRequest
		if !body(c, &input) {
			return
		}
		data, err := s.SaveGrant(owner(c), user, input)
		reply(c, data, err)
	})
	admin.POST("/users/:user_id/test-credit", func(c *gin.Context) {
		user, ok := pathUser(c)
		if !ok {
			return
		}
		var input CreditRequest
		if !body(c, &input) {
			return
		}
		entry, err := s.Credit(owner(c), user, input)
		if err != nil {
			reply(c, nil, err)
			return
		}
		wallet, err := s.Wallet(user)
		reply(c, gin.H{"wallet": wallet, "ledger_entry": entry, "credit_kind": "test_credit"}, err)
	})
	admin.GET("/users/:user_id/wallet", func(c *gin.Context) {
		user, ok := pathUser(c)
		if !ok {
			return
		}
		data, err := s.Wallet(user)
		reply(c, data, err)
	})
	admin.GET("/users", func(c *gin.Context) {
		var rows []struct {
			ID          int    `json:"id"`
			Username    string `json:"username"`
			DisplayName string `json:"display_name"`
			Status      int    `json:"status"`
		}
		err := s.DB.Model(&model.User{}).Select("id,username,display_name,status").Order("id").Limit(100).Scan(&rows).Error
		reply(c, rows, err)
	})
	admin.GET("/ledger", func(c *gin.Context) {
		var rows []LedgerEntry
		err := s.DB.Order("created_at DESC,id").Limit(100).Find(&rows).Error
		reply(c, rows, err)
	})
	engine.GET("/toc-return", func(c *gin.Context) {
		target := strings.TrimRight(os.Getenv("TOC_CLIENT_ORIGIN"), "/")
		parsed, err := url.Parse(target)
		if err != nil || parsed.Host == "" || parsed.RawQuery != "" || parsed.Fragment != "" || c.Request.URL.RawQuery != "" {
			c.AbortWithStatus(400)
			return
		}
		c.Header("Cache-Control", "no-store")
		c.Redirect(http.StatusSeeOther, target+"/creation")
	})
}

func (s *Service) taskList(c *gin.Context, artworks bool) {
	page, _ := strconv.Atoi(c.DefaultQuery("page", "1"))
	size, _ := strconv.Atoi(c.DefaultQuery("page_size", "20"))
	if page < 1 {
		page = 1
	}
	if size < 1 || size > 50 {
		size = 20
	}
	query := s.DB.Model(&Task{}).Where("user_id = ?", owner(c))
	if artworks {
		query = query.Where("settlement_state = ?", "settled")
	}
	var total int64
	if err := query.Count(&total).Error; err != nil {
		reply(c, nil, err)
		return
	}
	var rows []Task
	if err := query.Order("created_at DESC,id").Offset((page - 1) * size).Limit(size).Find(&rows).Error; err != nil {
		reply(c, nil, err)
		return
	}
	modelNames := map[string]string{}
	if artworks && len(rows) > 0 {
		modelIDs := make([]string, 0, len(rows))
		for _, row := range rows {
			modelIDs = append(modelIDs, row.ModelID)
		}
		var models []CatalogModel
		// Current catalog labels are display metadata only. Historical POINT
		// amounts below come exclusively from each task's immutable quote and
		// recorded settlement, even if this model has since been repriced.
		if err := s.DB.Select("id", "display_name").Where("id IN ?", modelIDs).Find(&models).Error; err != nil {
			reply(c, nil, err)
			return
		}
		for _, model := range models {
			modelNames[model.ID] = model.DisplayName
		}
	}
	items := []map[string]any{}
	for _, row := range rows {
		item, err := s.Task(c.Request.Context(), owner(c), row.ID)
		if err != nil {
			reply(c, nil, err)
			return
		}
		if !artworks {
			items = append(items, item)
			continue
		}
		snapshot, err := s.Executor.Snapshot(c.Request.Context(), row)
		if err != nil {
			reply(c, nil, err)
			return
		}
		requestPayload, err := object(row.RequestJSON)
		if err != nil {
			reply(c, nil, err)
			return
		}
		for _, asset := range snapshot.Outputs {
			items = append(items, map[string]any{
				"id": row.ID + ":" + asset.AssetID, "artifact_id": asset.AssetID, "task_id": row.ID, "asset_id": asset.AssetID,
				"model_id": row.ModelID, "model_display_name": modelNames[row.ModelID],
				"media_type": asset.MediaType, "content_type": asset.ContentType, "size_bytes": asset.SizeBytes, "sha256": asset.SHA256,
				"request_payload": requestPayload, "created_at": row.CreatedAt,
				"actual_cost_points": item["actual_cost_points"], "quote_points": item["quote_points"], "reserved_points": item["reserved_points"],
				"billing_unit": "POINT", "billing_version": 2, "billing_scope": "personal", "billing_authority": item["billing_authority"],
			})
		}
	}
	reply(c, gin.H{"items": items, "total": total, "page": page, "page_size": size, "billing_unit": "POINT", "billing_version": 2, "billing_authority": "relay_toc"}, nil)
}
