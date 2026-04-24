#!/usr/bin/env python3
"""
Deeper PFXT weight hunt — dumps every field in the first 3 assemblies
and searches for any numeric value that could plausibly be a weight.
Run: python find_weight2.py "path\to\file.pfxt"
"""
import zipfile, sys, xml.etree.ElementTree as ET, re

path = sys.argv[1]
with zipfile.ZipFile(path, 'r') as zf:
    xml_name = next(n for n in zf.namelist() if n.endswith('.xml') and '/' not in n)
    content = zf.read(xml_name)

root = ET.fromstring(content)
ns_match = re.match(r'\{([^}]+)\}', root.tag)
ns = {"fs": ns_match.group(1)} if ns_match else {}
ns_prefix = f"{{{ns_match.group(1)}}}" if ns_match else ""

assemblies = root.findall(f".//{ns_prefix}Assembly") or root.findall(".//Assembly")
print(f"Total assemblies: {len(assemblies)}\n")

# ── Dump EVERY tag+text in the first 3 assemblies
for asm in assemblies[:3]:
    mark = ""
    for el in asm.iter():
        tag = re.sub(r'\{[^}]+\}', '', el.tag)
        if tag == "AssemblyMark" and el.text:
            mark = el.text.strip()
            break
    print(f"{'='*60}")
    print(f"Assembly: {mark}")
    print(f"{'='*60}")
    for el in asm.iter():
        tag = re.sub(r'\{[^}]+\}', '', el.tag)
        text = (el.text or '').strip()
        attribs = dict(el.attrib)
        if text or attribs:
            print(f"  <{tag}> text={repr(text)}  attribs={attribs}")

# ── Also dump ALL unique tag names in the whole file
print(f"\n\n{'='*60}")
print("ALL UNIQUE TAG NAMES IN FILE:")
print(f"{'='*60}")
all_tags = sorted(set(re.sub(r'\{[^}]+\}', '', el.tag) for el in root.iter()))
for t in all_tags:
    print(f"  {t}")