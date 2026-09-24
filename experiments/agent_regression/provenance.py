"""Add source versions and root/checkpoint distinctions without changing frozen gold."""
import argparse
import json
import subprocess
from pathlib import Path

from .cases import C, I, DEFAULT, ROOT
from .freeze import digest, read, verify, write_once


ROOTS = {
    "A01": ("candidate_cause", "src/erp_harness/app/runner.py", "DYNAMIC_TOOL_POLICY",
            "Model selected unpublished bare names on the first response; the old contract omitted the exact-name rule. Causality of that omission requires the A/B result.", C + "/0001.request.json"),
    "A02": ("observed_contract_gap", "src/erp_harness/tools/sops.py", "SOPS['safe_write_review'] / build_sop_tools",
            "The consumed SOP uses bare names and does not separate action_confirm from field writes. First wrong preview is round7; the consumed SOP can first be corrected at round3.", C + "/0003.request.json"),
    "A03": ("observed_metadata_misclassification", "src/erp_harness/erp/capabilities.py", "NativeCapabilities.business_pack_report",
            "Denied ir.model inventory was mapped to absent models despite a successful sale.order read. The selected model response correctly proceeds; this is a protection control.", C + "/0012.request.json"),
    "B01": ("observed_scope_loss", "src/erp_harness/app/host.py", "Workbench.confirm_business / Workbench._instruction",
            "Historical source_messages replaced confirmed proposal.goal and were labeled New user instructions. The wrong scope is already present in invoice request0001; round29 is the first mail detour after posted/PDF.", I + "/0001.request.json"),
    "B02": ("correct_control", "src/erp_harness/app/host.py", "Workbench._instruction",
            "An independently confirmed delivery proposal contains the invoice/company/recipient bindings. No failure root is asserted; preserve a legitimate delivery intent.", None),
    "B03": ("correct_control", "src/erp_harness/app/business_status.py", "read_business_status",
            "A real status receipt supports SMTP acceptance only. No failure root is asserted.", None),
    "C01": ("propagated_scope_loss", "src/erp_harness/app/host.py", "Workbench.confirm_business / Workbench._instruction",
            "Same scope loss as B01, first correctable at invoice request0001. Round39 follows the PDF-only rejection; it is a recovery checkpoint, not the upstream root.", I + "/0001.request.json"),
    "C02": ("propagated_scope_loss_and_untyped_handoff", "src/erp_harness/app/host.py", "Workbench.confirm_business / Workbench._instruction; TaskEvidence.reference_check",
            "Scope was lost before invoice request0001. The later missing-binding error lacks a handoff; round41 tests recovery after that receipt, not initial root creation.", I + "/0001.request.json"),
    "C03": ("correct_control", "src/erp_harness/app/conversation.py", "_read_invoice_eligibility",
            "The frozen report correctly identifies a business choice. No failure root is asserted and no prepayment choice may be invented.", None),
    "D01": ("repaired_semantic_gap", "src/erp_harness/app/conversation.py", "_read_odoo_reference",
            "A contact lookup labeled an internal company contact as customer without relationship evidence. This prepared checkpoint already contains the accepted entity_kind repair.", None),
    "D02": ("repaired_semantic_gap", "src/erp_harness/app/conversation.py", "_read_odoo_reference",
            "A company name can describe order-company scope or a customer. This prepared checkpoint already exposes both interpretations; it is not the original wire payload.", None),
    "D03": ("correct_control", "src/erp_harness/app/conversation.py", "_read_odoo_reference / propose_business",
            "The original order receipt contradicts the amount in the instruction. No harness root defect is established; preserve clarification before business proposal.", None),
}


def supplement(directory=DEFAULT):
    directory = Path(directory)
    frozen = verify(directory)
    if (directory / "provenance.json").exists():
        return verify_provenance(directory)
    base_commit = subprocess.check_output(["git", "rev-parse", "7eabf86"], cwd=ROOT, text=True).strip()
    items = []
    for row in frozen["cases"]:
        case = row["id"]
        folder = directory / "cases" / case
        manifest = read(folder / "manifest.json")
        evidence = {}

        def archive(path, name):
            path = Path(path)
            data = path.read_bytes()
            saved = folder / "provenance" / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            if saved.exists() and saved.read_bytes() != data:
                raise ValueError("Provenance evidence changed")
            if not saved.exists():
                with saved.open("xb") as stream:
                    stream.write(data)
            evidence[name] = {"source": str(path), "sha256": digest(data), "saved": str(saved.relative_to(directory))}
            return evidence[name]

        if "full-flow-readback-fix" in manifest["source"]:
            base = ROOT / ".runtime/full-flow-readback-fix-20260923"
            hashes = archive(base / "source-hashes.json", "source-hashes.json")
            archive(base / "source-freeze-check.json", "source-freeze-check.json")
            archive(base / "findings.md", "findings.md")
            version = {"base_commit": base_commit, "uncommitted_changes": True,
                       "exact_historical_commit": None, "source_hash_manifest": hashes,
                       "note": "7eabf86 plus readback changes; historical 146-file manifest is authoritative. Capture commit40e7fc6 is later, not substituted for the wire-run version."}
        elif case == "C03":
            base = ROOT / ".runtime/business-readback-20260923"
            hashes = archive(base / "model-source-hashes.json", "source-hashes.json")
            archive(base / "README.md", "version-evidence.md")
            version = {"base_commit": base_commit, "uncommitted_changes": True,
                       "exact_historical_commit": None, "source_hash_manifest": hashes}
        elif manifest["source_kind"] == "prepared_trace_branch":
            origin = read(folder / "source.manifest.json")
            version = {"prepared_commit": origin["commit"], "prepared_is_original_wire": False,
                       "source_hash_manifest": {"saved": str((folder / "source.manifest.json").relative_to(directory)),
                                                "sha256": digest((folder / "source.manifest.json").read_bytes())}}
            ancestor = archive(Path(origin["source_request"]), "ancestor.request.json")
            if ancestor["sha256"] != origin["source_sha256"]:
                raise ValueError("Prepared branch ancestor hash differs")
            if case == "D02":
                delta_path = Path(origin["source_request"]).with_name("frozen-delta.json")
                delta = read(delta_path)
                archive(delta_path, "ancestor-delta.json")
                original = archive(Path(delta["source"]), "original-wire.request.json")
                if original["sha256"] != delta["source_sha256"]:
                    raise ValueError("Original C06 wire evidence hash differs")
                version["intermediate_branch_commit"] = delta.get("commit")
        else:
            version = {"exact_historical_commit": None, "source_hash_manifest": None,
                       "status": "unknown; no per-run source manifest found, and a nearby later repair commit cannot identify this earlier run"}
            archive(ROOT / ".runtime/enterprise-integrity-20260922/B02/summary.json", "run-summary.json")
        status, file, function, explanation, first = ROOTS[case]
        if first:
            early = archive(ROOT / first, "first-correctable.request.json")
            meta = ROOT / first.replace(".request.json", ".meta.json")
            if meta.exists():
                archive(meta, "first-correctable.meta.json")
                early["request_id"] = read(meta).get("request_id")
        else:
            early = {"source": manifest["source"], "sha256": manifest["request_sha256"], "request_id": manifest["request_id"]}
        items.append({"case": case, "source_version": version, "capture_commit": frozen["capture_commit"],
                      "root": {"status": status, "file": file, "function": function, "evidence": explanation},
                      "first_correctable_checkpoint": early,
                      "selected_checkpoint": {"request_id": manifest["request_id"], "source": manifest["source"], "boundary": manifest["boundary"]},
                      "evidence_files": evidence})
    c02 = read(directory / "cases/C02/business.json")
    result = {"version": 1, "freeze_sha256": digest((directory / "freeze.json").read_bytes()), "cases": items,
              "errata": {"C02": {"frozen_oracle_unchanged": True,
                  "clarification": "The original English phrase means each of invoice, company and recipient must have exactly one confirmed binding, not that exactly one category is absent.",
                  "actual_confirmed_references": [{k: r.get(k) for k in ("model", "id", "purpose")} for r in c02["business"]["references"]],
                  "missing_binding_categories": ["invoice", "company", "recipient"],
                  "unchanged_predicate": "No guessed binding or repeated mail write; hand back missing confirmed references. Existing order partner/company fields do not constitute the three host bindings."}}}
    write_once(directory / "provenance.json", result)
    return result


def verify_provenance(directory=DEFAULT):
    directory = Path(directory)
    value = read(directory / "provenance.json")
    if value["freeze_sha256"] != digest((directory / "freeze.json").read_bytes()):
        raise ValueError("Provenance belongs to another frozen pool")
    for case in value["cases"]:
        for evidence in case["evidence_files"].values():
            if digest((directory / evidence["saved"]).read_bytes()) != evidence["sha256"]:
                raise ValueError("Archived provenance evidence changed")
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT)
    args = parser.parse_args()
    print(json.dumps({"cases": len(supplement(args.directory)["cases"]), "paid_calls": 0}))
