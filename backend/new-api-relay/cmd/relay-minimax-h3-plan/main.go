// relay-minimax-h3-plan prepares unsigned H3 model releases for review.
// It never reads credentials, calls an API, signs or deploys a release, or
// changes routes, prices, Platform approval, publication or customer grants.
package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/generationrelease"
)

type miniMaxH3PlanOptions struct {
	createdAt     string
	createdBy     string
	reason        string
	publicModelID string
}

type miniMaxH3PlanModel struct {
	DisplayName          string                    `json:"display_name"`
	Lifecycle            string                    `json:"lifecycle"`
	EvidenceStatus       string                    `json:"evidence_status"`
	NewRoutesAllowed     bool                      `json:"new_routes_allowed"`
	Notes                []string                  `json:"notes"`
	OfficialSources      []string                  `json:"official_sources"`
	UnsignedModelRelease generationrelease.Release `json:"unsigned_model_release"`
}

type miniMaxH3Plan struct {
	SchemaVersion int                  `json:"schema_version"`
	ReviewedAsOf  string               `json:"reviewed_as_of"`
	Status        string               `json:"status"`
	Notices       []string             `json:"notices"`
	Models        []miniMaxH3PlanModel `json:"models"`
}

func main() {
	if err := runMiniMaxH3Plan(os.Args[1:], os.Stdout, os.Stderr); err != nil {
		fmt.Fprintln(os.Stderr, "MiniMax H3 declaration preparation failed:", err)
		os.Exit(1)
	}
}

func runMiniMaxH3Plan(args []string, output io.Writer, diagnostics io.Writer) error {
	options := miniMaxH3PlanOptions{}
	flags := flag.NewFlagSet("relay-minimax-h3-plan", flag.ContinueOnError)
	flags.SetOutput(diagnostics)
	flags.StringVar(&options.createdAt, "created-at", "", "required actual review timestamp in canonical UTC RFC3339")
	flags.StringVar(&options.createdBy, "created-by", "", "required identity of the operator actually reviewing these declarations")
	flags.StringVar(&options.reason, "reason", "", "required reason for this offline model-release preparation")
	flags.StringVar(&options.publicModelID, "model", "", "prepare one exact public model ID: minimax-h3 or minimax-h3-max; omitted prepares both")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return errors.New("positional arguments are not accepted")
	}
	plan, err := buildMiniMaxH3Plan(options)
	if err != nil {
		return err
	}
	encoded, err := common.Marshal(plan)
	if err != nil {
		return fmt.Errorf("encode reviewed declarations: %w", err)
	}
	encoded = append(encoded, '\n')
	written, err := output.Write(encoded)
	if err != nil {
		return err
	}
	if written != len(encoded) {
		return io.ErrShortWrite
	}
	return nil
}

func buildMiniMaxH3Plan(options miniMaxH3PlanOptions) (miniMaxH3Plan, error) {
	empty := miniMaxH3Plan{}
	createdAt, err := time.Parse(time.RFC3339, options.createdAt)
	if err != nil || options.createdAt != createdAt.UTC().Format(time.RFC3339) {
		return empty, errors.New("--created-at must be the actual review time in canonical UTC RFC3339")
	}
	if strings.TrimSpace(options.createdBy) == "" || strings.TrimSpace(options.reason) == "" ||
		strings.ContainsAny(options.createdBy, "\r\n\t") || strings.ContainsAny(options.reason, "\r\n\t") {
		return empty, errors.New("--created-by and --reason must identify the actual review without control characters")
	}
	if options.publicModelID != strings.TrimSpace(options.publicModelID) {
		return empty, errors.New("--model must be an exact public model ID")
	}
	models, err := generationprofile.MiniMaxH3ModelCatalog()
	if err != nil {
		return empty, fmt.Errorf("load reviewed MiniMax H3 catalog: %w", err)
	}
	plan := miniMaxH3Plan{
		SchemaVersion: 1,
		ReviewedAsOf:  options.createdAt,
		Status:        "review_required_not_deployed",
		Notices: []string{
			"Offline unsigned preparation only: this output is not signed, approved or deployed, and creates no provider request, route acceptance, Platform approval, price, publication or customer grant.",
			"No credentials or runtime configuration are read. The supplied reviewer, review timestamp and reason are recorded as provided; this tool does not fabricate an operator review or evidence of account/model access.",
			"Review and sign each model release separately, then bind and accept the exact native MiniMax channel and credential. Real Provider, artifact-storage and provider-bill evidence remain required before deployment.",
			"Platform synchronization can prepare unpublished drafts only. Customer creation requires explicit capability approval, publication, pricing and personal or company distribution through the existing authorization flow.",
			"The public subset uses fixed ratios, integer durations and one verified MP4: H3 text-only T2V, image-reference I2V (9 images/0 videos/3 audio) and video-reference V2V (6 images/3 videos/3 audio), at most 12 mixed files. H3 Max currently exposes text-only T2V.",
			"First/last-frame adaptive ratio, middle frames, audio-only reference input, regeneration, separate audio outputs, audio switches, seed and arbitrary provider metadata are not exposed. The per-model official sources and notes explain these limitations.",
			"Provider request_id is not an idempotency guarantee. Ambiguous POST outcomes must remain reconciliation-required without a new submit or channel failover; unsigned declarations do not alter that rule.",
		},
		Models: make([]miniMaxH3PlanModel, 0, len(models)),
	}
	for _, model := range models {
		if options.publicModelID != "" && model.PublicModelID != options.publicModelID {
			continue
		}
		if !model.NewRoutesAllowed || model.Lifecycle != "acceptance_candidate" || model.EvidenceStatus != "route_acceptance_required" {
			return empty, fmt.Errorf("reviewed model %q is not eligible for an unsigned onboarding plan", model.PublicModelID)
		}
		profile, found := generationprofile.Get(model.AdapterProfileID)
		if !found {
			return empty, fmt.Errorf("reviewed model %q has no registered adapter profile", model.PublicModelID)
		}
		release := generationrelease.Release{
			APIVersion:             generationrelease.APIVersion,
			Kind:                   generationrelease.Kind,
			ReleaseID:              model.PublicModelID + "." + createdAt.Format("20060102t150405z"),
			PublicModelID:          model.PublicModelID,
			ProviderModelID:        model.ProviderModelID,
			AdapterProfileID:       profile.ID,
			AdapterProfileRevision: profile.Revision,
			Capability:             model.CompatibleCapability(),
			Audit: generationrelease.Audit{
				CreatedAt: options.createdAt,
				CreatedBy: options.createdBy,
				Reason:    options.reason,
				SourceRef: "generationprofile/minimax_h3_models.v1.json",
			},
		}
		if err := release.Validate(profile); err != nil {
			return empty, fmt.Errorf("reviewed model %q cannot form a valid release: %w", model.PublicModelID, err)
		}
		plan.Models = append(plan.Models, miniMaxH3PlanModel{
			DisplayName:          model.DisplayName,
			Lifecycle:            model.Lifecycle,
			EvidenceStatus:       model.EvidenceStatus,
			NewRoutesAllowed:     model.NewRoutesAllowed,
			Notes:                append([]string(nil), model.Notes...),
			OfficialSources:      append([]string(nil), model.OfficialSources...),
			UnsignedModelRelease: release,
		})
	}
	if len(plan.Models) == 0 {
		return empty, errors.New("no exact reviewed public model; --model accepts minimax-h3 or minimax-h3-max, never a provider ID or legacy Hailuo alias")
	}
	sort.Slice(plan.Models, func(left, right int) bool {
		return plan.Models[left].UnsignedModelRelease.PublicModelID < plan.Models[right].UnsignedModelRelease.PublicModelID
	})
	return plan, nil
}
