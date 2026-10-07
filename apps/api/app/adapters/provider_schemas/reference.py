"""Schemas for raw LTA responses and normalized LTA, MRT, MOE and rail snapshots."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.adapters.provider_schemas.common import ProviderPayload


class LtaBusStopSnapshotRow(ProviderPayload):
    stop_code: str = Field(min_length=1)
    description: str
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    road_name: str
    services: list[str] = Field(default_factory=list)


class LtaDataMallBusStop(ProviderPayload):
    stop_code: str = Field(alias="BusStopCode", min_length=1)
    description: str = Field(alias="Description")
    latitude: float = Field(alias="Latitude", ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(alias="Longitude", ge=-180, le=180, allow_inf_nan=False)
    road_name: str = Field(default="", alias="RoadName")


class LtaBusRouteSnapshotRow(ProviderPayload):
    service_no: str = Field(min_length=1)
    operator: str = ""
    direction: Literal[1, 2]
    stop_sequence: int = Field(ge=1)
    bus_stop_code: str = Field(min_length=1)
    distance_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    wd_first_bus: str = ""
    wd_last_bus: str = ""
    sat_first_bus: str = ""
    sat_last_bus: str = ""
    sun_first_bus: str = ""
    sun_last_bus: str = ""


class LtaDataMallBusRoute(ProviderPayload):
    service_no: str = Field(alias="ServiceNo", min_length=1)
    operator: str = Field(default="", alias="Operator")
    direction: Literal[1, 2] = Field(alias="Direction")
    stop_sequence: int = Field(alias="StopSequence", ge=1)
    bus_stop_code: str = Field(alias="BusStopCode", min_length=1)
    distance_km: float | None = Field(default=None, alias="Distance", ge=0, allow_inf_nan=False)
    wd_first_bus: str = Field(default="", alias="WD_FirstBus")
    wd_last_bus: str = Field(default="", alias="WD_LastBus")
    sat_first_bus: str = Field(default="", alias="SAT_FirstBus")
    sat_last_bus: str = Field(default="", alias="SAT_LastBus")
    sun_first_bus: str = Field(default="", alias="SUN_FirstBus")
    sun_last_bus: str = Field(default="", alias="SUN_LastBus")


class LtaBusServiceSnapshotRow(ProviderPayload):
    service_no: str = Field(min_length=1)
    operator: str = ""
    direction: Literal[1, 2]
    category: str = ""
    origin_code: str = ""
    destination_code: str = ""
    loop_desc: str = ""
    am_peak_freq: str | None = None
    am_offpeak_freq: str | None = None
    pm_peak_freq: str | None = None
    pm_offpeak_freq: str | None = None


class LtaDataMallBusService(ProviderPayload):
    service_no: str = Field(alias="ServiceNo", min_length=1)
    operator: str = Field(default="", alias="Operator")
    direction: Literal[1, 2] = Field(alias="Direction")
    category: str = Field(default="", alias="Category")
    origin_code: str = Field(default="", alias="OriginCode")
    destination_code: str = Field(default="", alias="DestinationCode")
    loop_desc: str = Field(default="", alias="LoopDesc")
    am_peak_freq: str = Field(default="", alias="AM_Peak_Freq")
    am_offpeak_freq: str = Field(default="", alias="AM_Offpeak_Freq")
    pm_peak_freq: str = Field(default="", alias="PM_Peak_Freq")
    pm_offpeak_freq: str = Field(default="", alias="PM_Offpeak_Freq")


class LtaDataMallServiceAtStop(ProviderPayload):
    service_no: str = Field(alias="ServiceNo", min_length=1)


class LtaDataMallBusStopsResponse(ProviderPayload):
    value: list[LtaDataMallBusStop]


class LtaDataMallBusRoutesResponse(ProviderPayload):
    value: list[LtaDataMallBusRoute]


class LtaDataMallBusServicesResponse(ProviderPayload):
    value: list[LtaDataMallBusService]


class LtaDataMallServicesAtStopResponse(ProviderPayload):
    value: list[LtaDataMallServiceAtStop]


class MrtStationSnapshotRow(ProviderPayload):
    station_code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    lines: list[str] = Field(min_length=1)
    is_interchange: bool


class MoeSchoolSnapshotRow(ProviderPayload):
    school_name: str = Field(min_length=1)
    level: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    address: str = Field(min_length=1)


class RailStationSnapshotRow(ProviderPayload):
    station_name: str = Field(min_length=1)
    codes: list[str] = Field(min_length=1)
    lines: list[str] = Field(min_length=1)
    is_interchange: bool
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    active: bool = True


class RailStationSnapshot(ProviderPayload):
    source: str
    version: str
    stations: list[RailStationSnapshotRow]


class RailEdgeSnapshotRow(ProviderPayload):
    from_node: str = Field(min_length=1)
    to_node: str = Field(min_length=1)
    line: str = Field(min_length=1)
    edge_type: Literal["ride", "transfer"]
    estimated_minutes: float = Field(gt=0, allow_inf_nan=False)
