import re
import subprocess
import zipfile
from pathlib import Path

# Personal and device fields to remove. Anything not listed here is left alone,
# including C2PA, JUMBF, and Stable Diffusion/ComfyUI/Forge "parameters" chunks.
PERSONAL_IMAGE_TAGS = [
    # GPS and location (phones and cameras)
    "-GPS:all=",
    "-XMP-exif:GPSLatitude=", "-XMP-exif:GPSLongitude=",
    # Device identifiers
    "-EXIF:Make=", "-EXIF:Model=", "-EXIF:SerialNumber=",
    "-EXIF:LensSerialNumber=", "-EXIF:CameraSerialNumber=",
    "-EXIF:OwnerName=", "-EXIF:HostComputer=", "-EXIF:BodySerialNumber=",
    "-MakerNotes:all=",
    # Timestamps
    "-EXIF:DateTimeOriginal=", "-EXIF:CreateDate=", "-EXIF:ModifyDate=",
    "-EXIF:SubSecTime*=", "-XMP-xmp:CreateDate=", "-XMP-xmp:ModifyDate=",
    "-XMP-xmp:MetadataDate=",
    # Personal creator and rights fields (XMP and IPTC)
    "-XMP-dc:Creator=", "-XMP-dc:Rights=", "-XMP-dc:Description=",
    "-XMP-dc:Subject=", "-XMP-photoshop:AuthorsPosition=",
    "-XMP-photoshop:Credit=", "-XMP-photoshop:Source=",
    "-XMP-xmpRights:all=", "-XMP-iptcCore:all=",
    "-IPTC:all=",
    "-EXIF:Artist=", "-EXIF:Copyright=", "-EXIF:ImageDescription=",
    "-EXIF:UserComment=",
]

PERSONAL_PDF_TAGS = [
    "-PDF:Author=", "-PDF:Creator=", "-PDF:Producer=",
    "-PDF:Keywords=", "-PDF:Subject=", "-PDF:Title=",
    "-XMP-dc:Creator=", "-XMP-pdf:Producer=", "-XMP-xmp:CreatorTool=",
]

# Office files are not handled by ExifTool. Their properties live in XML
# parts, which _scrub_office edits directly.
OFFICE_PROPERTY_TAGS = {
    "docProps/core.xml": [
        "dc:creator", "cp:lastModifiedBy", "dc:title", "dc:subject",
        "cp:keywords", "dc:description", "cp:category",
    ],
    "docProps/app.xml": ["Company", "Manager"],
}
# Preview image of the first page, which can show the document's contents.
OFFICE_THUMBNAIL = "docProps/thumbnail.jpeg"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".avif"}
PDF_EXT = {".pdf"}
OFFICE_EXT = {".docx", ".pptx", ".xlsx"}


class ScrubError(RuntimeError):
    """A file could not be cleaned. The message is safe to show to users."""


def build_args(ext: str) -> list[str] | None:
    """Return the ExifTool tag list for a file type, or None if unsupported.

    Office files return an empty list: they are cleaned by _scrub_office.
    """
    if ext in IMAGE_EXT:
        return PERSONAL_IMAGE_TAGS
    if ext in PDF_EXT:
        return PERSONAL_PDF_TAGS
    if ext in OFFICE_EXT:
        return []
    return None


def _flatten_pdf(path: Path) -> None:
    """Rewrite a PDF as a single revision.

    ExifTool saves PDF edits as an incremental update, which leaves the old
    values (such as /Author) in the file bytes. qpdf rewrites the file and
    drops the unreferenced old objects. Exit code 3 means qpdf repaired
    warnings but still produced output, so it is accepted.
    """
    tmp = path.with_name(path.name + ".flat")
    result = subprocess.run(["qpdf", str(path), str(tmp)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode not in (0, 3):
        tmp.unlink(missing_ok=True)
        raise ScrubError(result.stderr.strip() or "PDF could not be rewritten")
    tmp.replace(path)


def _blank_xml_element(xml: str, tag: str) -> str:
    """Empty the text inside <tag>...</tag>, keeping the element itself."""
    t = re.escape(tag)
    pattern = re.compile(rf"(<{t}\b[^>]*>)(.*?)(</{t}>)", re.S)
    return pattern.sub(lambda m: m.group(1) + m.group(3), xml)


def _scrub_office(path: Path) -> None:
    """Blank author, company and similar fields in an Office (.docx/.pptx/.xlsx) file.

    The file is a zip archive. Each part is copied across unchanged except the
    property parts and the preview thumbnail, which are edited.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        with zipfile.ZipFile(path) as zin, \
                zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                name = info.filename
                if name == OFFICE_THUMBNAIL:
                    continue  # drop the preview image entirely
                data = zin.read(name)
                if re.match(r"^(word|ppt|xl)/.*\.xml$", name):
                    # Author names on tracked changes and comments.
                    text = data.decode("utf-8")
                    text = re.sub(r'\b(w:author|w:initials)="[^"]*"', r'\1=""', text)
                    if name == "ppt/commentAuthors.xml":
                        text = re.sub(r'\b(name|initials)="[^"]*"', r'\1=""', text)
                    data = text.encode("utf-8")
                if name in OFFICE_PROPERTY_TAGS:
                    xml = data.decode("utf-8")
                    for tag in OFFICE_PROPERTY_TAGS[name]:
                        xml = _blank_xml_element(xml, tag)
                    data = xml.encode("utf-8")
                elif name == "_rels/.rels":
                    # Remove the relationship that points to the thumbnail.
                    xml = data.decode("utf-8")
                    xml = re.sub(r"<Relationship\b[^>]*thumbnail[^>]*/>", "", xml)
                    data = xml.encode("utf-8")
                zout.writestr(info, data)
    except zipfile.BadZipFile as exc:
        tmp.unlink(missing_ok=True)
        raise ScrubError(f"Not a valid Office file: {exc}") from exc
    tmp.replace(path)


def scrub_file(src: Path, dst: Path) -> dict:
    """Copy src to dst with personal metadata removed. Returns a report.

    Raises ValueError for unsupported types and ScrubError when the file
    cannot be processed.
    """
    ext = src.suffix.lower()
    tags = build_args(ext)
    if tags is None:
        raise ValueError(f"Unsupported file type: {ext}")

    dst.write_bytes(src.read_bytes())  # work on a copy; never touch the original

    if ext in OFFICE_EXT:
        _scrub_office(dst)
        return {"file": src.name, "status": "cleaned", "log": "office properties blanked"}

    cmd = ["exiftool", "-overwrite_original", "-P", *tags, str(dst)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise ScrubError(result.stderr.strip() or "ExifTool could not read this file")

    if ext in PDF_EXT:
        _flatten_pdf(dst)

    return {"file": src.name, "status": "cleaned", "log": result.stdout.strip()}
