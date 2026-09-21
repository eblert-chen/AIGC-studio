// relay-ark-video-plan prepares unsigned, reviewed Ark model declarations.
// It never reads credentials, signs a release, calls a provider, or changes
// the live Relay route inventory. It is not a production runtime command.
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

type arkVideoPlanOptions struct {
	createdAt           string
	createdBy           string
	reason              string
	publicModelID       string
	includeExistingOnly bool
}

type arkVideoPlanModel struct {
	DisplayName          string                    `json:"display_name"`
	Lifecycle            string                    `json:"lifecycle"`
	NewRoutesAllowed     bool                      `json:"new_routes_allowed"`
	EOMAt                string                    `json:"eom_at,omitempty"`
	EOSAt                string                    `json:"eos_at,omitempty"`
	Notes                []string                  `json:"notes"`
	OfficialSources      []string                  `json:"official_sources"`
	UnsignedModelRelease generationrelease.Release `json:"unsigned_model_release"`
}

type arkVideoPlan struct {
	SchemaVersion int                 `json:"schema_version"`
	ReviewedAsOf  string              `json:"reviewed_as_of"`
	Status        string              `json:"status"`
	Notices       []string            `json:"notices"`
	Models        []arkVideoPlanModel `json:"models"`
}

func main() {
	if err := runArkVideoPlan(os.Args[1:], os.Stdout, os.Stderr); err != nil {
		fmt.Fprintln(os.Stderr, "Ark model declaration preparation failed:", err)
		os.Exit(1)
	}
}

func runArkVideoPlan(args []string, output io.Writer, diagnostics io.Writer) error {
	options := arkVideoPlanOptions{}
	flags := flag.NewFlagSet("relay-ark-video-plan", flag.ContinueOnError)
	flags.SetOutput(diagnostics)
	flags.StringVar(&options.createdAt, "created-at", "", "required canonical UTC RFC3339 review timestamp")
	flags.StringVar(&options.createdBy, "created-by", "", "required identity of the operator reviewing these declarations")
	flags.StringVar(&options.reason, "reason", "", "required reason for preparing this model release")
	flags.StringVar(&options.publicModelID, "model", "", "prepare one exact public model ID instead of all new-route candidates")
	flags.BoolVar(&options.includeExistingOnly, "include-existing-only", false, "also include deprecated models for review of existing routes; never includes retired or unverified IDs")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return errors.New("positional arguments are not accepted")
	}
	plan, err := buildArkVideoPlan(options)
	if err != nil {
		return err
	}
	encoded, err := common.Marshal(plan)
	if err != nil {
		return fmt.Errorf("encode reviewed declarations: %w", err)
	}
	_, err = output.Write(append(encoded, '\n'))
	return err
}

func buildArkVideoPlan(options arkVideoPlanOptions) (arkVideoPlan, error) {
	empty := arkVideoPlan{}
	createdAt, err := time.Parse(time.RFC3339, options.createdAt)
	if err != nil || options.createdAt != createdAt.UTC().Format(time.RFC3339) {
		return empty, errors.New("--created-at must be canonical UTC RFC3339")
	}
	if strings.TrimSpace(options.createdBy) == "" || strings.TrimSpace(options.reason) == "" {
		return empty, errors.New("--created-by and --reason must identify the actual review")
	}
	if options.publicModelID != strings.TrimSpace(options.publicModelID) {
		return empty, errors.New("--model must be an exact public model ID")
	}
	models, err := generationprofile.SeedanceModelCatalog()
	if err != nil {
		return empty, fmt.Errorf("load reviewed Ark catalog: %w", err)
	}
	plan := arkVideoPlan{
		SchemaVersion: 1,
		ReviewedAsOf:  options.createdAt,
		Status:        "review_required_not_deployed",
		Notices: []string{
			"Unsigned preparation only: no provider request, route acceptance, Platform approval, price, publication or grant is created.",
			"Lifecycle eligibility is evaluated at the supplied review timestamp; historical review output does not restore current provider availability.",
			"Review and sign each model release, then bind and accept the exact native channel and credential before deployment.",
			"Platform catalog synchronization creates unpublished drafts; customer creation lists only approved, published and explicitly granted models.",
			"The public contract exposes a compatible subset: fixed ratios, integer duration, one MP4, at most 15 reference assets; no automatic duration, edit/extend, audio-only input or advanced provider options.",
		},
		Models: make([]arkVideoPlanModel, 0),
	}
	for _, model := range models {
		if options.publicModelID != "" && model.PublicModelID != options.publicModelID {
			continue
		}
		if !model.NewRoutesAllowed && !(options.includeExistingOnly && model.Lifecycle == "deprecated") {
			continue
		}
		if model.Lifecycle != "acceptance_candidate" && model.Lifecycle != "deprecated" {
			continue
		}
		if err := model.ValidateSubmissionAt(createdAt); err != nil {
			if options.publicModelID != "" {
				return empty, fmt.Errorf("model is ineligible at the review timestamp: %w", err)
			}
			continue
		}
		profile, found := generationprofile.Get(model.AdapterProfileID)
		if !found {
			return empty, fmt.Errorf("reviewed model %q has no registered adapter profile", model.PublicModelID)
		}
		release := generationrelease.Release{
			APIVersion:             generationrelease.APIVersion,
			Kind:                   generationrelease.Kind,
			ReleaseID:              "ark." + model.PublicModelID + "." + createdAt.Format("20060102t150405z"),
			PublicModelID:          model.PublicModelID,
			ProviderModelID:        model.ProviderModelID,
			AdapterProfileID:       profile.ID,
			AdapterProfileRevision: profile.Revision,
			Capability:             model.CompatibleCapability(),
			Audit: generationrelease.Audit{
				CreatedAt: options.createdAt,
				CreatedBy: options.createdBy,
				Reason:    options.reason,
				SourceRef: "generationprofile/seedance_models.v1.json",
			},
		}
		if err := release.Validate(profile); err != nil {
			return empty, fmt.Errorf("reviewed model %q cannot form a valid release: %w", model.PublicModelID, err)
		}
		plan.Models = append(plan.Models, arkVideoPlanModel{
			DisplayName:          model.DisplayName,
			Lifecycle:            model.Lifecycle,
			NewRoutesAllowed:     model.NewRoutesAllowed,
			EOMAt:                model.EOMAt,
			EOSAt:                model.EOSAt,
			Notes:                append([]string{}, model.Notes...),
			OfficialSources:      append([]string(nil), model.OfficialSources...),
			UnsignedModelRelease: release,
		})
	}
	if len(plan.Models) == 0 {
		return empty, errors.New("no eligible reviewed model; deprecated models require --include-existing-only, retired and unverified models are never eligible")
	}
	sort.Slice(plan.Models, func(left, right int) bool {
		return plan.Models[left].UnsignedModelRelease.PublicModelID < plan.Models[right].UnsignedModelRelease.PublicModelID
	})
	return plan, nil
}
