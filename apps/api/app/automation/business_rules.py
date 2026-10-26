"""Deterministic business rules applied before generation and submission."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, time

from .exceptions import BusinessRuleViolation
from .schemas import (
    BusinessRulesResultDTO,
    CarrierProfileDTO,
    DriverProfileDTO,
    ExistingAllocationDTO,
    RuleIssueDTO,
    RuleSeverity,
    TripDraftDTO,
    VehicleProfileDTO,
)


class BusinessRulesService:
    """Evaluate rules without database or framework dependencies."""

    DEFAULT_END_TIME = time(18, 0)
    CAPACITY_THRESHOLD = 55

    def resolve_carrier(self, profile: CarrierProfileDTO) -> CarrierProfileDTO:
        """Return the tenant profile carrier and never inspect source documents."""

        missing = profile.missing_onboarding_fields()
        if missing:
            raise BusinessRuleViolation(
                "Carrier onboarding is incomplete",
                issues=[
                    RuleIssueDTO(
                        code="carrier.profile_incomplete",
                        severity=RuleSeverity.BLOCKING,
                        field="carrier_profile",
                        message=(
                            "Заполните профиль перевозчика: " + ", ".join(missing)
                        ),
                    )
                ],
            )
        return profile

    def propose_end_datetime(self, start: datetime) -> datetime:
        """Suggest 18:00; the caller must still ask the user to confirm it."""

        return datetime.combine(start.date(), self.DEFAULT_END_TIME, start.tzinfo)

    @staticmethod
    def intervals_overlap(
        first_start: datetime,
        first_end: datetime,
        second_start: datetime,
        second_end: datetime,
    ) -> bool:
        """Half-open intervals permit a resource to start when another ends."""

        return first_start < second_end and second_start < first_end

    def evaluate(
        self,
        *,
        draft: TripDraftDTO,
        carrier_profile: CarrierProfileDTO,
        vehicles: Iterable[VehicleProfileDTO],
        drivers: Iterable[DriverProfileDTO],
        existing_allocations: Iterable[ExistingAllocationDTO] = (),
    ) -> BusinessRulesResultDTO:
        issues: list[RuleIssueDTO] = []
        required_count = 2 if draft.children_count > self.CAPACITY_THRESHOLD else 1

        if carrier_profile.tenant_id != draft.tenant_id:
            issues.append(
                RuleIssueDTO(
                    code="carrier.tenant_mismatch",
                    severity=RuleSeverity.BLOCKING,
                    field="carrier_profile.tenant_id",
                    message="Профиль перевозчика принадлежит другой компании.",
                )
            )

        missing_profile = carrier_profile.missing_onboarding_fields()
        if missing_profile:
            issues.append(
                RuleIssueDTO(
                    code="carrier.profile_incomplete",
                    severity=RuleSeverity.BLOCKING,
                    field="carrier_profile",
                    message=(
                        "До продолжения заполните профиль перевозчика: "
                        + ", ".join(missing_profile)
                    ),
                )
            )

        if draft.date_end is None:
            suggested = self.propose_end_datetime(draft.date_start)
            issues.append(
                RuleIssueDTO(
                    code="trip.end_time_required",
                    severity=RuleSeverity.QUESTION,
                    field="date_end",
                    message=(
                        "Время окончания поездки не указано. "
                        "Подтвердите время окончания."
                    ),
                    suggested_value=suggested.strftime("%Y-%m-%d %H:%M"),
                )
            )

        selected_vehicles = self._resolve_selected(
            draft.selected_vehicle_ids,
            vehicles,
            resource_name="vehicle",
            issues=issues,
        )
        selected_drivers = self._resolve_selected(
            draft.selected_driver_ids,
            drivers,
            resource_name="driver",
            issues=issues,
        )

        self._validate_distinct_ids(
            draft.selected_vehicle_ids, "vehicle", "Автобусы", issues
        )
        self._validate_distinct_ids(
            draft.selected_driver_ids, "driver", "Водители", issues
        )

        if len(selected_vehicles) < required_count:
            issues.append(
                RuleIssueDTO(
                    code="vehicle.insufficient_count",
                    severity=RuleSeverity.BLOCKING,
                    field="selected_vehicle_ids",
                    message=(
                        f"Для {draft.children_count} детей требуется минимум "
                        f"{required_count} автобус(а)."
                    ),
                )
            )
        if len(selected_drivers) < required_count:
            issues.append(
                RuleIssueDTO(
                    code="driver.insufficient_count",
                    severity=RuleSeverity.BLOCKING,
                    field="selected_driver_ids",
                    message=(
                        f"Для {draft.children_count} детей требуется минимум "
                        f"{required_count} водитель(я)."
                    ),
                )
            )
        if len(selected_vehicles) != len(selected_drivers):
            issues.append(
                RuleIssueDTO(
                    code="resource.count_mismatch",
                    severity=RuleSeverity.BLOCKING,
                    field="selected_vehicle_ids",
                    message="Количество автобусов и водителей должно совпадать.",
                )
            )

        end_for_validity = draft.date_end or draft.date_start
        for vehicle in selected_vehicles:
            if not vehicle.active or vehicle.blocked:
                issues.append(
                    RuleIssueDTO(
                        code="vehicle.unavailable",
                        severity=RuleSeverity.BLOCKING,
                        field=f"vehicles.{vehicle.id}",
                        message=f"Автобус {vehicle.number} недоступен.",
                    )
                )
            # The validity end date is exclusive by the approved business rule.
            if end_for_validity.date() >= vehicle.diagnostic_card_valid_until:
                issues.append(
                    RuleIssueDTO(
                        code="vehicle.diagnostic_card_expired",
                        severity=RuleSeverity.BLOCKING,
                        field=f"vehicles.{vehicle.id}.diagnostic_card_valid_until",
                        message=(
                            f"Диагностическая карта автобуса {vehicle.number} "
                            f"не действует на дату поездки."
                        ),
                    )
                )

        for driver in selected_drivers:
            if not driver.active or driver.blocked:
                issues.append(
                    RuleIssueDTO(
                        code="driver.unavailable",
                        severity=RuleSeverity.BLOCKING,
                        field=f"drivers.{driver.id}",
                        message=f"Водитель {driver.name} недоступен.",
                    )
                )

        if draft.date_end is not None:
            self._validate_conflicts(
                draft=draft,
                allocations=existing_allocations,
                vehicle_ids=set(draft.selected_vehicle_ids),
                driver_ids=set(draft.selected_driver_ids),
                issues=issues,
            )

        valid = not any(
            issue.severity in {RuleSeverity.BLOCKING, RuleSeverity.QUESTION}
            for issue in issues
        )
        return BusinessRulesResultDTO(
            valid=valid,
            required_vehicle_count=required_count,
            required_driver_count=required_count,
            carrier_key=carrier_profile.carrier_key,
            issues=issues,
        )

    def ensure_valid(self, result: BusinessRulesResultDTO) -> None:
        if result.valid:
            return
        raise BusinessRuleViolation(
            "Business rules rejected the trip", issues=result.issues
        )

    @staticmethod
    def _resolve_selected(
        selected_ids: Iterable[str],
        available: Iterable[VehicleProfileDTO] | Iterable[DriverProfileDTO],
        *,
        resource_name: str,
        issues: list[RuleIssueDTO],
    ) -> list[VehicleProfileDTO] | list[DriverProfileDTO]:
        by_id = {item.id: item for item in available}
        resolved = []
        for resource_id in dict.fromkeys(selected_ids):
            resource = by_id.get(resource_id)
            if resource is None:
                issues.append(
                    RuleIssueDTO(
                        code=f"{resource_name}.not_found",
                        severity=RuleSeverity.BLOCKING,
                        field=f"selected_{resource_name}_ids",
                        message=f"Ресурс {resource_id} отсутствует в профиле компании.",
                    )
                )
                continue
            resolved.append(resource)
        return resolved

    @staticmethod
    def _validate_distinct_ids(
        ids: list[str],
        resource_name: str,
        human_name: str,
        issues: list[RuleIssueDTO],
    ) -> None:
        if len(ids) != len(set(ids)):
            issues.append(
                RuleIssueDTO(
                    code=f"{resource_name}.duplicate",
                    severity=RuleSeverity.BLOCKING,
                    field=f"selected_{resource_name}_ids",
                    message=f"{human_name} в назначении не должны повторяться.",
                )
            )

    def _validate_conflicts(
        self,
        *,
        draft: TripDraftDTO,
        allocations: Iterable[ExistingAllocationDTO],
        vehicle_ids: set[str],
        driver_ids: set[str],
        issues: list[RuleIssueDTO],
    ) -> None:
        assert draft.date_end is not None
        for allocation in allocations:
            if allocation.tenant_id != draft.tenant_id:
                continue
            if not self.intervals_overlap(
                draft.date_start,
                draft.date_end,
                allocation.date_start,
                allocation.date_end,
            ):
                continue
            for vehicle_id in sorted(vehicle_ids.intersection(allocation.vehicle_ids)):
                issues.append(
                    RuleIssueDTO(
                        code="vehicle.schedule_conflict",
                        severity=RuleSeverity.BLOCKING,
                        field=f"vehicles.{vehicle_id}",
                        message="Автобус уже назначен на пересекающуюся поездку.",
                        conflicting_trip_id=allocation.trip_id,
                    )
                )
            for driver_id in sorted(driver_ids.intersection(allocation.driver_ids)):
                issues.append(
                    RuleIssueDTO(
                        code="driver.schedule_conflict",
                        severity=RuleSeverity.BLOCKING,
                        field=f"drivers.{driver_id}",
                        message="Водитель уже назначен на пересекающуюся поездку.",
                        conflicting_trip_id=allocation.trip_id,
                    )
                )
