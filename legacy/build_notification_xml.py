from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable
from lxml import etree as ET
from zipfile import ZIP_DEFLATED, ZipFile


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


@dataclass
class ChildRow:
    name: str
    birth_date: str
    parent_name: str
    parent_phone: str


@dataclass
class CompanionRow:
    name: str
    role: str
    phone: str


def age_on_trip(birth_date: str, trip_date: date) -> str:
    born = datetime.strptime(birth_date, "%d.%m.%Y").date()
    years = trip_date.year - born.year - ((trip_date.month, trip_date.day) < (born.month, born.day))
    return str(years)


def iter_text_nodes(root: ET._Element) -> Iterable[ET._Element]:
    yield from root.findall(".//w:t", NS)


def replace_placeholders(root: ET._Element, replacements: dict[str, str]) -> None:
    for node in iter_text_nodes(root):
        if not node.text:
            continue
        text = node.text
        for old, new in replacements.items():
            text = text.replace(old, new)
        node.text = text


def clone_run_properties(run: ET._Element) -> ET._Element | None:
    run_properties = run.find("./w:rPr", NS)
    if run_properties is None:
        return None
    return deepcopy(run_properties)


def set_cell_text(cell: ET._Element, value: str) -> None:
    text_nodes = cell.findall(".//w:t", NS)
    if text_nodes:
        text_nodes[0].text = value
        for node in text_nodes[1:]:
            node.text = ""
        return
    paragraph = cell.find("./w:p", NS)
    if paragraph is None:
        paragraph = ET.SubElement(cell, f"{{{NS['w']}}}p")
    run = ET.SubElement(paragraph, f"{{{NS['w']}}}r")
    text = ET.SubElement(run, f"{{{NS['w']}}}t")
    text.text = value


def rows_for_table(table: ET._Element) -> list[ET._Element]:
    return table.findall("./w:tr", NS)


def cells_for_row(row: ET._Element) -> list[ET._Element]:
    return row.findall("./w:tc", NS)


def find_table_by_placeholder(root: ET._Element, placeholder: str) -> ET._Element:
    for table in root.findall(".//w:tbl", NS):
        if placeholder in "".join(node.text or "" for node in table.findall(".//w:t", NS)):
            return table
    raise ValueError(f"Table with placeholder {placeholder} not found")


def rebuild_table(table: ET._Element, placeholder: str, rows: list[list[str]]) -> None:
    table_rows = rows_for_table(table)
    template_index = None
    for index, row in enumerate(table_rows):
        text = "".join(node.text or "" for node in row.findall(".//w:t", NS))
        if placeholder in text:
            template_index = index
            break
    if template_index is None:
        raise ValueError(f"Template row with {placeholder} not found")

    template_row = table_rows[template_index]
    for row in table_rows[template_index:]:
        table.remove(row)

    for index, values in enumerate(rows):
        new_row = deepcopy(template_row)
        row_cells = cells_for_row(new_row)
        values_to_write = list(values)
        if placeholder == "{{COMPANION_NAME}}" and len(row_cells) == 4 and len(values_to_write) == 3:
            values_to_write = [
                values_to_write[0],
                values_to_write[1],
                "ответственный" if index == 0 else "сопровождающий",
                values_to_write[2],
            ]
        for cell, value in zip(row_cells, values_to_write):
            set_cell_text(cell, value)
        table.append(new_row)


def cleanup_optional_placeholders(root: ET._Element) -> None:
    replace_placeholders(
        root,
        {
            "{{INTERMEDIATE_STOPS}}": "-",
            "{{LONG_DISTANCE_STOPS}}": "-",
            "{{MEDICAL_WORKER}}": "-",
            "{{TRIP_DATE_TEXT}}": "",
            "{{COMPANION_PHONE}}": "",
        },
    )


def set_multiline_placeholder(root: ET._Element, placeholder: str, lines: list[str]) -> None:
    for text_node in iter_text_nodes(root):
        if text_node.text != placeholder:
            continue
        run = text_node.getparent()
        paragraph = run.getparent()
        run_properties = clone_run_properties(run)
        paragraph.remove(run)
        for index, line in enumerate(lines):
            new_run = ET.Element(f"{{{NS['w']}}}r")
            if run_properties is not None:
                new_run.append(deepcopy(run_properties))
            new_text = ET.SubElement(new_run, f"{{{NS['w']}}}t")
            if line.startswith(" ") or line.endswith(" ") or "  " in line:
                new_text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            new_text.text = line
            paragraph.append(new_run)
            if index < len(lines) - 1:
                break_run = ET.Element(f"{{{NS['w']}}}r")
                if run_properties is not None:
                    break_run.append(deepcopy(run_properties))
                ET.SubElement(break_run, f"{{{NS['w']}}}br")
                paragraph.append(break_run)
        return
    raise ValueError(f"Placeholder {placeholder} not found")


def normalize_route_cell(root: ET._Element, marker_text: str) -> None:
    for cell in root.findall(".//w:tc", NS):
        text = "".join(node.text or "" for node in cell.findall(".//w:t", NS))
        if marker_text not in text:
            continue

        paragraphs = cell.findall("./w:p", NS)
        keep = []
        for paragraph in paragraphs:
            paragraph_text = "".join(node.text or "" for node in paragraph.findall(".//w:t", NS)).strip()
            has_break = bool(paragraph.findall(".//w:br", NS))
            if paragraph_text or has_break:
                keep.append(paragraph)
        for paragraph in paragraphs:
            if paragraph not in keep:
                cell.remove(paragraph)

        row = cell.getparent()
        row_properties = row.find("./w:trPr", NS)
        if row_properties is not None:
            for height in row_properties.findall("./w:trHeight", NS):
                row_properties.remove(height)

        for row_cell in row.findall("./w:tc", NS):
            cell_properties = row_cell.find("./w:tcPr", NS)
            if cell_properties is None:
                cell_properties = ET.SubElement(row_cell, f"{{{NS['w']}}}tcPr")

            cell_margin = cell_properties.find("./w:tcMar", NS)
            if cell_margin is None:
                cell_margin = ET.SubElement(cell_properties, f"{{{NS['w']}}}tcMar")
            for side in ["top", "bottom", "left", "right"]:
                edge = cell_margin.find(f"./w:{side}", NS)
                if edge is None:
                    edge = ET.SubElement(cell_margin, f"{{{NS['w']}}}{side}")
                edge.set(f"{{{NS['w']}}}w", "0")
                edge.set(f"{{{NS['w']}}}type", "dxa")

            vertical_align = cell_properties.find("./w:vAlign", NS)
            if vertical_align is None:
                vertical_align = ET.SubElement(cell_properties, f"{{{NS['w']}}}vAlign")
            vertical_align.set(f"{{{NS['w']}}}val", "top")
        return
    raise ValueError(f"Route cell containing {marker_text} not found")


def build_docx(
    template_path: Path,
    target_path: Path,
    replacements: dict[str, str],
    child_rows: list[list[str]],
    companion_rows: list[list[str]],
    route_lines: list[str] | None = None,
    bus_rows: list[list[str]] | None = None,
    driver_rows: list[list[str]] | None = None,
) -> None:
    with TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        with ZipFile(template_path) as source_zip:
            source_zip.extractall(tmpdir_path)

        document_xml = tmpdir_path / "word" / "document.xml"
        parser = ET.XMLParser(remove_blank_text=False)
        tree = ET.parse(str(document_xml), parser)
        root = tree.getroot()

        replace_placeholders(root, replacements)
        rebuild_table(find_table_by_placeholder(root, "{{CHILD_FULL_NAME}}"), "{{CHILD_FULL_NAME}}", child_rows)
        rebuild_table(find_table_by_placeholder(root, "{{COMPANION_NAME}}"), "{{COMPANION_NAME}}", companion_rows)
        if bus_rows is not None:
            rebuild_table(find_table_by_placeholder(root, "{{BUS_MODEL}}"), "{{BUS_MODEL}}", bus_rows)
        if driver_rows is not None:
            rebuild_table(find_table_by_placeholder(root, "{{DRIVER_NAME}}"), "{{DRIVER_NAME}}", driver_rows)
        if route_lines is not None:
            set_multiline_placeholder(root, "{{ROUTE_DESCRIPTION_MANUAL}}", route_lines)
            normalize_route_cell(root, route_lines[0])
        cleanup_optional_placeholders(root)

        tree.write(str(document_xml), encoding="utf-8", xml_declaration=True, standalone=True)

        with ZipFile(target_path, "w", ZIP_DEFLATED) as target_zip:
            for path in sorted(tmpdir_path.rglob("*")):
                if path.is_file():
                    target_zip.write(path, path.relative_to(tmpdir_path))
