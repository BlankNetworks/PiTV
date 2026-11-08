# Owner-attested automatic cleanup recipes

A recipe records one exact model and firmware configuration that the appliance's owner has checked. It allows the same reviewed package selection to be reused on another matching TV after that TV grants normal debugging access. A recipe does not enable debugging, accept the TV's authorization dialog, or make an unsupported operating system compatible.

**No real-TV recipes are supplied as independently validated or certified.** The bundled community catalog supplies candidate lists. A model appearing in that catalog is not evidence that its current firmware has passed the checks below.

## What the owner confirms

Before creating a recipe, the service must link it to a successful cleanup and successful restoration on a real TV, or to separately marked simulation records in demo mode. The owner must explicitly attest that all seven checks passed:

| Check | What to verify on the television |
| --- | --- |
| Home | The default Home screen opens and remains usable. |
| Inputs | The household's HDMI inputs, live TV and other required sources work. |
| Remote | Navigation, volume, power and other required remote buttons work. |
| Streaming | The household's required applications open and play content. |
| Network | The TV's normal network connection and required network features work. |
| Reboot | The TV successfully restarts and remains usable after cleanup. |
| Restore | The recorded cleanup can be restored and the original behavior returns. |

These are **the owner's observations**, not laboratory results, manufacturer approval or independent certification. The recipe module validates that every answer is the literal boolean `true`; it cannot observe these physical checks itself. The service remains responsible for validating the linked cleanup and restoration records before calling the builder.

## Exact eligibility

The matcher requires equality across all of the following:

- Manufacturer, brand, model, device codename, product, Android release, SDK version, build, firmware fingerprint and OS family.
- The selected catalog profile ID and its SHA-256 digest.
- The full pinned upstream Git commit.
- The system package inventory, calculated as installed packages minus packages identified as user-installed.
- The current Home package/activity component and dynamically protected package set.
- The previously disabled package set, excluding packages deliberately selected by this recipe.
- Real versus demo mode.

Identity fields are literal strings. Wildcards, ranges and regular-expression selectors are rejected. The matcher does not use fuzzy model scores, partial fingerprints or a match percentage to authorize cleanup.

Device serial numbers and Android IDs are not part of a reusable recipe. The service separately binds each actual cleanup to the individual TV and checks that identity throughout the operation.

Unrelated enabled user-installed applications may differ between TVs. A selected cleanup package must still be a system package present on the target, and must not be dynamically protected or user-installed. If an application changes the Home screen, registered input/keyboard services, or the baseline disabled set, that difference blocks the recipe. A changed system inventory also blocks it, even if the firmware label did not change.

Selected packages may already be disabled on a matching TV. This supports idempotent operation: the service can act only on the remaining enabled selection, or report that the requested cleanup is already present. It must not treat an unrelated disabled-state change as part of a successful previous cleanup.

## Data format

Schema version 1 contains only these fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer schema version, currently `1`. |
| `id` | Random 32-character lowercase hexadecimal recipe ID. |
| `created_at` | Integer UTC Unix timestamp. |
| `enabled` | Owner-controlled boolean that can disable automatic use. |
| `demo` | Boolean that isolates simulation recipes from real TV operations. |
| `profile_id`, `profile_digest` | Exact catalog list and its SHA-256 content digest. |
| `upstream_commit` | Full lowercase 40-character pinned Git commit. |
| `selector` | The ten exact identity fields listed above. |
| `packages` | Sorted unique selected package names. |
| `system_packages` | Sorted unique system inventory from the checked TV. |
| `home` | Exact default Home component. |
| `protected` | Sorted unique dynamically protected package names. |
| `baseline_disabled` | Sorted disabled package set minus the selected packages. |
| `attestation` | `kind: "owner_attested"`, all seven boolean checks, and plain-text owner notes. |
| `digest` | SHA-256 of the canonical immutable content. |

The canonical digest uses UTF-8 JSON with sorted object keys, no extra whitespace and unescaped Unicode. It covers every recipe field except `digest` itself and the mutable `enabled` switch. Package lists must be sorted and contain no duplicates. The validator rejects unknown fields, unsupported schemas, invalid package names, empty identity values, incomplete inventories and inconsistent Home or selected-package state.

The digest detects a changed local record. **It is not a digital signature and does not prove who created a recipe.** Do not automatically import arbitrary internet recipes merely because their content digest is valid. Linked cleanup/restore evidence should be stored separately by the service, keyed to the recipe ID; arbitrary fields cannot be appended to the immutable recipe.

Recipes have no shell commands, executable hooks, download URLs or script bodies. They select packages through the application's existing guarded cleanup implementation. The service must continue applying the pinned upstream core-package guard, recording the exact before-state, verifying each small batch and retaining restoration data.

## Updates and mismatches

A new firmware fingerprint, altered catalog list, different system inventory, or changed upstream commit makes an old recipe ineligible. Keeping the record allows the owner to see what was previously checked, but does not carry its approval onto the new configuration.

An update should preserve recovery records and show which recipes need another supervised cleanup, functional check and restoration. Software must report a mismatch or an unsupported TV instead of cycling through package lists until one appears to work.

The pure matching API returns `false` for malformed or incomplete inputs. The builder and stored-record validator raise `RecipeError` with a reason. The public summary omits the full system inventory and device identifiers while retaining the selection, owner attestation and counts needed for review.
