"""Playwright portal adapter and a dry-run-safe submission orchestrator."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urljoin, urlparse

from pydantic import Field

from .captcha import CaptchaResolver
from .exceptions import SubmissionError, UnsafeSubmissionError
from .payload import PayloadBuilderService
from .schemas import (
    CaptchaInputDTO,
    GibddPayloadDTO,
    PartyDTO,
    PartyType,
    StrictDTO,
    SubmissionOutcomeDTO,
    SubmissionStatus,
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class SubmissionWorkerConfig(StrictDTO):
    base_url: str = (
        "https://xn--80aebkobnwfcnsfk1e0h.xn--p1ai/transportation"
    )
    live_submission: bool = False
    headful: bool = True
    route_direct: bool = True
    timeout_ms: int = Field(default=180_000, ge=1_000)
    keep_browser_open_on_dry_run: bool = True
    screenshot_dir: Path | None = None
    direct_no_proxy: str = (
        "xn--80aebkobnwfcnsfk1e0h.xn--p1ai,.xn--p1ai,"
        "localhost,127.0.0.1"
    )

    @classmethod
    def from_env(cls) -> SubmissionWorkerConfig:
        return cls(
            base_url=os.environ.get(
                "GIBDD_BASE_URL",
                "https://xn--80aebkobnwfcnsfk1e0h.xn--p1ai/transportation",
            ),
            live_submission=_env_bool("GIBDD_LIVE_SUBMISSION", False),
            headful=_env_bool("GIBDD_HEADFUL", True),
            route_direct=_env_bool("GIBDD_ROUTE_DIRECT", True),
        )


class FinalSubmissionPermit:
    """Capability object minted only by a live-enabled guard."""

    __slots__ = ("_allowed",)

    def __init__(self, *, allowed: bool) -> None:
        self._allowed = allowed


class FinalSubmissionGuard:
    def __init__(self, live_submission: bool) -> None:
        self.live_submission = live_submission

    def issue_permit(self) -> FinalSubmissionPermit:
        if not self.live_submission:
            raise UnsafeSubmissionError(
                "Final GIBDD submit is disabled: GIBDD_LIVE_SUBMISSION=false"
            )
        return FinalSubmissionPermit(allowed=True)

    @staticmethod
    def verify(permit: FinalSubmissionPermit) -> None:
        if not isinstance(permit, FinalSubmissionPermit) or not permit._allowed:
            raise UnsafeSubmissionError("Missing final-submission permit")


class StorageFileResolver(Protocol):
    async def resolve(self, storage_key: str) -> Path: ...


class GibddPortalAdapter(Protocol):
    async def fill_to_confirmation(
        self, payload: GibddPayloadDTO, resolved_files: list[Path]
    ) -> None: ...

    async def capture_captcha(self) -> tuple[bytes, str, str | None]: ...

    async def enter_captcha(self, value: str) -> None: ...

    async def final_submit(self, permit: FinalSubmissionPermit) -> None: ...

    async def extract_token(self) -> str | None: ...

    async def current_url(self) -> str: ...

    async def snapshot(self, label: str) -> Path | None: ...

    async def close(self) -> None: ...


class RootStorageFileResolver:
    """Resolve opaque storage keys under one worker-mounted directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    async def resolve(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise SubmissionError("Storage key escapes the worker storage root")
        if not candidate.is_file():
            raise SubmissionError(f"Submission artifact not found: {storage_key}")
        return candidate


class SubmissionWorker:
    def __init__(
        self,
        *,
        config: SubmissionWorkerConfig | None = None,
        captcha_resolver: CaptchaResolver | None = None,
        payload_builder: PayloadBuilderService | None = None,
    ) -> None:
        self.config = config or SubmissionWorkerConfig.from_env()
        self._captcha_resolver = captcha_resolver
        self._payload_builder = payload_builder or PayloadBuilderService()
        self._guard = FinalSubmissionGuard(self.config.live_submission)

    async def run(
        self,
        payload: GibddPayloadDTO | dict[str, Any],
        *,
        file_resolver: StorageFileResolver,
        adapter: GibddPortalAdapter | None = None,
    ) -> SubmissionOutcomeDTO:
        validated = self._payload_builder.validate(payload)
        resolved_files = [
            await file_resolver.resolve(key) for key in validated.files
        ]
        for path in resolved_files:
            if path.suffix.lower() != ".pdf":
                raise SubmissionError(
                    f"Only generated PDF artifacts may be uploaded: {path.name}"
                )
            if not path.read_bytes()[:4] == b"%PDF":
                raise SubmissionError(f"Artifact is not a PDF: {path.name}")

        owns_adapter = adapter is None
        portal = adapter or await PlaywrightGibddPortalAdapter.launch(self.config)
        final_attempted = False
        screenshots: list[Path] = []
        try:
            await portal.fill_to_confirmation(validated, resolved_files)
            current_url = await portal.current_url()
            confirmation = await portal.snapshot("confirmation")
            if confirmation:
                screenshots.append(confirmation)

            # This branch returns before CAPTCHA entry and before a permit can
            # even be minted. It is the core irreversible-action safety rule.
            if not self.config.live_submission:
                return SubmissionOutcomeDTO(
                    status=SubmissionStatus.DRY_RUN_READY,
                    final_submit_attempted=False,
                    current_url=current_url,
                    message=(
                        "Форма заполнена до финального подтверждения. "
                        "Отправка заблокирована dry-run режимом."
                    ),
                    screenshots=screenshots,
                )

            if self._captcha_resolver is None:
                raise SubmissionError(
                    "Live submission requires a configured CAPTCHA resolver"
                )
            image, mime_type, artifact_id = await portal.capture_captcha()
            captcha = await self._captcha_resolver.resolve(
                CaptchaInputDTO(
                    image_bytes=image,
                    mime_type=mime_type,
                    job_id="submission-runtime",
                    page_url=current_url,
                    artifact_id=artifact_id,
                )
            )
            await portal.enter_captcha(captcha.text)
            permit = self._guard.issue_permit()
            final_attempted = True
            await portal.final_submit(permit)
            token = await portal.extract_token()
            current_url = await portal.current_url()
            if token:
                return SubmissionOutcomeDTO(
                    status=SubmissionStatus.SUBMITTED,
                    final_submit_attempted=True,
                    current_url=current_url,
                    token=token,
                    captcha_mode=captcha.mode,
                    screenshots=screenshots,
                )
            result = await portal.snapshot("after-submit-token-missing")
            if result:
                screenshots.append(result)
            return SubmissionOutcomeDTO(
                status=SubmissionStatus.NEEDS_SUPPORT,
                final_submit_attempted=True,
                current_url=current_url,
                captcha_mode=captcha.mode,
                message=(
                    "Финальная кнопка была нажата, но token не найден. "
                    "Автоматический повтор запрещён до anti-duplicate проверки."
                ),
                screenshots=screenshots,
            )
        except Exception as exc:
            failure = await portal.snapshot("failure")
            if failure:
                screenshots.append(failure)
            if final_attempted:
                return SubmissionOutcomeDTO(
                    status=SubmissionStatus.NEEDS_SUPPORT,
                    final_submit_attempted=True,
                    current_url=await portal.current_url(),
                    message=(
                        "Ошибка после финальной отправки; требуется "
                        f"anti-duplicate проверка: {type(exc).__name__}"
                    ),
                    screenshots=screenshots,
                )
            raise
        finally:
            should_keep_dry_run = (
                owns_adapter
                and not self.config.live_submission
                and self.config.headful
                and self.config.keep_browser_open_on_dry_run
            )
            if owns_adapter and not should_keep_dry_run:
                await portal.close()


class PlaywrightGibddPortalAdapter:
    """Selector-based adapter isolated from domain and retry decisions.

    Selectors mirror the current historical Playwright automation and remain in
    one class so portal markup changes do not affect payload/business services.
    """

    def __init__(
        self,
        *,
        page: Any,
        config: SubmissionWorkerConfig,
        browser: Any = None,
        playwright: Any = None,
    ) -> None:
        self.page = page
        self.config = config
        self._browser = browser
        self._playwright = playwright

    @classmethod
    async def launch(
        cls, config: SubmissionWorkerConfig
    ) -> PlaywrightGibddPortalAdapter:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise SubmissionError("Playwright is not installed") from exc

        playwright = await async_playwright().start()
        browser_environment = dict(os.environ)
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            browser_environment.pop(key, None)
            browser_environment.pop(key.lower(), None)
        browser_environment["NO_PROXY"] = config.direct_no_proxy
        browser_environment["no_proxy"] = config.direct_no_proxy
        browser = await playwright.chromium.launch(
            headless=not config.headful,
            args=[
                "--no-proxy-server",
                "--proxy-server=direct://",
                "--proxy-bypass-list=*",
            ],
            env=browser_environment,
        )
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()
        page.set_default_timeout(config.timeout_ms)
        return cls(
            page=page,
            config=config,
            browser=browser,
            playwright=playwright,
        )

    async def fill_to_confirmation(
        self, payload: GibddPayloadDTO, resolved_files: list[Path]
    ) -> None:
        await self.page.goto(
            self.config.base_url, wait_until="domcontentloaded"
        )
        await self._wait_for_form("/transportation/applicant/", "заявителя")
        await self._fill_applicant(payload, resolved_files)
        initial = self.page.locator(
            '.js_continue_btn button.u-form__sbt, '
            'form[action="/transportation/applicant/"] button.u-form__sbt'
        ).first
        await initial.click(no_wait_after=True)
        await self._wait_for_form("/transportation/customers/", "заказчика")
        await self._fill_party(payload.customer)
        await self._next("/transportation/customers/", "/transportation/carriers/", "перевозчика")
        await self._fill_party(payload.carrier)
        await self._next("/transportation/carriers/", "/transportation/vehicles/", "автобусов")
        await self._fill_vehicles(payload)
        await self._next("/transportation/vehicles/", "/transportation/drivers/", "водителей")
        await self._fill_drivers(payload)
        await self._next("/transportation/drivers/", "/transportation/route/", "маршрута")
        await self._fill_route(payload)
        await self._click_form_next("/transportation/route/")
        await self.page.wait_for_function(
            """() => window.location.pathname.includes('/transportation/confirm')
              || Boolean(document.querySelector('h2 a[href="/transportation/route/"]'))"""
        )

    async def capture_captcha(self) -> tuple[bytes, str, str | None]:
        form = self.page.locator("form").last
        image = form.locator(
            'img[src*="captcha" i], img[class*="captcha" i], canvas'
        ).first
        await image.wait_for(state="visible")
        raw = await image.screenshot(type="png")
        artifact = await self.snapshot("captcha")
        return raw, "image/png", str(artifact) if artifact else None

    async def enter_captcha(self, value: str) -> None:
        field = self.page.locator(
            'input[name*="captcha" i], input[id*="captcha" i]'
        ).first
        await field.fill(value)

    async def final_submit(self, permit: FinalSubmissionPermit) -> None:
        FinalSubmissionGuard.verify(permit)
        submit = self.page.locator(
            'form button[type="submit"], form input[type="submit"]'
        ).last
        await submit.click()
        await self.page.wait_for_load_state("domcontentloaded")

    async def extract_token(self) -> str | None:
        url_token = self._token_from_url(self.page.url)
        if url_token:
            return url_token
        links = self.page.locator('a[href*="token="]')
        for index in range(await links.count()):
            href = await links.nth(index).get_attribute("href")
            token = self._token_from_url(href or "")
            if token:
                return token
        content = await self.page.content()
        match = re.search(r"[?&]token=([^&\"'<>\s]+)", content)
        return match.group(1) if match else None

    async def current_url(self) -> str:
        return str(self.page.url)

    async def snapshot(self, label: str) -> Path | None:
        if self.config.screenshot_dir is None:
            return None
        directory = self.config.screenshot_dir.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        safe_label = re.sub(r"[^A-Za-z0-9_-]", "-", label)
        target = directory / f"{safe_label}.png"
        html_target = directory / f"{safe_label}.html"
        await self.page.screenshot(path=str(target), full_page=True)
        html_target.write_text(await self.page.content(), encoding="utf-8")
        return target

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def _fill_applicant(
        self, payload: GibddPayloadDTO, files: list[Path]
    ) -> None:
        await self._fill_named("name", payload.applicant.name)
        await self._fill_named("position", payload.applicant.position)
        await self._fill_named("phone", payload.applicant.phone)
        await self._fill_named("email", payload.applicant.email)
        regions = self.page.locator('select[name="region_code[]"]').first
        await regions.wait_for(state="attached")
        await regions.select_option(payload.transportation.region_codes)
        await regions.dispatch_event("change")
        await self._fill_named(
            "count", str(payload.transportation.children_count)
        )
        await self._fill_named("goal", payload.transportation.goal)
        await self._set_input_value("date_start", payload.transportation.date_start)
        await self._set_input_value("date_end", payload.transportation.date_end)
        uploader = self.page.locator(
            "#fileupload-input, input[type='file'][name='file[]']"
        ).first
        await uploader.wait_for(state="attached")
        for path in files:
            await uploader.set_input_files(str(path))
            await self.page.wait_for_function(
                """fileName => {
                  const error = document.querySelector('#fileupload-error');
                  if (error && getComputedStyle(error).display !== 'none'
                      && error.textContent.trim()) throw new Error(error.textContent.trim());
                  const list = document.querySelector('#fileupload-list');
                  return Boolean(list && list.textContent.includes(fileName));
                }""",
                arg=path.name,
            )

    async def _fill_party(self, party: PartyDTO) -> None:
        portal_types = {
            PartyType.PHYSICAL: "1",
            PartyType.LEGAL: "2",
            PartyType.IP: "3",
        }
        type_field = self.page.locator('[name="type"]').first
        await type_field.wait_for(state="attached")
        await self.page.evaluate(
            """([selector, value]) => {
              const select = document.querySelector(selector);
              select.value = value;
              select.dispatchEvent(new Event('input', {bubbles: true}));
              select.dispatchEvent(new Event('change', {bubbles: true}));
              if (window.jQuery) window.jQuery(select).val(value).trigger('change');
            }""",
            ['[name="type"]', portal_types[party.type]],
        )
        await self.page.wait_for_timeout(300)
        for name in (
            "name",
            "entity_name",
            "entity_address",
            "entity_location",
            "address",
            "phone",
            "email",
            "inn",
        ):
            value = getattr(party, name)
            if value is not None:
                locator = self.page.locator(f'[name="{name}"]').first
                if await locator.count() and await locator.is_visible():
                    await self._set_input_value(name, value)

    async def _fill_vehicles(self, payload: GibddPayloadDTO) -> None:
        await self._ensure_blocks("#vehicles .vehicle", "#add_vehicle", len(payload.vehicles))
        blocks = self.page.locator("#vehicles .vehicle")
        for index, vehicle in enumerate(payload.vehicles):
            block = blocks.nth(index)
            await block.locator('[name$="[brand_and_model]"]').fill(
                vehicle.brand_and_model
            )
            await block.locator('[name$="[number]"]').fill(vehicle.number)
            await block.locator('[name$="[diagnostic_card_info]"]').fill(
                vehicle.diagnostic_card_info
            )
            navigator = block.locator('[name$="[navigator_info]"]')
            if await navigator.count():
                if vehicle.navigator_info:
                    await navigator.check()
                else:
                    await navigator.uncheck()

    async def _fill_drivers(self, payload: GibddPayloadDTO) -> None:
        await self._ensure_blocks("#drivers .driver", "#add_driver", len(payload.drivers))
        blocks = self.page.locator("#drivers .driver")
        for index, driver in enumerate(payload.drivers):
            block = blocks.nth(index)
            await block.locator('[name$="[name]"]').fill(driver.name)
            await block.locator('[name$="[licence]"]').fill(driver.licence)
            await block.locator('[name$="[licence_date]"]').fill(
                driver.licence_date
            )
            await block.locator('[name$="[experience]"]').fill(driver.experience)

    async def _fill_route(self, payload: GibddPayloadDTO) -> None:
        route = payload.route
        await self._fill_named("address_start", route.address_start)
        await self._ensure_blocks(
            ".stop-point-container .stop-point",
            "#add_stop_point",
            len(route.stop_points),
        )
        for index, stop in enumerate(route.stop_points):
            for field_name, value in (
                ("address", stop.address),
                ("time", stop.time),
                ("goal", stop.goal),
            ):
                locator = self.page.locator(
                    f'[name="stop_points[{index}][{field_name}]"]'
                )
                if field_name == "time":
                    await self._set_input_value(
                        f"stop_points[{index}][{field_name}]", value
                    )
                else:
                    await locator.fill(value)
        await self._select_or_add(
            "route_region_code",
            route.route_region_code,
            route.route_region_name,
        )
        await self.page.wait_for_timeout(500)
        await self._select_or_add("district", route.district, route.district)
        await self.page.wait_for_timeout(500)
        await self._fill_named("roads", route.roads)
        await self._fill_named("distance", route.distance)
        await self._fill_named("schedule", route.schedule or route.roads)
        await self._fill_named("duration", route.duration)

    async def _fill_named(self, name: str, value: str) -> None:
        locator = self.page.locator(
            f'input[name="{name}"], textarea[name="{name}"], select[name="{name}"]'
        ).first
        tag = await locator.evaluate("(element) => element.tagName.toLowerCase()")
        if tag == "select":
            try:
                await locator.select_option(value=value)
            except Exception:
                await locator.select_option(label=value)
        else:
            await locator.fill(value)

    async def _set_input_value(self, name: str, value: str) -> None:
        locator = self.page.locator(f'[name="{name}"]').first
        await locator.wait_for(state="attached")
        await self.page.evaluate(
            """([selector, nextValue]) => {
              const input = document.querySelector(selector);
              input.focus();
              input.value = String(nextValue);
              for (const eventName of ['input', 'keyup', 'change', 'blur']) {
                input.dispatchEvent(new Event(eventName, {bubbles: true}));
              }
            }""",
            [f'[name="{name}"]', value],
        )

    async def _select_or_add(
        self, name: str, value: str, label: str | None = None
    ) -> None:
        locator = self.page.locator(f'[name="{name}"]').first
        await locator.wait_for(state="attached")
        await self.page.evaluate(
            """([selector, nextValue, nextLabel]) => {
              const select = document.querySelector(selector);
              let option = Array.from(select.options)
                .find(item => item.value === String(nextValue));
              if (!option) {
                option = new Option(nextLabel || String(nextValue),
                                    String(nextValue), true, true);
                select.add(option);
              }
              select.value = String(nextValue);
              select.dispatchEvent(new Event('change', {bubbles: true}));
            }""",
            [f'[name="{name}"]', value, label],
        )

    async def _ensure_blocks(
        self, block_selector: str, add_selector: str, required: int
    ) -> None:
        blocks = self.page.locator(block_selector)
        attempts = 0
        while await blocks.count() < required:
            attempts += 1
            if attempts > 10:
                raise SubmissionError(
                    f"Could not create required dynamic blocks: {block_selector}"
                )
            add = self.page.locator(add_selector).first
            await add.wait_for(state="attached")
            await add.evaluate(
                """button => {
                  button.setAttribute('type', 'button');
                  button.dispatchEvent(new MouseEvent(
                    'click', {bubbles: true, cancelable: true, view: window}
                  ));
                }"""
            )
            await self.page.wait_for_timeout(500)

    @staticmethod
    def _form_selector(action: str) -> str:
        without_slash = action.rstrip("/")
        return (
            f'form[action="{without_slash}/"], '
            f'form[action="{without_slash}"]'
        )

    async def _click_form_next(self, action: str) -> None:
        selector = self._form_selector(action)
        form = self.page.locator(selector).first
        button = form.locator("button.u-form__sbt:not(.grey)").first
        await button.click(no_wait_after=True)

    async def _wait_for_form(self, action: str, label: str) -> None:
        selector = self._form_selector(action)
        target = urljoin(self.config.base_url, action)
        for attempt in range(1, 4):
            try:
                await self.page.locator(selector).first.wait_for(
                    state="attached",
                    timeout=self.config.timeout_ms,
                )
                return
            except Exception:
                if attempt < 3:
                    await self.page.goto(target, wait_until="domcontentloaded")
        raise SubmissionError(
            f"Не удалось открыть форму {label}: {await self.current_url()}"
        )

    async def _next(
        self,
        current_action: str,
        next_action: str,
        label: str,
    ) -> None:
        await self._click_form_next(current_action)
        await self._wait_for_form(next_action, label)

    @staticmethod
    def _token_from_url(value: str) -> str | None:
        tokens = parse_qs(urlparse(value).query).get("token")
        return tokens[0] if tokens else None
