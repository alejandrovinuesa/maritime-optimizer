"""Human-readable names for ids shown in the UI (the ids stay the keys everywhere else)."""

VESSELS = {
    "panamax_bulker": "Panamax bulk carrier",
    "neopanamax_container": "Neopanamax container ship",
    "aframax_lng_tanker": "Aframax LNG tanker",
    "iceclass_bulker": "Ice-class bulk carrier",
    "mpv_general_cargo": "Multipurpose general cargo ship",
    "lr2_lng_tanker": "LR2 LNG tanker",
}

VESSEL_TYPES = {"bulk_carrier": "Bulk carrier", "tanker": "Tanker", "container": "Container ship",
                "general_cargo": "General cargo"}

FUELS = {"HFO": "Heavy fuel oil (HFO)", "MGO": "Marine gas oil (MGO)", "LNG": "LNG",
         "BioLNG": "Bio-LNG", "HVO": "HVO biodiesel", "Methanol_fossil": "Fossil methanol",
         "E-Methanol": "E-methanol"}


def humanize(value: str) -> str:
    """'some_snake_case' -> 'Some snake case'."""
    return value.replace("_", " ").capitalize()


def vessel_label(vessel_id: str) -> str:
    return VESSELS.get(vessel_id) or humanize(vessel_id)


def vessel_type_label(value: str) -> str:
    return VESSEL_TYPES.get(value) or humanize(value)


def fuel_label(fuel: str) -> str:
    return FUELS.get(fuel) or humanize(fuel)
