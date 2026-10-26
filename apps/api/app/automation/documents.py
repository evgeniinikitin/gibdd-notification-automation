"""Tenant-template DOCX generation without modifying historical scripts."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Callable
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from lxml import etree
from PIL import Image

from .exceptions import DocumentGenerationError
from .schemas import (
    DocumentGenerationRequestDTO,
    DocumentGenerationResultDTO,
    display_date,
)


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
RELATIONSHIP_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPE_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PICTURE_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
WORDPROCESSING_DRAWING_NS = (
    "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
)
NS = {"w": WORD_NS}
PLACEHOLDER_PATTERN = re.compile(r"\{\{[A-Z0-9_]+\}\}")

CHILD_MARKER = "{{CHILD_FULL_NAME}}"
COMPANION_MARKER = "{{COMPANION_NAME}}"
BUS_MARKER = "{{BUS_MODEL}}"
DRIVER_MARKER = "{{DRIVER_NAME}}"
ROUTE_MARKER = "{{ROUTE_DESCRIPTION_MANUAL}}"


class DocumentGenerationService:
    """Generate a new versioned DOCX from a carrier-owned OOXML template."""

    OPTIONAL_DEFAULTS = {
        "{{INTERMEDIATE_STOPS}}": "-",
        "{{LONG_DISTANCE_STOPS}}": "-",
        "{{MEDICAL_WORKER}}": "-",
        "{{COMPANION_PHONE}}": "-",
        "{{CARRIER_LICENSE}}": "не применяется",
        "{{LICENSING_AUTHORITY}}": "не применяется",
    }

    def generate_docx(
        self, request: DocumentGenerationRequestDTO
    ) -> DocumentGenerationResultDTO:
        template = request.template_path.resolve()
        target = request.target_path.resolve()
        if not template.is_file():
            raise DocumentGenerationError(f"DOCX template not found: {template}")
        if template == target:
            raise DocumentGenerationError("Template and target paths must differ")
        if target.exists():
            raise DocumentGenerationError(
                f"Refusing to overwrite existing document: {target}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            with ZipFile(template, "r") as source:
                members = {
                    info.filename: source.read(info.filename)
                    for info in source.infolist()
                    if not info.is_dir()
                }
        except BadZipFile as exc:
            raise DocumentGenerationError("Template is not a valid DOCX ZIP") from exc

        if "word/document.xml" not in members:
            raise DocumentGenerationError("Template has no word/document.xml")

        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            remove_blank_text=False,
        )
        try:
            document_root = etree.fromstring(members["word/document.xml"], parser)
        except etree.XMLSyntaxError as exc:
            raise DocumentGenerationError("Invalid document.xml") from exc

        replacements = {
            "{{TRIP_DATE_TEXT}}": display_date(request.trip_date),
            **self.OPTIONAL_DEFAULTS,
            **request.replacements,
        }
        dynamic_markers = {
            CHILD_MARKER,
            COMPANION_MARKER,
            BUS_MARKER,
            DRIVER_MARKER,
            ROUTE_MARKER,
        }
        simple_replacements = {
            key: value
            for key, value in replacements.items()
            if key not in dynamic_markers
        }

        self._rebuild_table(
            document_root,
            CHILD_MARKER,
            [
                [
                    str(index),
                    child.full_name,
                    self.age_on_trip(child.birth_date, request.trip_date),
                    display_date(child.birth_date),
                    f"{child.parent_name}, {child.parent_phone}",
                ]
                for index, child in enumerate(request.children, start=1)
            ],
        )
        self._rebuild_table(
            document_root,
            COMPANION_MARKER,
            [
                [str(index), item.name, item.role, item.phone]
                for index, item in enumerate(request.companions, start=1)
            ],
        )
        self._rebuild_table(
            document_root,
            BUS_MARKER,
            [
                [item.model, item.number, item.diagnostic_card_info]
                for item in request.buses
            ],
        )
        self._rebuild_table(
            document_root,
            DRIVER_MARKER,
            [
                [
                    item.name,
                    display_date(item.birth_date) if item.birth_date else "-",
                    item.licence,
                    display_date(item.licence_issue_date),
                    item.experience,
                ]
                for item in request.drivers
            ],
        )
        self._replace_route(document_root, request.route_lines)
        self._normalize_route_cell(document_root, request.route_lines[0])
        self._embed_profile_images(
            document_root,
            members,
            signature_path=request.signature_path,
            stamp_path=request.stamp_path,
            parser=parser,
        )
        self._sanitize_package_metadata(document_root, members, parser)
        self._replace_placeholders(document_root, simple_replacements)
        members["word/document.xml"] = etree.tostring(
            document_root,
            xml_declaration=True,
            encoding="UTF-8",
            standalone=True,
        )

        # Simple placeholders may also live in headers and footers.
        for name, raw in list(members.items()):
            if (
                name == "word/document.xml"
                or not name.startswith("word/")
                or not name.endswith(".xml")
                or b"{{" not in raw
            ):
                continue
            try:
                root = etree.fromstring(raw, parser)
            except etree.XMLSyntaxError:
                continue
            self._replace_placeholders(root, simple_replacements)
            members[name] = etree.tostring(
                root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            )

        remaining = sorted(self._remaining_placeholders(members, parser))
        if remaining:
            raise DocumentGenerationError(
                "Unresolved template placeholders: " + ", ".join(remaining)
            )

        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".buscript-docx-",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as temporary:
                temp_name = temporary.name
            with ZipFile(temp_name, "w", ZIP_DEFLATED) as output:
                for name in sorted(members):
                    output.writestr(name, members[name])
            self._validate_docx(Path(temp_name))
            os.replace(temp_name, target)
            temp_name = None
        except Exception as exc:
            if isinstance(exc, DocumentGenerationError):
                raise
            raise DocumentGenerationError(f"Could not write DOCX: {exc}") from exc
        finally:
            if temp_name:
                Path(temp_name).unlink(missing_ok=True)

        return DocumentGenerationResultDTO(
            path=target,
            child_count=len(request.children),
            companion_count=len(request.companions),
            bus_count=len(request.buses),
            driver_count=len(request.drivers),
            remaining_placeholders=[],
        )

    def convert_to_pdf(
        self,
        docx_path: Path,
        *,
        target_path: Path | None = None,
        soffice_binary: str = "soffice",
        timeout_seconds: float = 120,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> Path:
        source = docx_path.resolve()
        if not source.is_file():
            raise DocumentGenerationError(f"DOCX not found: {source}")
        target = (
            target_path.resolve()
            if target_path
            else source.with_suffix(".pdf").resolve()
        )
        if target.exists():
            raise DocumentGenerationError(
                f"Refusing to overwrite existing PDF: {target}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(
            prefix="buscript-pdf-", dir=target.parent
        ) as tmp:
            result = runner(
                [
                    soffice_binary,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    tmp,
                    str(source),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            converted = Path(tmp) / f"{source.stem}.pdf"
            if result.returncode != 0 or not converted.is_file():
                raise DocumentGenerationError(
                    "LibreOffice PDF conversion failed: "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )
            if not converted.read_bytes().startswith(b"%PDF"):
                raise DocumentGenerationError(
                    "LibreOffice output does not have a PDF signature"
                )
            os.replace(converted, target)
        return target

    def _embed_profile_images(
        self,
        document_root: etree._Element,
        members: dict[str, bytes],
        *,
        signature_path: Path | None,
        stamp_path: Path | None,
        parser: etree.XMLParser,
    ) -> None:
        """Add tenant PNGs with surgical OOXML package changes.

        Only document.xml, its relationship part, [Content_Types].xml and the
        new media entries are touched; all preserve-only template parts remain
        byte-for-byte identical.
        """

        assets = [
            ("signature", signature_path, 1_152_000),
            ("stamp", stamp_path, 864_000),
        ]
        selected = [
            (kind, path.resolve(), width)
            for kind, path, width in assets
            if path is not None
        ]
        if not selected:
            return
        for _kind, path, _width in selected:
            if not path.is_file() or path.suffix.lower() != ".png":
                raise DocumentGenerationError(
                    f"Profile image is missing or is not PNG: {path}"
                )

        paragraph = next(
            (
                item
                for item in document_root.xpath(".//w:p", namespaces=NS)
                if "{{SIGNATORY_POSITION}}"
                in "".join(item.xpath(".//w:t/text()", namespaces=NS))
            ),
            None,
        )
        if paragraph is None:
            raise DocumentGenerationError(
                "Could not find the signatory paragraph in the system template"
            )

        rels_name = "word/_rels/document.xml.rels"
        types_name = "[Content_Types].xml"
        if rels_name not in members or types_name not in members:
            raise DocumentGenerationError("DOCX package relationship parts are missing")
        rels_root = etree.fromstring(members[rels_name], parser)
        types_root = etree.fromstring(members[types_name], parser)
        existing_ids = {
            item.get("Id", "")
            for item in rels_root.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
        }
        next_id = 1
        while f"rId{next_id}" in existing_ids:
            next_id += 1
        if not any(
            item.get("Extension", "").lower() == "png"
            for item in types_root.findall(f"{{{CONTENT_TYPE_NS}}}Default")
        ):
            etree.SubElement(
                types_root,
                f"{{{CONTENT_TYPE_NS}}}Default",
                Extension="png",
                ContentType="image/png",
            )

        signatory_text = next(
            (
                node
                for node in paragraph.xpath(".//w:t", namespaces=NS)
                if "{{SIGNATORY_POSITION}}" in (node.text or "")
                and "{{SIGNATORY_NAME}}" in (node.text or "")
            ),
            None,
        )
        if signatory_text is None:
            raise DocumentGenerationError(
                "Signatory placeholders must share the template signature run"
            )
        signatory_run = signatory_text.getparent()
        if signatory_run is None or signatory_run.tag != f"{{{WORD_NS}}}r":
            raise DocumentGenerationError("Invalid signatory run in system template")
        signatory_text.text = "{{SIGNATORY_POSITION}} / "
        signatory_text.set(
            "{http://www.w3.org/XML/1998/namespace}space",
            "preserve",
        )
        name_run = deepcopy(signatory_run)
        for child in list(name_run):
            if child.tag != f"{{{WORD_NS}}}rPr":
                name_run.remove(child)
        name_text = etree.SubElement(name_run, f"{{{WORD_NS}}}t")
        name_text.text = " {{SIGNATORY_NAME}} /"
        name_text.set(
            "{http://www.w3.org/XML/1998/namespace}space",
            "preserve",
        )
        insertion_index = paragraph.index(signatory_run) + 1
        for image_index, (kind, path, width_emu) in enumerate(selected, start=1):
            media_name = f"word/media/buscript-{kind}.png"
            members[media_name] = path.read_bytes()
            with Image.open(path) as image:
                pixel_width, pixel_height = image.size
            height_emu = max(
                1,
                round(width_emu * pixel_height / max(pixel_width, 1)),
            )
            while f"rId{next_id}" in existing_ids:
                next_id += 1
            relationship_id = f"rId{next_id}"
            existing_ids.add(relationship_id)
            next_id += 1
            etree.SubElement(
                rels_root,
                f"{{{PACKAGE_REL_NS}}}Relationship",
                Id=relationship_id,
                Type=f"{RELATIONSHIP_NS}/image",
                Target=f"media/buscript-{kind}.png",
            )
            run = self._inline_png_run(
                relationship_id=relationship_id,
                name=f"Buscript {kind}",
                width_emu=width_emu,
                height_emu=height_emu,
                drawing_id=10_000 + image_index,
            )
            paragraph.insert(insertion_index, run)
            insertion_index += 1
        paragraph.insert(insertion_index, name_run)
        members[rels_name] = etree.tostring(
            rels_root,
            xml_declaration=True,
            encoding="UTF-8",
            standalone=True,
        )
        members[types_name] = etree.tostring(
            types_root,
            xml_declaration=True,
            encoding="UTF-8",
            standalone=True,
        )

    @staticmethod
    def _inline_png_run(
        *,
        relationship_id: str,
        name: str,
        width_emu: int,
        height_emu: int,
        drawing_id: int,
    ) -> etree._Element:
        xml = f"""
        <w:r xmlns:w="{WORD_NS}" xmlns:r="{RELATIONSHIP_NS}"
             xmlns:wp="{WORDPROCESSING_DRAWING_NS}"
             xmlns:a="{DRAWING_NS}" xmlns:pic="{PICTURE_NS}">
          <w:drawing>
            <wp:inline distT="0" distB="0" distL="0" distR="0">
              <wp:extent cx="{width_emu}" cy="{height_emu}"/>
              <wp:effectExtent l="0" t="0" r="0" b="0"/>
              <wp:docPr id="{drawing_id}" name="{name}"/>
              <wp:cNvGraphicFramePr>
                <a:graphicFrameLocks noChangeAspect="1"/>
              </wp:cNvGraphicFramePr>
              <a:graphic>
                <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
                  <pic:pic>
                    <pic:nvPicPr>
                      <pic:cNvPr id="{drawing_id}" name="{name}"/>
                      <pic:cNvPicPr/>
                    </pic:nvPicPr>
                    <pic:blipFill>
                      <a:blip r:embed="{relationship_id}"/>
                      <a:stretch><a:fillRect/></a:stretch>
                    </pic:blipFill>
                    <pic:spPr>
                      <a:xfrm>
                        <a:off x="0" y="0"/>
                        <a:ext cx="{width_emu}" cy="{height_emu}"/>
                      </a:xfrm>
                      <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
                    </pic:spPr>
                  </pic:pic>
                </a:graphicData>
              </a:graphic>
            </wp:inline>
          </w:drawing>
        </w:r>
        """
        return etree.fromstring(xml.encode("utf-8"))

    @staticmethod
    def _sanitize_package_metadata(
        document_root: etree._Element,
        members: dict[str, bytes],
        parser: etree.XMLParser,
    ) -> None:
        """Remove author/company residue and orphan source mail links."""

        core_name = "docProps/core.xml"
        if core_name in members:
            root = etree.fromstring(members[core_name], parser)
            namespaces = {
                "dc": "http://purl.org/dc/elements/1.1/",
                "cp": (
                    "http://schemas.openxmlformats.org/package/2006/"
                    "metadata/core-properties"
                ),
            }
            for xpath in ("./dc:creator", "./cp:lastModifiedBy"):
                node = root.find(xpath, namespaces)
                if node is not None:
                    node.text = "Buscript"
            members[core_name] = etree.tostring(
                root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            )

        app_name = "docProps/app.xml"
        if app_name in members:
            root = etree.fromstring(members[app_name], parser)
            namespace = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
            company = root.find(f"{{{namespace}}}Company")
            if company is not None:
                company.text = "Buscript"
            members[app_name] = etree.tostring(
                root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            )

        rels_name = "word/_rels/document.xml.rels"
        if rels_name not in members:
            return
        used_ids = {
            value
            for node in document_root.iter()
            for attribute, value in node.attrib.items()
            if attribute.startswith(f"{{{RELATIONSHIP_NS}}}")
        }
        rels_root = etree.fromstring(members[rels_name], parser)
        changed = False
        for relationship in list(
            rels_root.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
        ):
            relationship_id = relationship.get("Id", "")
            target = relationship.get("Target", "")
            relationship_type = relationship.get("Type", "")
            if (
                relationship_id not in used_ids
                and target.lower().startswith("mailto:")
                and relationship_type.endswith("/hyperlink")
            ):
                rels_root.remove(relationship)
                changed = True
        if changed:
            members[rels_name] = etree.tostring(
                rels_root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            )

    @staticmethod
    def age_on_trip(birth_date: date, trip_date: date) -> str:
        if birth_date > trip_date:
            raise DocumentGenerationError("Birth date is after the trip date")
        years = trip_date.year - birth_date.year
        if (trip_date.month, trip_date.day) < (birth_date.month, birth_date.day):
            years -= 1
        return str(years)

    @staticmethod
    def _text_nodes(root: etree._Element) -> list[etree._Element]:
        return list(root.xpath(".//w:t", namespaces=NS))

    def _replace_placeholders(
        self, root: etree._Element, replacements: dict[str, str]
    ) -> None:
        # Fast path preserves every run when the marker is within one text node.
        for node in self._text_nodes(root):
            if not node.text:
                continue
            for marker, replacement in replacements.items():
                if marker in node.text:
                    node.text = node.text.replace(marker, replacement)

        # Word can split a marker across runs. Rebuild only the marker span,
        # preserving all text and run properties around it.
        for paragraph in root.xpath(".//w:p", namespaces=NS):
            for marker, replacement in replacements.items():
                self._replace_split_marker(paragraph, marker, replacement)

    def _replace_split_marker(
        self, paragraph: etree._Element, marker: str, replacement: str
    ) -> None:
        while True:
            nodes = self._text_nodes(paragraph)
            texts = [node.text or "" for node in nodes]
            combined = "".join(texts)
            start = combined.find(marker)
            if start < 0:
                return
            end = start + len(marker)
            offsets: list[tuple[int, int]] = []
            cursor = 0
            for text in texts:
                offsets.append((cursor, cursor + len(text)))
                cursor += len(text)

            start_index = next(
                index
                for index, (_, node_end) in enumerate(offsets)
                if start < node_end
            )
            end_index = next(
                index
                for index, (_, node_end) in enumerate(offsets)
                if end <= node_end
            )
            start_local = start - offsets[start_index][0]
            end_local = end - offsets[end_index][0]
            if start_index == end_index:
                original = texts[start_index]
                nodes[start_index].text = (
                    original[:start_local] + replacement + original[end_local:]
                )
                continue
            nodes[start_index].text = texts[start_index][:start_local] + replacement
            for index in range(start_index + 1, end_index):
                nodes[index].text = ""
            nodes[end_index].text = texts[end_index][end_local:]

    def _find_table(
        self, root: etree._Element, marker: str
    ) -> etree._Element:
        for table in root.xpath(".//w:tbl", namespaces=NS):
            text = "".join(
                node.text or "" for node in self._text_nodes(table)
            )
            if marker in text:
                return table
        raise DocumentGenerationError(
            f"Template table marker not found: {marker}"
        )

    def _rebuild_table(
        self, root: etree._Element, marker: str, values: list[list[str]]
    ) -> None:
        table = self._find_table(root, marker)
        if marker in {CHILD_MARKER, COMPANION_MARKER}:
            self._ensure_visible_table_borders(table)
        rows = list(table.xpath("./w:tr", namespaces=NS))
        template_index: int | None = None
        for index, row in enumerate(rows):
            text = "".join(
                node.text or "" for node in self._text_nodes(row)
            )
            if marker in text:
                template_index = index
                break
        if template_index is None:
            raise DocumentGenerationError(f"Template row not found: {marker}")

        template_row = rows[template_index]
        for row in rows[template_index:]:
            table.remove(row)
        for row_values in values:
            row = deepcopy(template_row)
            cells = list(row.xpath("./w:tc", namespaces=NS))
            if len(cells) != len(row_values):
                raise DocumentGenerationError(
                    f"Table {marker} has {len(cells)} columns, "
                    f"but {len(row_values)} values were supplied"
                )
            for cell, value in zip(cells, row_values, strict=True):
                self._set_cell_text(cell, value)
            table.append(row)

    @staticmethod
    def _ensure_visible_table_borders(table: etree._Element) -> None:
        """Give passenger-list tables explicit printable cell boundaries."""

        properties = table.find("./w:tblPr", namespaces=NS)
        if properties is None:
            properties = etree.Element(f"{{{WORD_NS}}}tblPr")
            table.insert(0, properties)
        borders = properties.find("./w:tblBorders", namespaces=NS)
        if borders is None:
            borders = etree.SubElement(
                properties,
                f"{{{WORD_NS}}}tblBorders",
            )
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            border = borders.find(f"./w:{edge}", namespaces=NS)
            if border is None:
                border = etree.SubElement(
                    borders,
                    f"{{{WORD_NS}}}{edge}",
                )
            border.set(f"{{{WORD_NS}}}val", "single")
            border.set(f"{{{WORD_NS}}}sz", "6")
            border.set(f"{{{WORD_NS}}}space", "0")
            border.set(f"{{{WORD_NS}}}color", "000000")
        for cell in table.xpath(".//w:tc", namespaces=NS):
            cell_properties = cell.find("./w:tcPr", namespaces=NS)
            if cell_properties is None:
                cell_properties = etree.Element(f"{{{WORD_NS}}}tcPr")
                cell.insert(0, cell_properties)
            cell_borders = cell_properties.find("./w:tcBorders", namespaces=NS)
            if cell_borders is None:
                cell_borders = etree.SubElement(
                    cell_properties,
                    f"{{{WORD_NS}}}tcBorders",
                )
            for edge in ("top", "left", "bottom", "right"):
                border = cell_borders.find(f"./w:{edge}", namespaces=NS)
                if border is None:
                    border = etree.SubElement(
                        cell_borders,
                        f"{{{WORD_NS}}}{edge}",
                    )
                border.set(f"{{{WORD_NS}}}val", "single")
                border.set(f"{{{WORD_NS}}}sz", "6")
                border.set(f"{{{WORD_NS}}}space", "0")
                border.set(f"{{{WORD_NS}}}color", "000000")

    def _set_cell_text(self, cell: etree._Element, value: str) -> None:
        nodes = self._text_nodes(cell)
        if nodes:
            nodes[0].text = value
            for node in nodes[1:]:
                node.text = ""
            return
        paragraph = etree.SubElement(cell, f"{{{WORD_NS}}}p")
        run = etree.SubElement(paragraph, f"{{{WORD_NS}}}r")
        text = etree.SubElement(run, f"{{{WORD_NS}}}t")
        text.text = value

    def _replace_route(
        self, root: etree._Element, lines: list[str]
    ) -> None:
        for paragraph in root.xpath(".//w:p", namespaces=NS):
            nodes = self._text_nodes(paragraph)
            combined = "".join(node.text or "" for node in nodes)
            if ROUTE_MARKER not in combined:
                continue
            first_run = nodes[0].getparent() if nodes else None
            run_properties = (
                deepcopy(first_run.find("./w:rPr", namespaces=NS))
                if first_run is not None
                and first_run.find("./w:rPr", namespaces=NS) is not None
                else None
            )
            for run in list(paragraph.xpath("./w:r", namespaces=NS)):
                paragraph.remove(run)
            for index, line in enumerate(lines):
                run = etree.SubElement(paragraph, f"{{{WORD_NS}}}r")
                if run_properties is not None:
                    run.append(deepcopy(run_properties))
                text = etree.SubElement(run, f"{{{WORD_NS}}}t")
                text.text = line
                if index < len(lines) - 1:
                    break_run = etree.SubElement(paragraph, f"{{{WORD_NS}}}r")
                    etree.SubElement(break_run, f"{{{WORD_NS}}}br")
            return
        raise DocumentGenerationError(
            f"Route placeholder not found: {ROUTE_MARKER}"
        )

    def _normalize_route_cell(
        self, root: etree._Element, first_route_line: str
    ) -> None:
        for cell in root.xpath(".//w:tc", namespaces=NS):
            text = "".join(node.text or "" for node in self._text_nodes(cell))
            if first_route_line not in text:
                continue
            row = cell.getparent()
            row_properties = row.find("./w:trPr", namespaces=NS)
            if row_properties is not None:
                for height in row_properties.findall(
                    "./w:trHeight", namespaces=NS
                ):
                    row_properties.remove(height)
            for row_cell in row.xpath("./w:tc", namespaces=NS):
                properties = row_cell.find("./w:tcPr", namespaces=NS)
                if properties is None:
                    properties = etree.SubElement(
                        row_cell, f"{{{WORD_NS}}}tcPr"
                    )
                vertical = properties.find("./w:vAlign", namespaces=NS)
                if vertical is None:
                    vertical = etree.SubElement(
                        properties, f"{{{WORD_NS}}}vAlign"
                    )
                vertical.set(f"{{{WORD_NS}}}val", "top")
            return
        raise DocumentGenerationError("Generated route table cell not found")

    def _remaining_placeholders(
        self, members: dict[str, bytes], parser: etree.XMLParser
    ) -> set[str]:
        remaining: set[str] = set()
        for name, raw in members.items():
            if not name.startswith("word/") or not name.endswith(".xml"):
                continue
            try:
                root = etree.fromstring(raw, parser)
            except etree.XMLSyntaxError:
                continue
            for paragraph in root.xpath(".//w:p", namespaces=NS):
                combined = "".join(
                    node.text or "" for node in self._text_nodes(paragraph)
                )
                remaining.update(PLACEHOLDER_PATTERN.findall(combined))
        return remaining

    @staticmethod
    def _validate_docx(path: Path) -> None:
        try:
            with ZipFile(path) as generated:
                if generated.testzip() is not None:
                    raise DocumentGenerationError("Generated DOCX has bad ZIP data")
                if "word/document.xml" not in generated.namelist():
                    raise DocumentGenerationError(
                        "Generated DOCX has no document.xml"
                    )
        except BadZipFile as exc:
            raise DocumentGenerationError("Generated DOCX is not a ZIP") from exc
