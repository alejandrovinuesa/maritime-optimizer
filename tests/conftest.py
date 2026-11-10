"""Shared fixtures: one maritime graph per test session and two reference vessels."""

import pytest

from maritime_optimizer import (
    FuelType, MaritimeGraph, OptimizationRequest, Vessel, VesselType, ensure_geodata,
    has_geodata,
)


@pytest.fixture(scope="session")
def graph() -> MaritimeGraph:
    if not has_geodata():
        assert ensure_geodata(verbose=False), "Natural Earth download failed"
    return MaritimeGraph()


@pytest.fixture(scope="session")
def panamax() -> Vessel:
    return Vessel(imo_number="0", name="Panamax", vessel_type=VesselType.BULK_CARRIER,
                  gt=43_000, dwt=76_000, design_speed=14.5, mcr=9_800, sfoc_base=175.0,
                  fuel_type=FuelType.HFO)


@pytest.fixture(scope="session")
def container() -> Vessel:
    return Vessel(imo_number="0", name="Container", vessel_type=VesselType.CONTAINER,
                  gt=80_000, dwt=95_000, design_speed=22.0, mcr=42_000, sfoc_base=170.0,
                  fuel_type=FuelType.MGO)


@pytest.fixture(scope="session")
def valencia_rotterdam(graph, panamax):
    """Reference intra-EU voyage used by several arithmetic checks."""
    from maritime_optimizer import optimize_voyage
    return optimize_voyage(graph, OptimizationRequest(
        vessel=panamax, origin_port="ESVLC", destination_port="NLRTM",
        eua_price=65, cargo_tons=68_000, max_transit_hours=600))
