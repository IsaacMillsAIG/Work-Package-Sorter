#!/usr/bin/env python3
"""
Steel Fabrication Drawing Package — Work Package Sorter
========================================================
Parses Tekla/SDS2-style shop drawing PDFs, extracts BOM data from each page,
classifies drawings into configurable work packages, and splits the PDF.

Usage:
    python work_package_sorter.py input.pdf [--config rules.yaml] [--output-dir ./output]
"""

import pdfplumber
import re
import json
import yaml
import os
import sys
import csv
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
    pct_str = f"{int(pct * 100):3d}%"
    label_str = label[:40] if label else ""
    # Only print every 5% or on the last item to avoid flooding the UI
    if current == total or current == 1 or int(pct * 100) % 5 == 0:
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
    # Force UTF-8 on stderr for progress lines (compatible with Python 3.7+)
    import io
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

    parser = argparse.ArgumentParser(description="Steel Drawing Package Work Package Sorter")
    parser.add_argument("pdf", help="Path to the drawing package PDF")
    parser.add_argument("--config", help="Path to YAML config file with work package rules")
    parser.add_argument("--output-dir", default="./output", help="Output directory for split PDFs")
    parser.add_argument("--json-file", help="Write JSON summary to this file path (required for app mode)")

    args = parser.parse_args()

    # Load config
    config = DEFAULT_RULES
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            config = yaml.safe_load(f)

    # Parse
    print("Parsing {}...".format(args.pdf), file=sys.stderr, flush=True)
    drawings = parse_pdf(args.pdf)
    print("  Found {} shop drawings".format(len(drawings)), file=sys.stderr, flush=True)

    # Classify
    print("Classifying drawings...", file=sys.stderr, flush=True)
    drawings = classify_drawings(drawings, config)

    # Split PDFs
    print("Splitting PDF into work packages...", file=sys.stderr, flush=True)
    results = split_pdf_by_work_packages(args.pdf, drawings, args.output_dir, config)
    for wp_name, info in results.items():
        print("  {}: {} pages".format(wp_name, info["count"]), file=sys.stderr, flush=True)

    # Generate CSV
    csv_path = os.path.join(args.output_dir, "drawing_summary.csv")
    generate_summary_csv(drawings, csv_path)
    print("Summary CSV: {}".format(csv_path), file=sys.stderr, flush=True)

    # Write JSON to file (avoids any stdout mixing issues)
    summary = generate_summary_json(drawings, config)
    if args.json_file:
        with open(args.json_file, "w", encoding="utf-8") as jf:
            json.dump(summary, jf, indent=2)
        print("Done!", file=sys.stderr, flush=True)
    else:
        # Fallback: print text summary to stderr and JSON to stdout
        print("Done!", file=sys.stderr, flush=True)
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
