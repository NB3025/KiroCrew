# Connector conformance foundation (W00-S4)

This document is the **conformance foundation** deliverable of stream `W00`.
It does not add a validator. It records, in one machine-readable place, which
component discharges each conformance responsibility the campaign requires,
so that no responsibility is silently dropped when the work is distributed
across streams.

## Why this slice ships a mapping and not a validator

The `ConformanceRun` and `EvidenceReceipt` **structural shape** is defined,
inline and in full, in the merged
[`connector-capability-manifest.md`](connector-capability-manifest.md)
(section "Conformance and evidence: the structural contract lives here",
lines 406–507). That spec states a validator can be built directly against
the document, and the **W00-S2 manifest validator**
(`scripts/check_connector_manifest.py`, PR
[#10869](https://github.com/kirodotdev/KiroCrew/pull/10869)) is that
validator. It already rules on every `ConformanceRun` field, every
`EvidenceReceipt` field, the per-effect cleanup rules, readback shape, and
the three-way reference identity.

The campaign's single-contract rule is that the manifest, run, and receipt
fields have **one specification source and one enforcer**. A second validator
in this slice would create a second decision point on fields S2 already
rules on — divergence, not independence. The slice-unique remainder for
run/receipt validation is therefore **empty**, and this slice adds no
`scripts/check_conformance_receipt.py`.

Empty remainder does **not** mean the conformance obligations disappear. They
are transferred, and this document is where the transfer is recorded so the
denominator of required work stays fully visible.

## Enforcer state cited in this document

- **Enforcer:** `scripts/check_connector_manifest.py` on the **published PR
  head `855e20f8b86d39aa3d50ae758583caaf1aa938b4`** (PR #10869). All line
  numbers below are against that published head.
- The PR is **not merged** (`mergeStateStatus: BLOCKED` at time of writing),
  so its enforcement is authoritative-by-design but **not yet active on
  `main`**. Any reliance on it by a later round is a post-merge dependency.
- A `request_shape_hash` `sha256:`+64-hex canonical-digest tightening exists
  in S2's unmerged working tree but is **in flight, not on the published PR
  head** — it is not cited here as shipped.

## Responsibility mapping

One row per original conformance responsibility. `current_state` never reads
as "discharged" for a responsibility that has only been reassigned to a
not-yet-merged or not-yet-built component.

<!-- machine-readable: the JSON block below is the canonical form of this table -->

```json
{
  "schema": "connector-conformance-responsibility-map/v1",
  "enforcer_cited_head": "855e20f8b86d39aa3d50ae758583caaf1aa938b4",
  "enforcer_pr": 10869,
  "enforcer_merged": false,
  "responsibilities": [
    {
      "id": "conformance-run-structure",
      "obligation": "ConformanceRun record structure (all required fields, types, timestamp, immutable tested_sha, verdict enum, response_summary object)",
      "owner": "W00-S2 manifest validator, _validate_run",
      "concrete_dependency": "scripts/check_connector_manifest.py:1267 (published head 855e20f8); shape source: connector-capability-manifest.md L406-431",
      "later_integration_step": "S2 PR #10869 merge",
      "current_state": "specified_and_enforced_by_unmerged_S2"
    },
    {
      "id": "evidence-receipt-structure",
      "obligation": "EvidenceReceipt record structure (receipt_id, conformance_run_ref, claim, runtime_verified, negative_test_refs, cleanup fields)",
      "owner": "W00-S2 manifest validator, _check_receipt_shape",
      "concrete_dependency": "scripts/check_connector_manifest.py:1342 (published head 855e20f8); shape source: connector-capability-manifest.md L440-451",
      "later_integration_step": "S2 PR #10869 merge",
      "current_state": "specified_and_enforced_by_unmerged_S2"
    },
    {
      "id": "per-effect-cleanup",
      "obligation": "Per-effect cleanup rules (read=not_applicable; write/admin/share forbid not_automatable; cleanup_confirmed derived from cleanup_status)",
      "owner": "W00-S2 manifest validator, _check_receipt_shape per-effect branch",
      "concrete_dependency": "scripts/check_connector_manifest.py:1342 (published head 855e20f8); rule source: connector-capability-manifest.md L453-506 per-effect table",
      "later_integration_step": "S2 PR #10869 merge",
      "current_state": "specified_and_enforced_by_unmerged_S2"
    },
    {
      "id": "readback",
      "obligation": "readback_result shape and rules (null iff effect=read; non-read requires an independent-read object with checked_at/method/matched/detail; matched must be true)",
      "owner": "W00-S2 manifest validator, _check_receipt_shape readback branch",
      "concrete_dependency": "scripts/check_connector_manifest.py:1342 (published head 855e20f8); rule source: connector-capability-manifest.md L448, L453-506",
      "later_integration_step": "S2 PR #10869 merge",
      "current_state": "specified_and_enforced_by_unmerged_S2"
    },
    {
      "id": "three-way-reference-identity",
      "obligation": "Three-way reference identity (verification_contract.receipt_ref <-> ConformanceRun.evidence_receipt_ref <-> EvidenceReceipt.conformance_run_ref), by exact-string lookup, plus orphan-evidence and path-safety scans",
      "owner": "W00-S2 manifest validator, three-way equality block + F1/F2 whole-tree scan",
      "concrete_dependency": "scripts/check_connector_manifest.py:1660 (three-way equality), :2484 (run_scan), :2684 (_scan_orphan_evidence) (published head 855e20f8); rule source: connector-capability-manifest.md L183, L406-451",
      "later_integration_step": "S2 PR #10869 merge",
      "current_state": "specified_and_enforced_by_unmerged_S2"
    },
    {
      "id": "credential-custody",
      "obligation": "Credential custody: a ConformanceRun binds to an authorized account/tenant via account_binding_ref and never carries a raw credential",
      "owner": "kiro-cli owns the OAuth chain and token custody; Kiro Crew holds no connection credential",
      "concrete_dependency": "merged connections.md L11, L43-44, L763 (credential boundary); ConformanceRun.account_binding_ref defined in connector-capability-manifest.md (\"never a raw credential\"). Interface NOT assumed from PR #9992 (its diff unread, mergeable_state=blocked); a missing binding interface is reported as a named gap, never invented.",
      "later_integration_step": "W01 (shared control plane: binding/auth/policy) per the W00->W01 DAG edge",
      "current_state": "boundary_defined_in_merged_connections_md; binding_interface_pending_W01"
    },
    {
      "id": "live-run-dependency",
      "obligation": "Live conformance run producing a real ConformanceRun/EvidenceReceipt with runtime_verified=true",
      "owner": "conformance runner (not yet implemented) + a real authorized tenant",
      "concrete_dependency": "runtime_verified stays false until a real live call; real-tenant supply is root work item it_fd22413c",
      "later_integration_step": "W00(4) evidence/runner/CI foundation, then W15 independent acceptance",
      "current_state": "EXTERNAL_BLOCKER_open (it_fd22413c); no live run in this slice"
    }
  ],
  "unresolved_discrepancies": [
    {
      "id": "evidence-tier-contract-vs-artifact",
      "statement": "The campaign contract (section 6.4, a document that does not live in this repository) asserts that EvidenceReceipt.evidence_tier reuses the three tiers source_verified_strict / search_snippet_or_partial / unverified. No merged artifact implements this binding.",
      "evidence": "evidence_tier is NOT a field on EvidenceReceipt in the merged connector-capability-manifest.md receipt table (L440-451), and NOT checked by S2's _check_receipt_shape. In S2's catalog-evidence.json (unmerged, arrives with PR #10869) evidence_tier is a CATALOG-LEVEL classification of operations (evidence_tier_definitions plus operations_by_evidence_tier counts); S2 validates coverage.operations_by_evidence_tier at scripts/check_connector_manifest.py:559 (published head). That is a different proposition from a per-receipt evidence_tier binding, and the per-receipt binding is satisfied by nothing.",
      "not_conflated_with": "source_status (merged manifest spec L166, L50-60; values user_required / official_baseline / unverified) is a DIFFERENT field in a DIFFERENT document with a DIFFERENT value set. evidence_tier is never aliased to it.",
      "owner": "campaign-contract owner (conductor/root) — this is a contract-vs-artifact gap, not a defect of W00-S2 or W00-S4",
      "current_state": "unresolved; the catalog-level count check must NOT be recorded as discharging the per-receipt binding"
    }
  ],
  "open_decisions": [
    {
      "id": "serialization-format-and-path",
      "statement": "The serialization format and in-repo storage location of a manifest entry, ConformanceRun, and EvidenceReceipt remain the manifest spec's explicitly open decision (connector-capability-manifest.md, closing paragraph of the conformance section). This slice does not invent them.",
      "owner": "the validator round and the entry-population round jointly, per the manifest spec's own tracking paragraph"
    }
  ]
}
```

### Human-readable view of the mapping

| Responsibility | Owner | Concrete dependency | Later integration step | Current state |
|---|---|---|---|---|
| `ConformanceRun` structure | S2 validator `_validate_run` | `check_connector_manifest.py:1267` (pub head `855e20f8`); shape: manifest L406–431 | S2 #10869 merge | specified & enforced by **unmerged** S2 |
| `EvidenceReceipt` structure | S2 validator `_check_receipt_shape` | `check_connector_manifest.py:1342`; shape: manifest L440–451 | S2 #10869 merge | specified & enforced by **unmerged** S2 |
| Per-effect cleanup | S2 `_check_receipt_shape` per-effect branch | `check_connector_manifest.py:1342`; rules: manifest L453–506 | S2 #10869 merge | specified & enforced by **unmerged** S2 |
| Readback | S2 `_check_receipt_shape` readback branch | `check_connector_manifest.py:1342`; rules: manifest L448, L453–506 | S2 #10869 merge | specified & enforced by **unmerged** S2 |
| Three-way reference identity | S2 three-way equality + F1/F2 scan | `check_connector_manifest.py:1660`, `:2484`, `:2684`; rules: manifest L183, L406–451 | S2 #10869 merge | specified & enforced by **unmerged** S2 |
| Credential custody | kiro-cli (token custody); Kiro Crew holds no credential | merged `connections.md` L11, L43–44, L763; `account_binding_ref` (never a raw credential). **#9992 interface not assumed.** | W01 shared control plane (`W00→W01` edge) | boundary defined in merged `connections.md`; binding interface pending W01 |
| Live-run dependency | conformance runner (not built) + real tenant | `runtime_verified` false until a real live call; tenant = root `it_fd22413c` | W00(4) evidence/runner/CI foundation → W15 | **EXTERNAL_BLOCKER open** (`it_fd22413c`); no live run here |

### Unresolved discrepancy (owned by the campaign-contract owner)

**`evidence_tier` contract-vs-artifact gap.** The campaign contract (§6.4, a
document that is **not** in this repository) asserts that
`EvidenceReceipt.evidence_tier` reuses the three tiers
`source_verified_strict` / `search_snippet_or_partial` / `unverified`. **No
merged artifact implements this binding.** `evidence_tier` is not a field on
`EvidenceReceipt` in the merged manifest receipt table (L440–451) and is not
checked by S2's `_check_receipt_shape`. In S2's `catalog-evidence.json`
(unmerged) `evidence_tier` is a **catalog-level** classification of
operations (definitions plus `operations_by_evidence_tier` counts), which S2
validates at `check_connector_manifest.py:559`. "S2 validates
`coverage.operations_by_evidence_tier`" and "`EvidenceReceipt.evidence_tier`
reuses those tiers" are **different propositions**; the second is satisfied by
nothing. This is recorded as an open contract-vs-artifact discrepancy owned by
the campaign-contract owner — it is not a defect of W00-S2 or W00-S4, and the
catalog-level count check must **not** be recorded as discharging it.

`evidence_tier` is never aliased to `source_status` (merged manifest L166,
L50–60; `user_required` / `official_baseline` / `unverified`), which is a
different field, in a different document, with a different value set.

### Fixed constraints

- **Serialization format and in-repo path** stay the manifest spec's open
  decision; they are **not invented here**.
- **Live run** is an open `EXTERNAL_BLOCKER` (root `it_fd22413c`).
- **`runtime_verified` stays false**; nothing in this slice runs live, so no
  `contract_verified` claim, no placeholder `tested_sha`, no all-zero hashes
  are produced by this slice.
