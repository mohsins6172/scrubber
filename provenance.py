"""Read-only detection of AI provenance markers.

This module only reports what a file contains. It never changes the file,
and it does not remove or hide any marker.

Markers checked:
  - C2PA Content Credentials (JUMBF manifest) and the tool that created them
  - IPTC DigitalSourceType (for example, "trainedAlgorithmicMedia")
  - Stable Diffusion / ComfyUI / Forge text chunks ("parameters", "prompt", "workflow")

Signature validation uses the optional c2pa-python package. If it is not
installed, the report says the signature was not checked.
"""
import json
import subprocess
from pathlib import Path

GENERATION_CHUNKS = {"parameters", "prompt", "workflow"}


def _exiftool_json(path: Path) -> dict:
    out = subprocess.run(
        ["exiftool", "-j", "-G1", "-a", "-s", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError("ExifTool could not read this file")
    return json.loads(out.stdout)[0]


def _values_for(data: dict, tag_name: str) -> list[str]:
    """Values for any tag whose name ends with tag_name (group prefix ignored)."""
    found = []
    for key, value in data.items():
        if key.split(":")[-1].lower() == tag_name.lower():
            found.append(str(value))
    return found


def _c2pa_labels(path: Path) -> list[str]:
    out = subprocess.run(
        ["exiftool", "-s3", "-JUMDLabel", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _validate_c2pa(path: Path) -> dict:
    """Validate the C2PA signature, if the optional c2pa-python package is installed."""
    try:
        import c2pa  # optional dependency
    except ImportError:
        return {"checked": False, "reason": "c2pa-python is not installed"}
    try:
        with c2pa.Reader(str(path)) as reader:
            manifest = json.loads(reader.json())
    except Exception as exc:  # the library raises several exception types
        return {"checked": False, "reason": f"manifest could not be read: {exc}"}

    active = manifest.get("active_manifest")
    info = manifest.get("manifests", {}).get(active, {}).get("signature_info", {})
    statuses = manifest.get("validation_status") or []
    codes = [s.get("code") for s in statuses]
    return {
        "checked": True,
        "state": manifest.get("validation_state", "unknown"),
        "issuer": info.get("issuer"),
        "signed_at": info.get("time"),
        "problems": codes,
    }


def inspect_file(path: Path) -> dict:
    """Return a report of AI provenance markers found in the file."""
    data = _exiftool_json(path)

    # C2PA
    labels = _c2pa_labels(path)
    c2pa_present = any(label.lower().startswith("c2pa") for label in labels)
    c2pa = {"present": c2pa_present}
    if c2pa_present:
        generators = _values_for(data, "ActionsSoftwareAgentName") + \
            _values_for(data, "Claim_Generator_InfoName")
        c2pa["generator"] = sorted(set(generators)) or None
        c2pa["signature"] = _validate_c2pa(path)

    # IPTC digital source type
    # Matches DigitalSourceType and the copy inside the C2PA manifest (ActionsDigitalSourceType).
    source_types = [str(v) for k, v in data.items()
                    if k.split(":")[-1].endswith("DigitalSourceType")]
    ai_source = [v for v in source_types if "trainedAlgorithmic" in v]

    # Stable Diffusion / ComfyUI / Forge text chunks
    generation_chunks = sorted({
        key.split(":")[-1].lower() for key in data
        if key.split(":")[-1].lower() in GENERATION_CHUNKS
    })

    # Software tag (informational only)
    software = _values_for(data, "Software")

    markers = []
    if c2pa_present:
        markers.append("C2PA Content Credentials")
    if ai_source:
        markers.append("IPTC source type: trainedAlgorithmicMedia")
    if generation_chunks:
        markers.append("Generation parameters: " + ", ".join(generation_chunks))

    return {
        "file": path.name,
        "ai_markers_found": bool(markers),
        "markers": markers,
        "c2pa": c2pa,
        "software": sorted(set(software)) or None,
    }
