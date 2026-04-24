#!/usr/bin/env python3
"""
Steel Fabrication Drawing Package — Work Package Sorter
========================================================
Parses steel fabrication drawing data from multiple sources:
  - PDF drawing packages (Tekla/SDS2-style title block parsing)
  - PFXT files (Tekla PowerFab eXchange — compressed XML with full BOM)

Classifies assemblies into configurable work packages and splits the PDF.

Usage:
    # PDF (original mode — unchanged):
    python work_package_sorter.py input.pdf [--config rules.yaml] [--output-dir ./output]

    # PFXT (new):
    python work_package_sorter.py export.pfxt [--config rules.yaml] [--output-dir ./output]

    # Force source type explicitly:
    python work_package_sorter.py input.pdf --source pdf
    python work_package_sorter.py export.pfxt --source pfxt
"""

import pdfplumber
import re
import json
import yaml
import os
import sys
import csv
import zipfile
import xml.etree.ElementTree as ET
from pypdf import PdfReader, PdfWriter
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path


# ─────────────────────────────────────────────────────────────
# PROGRESS BAR
# ─────────────────────────────────────────────────────────────

def progress_bar(current: int, total: int, label: str = "", width: int = 40):
    """Print progress to stderr as plain newline-terminated lines for the Electron app."""
    pct = current / total if total else 1.0
    pct_int = int(pct * 100)
    pct_str = f"{pct_int:3d}%"
    label_str = label[:40] if label else ""
    # Always emit the current label (live page tracking),
    # but only update the percentage display when it changes by at least 1%
    prev_pct = int(((current - 1) / total if total else 1.0) * 100)
    if current == total or current == 1 or pct_int > prev_pct:
        sys.stderr.write(f"  {pct_str}  [{current}/{total}]  {label_str}\n")
        sys.stderr.flush()


# ─────────────────────────────────────────────────────────────
# DATA MODEL
# ─────────────────────────────────────────────────────────────

@dataclass
class DrawingData:
    """Extracted data from a single shop drawing page."""
    page_number: int                    # 1-based page index in the PDF
    sheet_no: str = ""                  # e.g. "B1007", "BP1000", "C1009"
    mark_prefix: str = ""              # e.g. "B", "BP", "C", "A", "VB"
    ship_mark: str = ""                # primary assembly mark
    details_of: str = ""               # e.g. "BEAM", "COLUMN", "ANGLE", "BENT PLATE"
    main_section: str = ""             # e.g. "W27x84", "HSS4x4x5/16", "W14x68"
    section_depth: float = 0.0         # parsed depth in inches (e.g. 27 for W27x84)
    section_weight_per_ft: float = 0.0 # parsed plf (e.g. 84 for W27x84)
    total_weight: int = 0              # piece total weight from BOM
    num_minor_marks: int = 0           # count of attached parts
    minor_mark_types: list = field(default_factory=list)  # e.g. ["PL", "BPL", "FB", "L"]
    has_plates: bool = False
    has_bent_plates: bool = False
    has_angles_attached: bool = False
    has_channels: bool = False
    has_flat_bars: bool = False
    has_tubes_hss: bool = False
    has_cjp_dcw: bool = False          # CJP or Demand Critical Weld
    has_pjp: bool = False
    has_moment_connection: bool = False
    fab_status: str = ""               # "FOR FABRICATION", "HOLD", etc.
    galvanize: bool = False
    erection_dwg_ref: str = ""         # e.g. "E1005"
    grade: str = ""                    # e.g. "A992", "A572-50", "A36"
    # Classification result
    work_package: str = ""
    work_package_reason: str = ""


# ─────────────────────────────────────────────────────────────
# PDF PARSER
# ─────────────────────────────────────────────────────────────

def parse_drawing_page(page_index: int, text: str, page=None) -> Optional[DrawingData]:
    """Parse a single page's extracted text into a DrawingData record."""
    
    d = DrawingData(page_number=page_index + 1)
    
    # ── Sheet Number (handles A1000, B1007, BP1000, VB1000, C1009, M1000, etc.)
    m = re.search(r'SHEET\s*NO\.?\s*([A-Z]{1,3}\d{3,5})', text)
    if m:
        d.sheet_no = m.group(1)
    else:
        return None  # Not a shop drawing page (could be cover sheet, erection dwg, etc.)
    
    # ── Mark prefix
    prefix_m = re.match(r'^([A-Z]+)', d.sheet_no)
    d.mark_prefix = prefix_m.group(1) if prefix_m else ""
    
    # ── Ship mark
    d.ship_mark = d.sheet_no  # Usually same as sheet_no for single-piece drawings
    
    # ── Details of (member type)
    det_m = re.search(r'Details\s+of\s+([A-Z][A-Z\s]*?)(?:\s{2,}|\n|$)', text, re.IGNORECASE)
    if det_m:
        d.details_of = det_m.group(1).strip().upper()
    
    # ── Total weight
    # Strategy A: look for "[MARK] ONE [TYPE] [WEIGHT]" line in BOM — most reliable
    # Strategy B: crop top-right of page and find number after "Total weight :" label
    # Strategy C: line-by-line fallback on full text

    _wt_found = False
    _sheet_digits = d.sheet_no.lstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')

    # Strategy A — "[MARK] ONE [MEMBERTYPE] [WEIGHT]" pattern (weight on same line)
    one_pat = re.escape(d.sheet_no) + r'[ \t]+ONE[ \t]+[A-Z].*?(\d+)\s*$'
    one_m = re.search(one_pat, text, re.IGNORECASE | re.MULTILINE)
    if one_m:
        candidate = int(one_m.group(1))
        if str(candidate) != _sheet_digits and candidate > 0:
            d.total_weight = candidate
            _wt_found = True

    # Strategy B — crop top-right quadrant, skip sheet digits, take first valid number
    if not _wt_found and page is not None:
        try:
            pw, ph = float(page.width), float(page.height)
            # Use a wider crop to catch drawings where the value is further right
            crop_text = page.crop((pw * 0.55, 0, pw, ph * 0.15)).extract_text() or ""
            lm = re.search(r'Total[ \t]+weight[ \t]*:', crop_text, re.IGNORECASE)
            if lm:
                for nm in re.finditer(r'(\d+)', crop_text[lm.end():]):
                    c = int(nm.group(1))
                    if str(c) != _sheet_digits and c > 0:
                        d.total_weight = c
                        _wt_found = True
                        break
        except Exception:
            pass

    # Strategy C — full text, find "Total weight" line, take number after colon
    if not _wt_found:
        for line in text.splitlines():
            if re.search(r'total[ \t]+weight', line, re.IGNORECASE):
                after = re.split(r':', line, maxsplit=1)[-1]
                nums = [int(n) for n in re.findall(r'(\d+)', after)
                        if str(n) != _sheet_digits and int(n) > 0]
                if nums:
                    d.total_weight = nums[-1]
                    _wt_found = True
                break

    # Strategy D — total weight appears as a standalone number on its own line
    # in the lower half of the page (happens when the BOM row has no inline weight)
    # Take the LARGEST such standalone number to avoid picking up dimensions
    if not _wt_found:
        all_lines = text.splitlines()
        mid = len(all_lines) // 2
        standalone = []
        for line in all_lines[mid:]:
            stripped = line.strip()
            if re.match(r'^\d+$', stripped):
                val = int(stripped)
                # Exclude sheet digits, small dimension numbers, and 4-digit sheet marks
                if str(val) != _sheet_digits and val > 10 and val < 99999:
                    standalone.append(val)
        if standalone:
            d.total_weight = max(standalone)
            _wt_found = True

    sec_patterns = [
        r'(W\d+[xX]\d+)',
        r'(HSS\d+[xX×][\d.]+[xX×][\d./]+)',
        r'(TS\d+[xX×][\d.]+[xX×][\d./]+)',
        r'(L\d+[xX]\d+[xX][\d/]+)',
        r'(C\d+[xX][\d.]+)',
        r'(MC\d+[xX][\d.]+)',
        r'(WT\d+[xX][\d.]+)',
        r'(HP\d+[xX]\d+)',
        r'(S\d+[xX][\d.]+)',
    ]
    for pat in sec_patterns:
        sec_m = re.search(pat, text)
        if sec_m:
            d.main_section = sec_m.group(1).replace('×', 'x')
            break
    
    # ── Parse section depth and weight per foot
    w_m = re.match(r'W(\d+)[xX](\d+)', d.main_section)
    if w_m:
        d.section_depth = float(w_m.group(1))
        d.section_weight_per_ft = float(w_m.group(2))
    else:
        hss_m = re.match(r'(?:HSS|TS)(\d+)', d.main_section)
        if hss_m:
            d.section_depth = float(hss_m.group(1))
    
    # ── Minor marks / attached parts
    # BOM lines typically look like:  a17    3  L2x2x3/16   or   p159   1  PL3/8x8
    bom_parts = re.findall(
        r'\b[a-z]\d{1,4}\s+\d+\s+'
        r'(PL[\d/]+|BPL[\d/]+|FB[\d/]+|L\d+|C\d+|MC\d+|HSS\d+|TS\d+|WT\d+)',
        text
    )
    d.num_minor_marks = len(bom_parts)
    
    part_types = set()
    for p in bom_parts:
        if p.startswith('BPL'):
            part_types.add('BPL')
        elif p.startswith('PL'):
            part_types.add('PL')
        elif p.startswith('FB'):
            part_types.add('FB')
        elif p.startswith('L'):
            part_types.add('L')
        elif p.startswith(('C', 'MC')):
            part_types.add('C')
        elif p.startswith(('HSS', 'TS')):
            part_types.add('HSS')
        elif p.startswith('WT'):
            part_types.add('WT')
    d.minor_mark_types = sorted(part_types)
    
    # Also check the REMARKS column for BENT
    bent_in_remarks = bool(re.search(r'\bBENT\s+A\d{3}', text))
    
    d.has_plates = 'PL' in part_types
    d.has_bent_plates = 'BPL' in part_types or bent_in_remarks or bool(re.search(r'\bBENT\b', text, re.IGNORECASE) and re.search(r'BPL|BENT\s+(?:A\d|PLATE)', text, re.IGNORECASE))
    d.has_angles_attached = 'L' in part_types
    d.has_channels = 'C' in part_types
    d.has_flat_bars = 'FB' in part_types
    d.has_tubes_hss = 'HSS' in part_types
    
    # ── Weld types
    d.has_cjp_dcw = bool(re.search(r'CJP|DCW|DEMAND\s+CRITICAL', text, re.IGNORECASE))
    d.has_pjp = bool(re.search(r'PJP|PARTIAL\s+JOINT', text, re.IGNORECASE))
    d.has_moment_connection = bool(re.search(r'MOMENT|MOM\.?\s*CONN', text, re.IGNORECASE))
    
    # ── Fabrication status
    if re.search(r'HOLD\s+FROM\s+FABRICATION', text, re.IGNORECASE):
        d.fab_status = "HOLD"
    elif re.search(r'FOR\s+FABRICATION', text, re.IGNORECASE):
        d.fab_status = "FOR FABRICATION"
    else:
        d.fab_status = "UNKNOWN"
    
    # ── Galvanize
    d.galvanize = bool(re.search(r'GALVANIZE.*YES', text, re.IGNORECASE))
    
    # ── Erection drawing reference
    erec_m = re.search(r'WORK\s+THIS\s+SHEET\s+WITH.*?([A-Z]\d{3,5})', text)
    if erec_m:
        d.erection_dwg_ref = erec_m.group(1)
    
    # ── Grade
    grade_m = re.search(r'\b(A992|A572[\s-]?50|A572|A36|A500[\s-]?[ABC]?|A325[N]?|A490)\b', text)
    if grade_m:
        d.grade = grade_m.group(1).replace(' ', '-')
    
    return d


def parse_pdf(pdf_path: str) -> list[DrawingData]:
    """Parse all pages of a drawing package PDF."""
    drawings = []
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            d = parse_drawing_page(i, text, page=page)
            label = d.sheet_no if d else "skipped"
            progress_bar(i + 1, total, label=label)
            if d:
                drawings.append(d)
    return drawings


# ─────────────────────────────────────────────────────────────
# PFXT PARSER (Tekla PowerFab eXchange files)
# ─────────────────────────────────────────────────────────────

PFXT_NS = {"fs": "http://www.fabsuite.com/XML_Schemas/FabSuiteDataFile0104.xsd"}


def _pfxt_find_text(element, tag: str, default: str = "") -> str:
    """Find text content in a PFXT XML element, handling namespace."""
    if element is None:
        return default
    child = element.find(f"fs:{tag}", PFXT_NS)
    if child is None:
        child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return default


def _pfxt_parse_section(shape: str, dimensions: str) -> str:
    """Reconstruct a section string from PFXT Shape + Dimensions fields.

    PFXT stores these separately:
        Shape="W"  Dimensions="24 x 76"    → "W24x76"
        Shape="L"  Dimensions="4 x 4 x 3/8" → "L4x4x3/8"
        Shape="HSS" Dimensions="4 x 4 x 5/16" → "HSS4x4x5/16"
    """
    if not shape or not dimensions:
        return ""
    dim_clean = re.sub(r'\s*x\s*', 'x', dimensions.strip())
    dim_clean = dim_clean.replace('§', '').strip()
    return f"{shape}{dim_clean}"



def _pfxt_infer_member_type(mark: str, main_shape: str, main_category: str) -> str:
    """Infer the member type (BEAM, COLUMN, etc.) from PFXT data."""
    prefix = re.match(r'^([A-Z]+)', mark)
    prefix = prefix.group(1) if prefix else ""
    cat_upper = main_category.upper()

    if "COLUMN" in cat_upper:
        return "COLUMN"
    elif "BEAM" in cat_upper or "JOIST" in cat_upper:
        return "BEAM"
    elif "BRACE" in cat_upper:
        return "VERTICAL BRACE"
    elif "ANGLE" in cat_upper and prefix == "A":
        return "ANGLE"
    elif "BENT" in cat_upper:
        return "BENT PLATE"

    prefix_map = {
        "B": "BEAM", "C": "COLUMN", "A": "ANGLE",
        "BP": "BENT PLATE", "BR": "VERTICAL BRACE",
        "VB": "VERTICAL BRACE", "M": "POST", "PL": "PLATE",
    }
    return prefix_map.get(prefix, "BEAM")


def parse_pfxt_assembly(asm_element, index: int, drawing_statuses: dict) -> DrawingData:
    """Convert a single PFXT <Assembly> element into a DrawingData record."""
    d = DrawingData(page_number=index + 1)

    d.sheet_no = _pfxt_find_text(asm_element, "AssemblyMark")
    d.ship_mark = d.sheet_no

    prefix_m = re.match(r'^([A-Z]+)', d.sheet_no)
    d.mark_prefix = prefix_m.group(1) if prefix_m else ""

    drawing_no = _pfxt_find_text(asm_element, "DrawingNumber")

    all_parts = asm_element.findall("fs:AssemblyPart", PFXT_NS)
    if not all_parts:
        all_parts = asm_element.findall("AssemblyPart")

    main_parts = [p for p in all_parts if _pfxt_find_text(p, "MainMember") == "1"]
    minor_parts = [p for p in all_parts if _pfxt_find_text(p, "MainMember") != "1"]

    if main_parts:
        main = main_parts[0]
        shape = _pfxt_find_text(main, "Shape")
        dims = _pfxt_find_text(main, "Dimensions")
        d.main_section = _pfxt_parse_section(shape, dims)
        d.grade = _pfxt_find_text(main, "Grade")
        main_category = _pfxt_find_text(main, "Category")

        w_m = re.match(r'W(\d+)x(\d+)', d.main_section)
        if w_m:
            d.section_depth = float(w_m.group(1))
            d.section_weight_per_ft = float(w_m.group(2))
        else:
            hss_m = re.match(r'(?:HSS|TS)(\d+)', d.main_section)
            if hss_m:
                d.section_depth = float(hss_m.group(1))

        for part in main_parts:
            finish = _pfxt_find_text(part, "Finish").upper()
            if "GALV" in finish:
                d.galvanize = True
    else:
        main_category = ""

    d.details_of = _pfxt_infer_member_type(
        d.sheet_no,
        _pfxt_find_text(main_parts[0], "Shape") if main_parts else "",
        main_category
    )

    d.num_minor_marks = len(minor_parts)
    part_types = set()
    for part in minor_parts:
        shape = _pfxt_find_text(part, "Shape").upper()
        category = _pfxt_find_text(part, "Category").upper()
        remark = _pfxt_find_text(part, "Remark").upper()

        if "BENT" in category or remark == "BENT":
            part_types.add("BPL")
        elif shape == "FB" or "FLAT" in category:
            part_types.add("FB")
        elif shape == "PL" or "PLATE" in category or "LAYOUT" in category:
            part_types.add("PL")
        elif shape.startswith("L") or "ANGLE" in category:
            part_types.add("L")
        elif shape.startswith("C") or "CHANNEL" in category:
            part_types.add("C")
        elif "HSS" in shape or "TS" in shape or "TUBE" in category:
            part_types.add("HSS")
        elif "WT" in shape:
            part_types.add("WT")
        else:
            part_types.add("PL")

    d.minor_mark_types = sorted(part_types)
    d.has_plates = "PL" in part_types
    d.has_bent_plates = "BPL" in part_types
    d.has_angles_attached = "L" in part_types
    d.has_channels = "C" in part_types
    d.has_flat_bars = "FB" in part_types
    d.has_tubes_hss = "HSS" in part_types

    all_text = " ".join(
        _pfxt_find_text(p, "Remark") + " " + _pfxt_find_text(p, "Category")
        for p in all_parts
    )
    d.has_cjp_dcw = bool(re.search(r'CJP|DCW|DEMAND\s*CRITICAL', all_text, re.IGNORECASE))
    d.has_pjp = bool(re.search(r'PJP|PARTIAL\s*JOINT', all_text, re.IGNORECASE))

    status = drawing_statuses.get(drawing_no, "")
    if "HOLD" in status.upper():
        d.fab_status = "HOLD"
    elif status:
        d.fab_status = "FOR FABRICATION"
    else:
        d.fab_status = "FOR FABRICATION"

    return d


def parse_pfxt(pfxt_path: str) -> tuple[list[DrawingData], dict]:
    """
    Parse a .pfxt file (Tekla PowerFab eXchange format).

    PFXT is a ZIP archive containing an XML file with full BOM/assembly data.
    Weight is not present in the XML — it is read from the bundled detail
    sheet PDFs in Drawings/DetailSheetDrawings/<mark>.pdf using the same
    PDF weight-extraction logic used for standalone PDF input.

    Returns: (list of DrawingData, project metadata dict)
    """
    if not zipfile.is_zipfile(pfxt_path):
        raise ValueError(f"{pfxt_path} is not a valid .pfxt file (not a ZIP archive)")

    with zipfile.ZipFile(pfxt_path, 'r') as zf:
        names = zf.namelist()

        # ── Find and read the XML
        xml_name = None
        for name in names:
            if name.endswith('.xml') and '/' not in name:
                xml_name = name
                break
        if xml_name is None:
            for name in names:
                if name.endswith('.xml'):
                    xml_name = name
                    break
        if xml_name is None:
            raise ValueError(f"No XML file found in {pfxt_path}")

        xml_content = zf.read(xml_name)
        print(f"  Parsing XML: {xml_name} ({len(xml_content)} bytes)", file=sys.stderr, flush=True)

        root = ET.fromstring(xml_content)
        root_tag = root.tag
        ns_match = re.match(r'\{([^}]+)\}', root_tag)
        detected_ns = ns_match.group(1) if ns_match else None
        print(f"  XML root tag: {root_tag}", file=sys.stderr, flush=True)

        # ── SDS2 format — hand off
        if 'SDS2_Data_Transfer' in root_tag or 'SDS2' in root_tag.upper():
            print("  Detected SDS2 format — switching to SDS2 parser", file=sys.stderr, flush=True)
            return parse_sds2(pfxt_path)

        # ── Namespace
        global PFXT_NS
        PFXT_NS = {"fs": detected_ns} if detected_ns else \
                  {"fs": "http://www.fabsuite.com/XML_Schemas/FabSuiteDataFile0104.xsd"}

        # ── Project metadata
        project_info = {}
        proj = root.find(".//fs:ProjectData/fs:ContractData/fs:ProjectId", PFXT_NS)
        if proj is not None:
            project_info["project_number"] = _pfxt_find_text(proj, "ProjectNumber")
            project_info["project_name"]   = _pfxt_find_text(proj, "ProjectName")
        source = root.find(".//fs:FileSourceData", PFXT_NS)
        if source is not None:
            project_info["source_app"]     = _pfxt_find_text(source, "SourceApplication")
            project_info["source_version"] = _pfxt_find_text(source, "SourceApplicationVersion")
            project_info["export_date"]    = _pfxt_find_text(source, "FileCreationDate")
            company = source.find("fs:CompanyData", PFXT_NS)
            if company is not None:
                project_info["company"] = _pfxt_find_text(company, "CompanyName")

        # ── Drawing statuses
        drawing_statuses = {}
        for md in root.findall(".//fs:MultiDrawing", PFXT_NS):
            dnum = _pfxt_find_text(md, "DrawingNumber")
            rev  = _pfxt_find_text(md, "DrawingRevision/RevisionDescription")
            if dnum and rev:
                drawing_statuses[dnum] = rev
        for sd in root.findall(".//fs:SingleDrawing", PFXT_NS):
            dnum = _pfxt_find_text(sd, "DrawingNumber")
            rev  = _pfxt_find_text(sd, "DrawingRevision/RevisionDescription")
            if dnum and rev:
                drawing_statuses[dnum] = rev

        # ── Assembly elements
        assemblies = root.findall(".//fs:Assembly", PFXT_NS) or \
                     root.findall(".//Assembly")
        print(f"  Found {len(assemblies)} assembly elements in XML", file=sys.stderr, flush=True)

        # ── Index bundled detail PDFs by piecemark
        # PFXT archives store drawings in Drawings/MultiDrawings/<mark>.pdf
        # Fall back to DetailSheetDrawings for older exports
        pdf_index = {}
        for entry in names:
            if not entry.endswith(".pdf"):
                continue
            stem = entry.split("/")[-1].replace(".pdf", "")
            if "MultiDrawings" in entry:
                pdf_index[stem] = entry          # preferred — overwrites any prior match
            elif stem not in pdf_index:
                pdf_index[stem] = entry          # fallback
        print(f"  Bundled detail PDFs: {len(pdf_index)}", file=sys.stderr, flush=True)

        # ── Parse assemblies, look up weight from bundled PDF
        drawings = []
        seen_marks = set()
        total = len(assemblies)

        for i, asm in enumerate(assemblies):
            mark = _pfxt_find_text(asm, "AssemblyMark")
            if mark and mark in seen_marks:
                continue
            if mark:
                seen_marks.add(mark)

            d = parse_pfxt_assembly(asm, len(drawings), drawing_statuses)

            # Weight is not in PFXT XML — extract from bundled detail sheet PDF
            if mark and mark in pdf_index:
                try:
                    import io
                    pdf_bytes = zf.read(pdf_index[mark])
                    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                        if pdf.pages:
                            text = pdf.pages[0].extract_text() or ""
                            pdf_d = parse_drawing_page(0, text, page=pdf.pages[0])
                            if pdf_d and pdf_d.total_weight:
                                d.total_weight = pdf_d.total_weight
                except Exception as e:
                    print(f"  Warning: PDF weight lookup failed for {mark}: {e}",
                          file=sys.stderr, flush=True)

            drawings.append(d)
            progress_bar(i + 1, total, label=f"{mark}")

    project_info["total_assemblies"] = len(drawings)
    project_info["file_type"] = "pfxt"
    print(f"  Found {len(drawings)} assemblies", file=sys.stderr, flush=True)
    return drawings, project_info



# ─────────────────────────────────────────────────────────────
# SDS2 DATA TRANSFER XML PARSER  (v2.0 schema, confirmed 2025-04)
# ─────────────────────────────────────────────────────────────
#
# Confirmed structure (SDS2 2025.08, FileVersion 2.0):
#
#   <SDS2_Data_Transfer>
#     <MetaData>
#       <Job>, <Fabricator>, <SDS2Version>
#     </MetaData>
#     <DrawingSheet>   ← one per drawing
#       <SheetType>Detail Sheet</SheetType>  ← only these are assemblies
#       <Name>4001B</Name>
#       <WorkType>Beam</WorkType>
#       <DrawingData>
#         <RevisionDescription>ISSUED FOR FABRICATION</RevisionDescription>
#         <ApprovalStatus>Not reviewed</ApprovalStatus>
#         ...dates...
#       </DrawingData>
#       (NO section, weight, or minor mark data in the XML —
#        all of that comes from the bundled PDFs in the archive)
#     </DrawingSheet>
#   </SDS2_Data_Transfer>
#
# Strategy: read piecemark/type/status from XML, then parse each assembly's
# PDF from Drawings/DetailSheetDrawings/<Name>.pdf using parse_drawing_page()
# to get section size, weight, minor marks, and weld flags.


def _sds2_text(el, tag, default=""):
    """Return text of a direct child element, or default."""
    child = el.find(tag)
    return child.text.strip() if (child is not None and child.text) else default


def _sds2_member_type(work_type: str, mark: str) -> str:
    """Map SDS2 <WorkType> to our canonical member type string."""
    wt = work_type.upper()
    if "COLUMN" in wt:                                    return "COLUMN"
    if "VERTICAL BRACE" in wt or "DIAGONAL" in wt:       return "VERTICAL BRACE"
    if "BRACE" in wt:                                     return "VERTICAL BRACE"
    if "BEAM" in wt or "JOIST" in wt or "PURLIN" in wt:  return "BEAM"
    if "ANGLE" in wt:                                     return "ANGLE"
    if "BENT" in wt:                                      return "BENT PLATE"
    if "STAIR" in wt or "MISC" in wt:                    return "MISC"
    # Fall back to mark prefix
    prefix_m = re.match(r'^([A-Za-z]+)', mark)
    prefix = prefix_m.group(1).upper() if prefix_m else ""
    return {"B":"BEAM","C":"COLUMN","A":"ANGLE","BP":"BENT PLATE","BPL":"BENT PLATE",
            "BR":"VERTICAL BRACE","VB":"VERTICAL BRACE","HSS":"BEAM",
            "M":"POST","PL":"PLATE"}.get(prefix, "BEAM")


def _sds2_fab_status(dd_el) -> str:
    """
    Determine fab status from <DrawingData>.
    SDS2 v2.0 uses <RevisionDescription> — e.g. "ISSUED FOR FABRICATION", "HOLD".
    """
    if dd_el is None:
        return "FOR FABRICATION"
    rev = _sds2_text(dd_el, "RevisionDescription").upper()
    if "HOLD" in rev or "DO NOT FAB" in rev or "VOID" in rev:
        return "HOLD"
    if "FABRICATION" in rev or "IFC" in rev or "FOR FAB" in rev:
        return "FOR FABRICATION"
    approval = _sds2_text(dd_el, "ApprovalStatus").upper()
    if "REVISE" in approval or "REJECT" in approval:
        return "HOLD"
    return "FOR FABRICATION"


def parse_sds2(pfxs_path: str) -> tuple[list[DrawingData], dict]:
    """
    Parse an SDS2 Data Transfer .pfxs file.

    Step 1 — XML: read piecemark, WorkType, RevisionDescription per DrawingSheet.
    Step 2 — PDF: for each Detail Sheet assembly, open its bundled PDF from
             Drawings/DetailSheetDrawings/<Name>.pdf and run parse_drawing_page()
             to extract section size, weight, minor marks, and weld flags.
    """
    with zipfile.ZipFile(pfxs_path, 'r') as zf:
        # ── Parse XML
        xml_name = next(
            (n for n in zf.namelist() if n.endswith('.xml') and '/' not in n),
            next((n for n in zf.namelist() if n.endswith('.xml')), None)
        )
        if not xml_name:
            raise ValueError(f"No XML found in {pfxs_path}")

        xml_content = zf.read(xml_name)
        print(f"  Parsing SDS2 XML: {xml_name} ({len(xml_content)} bytes)", file=sys.stderr, flush=True)
        root = ET.fromstring(xml_content)

        # ── Project metadata
        project_info = {"file_type": "sds2", "source_app": "SDS2"}
        meta = root.find("MetaData")
        if meta is not None:
            project_info["project_name"] = _sds2_text(meta, "Job")
            project_info["fabricator"]   = _sds2_text(meta, "Fabricator")
            project_info["sds2_version"] = _sds2_text(meta, "SDS2Version")

        # ── Index bundled Detail Sheet PDFs by piecemark name
        pdf_index = {}
        for entry in zf.namelist():
            if entry.startswith("Drawings/DetailSheetDrawings/") and entry.endswith(".pdf"):
                stem = entry.split("/")[-1].replace(".pdf", "")
                pdf_index[stem] = entry

        # ── Collect Detail Sheet elements from XML
        all_sheets = root.findall("DrawingSheet")
        detail_sheets = [s for s in all_sheets
                         if _sds2_text(s, "SheetType") == "Detail Sheet"]

        print(f"  Total DrawingSheet elements: {len(all_sheets)}", file=sys.stderr, flush=True)
        print(f"  Detail Sheets (assemblies): {len(detail_sheets)}", file=sys.stderr, flush=True)
        print(f"  Bundled detail PDFs: {len(pdf_index)}", file=sys.stderr, flush=True)

        drawings = []
        seen_marks = set()
        total = len(detail_sheets)

        for i, sheet in enumerate(detail_sheets):
            name = _sds2_text(sheet, "Name")
            if not name or name in seen_marks:
                continue
            seen_marks.add(name)

            work_type = _sds2_text(sheet, "WorkType")
            dd = sheet.find("DrawingData")

            # Base record from XML
            d = DrawingData(page_number=i + 1)
            d.sheet_no    = name
            d.ship_mark   = name
            prefix_m = re.match(r'^([A-Za-z]+)', name)
            d.mark_prefix  = prefix_m.group(1).upper() if prefix_m else ""
            d.details_of   = _sds2_member_type(work_type, name)
            d.fab_status   = _sds2_fab_status(dd)

            # Enrich from bundled PDF
            if name in pdf_index:
                try:
                    import io
                    pdf_bytes = zf.read(pdf_index[name])
                    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                        if pdf.pages:
                            page = pdf.pages[0]
                            text = page.extract_text() or ""
                            pdf_d = parse_drawing_page(0, text, page=page)
                            if pdf_d:
                                d.main_section          = pdf_d.main_section
                                d.section_depth         = pdf_d.section_depth
                                d.section_weight_per_ft = pdf_d.section_weight_per_ft
                                d.total_weight          = pdf_d.total_weight
                                d.num_minor_marks       = pdf_d.num_minor_marks
                                d.minor_mark_types      = pdf_d.minor_mark_types
                                d.has_plates            = pdf_d.has_plates
                                d.has_bent_plates       = pdf_d.has_bent_plates
                                d.has_angles_attached   = pdf_d.has_angles_attached
                                d.has_channels          = pdf_d.has_channels
                                d.has_flat_bars         = pdf_d.has_flat_bars
                                d.has_tubes_hss         = pdf_d.has_tubes_hss
                                d.has_cjp_dcw           = pdf_d.has_cjp_dcw
                                d.has_pjp               = pdf_d.has_pjp
                                d.has_moment_connection = pdf_d.has_moment_connection
                                d.galvanize             = pdf_d.galvanize
                                d.grade                 = pdf_d.grade or d.grade
                                # Keep XML's fab_status — more reliable than PDF for SDS2
                except Exception as e:
                    print(f"  Warning: could not parse PDF for {name}: {e}", file=sys.stderr, flush=True)

            drawings.append(d)
            progress_bar(i + 1, total, label=f"{name} ({work_type})")

    project_info["total_assemblies"] = len(drawings)
    print(f"  Parsed {len(drawings)} unique assemblies", file=sys.stderr, flush=True)
    return drawings, project_info

# ─────────────────────────────────────────────────────────────
# UNIFIED FILE DETECTION
# ─────────────────────────────────────────────────────────────

def detect_file_type(filepath: str) -> str:
    """Auto-detect whether a file is a PDF or PFXT."""
    ext = Path(filepath).suffix.lower()
    if ext in ('.pfxt', '.pfxs', '.pfxa'):
        return 'pfxt'
    elif ext == '.pdf':
        return 'pdf'
    elif ext == '.zip' and zipfile.is_zipfile(filepath):
        with zipfile.ZipFile(filepath, 'r') as zf:
            for name in zf.namelist():
                if name.endswith('.xml') and '/' not in name:
                    with zf.open(name) as f:
                        header = f.read(500).decode('utf-8', errors='ignore')
                        if 'FabSuiteDataExchange' in header:
                            return 'pfxt'
    return 'unknown'


def parse_file(filepath: str, source_type: str = None) -> tuple[list[DrawingData], dict]:
    """
    Parse any supported file type and return DrawingData list + metadata.
    Auto-detects file type if source_type is not specified.
    """
    if source_type is None:
        source_type = detect_file_type(filepath)

    if source_type == 'pfxt':
        return parse_pfxt(filepath)
    elif source_type == 'pdf':
        drawings = parse_pdf(filepath)
        meta = {
            "file_type": "pdf",
            "total_assemblies": len(drawings),
            "source_file": os.path.basename(filepath),
        }
        return drawings, meta
    else:
        raise ValueError(
            f"Unsupported file type: {Path(filepath).suffix}\n"
            f"Supported formats: .pdf, .pfxt, .pfxs, .pfxa"
        )


# ─────────────────────────────────────────────────────────────
# CLASSIFICATION ENGINE
# ─────────────────────────────────────────────────────────────

DEFAULT_RULES = {
    "work_packages": [
        {
            "name": "WP-01 Small Parts & Embeds",
            "description": "Standalone angles, bent plates, miscellaneous small pieces",
            "color": "#22c55e",
            "rules": {
                "mark_prefix_in": ["A", "BP", "M", "PL"],
            }
        },
        {
            "name": "WP-02 Simple Beams (CNC only)",
            "description": "Beams with no attached parts — straight off PythonX, no fitting or welding",
            "color": "#3b82f6",
            "rules": {
                "details_of_in": ["BEAM"],
                "max_minor_marks": 0,
                "has_bent_plates": False,
                "has_cjp_dcw": False,
            }
        },
        {
            "name": "WP-03 Beams w/ Bent Plates",
            "description": "Beams with bent plate attachments — press brake coordination required",
            "color": "#f97316",
            "rules": {
                "details_of_in": ["BEAM"],
                "has_bent_plates": True,
                "has_cjp_dcw": False,
            }
        },
        {
            "name": "WP-04 Moderate Beams",
            "description": "Beams with 1-4 attached parts, no bent plates, standard fitting & welding",
            "color": "#8b5cf6",
            "rules": {
                "details_of_in": ["BEAM"],
                "min_minor_marks": 1,
                "max_minor_marks": 4,
                "has_bent_plates": False,
                "has_cjp_dcw": False,
            }
        },
        {
            "name": "WP-05 Complex Beams",
            "description": "Beams with 5+ parts, no bent plates, heavy fitting",
            "color": "#f59e0b",
            "rules": {
                "details_of_in": ["BEAM"],
                "min_minor_marks": 5,
                "has_bent_plates": False,
                "has_cjp_dcw": False,
            }
        },
        {
            "name": "WP-06 Beams with CJP/DCW",
            "description": "Any beam requiring CJP or demand critical welds",
            "color": "#ef4444",
            "rules": {
                "details_of_in": ["BEAM"],
                "has_cjp_dcw": True,
            }
        },
        {
            "name": "WP-07 Simple Columns",
            "description": "Columns without CJP welds",
            "color": "#06b6d4",
            "rules": {
                "details_of_in": ["COLUMN"],
                "has_cjp_dcw": False,
            }
        },
        {
            "name": "WP-08 Complex Columns (CJP/DCW)",
            "description": "Columns with demand critical welds — certified welders & UT required",
            "color": "#dc2626",
            "rules": {
                "details_of_in": ["COLUMN"],
                "has_cjp_dcw": True,
            }
        },
        {
            "name": "WP-09 Vertical Braces",
            "description": "VB-series bracing members",
            "color": "#14b8a6",
            "rules": {
                "mark_prefix_in": ["VB", "BR"],
            }
        },
        {
            "name": "WP-99 Unclassified",
            "description": "Drawings not matching any rule — review manually",
            "color": "#6b7280",
            "rules": {},  # catch-all
        }
    ]
}


def matches_rule(drawing: DrawingData, rules: dict) -> bool:
    """Check if a drawing matches a set of classification rules."""
    
    if not rules:  # Empty rules = catch-all
        return True
    
    for key, value in rules.items():
        if key == "mark_prefix_in":
            if drawing.mark_prefix not in value:
                return False
                
        elif key == "details_of_in":
            if drawing.details_of not in value:
                return False
                
        elif key == "min_minor_marks":
            if drawing.num_minor_marks < value:
                return False
                
        elif key == "max_minor_marks":
            if drawing.num_minor_marks > value:
                return False
                
        elif key == "has_bent_plates":
            if drawing.has_bent_plates != value:
                return False
                
        elif key == "has_cjp_dcw":
            if drawing.has_cjp_dcw != value:
                return False
                
        elif key == "has_pjp":
            if drawing.has_pjp != value:
                return False
                
        elif key == "has_moment_connection":
            if drawing.has_moment_connection != value:
                return False
                
        elif key == "galvanize":
            if drawing.galvanize != value:
                return False
                
        elif key == "fab_status":
            if drawing.fab_status != value:
                return False
                
        elif key == "min_weight":
            if drawing.total_weight < value:
                return False
                
        elif key == "max_weight":
            if drawing.total_weight > value:
                return False
                
        elif key == "min_section_depth":
            if drawing.section_depth < value:
                return False
                
        elif key == "max_section_depth":
            if drawing.section_depth > value:
                return False
                
        elif key == "grade_in":
            if drawing.grade not in value:
                return False
                
        elif key == "erection_dwg_ref_in":
            if drawing.erection_dwg_ref not in value:
                return False
                
        elif key == "main_section_in":
            if drawing.main_section not in value:
                return False
    
    return True


def classify_drawings(drawings: list[DrawingData], config: dict = None) -> list[DrawingData]:
    """Apply work package rules to all drawings. First matching rule wins."""
    
    if config is None:
        config = DEFAULT_RULES
    
    total = len(drawings)
    for idx, drawing in enumerate(drawings):
        drawing.work_package = ""
        drawing.work_package_reason = ""
        
        for wp in config["work_packages"]:
            if matches_rule(drawing, wp.get("rules", {})):
                drawing.work_package = wp["name"]
                drawing.work_package_reason = wp.get("description", "")
                break
        
        progress_bar(idx + 1, total, label=f"{drawing.sheet_no} → {drawing.work_package[:20]}")
    
    return drawings


# ─────────────────────────────────────────────────────────────
# PDF SPLITTING
# ─────────────────────────────────────────────────────────────

def split_pdf_by_work_packages(
    pdf_path: str,
    drawings: list[DrawingData],
    output_dir: str,
    config: dict = None
) -> dict:
    """Split the source PDF into separate PDFs per work package."""
    
    os.makedirs(output_dir, exist_ok=True)
    reader = PdfReader(pdf_path)
    
    if config is None:
        config = DEFAULT_RULES
    
    # Group drawings by work package
    wp_groups = {}
    for d in drawings:
        wp = d.work_package or "WP-99 Unclassified"
        if wp not in wp_groups:
            wp_groups[wp] = []
        wp_groups[wp].append(d)
    
    results = {}
    wp_items = list(wp_groups.items())
    total_wps = len(wp_items)
    for wp_idx, (wp_name, wp_drawings) in enumerate(wp_items):
        safe_name = re.sub(r'[^\w\-]', '_', wp_name)
        out_path = os.path.join(output_dir, f"{safe_name}.pdf")
        
        writer = PdfWriter()
        for d in sorted(wp_drawings, key=lambda x: x.sheet_no):
            page_idx = d.page_number - 1  # 0-based
            if page_idx < len(reader.pages):
                writer.add_page(reader.pages[page_idx])
        
        with open(out_path, "wb") as f:
            writer.write(f)
        
        progress_bar(wp_idx + 1, total_wps, label=wp_name[:30])
        results[wp_name] = {
            "file": out_path,
            "count": len(wp_drawings),
            "marks": [d.sheet_no for d in sorted(wp_drawings, key=lambda x: x.sheet_no)],
        }
    
    return results


# ─────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────

def generate_summary_csv(drawings: list[DrawingData], output_path: str):
    """Generate a CSV summary of all drawings and their work package assignments."""
    
    fieldnames = [
        "sheet_no", "mark_prefix", "details_of", "main_section",
        "total_weight", "num_minor_marks", "minor_mark_types",
        "has_bent_plates", "has_cjp_dcw", "has_pjp",
        "fab_status", "galvanize", "erection_dwg_ref", "grade",
        "work_package", "page_number"
    ]
    
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for d in sorted(drawings, key=lambda x: x.sheet_no):
            row = {k: getattr(d, k) for k in fieldnames}
            row["minor_mark_types"] = ", ".join(d.minor_mark_types)
            writer.writerow(row)


def generate_summary_json(drawings: list[DrawingData], config: dict = None) -> dict:
    """Generate a JSON summary suitable for the UI."""
    
    if config is None:
        config = DEFAULT_RULES
    
    # Build work package summary
    wp_summary = {}
    for d in drawings:
        wp = d.work_package or "WP-99 Unclassified"
        if wp not in wp_summary:
            wp_summary[wp] = {
                "name": wp,
                "count": 0,
                "total_weight": 0,
                "marks": [],
                "hold_count": 0,
                "for_fab_count": 0,
            }
        wp_summary[wp]["count"] += 1
        wp_summary[wp]["total_weight"] += d.total_weight
        wp_summary[wp]["marks"].append(d.sheet_no)
        if d.fab_status == "HOLD":
            wp_summary[wp]["hold_count"] += 1
        elif d.fab_status == "FOR FABRICATION":
            wp_summary[wp]["for_fab_count"] += 1
    
    # Add colors from config
    color_map = {}
    for wp in config.get("work_packages", []):
        color_map[wp["name"]] = wp.get("color", "#6b7280")
    
    for name, data in wp_summary.items():
        data["color"] = color_map.get(name, "#6b7280")
    
    return {
        "total_drawings": len(drawings),
        "total_pages_with_data": len(drawings),
        "hold_count": sum(1 for d in drawings if d.fab_status == "HOLD"),
        "for_fab_count": sum(1 for d in drawings if d.fab_status == "FOR FABRICATION"),
        "work_packages": wp_summary,
        "drawings": [asdict(d) for d in drawings],
    }


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main():
    import argparse

    # Force UTF-8 output so Windows doesn't choke on special characters (▾, █, etc.)
    import io
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

    parser = argparse.ArgumentParser(
        description="Steel Drawing Package Work Package Sorter",
        epilog="Supports .pdf drawing packages and .pfxt Tekla PowerFab exchange files."
    )
    parser.add_argument("input_file", help="Path to .pdf or .pfxt file")
    parser.add_argument("--source", choices=["pdf", "pfxt"],
                        help="Force source type (auto-detected if omitted)")
    parser.add_argument("--config", help="Path to YAML config file with work package rules")
    parser.add_argument("--output-dir", default="./output", help="Output directory for split PDFs")
    parser.add_argument("--json-file", help="Write JSON summary to this file path (required for app mode)")

    args = parser.parse_args()

    # Load config
    config = DEFAULT_RULES
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            config = yaml.safe_load(f)

    # Detect and parse
    filepath = args.input_file
    file_type = args.source or detect_file_type(filepath)

    print(f"Parsing {filepath} (type: {file_type})...", file=sys.stderr, flush=True)
    drawings, meta = parse_file(filepath, file_type)

    if meta.get('project_name'):
        print(f"  Project: {meta.get('project_number', '')} — {meta['project_name']}", file=sys.stderr, flush=True)
    if meta.get('company'):
        print(f"  Company: {meta['company']}", file=sys.stderr, flush=True)
    print(f"  Found {len(drawings)} assemblies", file=sys.stderr, flush=True)

    # Classify
    print("Classifying drawings...", file=sys.stderr, flush=True)
    drawings = classify_drawings(drawings, config)

    os.makedirs(args.output_dir, exist_ok=True)

    # Split PDFs — only available for PDF input
    if file_type == 'pdf':
        print("Splitting PDF into work packages...", file=sys.stderr, flush=True)
        results = split_pdf_by_work_packages(filepath, drawings, args.output_dir, config)
        for wp_name, info in results.items():
            print("  {}: {} pages".format(wp_name, info["count"]), file=sys.stderr, flush=True)
    else:
        print("(PDF splitting not available for .pfxt input — CSV/JSON output only)", file=sys.stderr, flush=True)

    # Generate CSV
    csv_path = os.path.join(args.output_dir, "drawing_summary.csv")
    generate_summary_csv(drawings, csv_path)
    print("Summary CSV: {}".format(csv_path), file=sys.stderr, flush=True)

    # Write JSON — to file if --json-file given (app mode), otherwise stdout
    summary = generate_summary_json(drawings, config)
    summary["project_info"] = meta
    if args.json_file:
        with open(args.json_file, "w", encoding="utf-8") as jf:
            json.dump(summary, jf, indent=2)
        print("Done!", file=sys.stderr, flush=True)
    else:
        print("Done!", file=sys.stderr, flush=True)
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
