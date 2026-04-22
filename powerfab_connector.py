#!/usr/bin/env python3
"""
Tekla PowerFab API Integration — Work Package Sorter
=====================================================
Connects to Tekla PowerFab (EPM) via the Open API, retrieves assembly
and BOM data for a job, and feeds it into the work package classification
engine. Optionally writes sequence/lot assignments back to PowerFab.

Prerequisites:
    - Tekla PowerFab installed with API access enabled
    - An External User created in PowerFab with appropriate permissions
    - Network access to the PowerFab database (default port 3306)

Usage:
    python powerfab_connector.py --host localhost --port 3306 \\
        --user api_user --password secret \\
        --job "25509" --config rules.yaml --output-dir ./output

    # List available jobs:
    python powerfab_connector.py --host localhost --user api_user --password secret --list-jobs

    # Explore what API commands return (discovery mode):
    python powerfab_connector.py --host localhost --user api_user --password secret \\
        --job "25509" --discover
"""

import xml.etree.ElementTree as ET
import requests
import re
import json
import yaml
import csv
import os
import sys
import argparse
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

# ─── Import the shared data model and classification engine ───
# If work_package_sorter.py is in the same directory:
try:
    from work_package_sorter import (
        DrawingData,
        classify_drawings,
        generate_summary_csv,
        generate_summary_json,
        split_pdf_by_work_packages,
        DEFAULT_RULES,
    )
except ImportError:
    print("WARNING: work_package_sorter.py not found in path.")
    print("         Place both files in the same directory.")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════
# POWERFAB XML API CLIENT
# ═════════════════════════════════════════════════════════════════

class PowerFabClient:
    """
    Low-level client for the Tekla PowerFab XML API.
    
    Supports both:
      - Direct connection (to the PowerFab MySQL database on the local network)
      - Remote connection (via PowerFab Remote Service / PowerFab GO)
    
    All communication is XML request/response. This client wraps the raw
    XML in Python-friendly methods and handles connection lifecycle.
    """
    
    # Namespace used in PowerFab responses
    RESPONSE_NS = "http://www.fabsuite.com/xml/fabsuite-xml-response-v0108.xsd"
    
    def __init__(self, host: str, port: int, username: str, password: str,
                 remote: bool = False, remote_url: str = None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.remote = remote
        self.remote_url = remote_url
        self.connected = False
        
        # For remote connections, we use HTTP POST to the Remote Service
        # For direct connections, we use the COM/DLL interface or HTTP
        # In Python, we'll always use HTTP POST for both modes
        if self.remote and self.remote_url:
            self.api_url = self.remote_url
        else:
            # Direct connection — PowerFab listens on the DB port
            # The API endpoint is typically the same host
            self.api_url = None  # Will use DLL-style connection
    
    def _build_request(self, *command_elements: str) -> str:
        """Build a FabSuiteXMLRequest document from command XML fragments."""
        commands = "\n  ".join(command_elements)
        return f"""<FabSuiteXMLRequest>
  {commands}
</FabSuiteXMLRequest>"""
    
    def _send_request(self, xml_request: str) -> ET.Element:
        """
        Send an XML request to PowerFab and return the parsed response.
        
        NOTE: The actual transport mechanism depends on your setup:
        
        Option A — HTTP POST (Remote Service / PowerFab GO):
            Sends to the Remote Service URL via HTTP POST.
            
        Option B — Direct DLL (Tekla.PowerFab.API.dll via pythonnet):
            Uses the .NET DLL directly. Requires pythonnet and the DLL.
            
        Option C — Interface Test Tool (for development/testing):
            You can paste these XML requests directly into the PowerFab
            Interface Test program to verify they work before automating.
        
        This implementation supports Option A (HTTP) and falls back to
        saving XML files for manual testing via the Interface Test Tool.
        """
        
        if self.api_url:
            # HTTP POST to Remote Service
            try:
                headers = {"Content-Type": "application/xml"}
                response = requests.post(self.api_url, data=xml_request, headers=headers)
                response.raise_for_status()
                return ET.fromstring(response.text)
            except requests.RequestException as e:
                log.error(f"HTTP request to PowerFab failed: {e}")
                raise
        else:
            # Direct connection — try pythonnet if available
            try:
                return self._send_via_dotnet(xml_request)
            except Exception:
                # Fallback: save request XML for manual execution
                log.warning("No HTTP endpoint and pythonnet not available.")
                log.warning("Saving XML request for manual execution via Interface Test Tool.")
                self._save_request_for_manual_test(xml_request)
                raise ConnectionError(
                    "Cannot connect to PowerFab directly from Python without "
                    "either a Remote Service URL or pythonnet + Tekla.PowerFab.API.dll. "
                    "See the saved XML files in ./powerfab_requests/ to test manually."
                )
    
    def _send_via_dotnet(self, xml_request: str) -> ET.Element:
        """
        Send request using the .NET Tekla.PowerFab.API.dll via pythonnet.
        
        Requires:
            pip install pythonnet
            Tekla.PowerFab.API.dll in the system path or current directory
        """
        import clr  # pythonnet
        
        # Add reference to the PowerFab API DLL
        clr.AddReference("Tekla.PowerFab.API")
        from TeklaPowerFab.TeklaPowerFabAPI import TeklaPowerFabAPI
        
        api = TeklaPowerFabAPI()
        response_xml = api.ExecuteRequest(xml_request)
        return ET.fromstring(response_xml)
    
    def _save_request_for_manual_test(self, xml_request: str):
        """Save XML request to a file for manual testing in PowerFab Interface Test."""
        os.makedirs("powerfab_requests", exist_ok=True)
        
        # Extract command name from the XML
        root = ET.fromstring(xml_request)
        cmd_name = "unknown"
        for child in root:
            cmd_name = child.tag
            break
        
        filepath = f"powerfab_requests/{cmd_name}.xml"
        with open(filepath, "w") as f:
            f.write(xml_request)
        log.info(f"  Saved request to {filepath}")
    
    def _find_element(self, root: ET.Element, tag: str) -> Optional[ET.Element]:
        """Find an element in response XML, handling namespace."""
        # Try with namespace
        el = root.find(f".//{{{self.RESPONSE_NS}}}{tag}")
        if el is not None:
            return el
        # Try without namespace
        el = root.find(f".//{tag}")
        return el
    
    def _find_all(self, root: ET.Element, tag: str) -> list:
        """Find all elements matching tag, handling namespace."""
        results = root.findall(f".//{{{self.RESPONSE_NS}}}{tag}")
        if not results:
            results = root.findall(f".//{tag}")
        return results
    
    def _get_text(self, element: ET.Element, tag: str, default: str = "") -> str:
        """Get text content of a child element."""
        if element is None:
            return default
        child = element.find(f"{{{self.RESPONSE_NS}}}{tag}")
        if child is None:
            child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return default
    
    def _is_successful(self, response: ET.Element, command: str) -> bool:
        """Check if a command response was successful."""
        cmd_el = self._find_element(response, command)
        if cmd_el is None:
            return False
        success = self._get_text(cmd_el, "Successful")
        return success == "1"
    
    # ─── CONNECTION ───
    
    def connect(self) -> bool:
        """Establish connection to PowerFab."""
        log.info(f"Connecting to PowerFab at {self.host}:{self.port}...")
        
        if self.remote:
            xml = self._build_request(f"""<ConnectRemote>
    <ServerName>{self.host}</ServerName>
    <ServerPort>{self.port}</ServerPort>
    <Username>{self.username}</Username>
    <Password>{self.password}</Password>
  </ConnectRemote>""")
        else:
            xml = self._build_request(f"""<Connect>
    <IPAddress>{self.host}</IPAddress>
    <PortNumber>{self.port}</PortNumber>
    <Username>{self.username}</Username>
    <Password>{self.password}</Password>
  </Connect>""")
        
        try:
            response = self._send_request(xml)
            cmd = "ConnectRemote" if self.remote else "Connect"
            if self._is_successful(response, cmd):
                self.connected = True
                log.info("  Connected successfully.")
                return True
            else:
                cmd_el = self._find_element(response, cmd)
                err = self._get_text(cmd_el, "ErrorMessage") if cmd_el else "Unknown error"
                log.error(f"  Connection failed: {err}")
                return False
        except ConnectionError:
            # Manual mode — generate all request XMLs
            self.connected = False
            return False
    
    def disconnect(self):
        """Close the PowerFab connection."""
        if not self.connected:
            return
        cmd = "CloseRemote" if self.remote else "Close"
        xml = self._build_request(f"<{cmd}/>")
        try:
            self._send_request(xml)
        except Exception:
            pass
        self.connected = False
        log.info("Disconnected from PowerFab.")
    
    def get_version(self) -> str:
        """Get the PowerFab API version (useful for testing connection)."""
        xml = self._build_request("<Version/>")
        response = self._send_request(xml)
        ver_el = self._find_element(response, "Version")
        major = self._get_text(ver_el, "MajorVersion")
        minor = self._get_text(ver_el, "MinorVersion")
        return f"{major}.{minor}"
    
    # ─── JOB / PROJECT QUERIES ───
    
    def get_production_control_jobs(self) -> list[dict]:
        """Retrieve list of all Production Control jobs."""
        xml = self._build_request("<GetProductionControlJobs/>")
        response = self._send_request(xml)
        
        jobs = []
        for job_el in self._find_all(response, "ProductionControlJob"):
            jobs.append({
                "id": self._get_text(job_el, "ProductionControlID"),
                "number": self._get_text(job_el, "JobNumber"),
                "description": self._get_text(job_el, "JobDescription"),
                "location": self._get_text(job_el, "JobLocation"),
                "group": self._get_text(job_el, "GroupName"),
            })
        return jobs
    
    def get_project_status(self, production_control_id: str,
                           include_cut_lists: bool = False,
                           include_assemblies: bool = True) -> dict:
        """Get project status summary for a job."""
        options = []
        if include_cut_lists:
            options.append("<IncludeCutLists>1</IncludeCutLists>")
        if include_assemblies:
            options.append("<IncludeAssemblies>1</IncludeAssemblies>")
        
        opts_xml = "\n    ".join(options)
        xml = self._build_request(f"""<GetProjectStatus>
    <ProductionControlID>{production_control_id}</ProductionControlID>
    {opts_xml}
  </GetProjectStatus>""")
        
        response = self._send_request(xml)
        status_el = self._find_element(response, "GetProjectStatus")
        
        result = {
            "job_number": self._get_text(status_el, "JobNumber"),
            "job_description": self._get_text(status_el, "JobDescription"),
        }
        
        # Parse assembly count
        asm_el = self._find_element(status_el, "Assemblies")
        if asm_el is not None:
            result["assembly_count"] = self._get_text(asm_el, "Quantity")
        
        # Parse drawing count
        dwg_el = self._find_element(status_el, "Drawings")
        if dwg_el is not None:
            result["drawing_total"] = self._get_text(dwg_el, "Total")
            result["drawing_approved"] = self._get_text(dwg_el, "TotalApproved")
        
        # Parse sequence/lot counts
        seq_el = self._find_element(status_el, "Sequences")
        if seq_el is not None:
            result["sequence_count"] = self._get_text(seq_el, "Total")
        
        lot_el = self._find_element(status_el, "Lots")
        if lot_el is not None:
            result["lot_count"] = self._get_text(lot_el, "Total")
        
        return result
    
    def get_assemblies(self, production_control_id: str) -> list[dict]:
        """
        Retrieve the full list of assemblies (advance bill) for a job.
        
        This is the KEY command — it returns per-assembly BOM data:
        ship mark, minor marks, section sizes, weights, grades, etc.
        
        Maps directly to our DrawingData model for classification.
        """
        xml = self._build_request(f"""<GetAssemblies>
    <ProductionControlID>{production_control_id}</ProductionControlID>
    <IncludeMinorMarks>1</IncludeMinorMarks>
    <IncludeDrawingInfo>1</IncludeDrawingInfo>
    <IncludeBoltInfo>1</IncludeBoltInfo>
    <IncludeWeldInfo>1</IncludeWeldInfo>
  </GetAssemblies>""")
        
        response = self._send_request(xml)
        
        assemblies = []
        for asm_el in self._find_all(response, "Assembly"):
            assembly = {
                "assembly_id": self._get_text(asm_el, "AssemblyID"),
                "ship_mark": self._get_text(asm_el, "ShipMark"),
                "piecemark": self._get_text(asm_el, "Piecemark"),
                "description": self._get_text(asm_el, "Description"),
                "quantity": self._get_text(asm_el, "Quantity", "1"),
                "shape": self._get_text(asm_el, "Shape"),
                "grade": self._get_text(asm_el, "Grade"),
                "weight": self._get_text(asm_el, "Weight"),
                "length": self._get_text(asm_el, "Length"),
                "width": self._get_text(asm_el, "Width"),
                "depth": self._get_text(asm_el, "Depth"),
                "dimensions": self._get_text(asm_el, "Dimensions"),
                "sequence": self._get_text(asm_el, "Sequence"),
                "lot": self._get_text(asm_el, "Lot"),
                "status": self._get_text(asm_el, "Status"),
                "drawing_number": self._get_text(asm_el, "DrawingNumber"),
                "erection_drawing": self._get_text(asm_el, "ErectionDrawing"),
                "galvanize": self._get_text(asm_el, "Galvanize"),
                "minor_marks": [],
                "weld_info": [],
            }
            
            # Parse minor marks (attached parts)
            for mm_el in self._find_all(asm_el, "MinorMark"):
                minor = {
                    "mark": self._get_text(mm_el, "Mark"),
                    "description": self._get_text(mm_el, "Description"),
                    "shape": self._get_text(mm_el, "Shape"),
                    "grade": self._get_text(mm_el, "Grade"),
                    "dimensions": self._get_text(mm_el, "Dimensions"),
                    "quantity": self._get_text(mm_el, "Quantity", "1"),
                    "weight": self._get_text(mm_el, "Weight"),
                    "length": self._get_text(mm_el, "Length"),
                }
                assembly["minor_marks"].append(minor)
            
            # Parse weld info
            for weld_el in self._find_all(asm_el, "Weld"):
                weld = {
                    "type": self._get_text(weld_el, "WeldType"),
                    "size": self._get_text(weld_el, "WeldSize"),
                    "process": self._get_text(weld_el, "WeldProcess"),
                }
                assembly["weld_info"].append(weld)
            
            assemblies.append(assembly)
        
        return assemblies
    
    def get_drawings(self, production_control_id: str) -> list[dict]:
        """Retrieve drawing list for a job."""
        xml = self._build_request(f"""<DrawingGet>
    <ProductionControlID>{production_control_id}</ProductionControlID>
    <GetOptions>
      <SortOrder>DrawingNumber</SortOrder>
    </GetOptions>
  </DrawingGet>""")
        
        response = self._send_request(xml)
        
        drawings = []
        for dwg_el in self._find_all(response, "Drawing"):
            drawings.append({
                "drawing_id": self._get_text(dwg_el, "DrawingID"),
                "drawing_number": self._get_text(dwg_el, "DrawingNumber"),
                "description": self._get_text(dwg_el, "Description"),
                "status": self._get_text(dwg_el, "Status"),
                "revision": self._get_text(dwg_el, "CurrentRevision"),
                "sheet_count": self._get_text(dwg_el, "SheetCount"),
            })
        return drawings


# ═════════════════════════════════════════════════════════════════
# DATA TRANSFORMATION — PowerFab Assembly → DrawingData
# ═════════════════════════════════════════════════════════════════

def powerfab_assembly_to_drawing_data(assembly: dict, index: int) -> DrawingData:
    """
    Convert a PowerFab assembly record into our DrawingData model
    so the classification engine can process it identically to PDF-parsed data.
    """
    
    d = DrawingData(page_number=index + 1)
    
    # ── Mark / sheet number
    d.sheet_no = assembly.get("piecemark") or assembly.get("ship_mark", "")
    d.ship_mark = assembly.get("ship_mark", d.sheet_no)
    
    # ── Mark prefix (e.g. "B" from "B1007", "BP" from "BP1000")
    prefix_m = re.match(r'^([A-Z]+)', d.sheet_no)
    d.mark_prefix = prefix_m.group(1) if prefix_m else ""
    
    # ── Description / details type
    desc = assembly.get("description", "").upper()
    if "COLUMN" in desc:
        d.details_of = "COLUMN"
    elif "BEAM" in desc:
        d.details_of = "BEAM"
    elif "BRACE" in desc or "VERT" in desc:
        d.details_of = "VERTICAL BRACE"
    elif "ANGLE" in desc:
        d.details_of = "ANGLE"
    elif "BENT" in desc:
        d.details_of = "BENT PLATE"
    elif "PLATE" in desc:
        d.details_of = "PLATE"
    elif "POST" in desc:
        d.details_of = "POST"
    else:
        # Infer from mark prefix
        prefix_map = {
            "B": "BEAM", "C": "COLUMN", "A": "ANGLE",
            "BP": "BENT PLATE", "BR": "VERTICAL BRACE",
            "VB": "VERTICAL BRACE", "M": "POST", "PL": "PLATE",
        }
        d.details_of = prefix_map.get(d.mark_prefix, desc)
    
    # ── Main section size
    dimensions = assembly.get("dimensions", "")
    shape = assembly.get("shape", "")
    
    # Reconstruct section string: shape + dimensions → "W27x84", "HSS4x4x5/16"
    if shape and dimensions:
        # PowerFab returns shape like "W" and dimensions like "27 x 84"
        dim_clean = dimensions.replace(" ", "").replace("X", "x")
        d.main_section = f"{shape}{dim_clean}"
    elif dimensions:
        d.main_section = dimensions.replace(" ", "")
    
    # Parse section depth
    w_m = re.match(r'W(\d+)[xX](\d+)', d.main_section)
    if w_m:
        d.section_depth = float(w_m.group(1))
        d.section_weight_per_ft = float(w_m.group(2))
    
    # ── Weight
    try:
        d.total_weight = int(float(assembly.get("weight", 0)))
    except (ValueError, TypeError):
        d.total_weight = 0
    
    # ── Grade
    d.grade = assembly.get("grade", "")
    
    # ── Erection drawing reference
    d.erection_dwg_ref = assembly.get("erection_drawing", "")
    
    # ── Galvanize
    galv = assembly.get("galvanize", "").upper()
    d.galvanize = galv in ("1", "YES", "TRUE", "Y")
    
    # ── Fabrication status
    status = assembly.get("status", "").upper()
    if "HOLD" in status:
        d.fab_status = "HOLD"
    elif status in ("", "RELEASED", "APPROVED", "FOR FABRICATION"):
        d.fab_status = "FOR FABRICATION"
    else:
        d.fab_status = status or "FOR FABRICATION"
    
    # ── Minor marks (attached parts)
    minor_marks = assembly.get("minor_marks", [])
    d.num_minor_marks = len(minor_marks)
    
    part_types = set()
    for mm in minor_marks:
        mm_shape = (mm.get("shape") or "").upper()
        mm_desc = (mm.get("description") or "").upper()
        mm_dims = (mm.get("dimensions") or "").upper()
        
        # Classify minor mark type
        if "BPL" in mm_shape or "BPL" in mm_desc or "BENT" in mm_desc:
            part_types.add("BPL")
        elif "PL" in mm_shape or "PLATE" in mm_desc:
            part_types.add("PL")
        elif "FB" in mm_shape or "FLAT" in mm_desc:
            part_types.add("FB")
        elif mm_shape.startswith("L") or "ANGLE" in mm_desc:
            part_types.add("L")
        elif mm_shape.startswith("C") or "CHANNEL" in mm_desc:
            part_types.add("C")
        elif "HSS" in mm_shape or "TS" in mm_shape or "TUBE" in mm_desc:
            part_types.add("HSS")
        elif "WT" in mm_shape:
            part_types.add("WT")
    
    d.minor_mark_types = sorted(part_types)
    d.has_plates = "PL" in part_types
    d.has_bent_plates = "BPL" in part_types
    d.has_angles_attached = "L" in part_types
    d.has_channels = "C" in part_types
    d.has_flat_bars = "FB" in part_types
    d.has_tubes_hss = "HSS" in part_types
    
    # ── Weld types (from weld info if available)
    weld_info = assembly.get("weld_info", [])
    for w in weld_info:
        wtype = (w.get("type") or "").upper()
        if "CJP" in wtype or "COMPLETE" in wtype or "DCW" in wtype or "DEMAND" in wtype:
            d.has_cjp_dcw = True
        elif "PJP" in wtype or "PARTIAL" in wtype:
            d.has_pjp = True
    
    # Also check description for weld keywords if no explicit weld info
    if not d.has_cjp_dcw and not d.has_pjp:
        full_text = f"{desc} {assembly.get('description', '')}"
        if re.search(r'CJP|DCW|DEMAND\s*CRITICAL', full_text, re.IGNORECASE):
            d.has_cjp_dcw = True
    
    return d


def fetch_and_classify(client: PowerFabClient, job_number: str,
                       config: dict = None) -> tuple[list[DrawingData], dict]:
    """
    Complete workflow: fetch assemblies from PowerFab, convert to
    DrawingData, and classify into work packages.
    
    Returns: (classified_drawings, job_info)
    """
    if config is None:
        config = DEFAULT_RULES
    
    # Find the job
    log.info(f"Looking up job '{job_number}'...")
    jobs = client.get_production_control_jobs()
    
    matching = [j for j in jobs if j["number"] == job_number]
    if not matching:
        # Try partial match
        matching = [j for j in jobs if job_number.lower() in j["number"].lower()]
    
    if not matching:
        log.error(f"Job '{job_number}' not found. Available jobs:")
        for j in jobs[:20]:
            log.error(f"  {j['number']}: {j['description']}")
        return [], {}
    
    job = matching[0]
    pc_id = job["id"]
    log.info(f"  Found: {job['number']} — {job['description']} (ID: {pc_id})")
    
    # Get project status
    status = client.get_project_status(pc_id, include_assemblies=True)
    log.info(f"  Assemblies: {status.get('assembly_count', '?')}, "
             f"Drawings: {status.get('drawing_total', '?')}")
    
    # Get all assemblies with BOM detail
    log.info("Fetching assembly data with minor marks...")
    assemblies = client.get_assemblies(pc_id)
    log.info(f"  Retrieved {len(assemblies)} assemblies")
    
    # Convert to DrawingData
    drawings = []
    for i, asm in enumerate(assemblies):
        d = powerfab_assembly_to_drawing_data(asm, i)
        drawings.append(d)
    
    # Classify
    classified = classify_drawings(drawings, config)
    
    return classified, {**job, **status}


# ═════════════════════════════════════════════════════════════════
# WRITE-BACK — Assign Sequences/Lots in PowerFab
# ═════════════════════════════════════════════════════════════════

def generate_sequence_update_xml(classified: list[DrawingData],
                                 production_control_id: str) -> str:
    """
    Generate the XML request(s) to update sequence assignments
    in PowerFab Production Control based on work package classifications.
    
    NOTE: The exact command depends on your PowerFab version and setup.
    Common approaches:
      1. Update the Sequence field on each assembly
      2. Create Lots and assign assemblies to lots
      3. Use custom fields / user-defined fields
    
    This generates the XML — you should review it before sending.
    """
    
    # Group by work package
    wp_groups = {}
    for d in classified:
        wp = d.work_package or "WP-99 Unclassified"
        wp_groups.setdefault(wp, []).append(d)
    
    # Build sequence update commands
    # This uses a pattern where each WP becomes a sequence name
    commands = []
    
    for wp_name, drawings in wp_groups.items():
        # Clean WP name for use as sequence identifier
        seq_name = wp_name.replace(" ", "_")[:30]
        
        for d in drawings:
            # This is a template — the exact XML depends on the API version
            # and what fields are available for your Production Control setup
            commands.append(f"""  <!-- {d.sheet_no} → {wp_name} -->
  <!--
  <AssemblyUpdate>
    <ProductionControlID>{production_control_id}</ProductionControlID>
    <Piecemark>{d.sheet_no}</Piecemark>
    <Sequence>{seq_name}</Sequence>
  </AssemblyUpdate>
  -->""")
    
    header = f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
  WORK PACKAGE SEQUENCE ASSIGNMENTS
  Generated by Work Package Sorter
  
  Production Control ID: {production_control_id}
  Total assemblies: {len(classified)}
  Work packages: {len(wp_groups)}
  
  REVIEW THIS FILE before sending to PowerFab.
  Uncomment the AssemblyUpdate commands when ready to apply.
  
  Work Package Summary:
"""
    for wp_name, drawings in sorted(wp_groups.items()):
        marks = ", ".join(d.sheet_no for d in drawings[:5])
        suffix = f"... +{len(drawings)-5} more" if len(drawings) > 5 else ""
        header += f"    {wp_name}: {len(drawings)} assemblies ({marks} {suffix})\n"
    
    header += """-->
<FabSuiteXMLRequest>
"""
    footer = "\n</FabSuiteXMLRequest>"
    
    return header + "\n".join(commands) + footer


# ═════════════════════════════════════════════════════════════════
# OFFLINE / MANUAL MODE — Generate Request XMLs for Testing
# ═════════════════════════════════════════════════════════════════

def generate_test_requests(host: str, port: int, username: str, password: str,
                           job_number: str, output_dir: str):
    """
    Generate all the XML request files needed to test the integration
    manually using the PowerFab Interface Test Tool.
    
    Use this when you can't connect directly from Python — paste each
    XML file into the Interface Test program to verify the responses.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    requests_list = [
        ("01_connect.xml", f"""<FabSuiteXMLRequest>
  <Connect>
    <IPAddress>{host}</IPAddress>
    <PortNumber>{port}</PortNumber>
    <Username>{username}</Username>
    <Password>{password}</Password>
  </Connect>
</FabSuiteXMLRequest>"""),
        
        ("02_version.xml", """<FabSuiteXMLRequest>
  <Version/>
</FabSuiteXMLRequest>"""),
        
        ("03_get_jobs.xml", """<FabSuiteXMLRequest>
  <GetProductionControlJobs/>
</FabSuiteXMLRequest>"""),
        
        ("04_get_project_status.xml", f"""<FabSuiteXMLRequest>
  <!-- Replace PRODUCTION_CONTROL_ID with the actual ID from step 03 -->
  <GetProjectStatus>
    <ProductionControlID>PRODUCTION_CONTROL_ID</ProductionControlID>
    <IncludeAssemblies>1</IncludeAssemblies>
    <IncludeCutLists>1</IncludeCutLists>
  </GetProjectStatus>
</FabSuiteXMLRequest>"""),
        
        ("05_get_assemblies.xml", f"""<FabSuiteXMLRequest>
  <!-- Replace PRODUCTION_CONTROL_ID with the actual ID from step 03 -->
  <GetAssemblies>
    <ProductionControlID>PRODUCTION_CONTROL_ID</ProductionControlID>
    <IncludeMinorMarks>1</IncludeMinorMarks>
    <IncludeDrawingInfo>1</IncludeDrawingInfo>
    <IncludeBoltInfo>1</IncludeBoltInfo>
    <IncludeWeldInfo>1</IncludeWeldInfo>
  </GetAssemblies>
</FabSuiteXMLRequest>"""),
        
        ("06_get_drawings.xml", f"""<FabSuiteXMLRequest>
  <!-- Replace PRODUCTION_CONTROL_ID with the actual ID from step 03 -->
  <DrawingGet>
    <ProductionControlID>PRODUCTION_CONTROL_ID</ProductionControlID>
    <GetOptions>
      <SortOrder>DrawingNumber</SortOrder>
    </GetOptions>
  </DrawingGet>
</FabSuiteXMLRequest>"""),
    ]
    
    log.info(f"Generating {len(requests_list)} test request files in {output_dir}/")
    for filename, content in requests_list:
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w") as f:
            f.write(content)
        log.info(f"  {filename}")
    
    # Also generate a README
    readme = f"""POWERFAB API TEST REQUESTS
=========================

These XML files are for testing the PowerFab API integration using
the PowerFab Interface Test Tool:

  C:\\Program Files (x86)\\Tekla\\Tekla PowerFab\\Tekla.PowerFab.Interface.Test.exe

STEP-BY-STEP:

  1. Open the Interface Test Tool
  2. Paste the contents of 01_connect.xml and click "Submit Request"
     → You should see <Successful>1</Successful>
  
  3. Paste 02_version.xml → Confirms API version
  
  4. Paste 03_get_jobs.xml → Returns list of Production Control jobs
     → Find your job and note the <ProductionControlID> value
  
  5. Edit 04_get_project_status.xml:
     Replace PRODUCTION_CONTROL_ID with the actual ID from step 4
     Paste and submit → Shows assembly count, drawing count, sequences
  
  6. Edit 05_get_assemblies.xml with the same ID:
     Paste and submit → This is the BIG one. Returns every assembly
     with ship marks, minor marks, sections, grades, weights.
     
     SAVE THE RESPONSE to a file (e.g., assemblies_response.xml)
     
     Then run:
       python powerfab_connector.py --parse-response assemblies_response.xml \\
           --config rules.yaml --output-dir ./output

  7. (Optional) 06_get_drawings.xml → Drawing management info

CONNECTION INFO USED:
  Host: {host}
  Port: {port}
  Username: {username}
  Job: {job_number}
"""
    
    with open(os.path.join(output_dir, "README.txt"), "w") as f:
        f.write(readme)
    log.info(f"  README.txt (step-by-step instructions)")


# ═════════════════════════════════════════════════════════════════
# PARSE SAVED RESPONSE (for offline workflow)
# ═════════════════════════════════════════════════════════════════

def parse_assemblies_response(response_file: str, config: dict = None) -> list[DrawingData]:
    """
    Parse a saved GetAssemblies XML response file (from the Interface Test Tool)
    and run classification against it.
    
    This is the offline workflow:
    1. Use Interface Test Tool to run GetAssemblies
    2. Save the XML response to a file
    3. Run this function to classify assemblies
    """
    if config is None:
        config = DEFAULT_RULES
    
    log.info(f"Parsing saved response: {response_file}")
    tree = ET.parse(response_file)
    root = tree.getroot()
    
    # The PowerFab response namespace
    ns = {"fs": "http://www.fabsuite.com/xml/fabsuite-xml-response-v0108.xsd"}
    
    assemblies = []
    
    # Try with namespace first, then without
    asm_elements = root.findall(".//fs:Assembly", ns)
    if not asm_elements:
        asm_elements = root.findall(".//Assembly")
    
    def get_text(el, tag):
        if el is None:
            return ""
        child = el.find(f"fs:{tag}", ns)
        if child is None:
            child = el.find(tag)
        return (child.text or "").strip() if child is not None else ""
    
    for asm_el in asm_elements:
        assembly = {
            "assembly_id": get_text(asm_el, "AssemblyID"),
            "ship_mark": get_text(asm_el, "ShipMark"),
            "piecemark": get_text(asm_el, "Piecemark"),
            "description": get_text(asm_el, "Description"),
            "quantity": get_text(asm_el, "Quantity") or "1",
            "shape": get_text(asm_el, "Shape"),
            "grade": get_text(asm_el, "Grade"),
            "weight": get_text(asm_el, "Weight"),
            "dimensions": get_text(asm_el, "Dimensions"),
            "sequence": get_text(asm_el, "Sequence"),
            "lot": get_text(asm_el, "Lot"),
            "status": get_text(asm_el, "Status"),
            "drawing_number": get_text(asm_el, "DrawingNumber"),
            "erection_drawing": get_text(asm_el, "ErectionDrawing"),
            "galvanize": get_text(asm_el, "Galvanize"),
            "minor_marks": [],
            "weld_info": [],
        }
        
        # Parse minor marks
        mm_elements = asm_el.findall(f".//fs:MinorMark", ns)
        if not mm_elements:
            mm_elements = asm_el.findall(".//MinorMark")
        
        for mm_el in mm_elements:
            assembly["minor_marks"].append({
                "mark": get_text(mm_el, "Mark"),
                "description": get_text(mm_el, "Description"),
                "shape": get_text(mm_el, "Shape"),
                "grade": get_text(mm_el, "Grade"),
                "dimensions": get_text(mm_el, "Dimensions"),
                "quantity": get_text(mm_el, "Quantity") or "1",
                "weight": get_text(mm_el, "Weight"),
            })
        
        # Parse weld info
        weld_elements = asm_el.findall(f".//fs:Weld", ns)
        if not weld_elements:
            weld_elements = asm_el.findall(".//Weld")
        
        for weld_el in weld_elements:
            assembly["weld_info"].append({
                "type": get_text(weld_el, "WeldType"),
                "size": get_text(weld_el, "WeldSize"),
            })
        
        assemblies.append(assembly)
    
    log.info(f"  Parsed {len(assemblies)} assemblies from response")
    
    # Convert to DrawingData and classify
    drawings = [powerfab_assembly_to_drawing_data(a, i) for i, a in enumerate(assemblies)]
    classified = classify_drawings(drawings, config)
    
    return classified


# ═════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Tekla PowerFab Integration — Work Package Sorter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES:

  # Generate test XML requests for the Interface Test Tool:
  python powerfab_connector.py --host 192.168.1.100 --user api_user --password secret \\
      --job 25509 --generate-test-requests

  # Parse a saved GetAssemblies response (offline workflow):
  python powerfab_connector.py --parse-response assemblies_response.xml \\
      --config rules.yaml --output-dir ./output

  # Connect live and classify (requires network access to PowerFab):
  python powerfab_connector.py --host 192.168.1.100 --user api_user --password secret \\
      --job 25509 --config rules.yaml --output-dir ./output

  # List available jobs:
  python powerfab_connector.py --host 192.168.1.100 --user api_user --password secret \\
      --list-jobs
""")
    
    # Connection settings
    conn = parser.add_argument_group("Connection")
    conn.add_argument("--host", default="localhost", help="PowerFab server IP/hostname")
    conn.add_argument("--port", type=int, default=3306, help="PowerFab database port")
    conn.add_argument("--user", "--username", default="admin", help="API username (External User)")
    conn.add_argument("--password", default="", help="API password")
    conn.add_argument("--remote", action="store_true", help="Use Remote Service connection")
    conn.add_argument("--remote-url", help="Remote Service URL (for PowerFab GO)")
    
    # Job selection
    job = parser.add_argument_group("Job")
    job.add_argument("--job", help="Job number to process")
    job.add_argument("--list-jobs", action="store_true", help="List available Production Control jobs")
    
    # Configuration
    cfg = parser.add_argument_group("Configuration")
    cfg.add_argument("--config", help="Path to YAML work package rules file")
    cfg.add_argument("--output-dir", default="./output", help="Output directory")
    
    # Modes
    modes = parser.add_argument_group("Modes")
    modes.add_argument("--generate-test-requests", action="store_true",
                       help="Generate XML request files for the Interface Test Tool")
    modes.add_argument("--parse-response", metavar="FILE",
                       help="Parse a saved GetAssemblies XML response file")
    modes.add_argument("--discover", action="store_true",
                       help="Run discovery queries and dump raw API responses")
    modes.add_argument("--write-sequences", action="store_true",
                       help="Generate sequence update XML (review before applying)")
    
    args = parser.parse_args()
    
    # Load config
    config = DEFAULT_RULES
    if args.config:
        with open(args.config) as f:
            config = yaml.safe_load(f)
    
    os.makedirs(args.output_dir, exist_ok=True)
    
    # ── Mode: Generate test request XMLs ──
    if args.generate_test_requests:
        generate_test_requests(
            args.host, args.port, args.user, args.password,
            args.job or "YOUR_JOB_NUMBER",
            os.path.join(args.output_dir, "powerfab_test_requests")
        )
        return
    
    # ── Mode: Parse saved response ──
    if args.parse_response:
        classified = parse_assemblies_response(args.parse_response, config)
        
        # Print summary
        wp_counts = {}
        for d in classified:
            wp_counts[d.work_package] = wp_counts.get(d.work_package, 0) + 1
        
        print(f"\nWork Package Assignments ({len(classified)} assemblies):")
        for wp, count in sorted(wp_counts.items()):
            print(f"  {wp}: {count}")
        
        # Generate CSV
        csv_path = os.path.join(args.output_dir, "assembly_summary.csv")
        generate_summary_csv(classified, csv_path)
        print(f"\nSummary CSV: {csv_path}")
        
        # Generate sequence update XML if requested
        if args.write_sequences:
            seq_path = os.path.join(args.output_dir, "sequence_updates.xml")
            xml = generate_sequence_update_xml(classified, "PRODUCTION_CONTROL_ID")
            with open(seq_path, "w") as f:
                f.write(xml)
            print(f"Sequence update XML: {seq_path}")
        
        return
    
    # ── Mode: Live connection ──
    client = PowerFabClient(
        host=args.host, port=args.port,
        username=args.user, password=args.password,
        remote=args.remote, remote_url=args.remote_url,
    )
    
    if not client.connect():
        log.error("Could not connect to PowerFab.")
        log.info("Try --generate-test-requests for offline testing.")
        return
    
    try:
        # List jobs
        if args.list_jobs:
            jobs = client.get_production_control_jobs()
            print(f"\nProduction Control Jobs ({len(jobs)}):")
            print(f"{'ID':>8}  {'Job Number':<20}  {'Description'}")
            print(f"{'─'*8}  {'─'*20}  {'─'*40}")
            for j in jobs:
                print(f"{j['id']:>8}  {j['number']:<20}  {j['description']}")
            return
        
        if not args.job:
            log.error("No job specified. Use --job JOB_NUMBER or --list-jobs.")
            return
        
        # Discover mode — dump raw data
        if args.discover:
            jobs = client.get_production_control_jobs()
            matching = [j for j in jobs if args.job in j["number"]]
            if matching:
                pc_id = matching[0]["id"]
                status = client.get_project_status(pc_id, include_assemblies=True,
                                                    include_cut_lists=True)
                print(json.dumps(status, indent=2))
                
                assemblies = client.get_assemblies(pc_id)
                print(f"\nFirst 3 assemblies (of {len(assemblies)}):")
                for a in assemblies[:3]:
                    print(json.dumps(a, indent=2))
            return
        
        # Full workflow
        classified, job_info = fetch_and_classify(client, args.job, config)
        
        if not classified:
            return
        
        # Print summary
        wp_counts = {}
        for d in classified:
            wp_counts[d.work_package] = wp_counts.get(d.work_package, 0) + 1
        
        print(f"\nJob: {job_info.get('number', '?')} — {job_info.get('description', '?')}")
        print(f"\nWork Package Assignments ({len(classified)} assemblies):")
        for wp, count in sorted(wp_counts.items()):
            print(f"  {wp}: {count}")
        
        # Generate outputs
        csv_path = os.path.join(args.output_dir, "assembly_summary.csv")
        generate_summary_csv(classified, csv_path)
        print(f"\nSummary CSV: {csv_path}")
        
        json_path = os.path.join(args.output_dir, "assembly_summary.json")
        summary = generate_summary_json(classified, config)
        with open(json_path, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"Summary JSON: {json_path}")
        
        # Generate sequence update XML
        if args.write_sequences:
            pc_id = job_info.get("id", "UNKNOWN")
            seq_path = os.path.join(args.output_dir, "sequence_updates.xml")
            xml = generate_sequence_update_xml(classified, pc_id)
            with open(seq_path, "w") as f:
                f.write(xml)
            print(f"Sequence update XML: {seq_path} (REVIEW BEFORE APPLYING)")
        
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
