"""Public tests with neutral input only."""

from datetime import date
from pathlib import Path
from zipfile import ZipFile

from app.automation.customer_memory import customer_name_similarity
from app.automation.documents import DocumentGenerationService
from app.config import Settings
from app.demo_template import ensure_demo_template


def test_age_on_trip_respects_birthday() -> None:
    age = DocumentGenerationService.age_on_trip
    assert age(date(2015, 10, 27), date(2026, 10, 26)) == "10"
    assert age(date(2015, 10, 26), date(2026, 10, 26)) == "11"


def test_customer_similarity_ignores_word_order() -> None:
    assert customer_name_similarity("Example Transit", "Transit Example") == 1.0


def test_demo_template_contains_only_placeholders(tmp_path: Path) -> None:
    template = ensure_demo_template(tmp_path / "demo.docx")
    assert ensure_demo_template(template) == template
    with ZipFile(template) as archive:
        content = archive.read("word/document.xml").decode()
    assert "{{CHILD_FULL_NAME}}" in content
    assert "{{ROUTE_DESCRIPTION_MANUAL}}" in content
    assert "{{SIGNATORY_POSITION}} / {{SIGNATORY_NAME}} /" in content


def test_local_settings_generate_demo_template(tmp_path: Path) -> None:
    template = tmp_path / "template.docx"
    Settings(notification_template_path=template).validate_for_runtime()
    assert template.is_file()
