from __future__ import annotations


SKIP_DEVICE_NAME_TOKENS = ("NULL",)
DEPRIORITIZED_INPUT_NAME_TOKENS = ("VOICEMOD", "VIRTUAL")
DEPRIORITIZED_OUTPUT_NAME_TOKENS = ("FAKE", "VOICEMOD", "VIRTUAL")


def usable_devices(devices: list[dict]) -> list[dict]:
    return [
        device
        for device in devices
        if all(token not in device["name"].upper() for token in SKIP_DEVICE_NAME_TOKENS)
    ]


def preferred_device(devices: list[dict], *, deprioritized_tokens: tuple[str, ...]) -> dict | None:
    usable = usable_devices(devices)
    if not usable:
        return None
    preferred = [
        device
        for device in usable
        if all(token not in device["name"].upper() for token in deprioritized_tokens)
    ]
    return preferred[0] if preferred else usable[0]


def resolve_preferred_or_fallback(
    devices: list[dict],
    preferred_name: str,
    *,
    deprioritized_tokens: tuple[str, ...],
) -> tuple[dict | None, str | None]:
    usable = usable_devices(devices)
    if not usable:
        return None, None
    if preferred_name:
        for device in usable:
            if device["name"] == preferred_name:
                return device, None
        fallback = preferred_device(usable, deprioritized_tokens=deprioritized_tokens)
        if fallback:
            return fallback, preferred_name
    return preferred_device(usable, deprioritized_tokens=deprioritized_tokens), None
