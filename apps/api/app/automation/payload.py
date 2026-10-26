"""Build and validate the canonical browser-worker payload."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from pydantic import ValidationError

from .business_rules import BusinessRulesService
from .exceptions import BusinessRuleViolation, PayloadValidationError
from .schemas import (
    ApplicantDTO,
    GibddPayloadDTO,
    PartyDTO,
    PayloadBuildInputDTO,
    PortalDriverDTO,
    PortalTransportationDTO,
    PortalVehicleDTO,
    display_date,
    portal_datetime,
)


class PayloadBuilderService:
    """Convert confirmed domain data into the sole SubmissionWorker contract."""

    def __init__(
        self, business_rules: BusinessRulesService | None = None
    ) -> None:
        self._business_rules = business_rules or BusinessRulesService()

    def build(self, source: PayloadBuildInputDTO) -> GibddPayloadDTO:
        if source.carrier_profile.tenant_id != source.tenant_id:
            raise PayloadValidationError(
                "Carrier profile belongs to another tenant"
            )

        try:
            profile = self._business_rules.resolve_carrier(source.carrier_profile)
        except BusinessRuleViolation as exc:
            raise PayloadValidationError(str(exc)) from exc

        # resolve_carrier guarantees these fields. Casts make the invariant
        # explicit without accepting document-extracted carrier values.
        applicant = cast(ApplicantDTO, profile.applicant)
        carrier = cast(PartyDTO, profile.party)
        carrier_key = cast(str, profile.carrier_key)

        if any(not vehicle.active or vehicle.blocked for vehicle in source.vehicles):
            raise PayloadValidationError("Inactive or blocked vehicle selected")
        if any(not driver.active or driver.blocked for driver in source.drivers):
            raise PayloadValidationError("Inactive or blocked driver selected")
        invalid_cards = [
            vehicle.number
            for vehicle in source.vehicles
            if source.transportation.date_end.date()
            >= vehicle.diagnostic_card_valid_until
        ]
        if invalid_cards:
            raise PayloadValidationError(
                "Diagnostic card is not valid for trip: " + ", ".join(invalid_cards)
            )

        try:
            return GibddPayloadDTO(
                tenant_id=source.tenant_id,
                carrier_key=carrier_key,
                applicant=applicant,
                transportation=PortalTransportationDTO(
                    region_codes=source.transportation.region_codes,
                    children_count=source.transportation.children_count,
                    goal=source.transportation.goal,
                    date_start=portal_datetime(source.transportation.date_start),
                    date_end=portal_datetime(source.transportation.date_end),
                ),
                customer=source.customer,
                carrier=carrier,
                vehicles=[
                    PortalVehicleDTO(
                        brand_and_model=vehicle.brand_and_model,
                        number=vehicle.number,
                        diagnostic_card_info=vehicle.diagnostic_card_info,
                        navigator_info=vehicle.navigator_info,
                    )
                    for vehicle in source.vehicles
                ],
                drivers=[
                    PortalDriverDTO(
                        name=driver.name,
                        licence=driver.licence,
                        licence_date=display_date(driver.licence_issue_date),
                        experience=driver.experience,
                    )
                    for driver in source.drivers
                ],
                route=source.route,
                files=source.files,
            )
        except ValidationError as exc:
            raise PayloadValidationError(
                f"Invalid GIBDD payload: {exc}"
            ) from exc

    def validate(self, payload: GibddPayloadDTO | dict[str, Any]) -> GibddPayloadDTO:
        try:
            if isinstance(payload, GibddPayloadDTO):
                # Revalidation also catches invalid data introduced via
                # model_construct or future deserialization layers.
                payload = payload.model_dump(mode="python")
            return GibddPayloadDTO.model_validate(payload)
        except ValidationError as exc:
            raise PayloadValidationError(
                f"Invalid GIBDD payload: {exc}"
            ) from exc

    @staticmethod
    def json_schema() -> dict[str, Any]:
        """Return a closed Draft 2020-12 schema for storage and worker checks."""

        schema = deepcopy(GibddPayloadDTO.model_json_schema())
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = "https://buscript.local/schemas/gibdd-payload.json"
        schema["title"] = "Buscript GIBDD transportation notification payload"

        # Pydantic model validators enforce the same rules at runtime.  Add the
        # conditional requirements here so non-Python workers can reject
        # structurally incomplete party objects before browser startup.
        party_schema = schema.get("$defs", {}).get("PartyDTO")
        if isinstance(party_schema, dict):
            party_schema["allOf"] = [
                {
                    "if": {
                        "properties": {
                            "type": {"enum": ["legal", "ip"]},
                        },
                        "required": ["type"],
                    },
                    "then": {
                        "required": [
                            "entity_name",
                            "entity_address",
                            "entity_location",
                            "inn",
                        ]
                    },
                },
                {
                    "if": {
                        "properties": {"type": {"const": "physical"}},
                        "required": ["type"],
                    },
                    "then": {
                        "required": ["name", "address"],
                        "properties": {"inn": {"type": "null"}},
                    },
                },
            ]
        return schema
