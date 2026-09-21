//go:build relay_local_video_lab

package main

import (
	"bytes"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/localvideoconfig"
	"github.com/QuantumNous/new-api/service"
)

type labPaidApproval struct {
	SchemaVersion      int       `json:"schema_version"`
	StateID            string    `json:"state_id"`
	OperationID        string    `json:"operation_id"`
	ProviderModelIDs   []string  `json:"provider_model_ids"`
	MaxProviderCreates int       `json:"max_provider_creates"`
	ExpiresAt          time.Time `json:"expires_at"`
}

type labPaidBudget struct {
	ApprovalSHA256 string `json:"approval_sha256"`
	UsedCreates    int    `json:"used_creates"`
}

type labPaidAuthorization struct {
	approval     labPaidApproval
	models       map[string]bool
	publicModels map[string]bool
	budget       labPaidBudget
	path         string
	mutex        sync.Mutex
}

func loadLabPaidAuthorization(options labOptions, config localvideoconfig.Config, directory string) (*labPaidAuthorization, error) {
	if options.Mode != "live" || !options.AllowPaidProbe {
		return nil, nil
	}
	if !filepath.IsAbs(options.PaidApprovalFile) {
		return nil, errors.New("paid approval must be an absolute private server-side file")
	}
	payload, err := readLabPrivateFile(options.PaidApprovalFile, 16*1024)
	if err != nil {
		return nil, err
	}
	var approval labPaidApproval
	if common.Unmarshal(payload, &approval) != nil || approval.SchemaVersion != 1 || approval.StateID != config.StateID ||
		!regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$`).MatchString(approval.OperationID) ||
		approval.MaxProviderCreates < 1 || approval.MaxProviderCreates > 100 || len(approval.ProviderModelIDs) < 1 ||
		!approval.ExpiresAt.After(time.Now().UTC()) || approval.ExpiresAt.After(time.Now().UTC().Add(24*time.Hour)) {
		return nil, errors.New("paid approval must match this state, a bounded operation, model list, count and expiry within 24 hours")
	}
	configured := map[string]bool{}
	for _, model := range config.Models {
		configured[model.ProviderModelID] = true
	}
	allowed := map[string]bool{}
	publicModels := map[string]bool{}
	for _, id := range approval.ProviderModelIDs {
		if !configured[id] || allowed[id] {
			return nil, errors.New("paid approval model list differs from this configured lab")
		}
		allowed[id] = true
	}
	for _, model := range config.Models {
		if allowed[model.ProviderModelID] {
			publicModels[model.PublicModelID] = true
		}
	}
	canonical, err := common.Marshal(approval)
	if err != nil {
		return nil, err
	}
	identity := sha256.Sum256([]byte(config.StateID + "\x00" + approval.OperationID))
	path := filepath.Join(directory, fmt.Sprintf("paid-budget-%x.json", identity[:12]))
	authorization := &labPaidAuthorization{
		approval: approval, models: allowed, publicModels: publicModels, path: path,
		budget: labPaidBudget{ApprovalSHA256: fmt.Sprintf("%x", sha256.Sum256(canonical))},
	}
	if _, err := os.Stat(path); os.IsNotExist(err) {
		if err := writeLabJSON(path, authorization.budget, true); err != nil {
			return nil, err
		}
		return authorization, nil
	}
	stored, err := readLabPrivateFile(path, 4096)
	var budget labPaidBudget
	if err != nil || common.Unmarshal(stored, &budget) != nil || budget.ApprovalSHA256 != authorization.budget.ApprovalSHA256 || budget.UsedCreates < 0 || budget.UsedCreates > approval.MaxProviderCreates {
		return nil, errors.New("paid approval was changed or its durable usage is invalid")
	}
	authorization.budget = budget
	return authorization, nil
}

func (authorization *labPaidAuthorization) consume(modelID string) error {
	if authorization == nil {
		return errors.New("live provider creates are locked without explicit paid approval")
	}
	authorization.mutex.Lock()
	defer authorization.mutex.Unlock()
	if !authorization.approval.ExpiresAt.After(time.Now().UTC()) || !authorization.models[modelID] || authorization.budget.UsedCreates >= authorization.approval.MaxProviderCreates {
		return errors.New("live provider create is outside the approved model, time or count")
	}
	// Persist before the HTTP side effect. Timeouts and unknown outcomes consume
	// their reservation permanently; restarting cannot reset the paid budget.
	authorization.budget.UsedCreates++
	if err := writeLabJSON(authorization.path, authorization.budget, false); err != nil {
		return errors.New("could not durably reserve a provider create")
	}
	return nil
}

type labLiveGuard struct {
	base          http.RoundTripper
	providerHosts map[string]bool
	internalURL   string
	paid          *labPaidAuthorization
}

func (guard *labLiveGuard) RoundTrip(request *http.Request) (*http.Response, error) {
	if request.URL.User != nil || request.URL.Fragment != "" {
		return nil, errors.New("unsafe URL in live lab")
	}
	if request.URL.Scheme+"://"+request.URL.Host == guard.internalURL && request.URL.Path == "/internal/platform-generations/native-submit" {
		return guard.base.RoundTrip(request)
	}
	if request.URL.Scheme != "https" || !guard.providerHosts[request.URL.Hostname()] || (request.URL.Port() != "" && request.URL.Port() != "443") || !labProviderPath(request) {
		return nil, errors.New("live lab only permits reviewed official video endpoints")
	}
	if request.Method == http.MethodPost {
		if request.Body == nil {
			return nil, errors.New("live provider request body is missing")
		}
		payload, err := io.ReadAll(io.LimitReader(request.Body, 128*1024+1))
		if err != nil || len(payload) > 128*1024 {
			return nil, errors.New("live provider request body is invalid")
		}
		_ = request.Body.Close()
		request.Body = io.NopCloser(bytes.NewReader(payload))
		var binding struct {
			Model string `json:"model"`
		}
		if common.Unmarshal(payload, &binding) != nil {
			return nil, errors.New("live provider model binding is invalid")
		}
		if err := guard.paid.consume(binding.Model); err != nil {
			return nil, err
		}
	}
	return guard.base.RoundTrip(request)
}

func installLabLiveGuard(models []labMockModel, internalURL string, paid *labPaidAuthorization) error {
	client, err := service.GetHttpClientWithProxy("")
	if err != nil || client == nil || client.Transport == nil {
		return errors.New("live Relay HTTP client is unavailable")
	}
	hosts := map[string]bool{}
	for _, model := range models {
		hosts[model.Host] = true
	}
	// No mock dialer, test CA or response generator is installed in live mode.
	client.Transport = &labLiveGuard{base: client.Transport, providerHosts: hosts, internalURL: internalURL, paid: paid}
	return nil
}
