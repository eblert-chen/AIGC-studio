//go:build relay_local_video_lab

package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/google/uuid"
)

type labMockModel struct {
	ID     string
	Family string
	Host   string
	Key    string
}

type labMockTask struct {
	ID            string                 `json:"id"`
	Model         string                 `json:"model"`
	Family        string                 `json:"family"`
	FixtureKey    string                 `json:"fixture_key"`
	FixtureSHA256 string                 `json:"fixture_sha256"`
	KeySHA256     string                 `json:"key_sha256"`
	Scenario      string                 `json:"scenario"`
	Polls         int                    `json:"polls"`
	CreatedAt     int64                  `json:"created_at"`
	Inputs        []labMockInputEvidence `json:"inputs,omitempty"`
}

type labMockProvider struct {
	models       map[string]labMockModel
	fixtures     map[string]labVideoFixture
	directory    string
	signingKey   []byte
	mutex        sync.Mutex
	posts        atomic.Int64
	polls        atomic.Int64
	downloads    atomic.Int64
	inputClient  *http.Client
	inputFetches atomic.Int64
	inputBytes   atomic.Int64
}

func newLabMockProvider(directory string, models []labMockModel, fixtures map[string]labVideoFixture, signingKey []byte) (*labMockProvider, error) {
	if len(signingKey) < 32 || len(fixtures) == 0 {
		return nil, errors.New("mock provider requires a runtime key and verified MP4 fixtures")
	}
	if err := os.MkdirAll(directory, 0700); err != nil {
		return nil, errors.New("could not create mock provider state")
	}
	provider := &labMockProvider{models: map[string]labMockModel{}, directory: directory, fixtures: fixtures, signingKey: signingKey}
	for _, item := range models {
		if item.ID == "" || item.Key == "" || (item.Family != "ark" && item.Family != "minimax") {
			return nil, errors.New("mock model binding is invalid")
		}
		provider.models[item.ID] = item
	}
	return provider, nil
}

func (provider *labMockProvider) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	writer.Header().Set("Cache-Control", "no-store")
	if request.TLS == nil {
		http.Error(writer, "TLS required", http.StatusBadRequest)
		return
	}
	if request.Host == labArtifactHost && request.Method == http.MethodGet && strings.HasPrefix(request.URL.Path, "/outputs/") {
		provider.serveArtifact(writer, request)
		return
	}
	if request.URL.RawQuery != "" {
		labWriteJSON(writer, http.StatusBadRequest, map[string]any{"error": "mock API does not accept query parameters"})
		return
	}
	switch {
	case request.Method == http.MethodPost && request.URL.Path == "/v2/video_generation":
		provider.submit(writer, request, "minimax")
	case request.Method == http.MethodPost && request.URL.Path == "/api/v3/contents/generations/tasks":
		provider.submit(writer, request, "ark")
	case request.Method == http.MethodGet && strings.HasPrefix(request.URL.Path, "/v2/query/video_generation/"):
		provider.poll(writer, request, "minimax", strings.TrimPrefix(request.URL.Path, "/v2/query/video_generation/"))
	case request.Method == http.MethodGet && strings.HasPrefix(request.URL.Path, "/api/v3/contents/generations/tasks/"):
		provider.poll(writer, request, "ark", strings.TrimPrefix(request.URL.Path, "/api/v3/contents/generations/tasks/"))
	default:
		http.NotFound(writer, request)
	}
}

func (provider *labMockProvider) submit(writer http.ResponseWriter, request *http.Request, family string) {
	provider.posts.Add(1)
	request.Body = http.MaxBytesReader(writer, request.Body, 128*1024)
	body, err := io.ReadAll(request.Body)
	var input struct {
		Model      string           `json:"model"`
		Resolution string           `json:"resolution"`
		Duration   int              `json:"duration"`
		Ratio      string           `json:"ratio"`
		Content    []labMockContent `json:"content"`
	}
	if err != nil || common.RejectDuplicateJSONKeys(body) != nil || common.Unmarshal(body, &input) != nil {
		provider.reject(writer, family, "invalid fixture request", http.StatusBadRequest)
		return
	}
	model, found := provider.models[input.Model]
	if !found || model.Family != family || request.Host != model.Host || !labExactSecret(request.Header.Get("Authorization"), "Bearer "+model.Key) {
		provider.reject(writer, family, "invalid mock provider identity", http.StatusUnauthorized)
		return
	}
	fixtureKey := labFixtureKey(input.Resolution, input.Duration, input.Ratio)
	fixture, found := provider.fixtures[fixtureKey]
	if !found {
		provider.reject(writer, family, "local mock has no matching verified MP4 fixture", http.StatusUnprocessableEntity)
		return
	}
	prompt := ""
	for _, item := range input.Content {
		if item.Type == "text" {
			prompt += item.Text
		}
	}
	if strings.TrimSpace(prompt) == "" {
		provider.reject(writer, family, "prompt is required", http.StatusBadRequest)
		return
	}
	inputs, err := provider.readReferenceInputs(request.Context(), input.Content)
	if err != nil {
		provider.reject(writer, family, err.Error(), http.StatusUnprocessableEntity)
		return
	}
	scenario := "success"
	for _, allowed := range []string{"response-loss", "provider-failed", "proof-conflict"} {
		if strings.Contains(prompt, "[lab:"+allowed+"]") {
			scenario = allowed
			break
		}
	}
	task := labMockTask{
		ID: "lab-" + uuid.NewString(), Model: input.Model, Family: family, FixtureKey: fixtureKey,
		FixtureSHA256: fixture.SHA256, Inputs: inputs,
		KeySHA256: fmt.Sprintf("%x", sha256.Sum256([]byte(model.Key))), Scenario: scenario, CreatedAt: time.Now().UTC().Unix(),
	}
	provider.mutex.Lock()
	err = writeLabJSON(filepath.Join(provider.directory, task.ID+".json"), task, true)
	provider.mutex.Unlock()
	if err != nil {
		provider.reject(writer, family, "mock provider persistence failed", http.StatusInternalServerError)
		return
	}
	if scenario == "response-loss" {
		// The simulated side effect is already durable. Losing this response must
		// leave the real Relay in submission_unknown, never trigger a second POST.
		if hijacker, ok := writer.(http.Hijacker); ok {
			connection, _, err := hijacker.Hijack()
			if err == nil {
				_ = connection.Close()
				return
			}
		}
		panic(http.ErrAbortHandler)
	}
	if family == "minimax" {
		labWriteJSON(writer, http.StatusOK, map[string]any{"task_id": task.ID})
	} else {
		labWriteJSON(writer, http.StatusOK, map[string]any{"id": task.ID})
	}
}

var labMockTaskID = regexp.MustCompile(`^lab-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

func (provider *labMockProvider) readTask(id string) (labMockTask, error) {
	var task labMockTask
	if !labMockTaskID.MatchString(id) {
		return task, errors.New("mock task not found")
	}
	payload, err := readLabPrivateFile(filepath.Join(provider.directory, id+".json"), 16*1024)
	if err != nil || common.Unmarshal(payload, &task) != nil || task.ID != id {
		return task, errors.New("mock task not found")
	}
	return task, nil
}

func (provider *labMockProvider) poll(writer http.ResponseWriter, request *http.Request, family string, id string) {
	provider.polls.Add(1)
	provider.mutex.Lock()
	defer provider.mutex.Unlock()
	task, err := provider.readTask(id)
	if err != nil || task.Family != family {
		provider.reject(writer, family, "mock task not found", http.StatusNotFound)
		return
	}
	model, found := provider.models[task.Model]
	providedKey := strings.TrimPrefix(request.Header.Get("Authorization"), "Bearer ")
	if !found || request.Host != model.Host || !strings.HasPrefix(request.Header.Get("Authorization"), "Bearer ") ||
		!labExactSecret(fmt.Sprintf("%x", sha256.Sum256([]byte(providedKey))), task.KeySHA256) {
		provider.reject(writer, family, "mock poll credential mismatch", http.StatusUnauthorized)
		return
	}
	fixture, found := provider.fixtures[task.FixtureKey]
	if !found || fixture.SHA256 != task.FixtureSHA256 {
		provider.reject(writer, family, "stored mock fixture is unavailable", http.StatusServiceUnavailable)
		return
	}
	task.Polls++
	if err := writeLabJSON(filepath.Join(provider.directory, task.ID+".json"), task, false); err != nil {
		provider.reject(writer, family, "mock poll persistence failed", http.StatusInternalServerError)
		return
	}
	status := "succeeded"
	if task.Polls == 1 {
		status = "queued"
	} else if task.Polls == 2 {
		status = "running"
	} else if task.Scenario == "provider-failed" {
		status = "failed"
	}
	result := map[string]any{
		"id": task.ID, "model": task.Model, "status": status, "created_at": task.CreatedAt,
		"updated_at": time.Now().UTC().Unix(), "resolution": fixture.Resolution,
		"duration": fixture.DurationSeconds, "ratio": fixture.AspectRatio,
	}
	if family == "minimax" {
		result["resolution"] = strings.ToUpper(fixture.Resolution)
		result["task_type"], result["modality"] = "generation", "video"
	}
	if status == "succeeded" {
		artifactURL := "https://" + labArtifactHost + "/outputs/" + task.ID + ".mp4?signature=" + provider.artifactSignature(task.ID)
		if family == "minimax" {
			result["content"] = map[string]any{"url": artifactURL}
		} else {
			result["content"] = map[string]any{"video_url": artifactURL}
		}
		if task.Scenario == "proof-conflict" {
			result["duration"] = fixture.DurationSeconds + 1
		}
	}
	if status == "failed" {
		result["error"] = map[string]any{"code": "SensitiveContent", "message": "Explicit local mock provider failure"}
	}
	if family == "minimax" {
		labWriteJSON(writer, http.StatusOK, map[string]any{"task": result})
	} else {
		// Ark's official GetContentsGenerationsTask response defines duration as
		// a string, although the create request and H3 v2 response use integers.
		result["duration"] = strconv.Itoa(result["duration"].(int))
		labWriteJSON(writer, http.StatusOK, result)
	}
}

func (provider *labMockProvider) artifactSignature(id string) string {
	mac := hmac.New(sha256.New, provider.signingKey)
	_, _ = mac.Write([]byte("local-mock-artifact-v1\x00" + id))
	return fmt.Sprintf("%x", mac.Sum(nil))
}

func (provider *labMockProvider) serveArtifact(writer http.ResponseWriter, request *http.Request) {
	id := strings.TrimSuffix(strings.TrimPrefix(request.URL.Path, "/outputs/"), ".mp4")
	if !strings.HasSuffix(request.URL.Path, ".mp4") || len(request.URL.Query()) != 1 || len(request.URL.Query()["signature"]) != 1 ||
		!labExactSecret(request.URL.Query().Get("signature"), provider.artifactSignature(id)) {
		http.NotFound(writer, request)
		return
	}
	provider.mutex.Lock()
	task, err := provider.readTask(id)
	provider.mutex.Unlock()
	fixture, found := provider.fixtures[task.FixtureKey]
	if err != nil || !found || fixture.SHA256 != task.FixtureSHA256 || task.Polls < 3 || task.Scenario == "provider-failed" || task.Scenario == "response-loss" {
		http.NotFound(writer, request)
		return
	}
	provider.downloads.Add(1)
	writer.Header().Set("Content-Type", "video/mp4")
	writer.Header().Set("Content-Length", fmt.Sprint(len(fixture.payload)))
	writer.Header().Set("X-Local-Video-Fixture", "true")
	_, _ = io.Copy(writer, bytes.NewReader(fixture.payload))
}

func (provider *labMockProvider) reject(writer http.ResponseWriter, family string, message string, status int) {
	if family == "minimax" {
		labWriteJSON(writer, status, map[string]any{"type": "error", "error": map[string]any{"type": "invalid_request_error", "http_code": fmt.Sprint(status), "message": message}})
	} else {
		labWriteJSON(writer, status, map[string]any{"error": map[string]any{"code": "InvalidParameter", "message": message}})
	}
}

func labExactSecret(provided, expected string) bool {
	left, right := sha256.Sum256([]byte(provided)), sha256.Sum256([]byte(expected))
	return expected != "" && subtle.ConstantTimeCompare(left[:], right[:]) == 1
}

func labWriteJSON(writer http.ResponseWriter, status int, value any) {
	payload, err := common.Marshal(value)
	if err != nil {
		http.Error(writer, "could not encode local response", http.StatusInternalServerError)
		return
	}
	writer.Header().Set("Content-Type", "application/json")
	writer.Header().Set("Cache-Control", "no-store")
	writer.WriteHeader(status)
	_, _ = writer.Write(payload)
}
