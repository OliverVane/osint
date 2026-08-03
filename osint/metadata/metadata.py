"""
osint_metadata.py - Single-file metadata extraction/OSINT toolkit.

Covers: file hashes + type detection, image properties + EXIF/GPS via
Pillow, document properties from PDFs, and author/company/username
metadata from Office Open XML files (docx/xlsx/pptx), plus a scan for
any embedded XMP packet.

Useful both offensively (mining metadata out of documents/images found
during an investigation) and defensively (auditing your own files for
leaked GPS coordinates, usernames, or internal paths before sharing them).

Requires Pillow for image handling:
    pip install Pillow
Optional for PDF document properties:
    pip install pypdf
Office metadata needs nothing extra - it's stdlib zipfile + xml parsing.
"""

import hashlib
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

_HAVE_PYPDF = False
try:
    from pypdf import PdfReader
    _HAVE_PYPDF = True
except ImportError:
    try:
        from PyPDF2 import PdfReader
        _HAVE_PYPDF = True
    except ImportError:
        pass

# ============================================================
# CONFIG - edit this
# ============================================================
FILE_PATH = "screenshot.png"
# ============================================================

REPORTS_DIR = "reports"
LINE_WIDTH = 64

# Fallback signatures for non-image formats (and for images Pillow can't open)
MAGIC_SIGNATURES = [
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "zip_or_ooxml"),
]

XMP_PATTERN = re.compile(rb"<\?xpacket begin=.*?<\?xpacket end=.*?\?>", re.DOTALL)


# ------------------------------------------------------------
# Formatting helpers
# ------------------------------------------------------------

def header(title: str) -> str:
    bar = "=" * LINE_WIDTH
    return f"\n{bar}\n {title}\n{bar}"


def subheader(title: str) -> str:
    bar = "-" * LINE_WIDTH
    return f"\n{bar}\n {title}\n{bar}"


def kv_line(key: str, value, key_width: int = 20) -> str:
    if value is None or value == "":
        value = "n/a"
    return f"  {key.ljust(key_width, '.')}: {value}"


# ------------------------------------------------------------
# File type detection + hashing
# ------------------------------------------------------------

def detect_file_type(path: str) -> str:
    # Try Pillow first - covers JPEG, PNG, GIF, BMP, TIFF, WEBP, ICO, etc.
    try:
        with Image.open(path) as img:
            return img.format.lower()
    except Exception:
        pass

    with open(path, "rb") as f:
        head = f.read(16)

    for sig, name in MAGIC_SIGNATURES:
        if head.startswith(sig):
            if name == "zip_or_ooxml":
                try:
                    with zipfile.ZipFile(path) as z:
                        names = z.namelist()
                        if "word/document.xml" in names:
                            return "docx"
                        if any(n.startswith("xl/") for n in names):
                            return "xlsx"
                        if any(n.startswith("ppt/") for n in names):
                            return "pptx"
                except zipfile.BadZipFile:
                    pass
                return "zip"
            return name

    return "unknown"


def file_basic_info(path: str) -> dict:
    size = os.path.getsize(path)
    hashers = {"md5": hashlib.md5(), "sha1": hashlib.sha1(), "sha256": hashlib.sha256()}

    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            for h in hashers.values():
                h.update(chunk)

    return {
        "path": os.path.abspath(path),
        "size_bytes": size,
        "detected_type": detect_file_type(path),
        "md5": hashers["md5"].hexdigest(),
        "sha1": hashers["sha1"].hexdigest(),
        "sha256": hashers["sha256"].hexdigest(),
    }


# ------------------------------------------------------------
# Image properties (Pillow) - format, dimensions, info dict
# ------------------------------------------------------------

def extract_image_info(path: str) -> dict:
    try:
        with Image.open(path) as img:
            info = {}
            for key, value in img.info.items():
                if isinstance(value, bytes):
                    info[key] = f"<{len(value)} bytes binary data>"
                else:
                    info[key] = value
            return {
                "format": img.format,
                "mode": img.mode,
                "width": img.width,
                "height": img.height,
                "info": info,
            }
    except Exception as exc:
        return {"error": str(exc)}


# ------------------------------------------------------------
# EXIF / GPS (images)
# ------------------------------------------------------------

def _convert_to_degrees(value) -> float:
    d, m, s = value
    return float(d) + (float(m) / 60.0) + (float(s) / 3600.0)


def extract_exif(path: str) -> dict:
    try:
        img = Image.open(path)
        exif = img.getexif()
    except Exception as exc:
        return {"error": str(exc)}

    if not exif:
        return {"found": False}

    result = {"found": True, "tags": {}, "gps": None}

    for tag_id, value in exif.items():
        tag_name = TAGS.get(tag_id, tag_id)
        if isinstance(value, bytes):
            value = f"<{len(value)} bytes binary data>"
        result["tags"][str(tag_name)] = value

    try:
        gps_ifd = exif.get_ifd(0x8825)  # GPS IFD pointer tag
    except Exception:
        gps_ifd = None

    if gps_ifd:
        gps_info = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
        try:
            lat = _convert_to_degrees(gps_info["GPSLatitude"])
            if gps_info.get("GPSLatitudeRef") == "S":
                lat = -lat
            lon = _convert_to_degrees(gps_info["GPSLongitude"])
            if gps_info.get("GPSLongitudeRef") == "W":
                lon = -lon
            result["gps"] = {
                "latitude": lat,
                "longitude": lon,
                "maps_url": f"https://www.google.com/maps?q={lat},{lon}",
            }
        except (KeyError, TypeError, ZeroDivisionError):
            pass

    return result


# ------------------------------------------------------------
# PDF metadata
# ------------------------------------------------------------

def extract_pdf_metadata(path: str) -> dict:
    if not _HAVE_PYPDF:
        return {"error": "pypdf not installed - pip install pypdf"}

    try:
        reader = PdfReader(path)
        info = reader.metadata or {}
        return {
            "page_count": len(reader.pages),
            "title": info.get("/Title"),
            "author": info.get("/Author"),
            "subject": info.get("/Subject"),
            "creator": info.get("/Creator"),
            "producer": info.get("/Producer"),
            "creation_date": str(info.get("/CreationDate")) if info.get("/CreationDate") else None,
            "mod_date": str(info.get("/ModDate")) if info.get("/ModDate") else None,
            "keywords": info.get("/Keywords"),
            "encrypted": reader.is_encrypted,
        }
    except Exception as exc:
        return {"error": str(exc)}


# ------------------------------------------------------------
# Office Open XML metadata (docx/xlsx/pptx)
# ------------------------------------------------------------

def extract_ooxml_metadata(path: str) -> dict:
    result = {"core": {}, "app": {}}
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()

            if "docProps/core.xml" in names:
                root = ET.fromstring(z.read("docProps/core.xml"))
                for child in root:
                    tag = child.tag.split("}")[-1]
                    result["core"][tag] = child.text

            if "docProps/app.xml" in names:
                root = ET.fromstring(z.read("docProps/app.xml"))
                for child in root:
                    tag = child.tag.split("}")[-1]
                    result["app"][tag] = child.text
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        return {"error": str(exc)}

    return result


# ------------------------------------------------------------
# Embedded XMP packet (images, PDFs)
# ------------------------------------------------------------

def extract_xmp(path: str):
    with open(path, "rb") as f:
        raw = f.read()
    match = XMP_PATTERN.search(raw)
    if not match:
        return None
    return match.group(0).decode(errors="replace")


# ------------------------------------------------------------
# Report saving
# ------------------------------------------------------------

def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def save_report(kind: str, name: str, data: dict) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{kind}_{_safe_filename(name)}_{timestamp}.json"
    path = os.path.join(REPORTS_DIR, filename)

    payload = {
        "kind": kind,
        "target": name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    return path


# ------------------------------------------------------------
# Actions
# ------------------------------------------------------------

def action_basic_info(path):
    print(subheader("FILE INFO"))
    info = file_basic_info(path)
    print(kv_line("Path", info["path"]))
    print(kv_line("Size", f"{info['size_bytes']:,} bytes"))
    print(kv_line("Detected type", info["detected_type"]))
    print(kv_line("MD5", info["md5"]))
    print(kv_line("SHA1", info["sha1"]))
    print(kv_line("SHA256", info["sha256"]))
    return info["detected_type"]


def action_image_metadata(path):
    print(subheader("IMAGE PROPERTIES (Pillow)"))
    img_info = extract_image_info(path)
    if "error" in img_info:
        print(f"  {img_info['error']}")
        return

    print(kv_line("Format", img_info["format"]))
    print(kv_line("Mode", img_info["mode"]))
    print(kv_line("Dimensions", f"{img_info['width']} x {img_info['height']}"))

    text_like = {k: v for k, v in img_info["info"].items() if isinstance(v, (str, int, float, tuple))}
    for key, value in text_like.items():
        print(kv_line(f"info.{key}", value))

    print(subheader("EXIF METADATA"))
    result = extract_exif(path)
    if "error" in result:
        print(f"  {result['error']}")
        return
    if not result.get("found"):
        print("  no EXIF data found")
        return

    interesting = ["Make", "Model", "Software", "DateTime", "DateTimeOriginal", "Artist", "Copyright"]
    for key in interesting:
        if key in result["tags"]:
            print(kv_line(key, result["tags"][key]))

    if result["gps"]:
        gps = result["gps"]
        print(kv_line("GPS latitude", gps["latitude"]))
        print(kv_line("GPS longitude", gps["longitude"]))
        print(kv_line("Maps link", gps["maps_url"]))
    else:
        print(kv_line("GPS data", "none found"))

    other = {k: v for k, v in result["tags"].items() if k not in interesting}
    if other:
        print(f"\n  {len(other)} additional EXIF tag(s) present (full list in saved report)")


def action_pdf(path):
    print(subheader("PDF METADATA"))
    result = extract_pdf_metadata(path)
    if "error" in result:
        print(f"  {result['error']}")
        return
    print(kv_line("Pages", result["page_count"]))
    print(kv_line("Title", result["title"]))
    print(kv_line("Author", result["author"]))
    print(kv_line("Subject", result["subject"]))
    print(kv_line("Creator", result["creator"]))
    print(kv_line("Producer", result["producer"]))
    print(kv_line("Created", result["creation_date"]))
    print(kv_line("Modified", result["mod_date"]))
    print(kv_line("Keywords", result["keywords"]))
    print(kv_line("Encrypted", result["encrypted"]))


def action_ooxml(path):
    print(subheader("OFFICE DOCUMENT METADATA"))
    result = extract_ooxml_metadata(path)
    if "error" in result:
        print(f"  {result['error']}")
        return

    core, app = result["core"], result["app"]
    print(kv_line("Title", core.get("title")))
    print(kv_line("Author/Creator", core.get("creator")))
    print(kv_line("Last modified by", core.get("lastModifiedBy")))
    print(kv_line("Created", core.get("created")))
    print(kv_line("Modified", core.get("modified")))
    print(kv_line("Revision", core.get("revision")))
    print(kv_line("Subject", core.get("subject")))
    print(kv_line("Keywords", core.get("keywords")))
    print(kv_line("Description", core.get("description")))
    print(kv_line("Application", app.get("Application")))
    print(kv_line("Company", app.get("Company")))
    print(kv_line("Manager", app.get("Manager")))
    print(kv_line("Template", app.get("Template")))


def action_xmp(path):
    print(subheader("EMBEDDED XMP PACKET"))
    xmp = extract_xmp(path)
    if not xmp:
        print("  no XMP packet found")
        return
    preview = xmp[:500]
    print(f"  found XMP packet ({len(xmp)} chars) - full content saved to report")
    print(f"\n{preview}{'...' if len(xmp) > 500 else ''}")


def action_save_report(path):
    print(subheader("SAVE REPORT"))
    file_type = detect_file_type(path)
    data = {"basic": file_basic_info(path)}

    img_info = extract_image_info(path)
    if "error" not in img_info:
        data["image_info"] = img_info
        data["exif"] = extract_exif(path)
    elif file_type == "pdf":
        data["pdf"] = extract_pdf_metadata(path)
    elif file_type in ("docx", "xlsx", "pptx"):
        data["ooxml"] = extract_ooxml_metadata(path)

    xmp = extract_xmp(path)
    if xmp:
        data["xmp"] = xmp[:20000]

    report_path = save_report("metadata", os.path.basename(path), data)
    print(f"  Saved -> {report_path}")


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    if not os.path.isfile(FILE_PATH):
        print(f"File not found: {FILE_PATH}")
        return

    print(header(f"METADATA OSINT: {os.path.basename(FILE_PATH)}"))

    file_type = action_basic_info(FILE_PATH)
    img_check = extract_image_info(FILE_PATH)

    if "error" not in img_check:
        action_image_metadata(FILE_PATH)
    elif file_type == "pdf":
        action_pdf(FILE_PATH)
    elif file_type in ("docx", "xlsx", "pptx"):
        action_ooxml(FILE_PATH)
    else:
        print(subheader("METADATA"))
        print(f"  no dedicated extractor for detected type: {file_type}")

    action_xmp(FILE_PATH)
    action_save_report(FILE_PATH)

    print("\n" + "=" * LINE_WIDTH + "\n")


if __name__ == "__main__":
    main()