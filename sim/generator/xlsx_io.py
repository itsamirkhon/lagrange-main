"""Deterministic pure-Python OpenXML (.xlsx) writer and reader."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from sim.generator.domain import SheetSpec

_FIXED_ZIP_DT = (2026, 1, 1, 0, 0, 0)
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_CONTENT_TYPES_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    '<Override PartName="/xl/sharedStrings.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
)

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="xl/workbook.xml"/>'
    '</Relationships>'
)


def _col_letter(idx: int) -> str:
    """Convert 1-based column index to Excel column letters (1 -> A)."""
    chars: list[str] = []
    n = idx
    while n > 0:
        n, rem = divmod(n - 1, 26)
        chars.append(chr(65 + rem))
    return "".join(reversed(chars))


def _col_index(cell_ref: str) -> int:
    """Convert cell reference like 'AB12' to 0-based column index."""
    idx = 0
    for ch in cell_ref:
        if not ch.isalpha():
            break
        idx = idx * 26 + (ord(ch.upper()) - 64)
    return idx - 1


def _collect_sheet_rows(spec: SheetSpec) -> list[tuple[list[Any], int]]:
    items: list[tuple[list[Any], int]] = []
    for row in spec.pre_header_rows:
        items.append((list(row), 1))
    if spec.headers:
        items.append((list(spec.headers), 1))
    for row in spec.rows:
        items.append((list(row), 0))
    return items


def _register_strings(sheets: list[SheetSpec]) -> tuple[list[str], dict[str, int]]:
    strings: list[str] = []
    str_map: dict[str, int] = {}
    for spec in sheets:
        for row, _ in _collect_sheet_rows(spec):
            for val in row:
                if isinstance(val, str) and val not in str_map:
                    str_map[val] = len(strings)
                    strings.append(val)
    return strings, str_map


def _build_shared_strings_xml(strings: list[str]) -> str:
    items = "".join(f"<si><t xml:space=\"preserve\">{escape(s)}</t></si>" for s in strings)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="{_NS_MAIN}" count="{len(strings)}" uniqueCount="{len(strings)}">'
        f"{items}</sst>"
    )


def _build_styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<styleSheet xmlns="{_NS_MAIN}">'
        '<fonts count="2">'
        '<font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font>'
        "</fonts>"
        '<fills count="2">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE8EEF5"/></patternFill></fill>'
        "</fills>"
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="1" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
        "</cellXfs>"
        "</styleSheet>"
    )


def _build_cols_xml(col_widths: list[float] | None) -> str:
    if not col_widths:
        return ""
    cols = "".join(
        f'<col min="{i}" max="{i}" width="{w:.2f}" customWidth="1"/>'
        for i, w in enumerate(col_widths, start=1)
    )
    return f"<cols>{cols}</cols>"


def _build_cell_xml(ref: str, val: Any, style_idx: int, str_map: dict[str, int]) -> str:
    if val is None:
        return f'<c r="{ref}" s="{style_idx}"/>'
    if isinstance(val, bool):
        return f'<c r="{ref}" t="b" s="{style_idx}"><v>{1 if val else 0}</v></c>'
    if isinstance(val, (int, float)):
        return f'<c r="{ref}" s="{style_idx}"><v>{val}</v></c>'
    sid = str_map[str(val)]
    return f'<c r="{ref}" t="s" s="{style_idx}"><v>{sid}</v></c>'


def _build_sheet_xml(spec: SheetSpec, str_map: dict[str, int]) -> str:
    rows_xml: list[str] = []
    for r_idx, (row_vals, style_idx) in enumerate(_collect_sheet_rows(spec), start=1):
        cells = "".join(
            _build_cell_xml(f"{_col_letter(c_idx)}{r_idx}", val, style_idx, str_map)
            for c_idx, val in enumerate(row_vals, start=1)
        )
        rows_xml.append(f'<row r="{r_idx}">{cells}</row>')
    merges_xml = ""
    if spec.merged_cells:
        tags = "".join(f'<mergeCell ref="{escape(ref)}"/>' for ref in spec.merged_cells)
        merges_xml = f'<mergeCells count="{len(spec.merged_cells)}">{tags}</mergeCells>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{_NS_MAIN}">'
        f"{_build_cols_xml(spec.col_widths)}"
        f"<sheetData>{''.join(rows_xml)}</sheetData>"
        f"{merges_xml}</worksheet>"
    )


def _build_workbook_xml(sheets: list[SheetSpec]) -> tuple[str, str, str]:
    sheet_tags = "".join(
        f'<sheet name="{escape(s.name[:31])}" sheetId="{i}" r:id="rId{i}"/>'
        for i, s in enumerate(sheets, start=1)
    )
    wb_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{_NS_MAIN}" xmlns:r="{_NS_REL}">'
        f"<sheets>{sheet_tags}</sheets></workbook>"
    )
    rels_tags = "".join(
        f'<Relationship Id="rId{i}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    offset = len(sheets) + 1
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels_tags}"
        f'<Relationship Id="rId{offset}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        f'<Relationship Id="rId{offset + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        'Target="sharedStrings.xml"/>'
        "</Relationships>"
    )
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    ct_xml = f"{_CONTENT_TYPES_HEAD}{overrides}</Types>"
    return wb_xml, rels_xml, ct_xml


def _write_zip_entry(zf: zipfile.ZipFile, arcname: str, text: str) -> None:
    info = zipfile.ZipInfo(filename=arcname, date_time=_FIXED_ZIP_DT)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, text.encode("utf-8"))


def write_xlsx(path: Path | str, sheets: list[SheetSpec]) -> Path:
    """Write SheetSpec list deterministically to an OpenXML .xlsx file."""
    if not sheets:
        raise ValueError("sheets list cannot be empty")
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    strings, str_map = _register_strings(sheets)
    wb_xml, wb_rels_xml, ct_xml = _build_workbook_xml(sheets)

    with zipfile.ZipFile(out_path, "w") as zf:
        _write_zip_entry(zf, "[Content_Types].xml", ct_xml)
        _write_zip_entry(zf, "_rels/.rels", _ROOT_RELS)
        _write_zip_entry(zf, "xl/workbook.xml", wb_xml)
        _write_zip_entry(zf, "xl/_rels/workbook.xml.rels", wb_rels_xml)
        _write_zip_entry(zf, "xl/styles.xml", _build_styles_xml())
        _write_zip_entry(zf, "xl/sharedStrings.xml", _build_shared_strings_xml(strings))
        for i, spec in enumerate(sheets, start=1):
            _write_zip_entry(zf, f"xl/worksheets/sheet{i}.xml", _build_sheet_xml(spec, str_map))
    return out_path


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    ns = f"{{{_NS_MAIN}}}"
    return ["".join(t.text or "" for t in si.findall(f".//{ns}t")) for si in root.findall(f"{ns}si")]


def _convert_numeric(raw: str) -> int | float:
    val = float(raw)
    return int(val) if val.is_integer() and "." not in raw else val


def _read_inline_str(cell: ET.Element, ns: str) -> str:
    t_node = cell.find(f"{ns}is/{ns}t")
    return t_node.text if t_node is not None and t_node.text is not None else ""


def _parse_cell_value(cell: ET.Element, shared: list[str], ns: str) -> Any:
    ctype = cell.attrib.get("t", "")
    if ctype == "inlineStr":
        return _read_inline_str(cell, ns)
    v_node = cell.find(f"{ns}v")
    if v_node is None or v_node.text is None:
        return None
    if ctype == "s":
        return shared[int(v_node.text)]
    if ctype == "b":
        return v_node.text == "1"
    if ctype == "str":
        return v_node.text
    return _convert_numeric(v_node.text)


def _parse_row_cells(row_el: ET.Element, shared: list[str], ns: str) -> list[Any]:
    cells = row_el.findall(f"{ns}c")
    if not cells:
        return []
    max_col = max(_col_index(c.attrib["r"]) for c in cells)
    row_vals: list[Any] = [None] * (max_col + 1)
    for c in cells:
        row_vals[_col_index(c.attrib["r"])] = _parse_cell_value(c, shared, ns)
    return row_vals


def _parse_sheet_xml(xml_bytes: bytes, shared: list[str]) -> dict[str, Any]:
    root = ET.fromstring(xml_bytes)
    ns = f"{{{_NS_MAIN}}}"
    all_rows = [
        _parse_row_cells(row_el, shared, ns)
        for row_el in root.findall(f".//{ns}sheetData/{ns}row")
    ]
    merges = [m.attrib["ref"] for m in root.findall(f".//{ns}mergeCells/{ns}mergeCell")]
    headers = all_rows[0] if all_rows else []
    rows = all_rows[1:] if len(all_rows) > 1 else []
    return {"headers": headers, "rows": rows, "all_rows": all_rows, "merged_cells": merges}


def read_xlsx(path: Path | str) -> dict[str, dict[str, Any]]:
    """Read an OpenXML .xlsx workbook into a dict keyed by sheet name."""
    with zipfile.ZipFile(Path(path), "r") as zf:
        shared = _read_shared_strings(zf)
        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {r.attrib["Id"]: r.attrib["Target"] for r in rels_root}
        result: dict[str, dict[str, Any]] = {}
        for sheet_el in wb_root.findall(f".//{{{_NS_MAIN}}}sheet"):
            name = sheet_el.attrib["name"]
            rid = sheet_el.attrib[f"{{{_NS_REL}}}id"]
            target = rel_map[rid].lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            result[name] = _parse_sheet_xml(zf.read(target), shared)
        return result
