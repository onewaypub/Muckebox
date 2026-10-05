# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings chosen on the parents' page, kept in ``DATA_DIR/settings.json``.

The file holds the room, an optional speaker address for other networks,
the volume limit and step, and the parents' PIN as a salted scrypt hash.
A PIN that Muckebox generated itself is also kept in plain text until the
parents change it, so that it can be shown in the log on every start.

Writers (the web server and ``python -m muckebox.admin reset-pin``) take an
exclusive lock and re-read the file before changing it. The server notices
changes made by other processes within a second.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import time as clock_time
from pathlib import Path
from typing import Any

from muckebox.localtime import load_zone
from muckebox.schedule import (
    DEFAULT_WAKE,
    Schedule,
    WindowOrderError,
    format_time,
    parse_schedule,
    parse_time,
    schedule_to_json,
)
from muckebox.storage import atomic_write

log = logging.getLogger(__name__)

SCHEMA = 1
FILE_NAME = "settings.json"
LOCK_NAME = "settings.lock"
DEFAULT_MAX_VOLUME = 25
DEFAULT_VOLUME_STEP = 3
MIN_PIN_LENGTH = 4
MAX_PIN_LENGTH = 64
#: Example values that must never work as a real PIN.
PLACEHOLDER_PINS = frozenset({"change-me", "changeme", "1234", "0000"})
GENERATED_PIN_DIGITS = 6
#: scrypt cost parameters (about 16 MiB and 50 ms per check).
SCRYPT = {"n": 2**14, "r": 8, "p": 1}
RELOAD_INTERVAL = 1.0  # seconds between checks for changes by other processes
GAMES = ("freeze_dance", "sound_quiz", "move_like", "breathing")
DEFAULT_SLEEP_MINUTES = 30
SLEEP_MINUTES = (5, 90)
DEFAULT_GAME_MINUTES = 15
GAME_MINUTES = (5, 60)
DEFAULT_TAP_COOLDOWN = 5
TAP_COOLDOWN = (0, 30)  # seconds; 0 = off
DEFAULT_IDLE_MINUTES = 60
IDLE_MINUTES = (0, 240)  # 0 = off
#: Layouts of the kids view: "small" (0-6 years) and "big" (7-14 years).
PROFILES = ("small", "big")
#: Pictures of the light buttons, and how many there may be.
LIGHT_PICTURES = ("sun", "book", "star", "moon", "bulb")
MAX_LIGHT_SLOTS = 3
#: What the sleep timer does with the lights at its end (or a scene id).
SLEEP_LIGHTS = ("keep", "off")
_HUE_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_HUE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_TILE_ID_RE = re.compile(r"^t[0-9a-f]{8,32}$")

# RFC 1123 host name: dot-separated labels of letters, digits and hyphens.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_HEX_RE = re.compile(r"^[0-9a-f]{16,128}$")
# At most two expensive scrypt checks at the same time.
_hashing = threading.BoundedSemaphore(2)


class SettingsError(Exception):
    """An invalid value; ``code`` is the stable error key for the API."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class SettingsFileError(Exception):
    """The settings file cannot be used (e.g. written by a newer version)."""


@dataclass(frozen=True)
class PinRecord:
    salt: str
    hash: str
    n: int
    r: int
    p: int
    #: The PIN in plain text, only while it is the generated one.
    generated: str | None = field(default=None, repr=False)

    @property
    def version(self) -> str:
        """Changes whenever the PIN changes (used to bind sessions)."""
        return self.hash


@dataclass(frozen=True)
class SleepTimerSettings:
    enabled: bool = False
    minutes: int = DEFAULT_SLEEP_MINUTES
    #: Until when the tiles stay locked afterwards on days without a window.
    wake: clock_time = DEFAULT_WAKE
    #: The lights at the end: "keep", "off" or the id of a Hue scene.
    lights: str = "keep"


@dataclass(frozen=True)
class GameSetting:
    enabled: bool = False
    level: int = 2  # 1 small (2-3 years), 2 middle (4-5), 3 big (6+)


@dataclass(frozen=True)
class GameSettings:
    daily_minutes: int = DEFAULT_GAME_MINUTES
    #: The tile that plays the music for the freeze dance.
    dance_tile: str | None = None
    items: dict[str, GameSetting] = field(
        default_factory=lambda: {game: GameSetting() for game in GAMES}
    )

    def enabled(self, game: str) -> bool:
        setting = self.items.get(game)
        return bool(setting and setting.enabled)


@dataclass(frozen=True)
class ControlSettings:
    #: Seconds after a tile start during which no other tile starts.
    tap_cooldown: int = DEFAULT_TAP_COOLDOWN
    #: Minutes of playback without a tap on the tablet before Muckebox fades
    #: and pauses.
    idle_minutes: int = DEFAULT_IDLE_MINUTES
    #: Layout of the kids view.
    profile: str = "small"
    #: Previous/next on the "small" layout ("big" always has them).
    skip_buttons: bool = False
    #: "small": after a tile starts, show it big with a home button.
    now_view: bool = True
    #: A soft "plop" from the tablet when a tap starts something.
    tap_sound: bool = True


@dataclass(frozen=True)
class HueBridge:
    ip: str
    id: str
    name: str
    #: The application key from pairing and the pinned certificate: secrets
    #: that never leave the server.
    key: str
    fingerprint: str


@dataclass(frozen=True)
class HueSlot:
    scene: str
    picture: str


@dataclass(frozen=True)
class HueSettings:
    bridge: HueBridge | None = None
    #: The room or zone whose lights the kids switch.
    room: str | None = None
    slots: tuple[HueSlot, ...] = ()


@dataclass(frozen=True)
class StoredSettings:
    room: str | None
    room_uid: str | None
    seed_ip: str | None
    max_volume: int
    volume_step: int
    pin: PinRecord
    #: IANA name chosen on the parents' page, or None (then TZ or the system).
    time_zone: str | None = None
    schedule: Schedule = field(default_factory=Schedule)
    sleep_timer: SleepTimerSettings = field(default_factory=lambda: SleepTimerSettings())
    games: GameSettings = field(default_factory=lambda: GameSettings())
    controls: ControlSettings = field(default_factory=lambda: ControlSettings())
    hue: HueSettings = field(default_factory=lambda: HueSettings())

    @property
    def configured(self) -> bool:
        return bool(self.room)

    @property
    def pin_generated(self) -> bool:
        return self.pin.generated is not None


# -- validation ------------------------------------------------------------------


def validate_pin(pin: object) -> str:
    if not isinstance(pin, str) or _CONTROL_RE.search(pin) or not _encodable(pin):
        raise SettingsError("pin_invalid")
    pin = pin.strip()
    if len(pin) < MIN_PIN_LENGTH or len(pin) > MAX_PIN_LENGTH:
        raise SettingsError("pin_too_short" if len(pin) < MIN_PIN_LENGTH else "pin_invalid")
    if pin.casefold() in PLACEHOLDER_PINS:
        raise SettingsError("pin_placeholder")
    return pin


def validate_seed_ip(value: object) -> str | None:
    """An IPv4 address or a host name (no port, no URL, no IPv6), or None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise SettingsError("seed_ip_invalid")
    value = value.strip()
    if not value:
        return None
    if re.fullmatch(r"[\d.]+", value):
        parts = value.split(".")
        if len(parts) == 4 and all(p.isdigit() and int(p) <= 255 for p in parts):
            return value
        raise SettingsError("seed_ip_invalid")
    if not _HOSTNAME_RE.match(value):
        raise SettingsError("seed_ip_invalid")
    return value


def validate_room(name: object) -> str:
    if not isinstance(name, str) or _CONTROL_RE.search(name):
        raise SettingsError("room_invalid")
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise SettingsError("room_invalid")
    return name


def validate_time_zone(name: object) -> str | None:
    if name is None or name == "":
        return None
    if load_zone(name) is None:
        raise SettingsError("time_zone_invalid")
    return str(name)


def validate_schedule(data: object) -> Schedule:
    try:
        return parse_schedule(data)
    except WindowOrderError:
        raise SettingsError("schedule_order_invalid") from None
    except ValueError:
        raise SettingsError("schedule_invalid") from None


def validate_sleep_timer(data: object) -> SleepTimerSettings:
    if not isinstance(data, dict):
        raise SettingsError("sleep_timer_invalid")
    enabled, minutes = data.get("enabled", False), data.get("minutes", DEFAULT_SLEEP_MINUTES)
    low, high = SLEEP_MINUTES
    if type(enabled) is not bool or type(minutes) is not int or not low <= minutes <= high:
        raise SettingsError("sleep_timer_invalid")
    try:
        wake = parse_time(data.get("wake", format_time(DEFAULT_WAKE)))
    except ValueError:
        raise SettingsError("sleep_timer_invalid") from None
    lights = data.get("lights", "keep")
    if not isinstance(lights, str) or not (lights in SLEEP_LIGHTS or _HUE_ID_RE.match(lights)):
        raise SettingsError("sleep_timer_invalid")
    return SleepTimerSettings(enabled, minutes, wake or DEFAULT_WAKE, lights)


def validate_games(data: object) -> GameSettings:
    if not isinstance(data, dict):
        raise SettingsError("games_invalid")
    minutes = data.get("daily_minutes", DEFAULT_GAME_MINUTES)
    tile = data.get("dance_tile")
    items = data.get("items", {})
    low, high = GAME_MINUTES
    if (
        type(minutes) is not int
        or not low <= minutes <= high
        or not (tile is None or (isinstance(tile, str) and _TILE_ID_RE.match(tile)))
        or not isinstance(items, dict)
        or set(items) - set(GAMES)
    ):
        raise SettingsError("games_invalid")
    settings = {}
    for game in GAMES:
        item = items.get(game, {})
        if not isinstance(item, dict):
            raise SettingsError("games_invalid")
        enabled, level = item.get("enabled", False), item.get("level", 2)
        if type(enabled) is not bool or type(level) is not int or not 1 <= level <= 3:
            raise SettingsError("games_invalid")
        settings[game] = GameSetting(enabled, level)
    return GameSettings(minutes, tile, settings)


def validate_controls(data: object) -> ControlSettings:
    if not isinstance(data, dict):
        raise SettingsError("controls_invalid")
    cooldown = data.get("tap_cooldown", DEFAULT_TAP_COOLDOWN)
    idle = data.get("idle_minutes", DEFAULT_IDLE_MINUTES)
    profile = data.get("profile", "small")
    skip = data.get("skip_buttons", False)
    now_view = data.get("now_view", True)
    tap_sound = data.get("tap_sound", True)
    if (
        type(cooldown) is not int
        or not TAP_COOLDOWN[0] <= cooldown <= TAP_COOLDOWN[1]
        or type(idle) is not int
        or not IDLE_MINUTES[0] <= idle <= IDLE_MINUTES[1]
        or profile not in PROFILES
        or type(skip) is not bool
        or type(now_view) is not bool
        or type(tap_sound) is not bool
    ):
        raise SettingsError("controls_invalid")
    return ControlSettings(cooldown, idle, profile, skip, now_view, tap_sound)


def controls_to_json(settings: ControlSettings) -> dict[str, Any]:
    return {
        "tap_cooldown": settings.tap_cooldown,
        "idle_minutes": settings.idle_minutes,
        "profile": settings.profile,
        "skip_buttons": settings.skip_buttons,
        "now_view": settings.now_view,
        "tap_sound": settings.tap_sound,
    }


def sleep_timer_to_json(settings: SleepTimerSettings) -> dict[str, Any]:
    return {
        "enabled": settings.enabled,
        "minutes": settings.minutes,
        "wake": format_time(settings.wake),
        "lights": settings.lights,
    }


def validate_hue_bridge(data: object) -> HueBridge | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise SettingsError("hue_invalid")
    name = data.get("name")
    try:
        ip = validate_seed_ip(data.get("ip"))
    except SettingsError:
        raise SettingsError("hue_invalid") from None
    if (
        ip is None
        or not isinstance(data.get("id"), str)
        or not _HUE_ID_RE.match(data["id"])
        or not isinstance(name, str)
        or not 1 <= len(name) <= 60
        or _CONTROL_RE.search(name)
        or not isinstance(data.get("key"), str)
        or not _HUE_KEY_RE.match(data["key"])
        or not isinstance(data.get("fingerprint"), str)
        or not _FINGERPRINT_RE.match(data["fingerprint"])
    ):
        raise SettingsError("hue_invalid")
    return HueBridge(ip, data["id"], name, data["key"], data["fingerprint"])


def validate_hue_choice(data: object) -> tuple[str | None, tuple[HueSlot, ...]]:
    """The room and the light buttons chosen on the parents' page."""
    if not isinstance(data, dict):
        raise SettingsError("hue_invalid")
    room, slots = data.get("room"), data.get("slots", [])
    if room is not None and not (isinstance(room, str) and _HUE_ID_RE.match(room)):
        raise SettingsError("hue_invalid")
    if not isinstance(slots, list) or len(slots) > MAX_LIGHT_SLOTS:
        raise SettingsError("hue_invalid")
    result = []
    for slot in slots:
        if (
            not isinstance(slot, dict)
            or not isinstance(slot.get("scene"), str)
            or not _HUE_ID_RE.match(slot["scene"])
            or slot.get("picture") not in LIGHT_PICTURES
        ):
            raise SettingsError("hue_invalid")
        result.append(HueSlot(slot["scene"], slot["picture"]))
    return room, tuple(result)


def validate_hue(data: object) -> HueSettings:
    if not isinstance(data, dict):
        raise SettingsError("hue_invalid")
    room, slots = validate_hue_choice(data)
    return HueSettings(validate_hue_bridge(data.get("bridge")), room, slots)


def hue_to_json(settings: HueSettings, secrets: bool = False) -> dict[str, Any]:
    """For settings.json (``secrets``) or for the parents' page (never the key)."""
    bridge = settings.bridge
    shown = None
    if bridge is not None:
        shown = {"ip": bridge.ip, "id": bridge.id, "name": bridge.name}
        if secrets:
            shown |= {"key": bridge.key, "fingerprint": bridge.fingerprint}
    return {
        "bridge": shown,
        "room": settings.room,
        "slots": [{"scene": slot.scene, "picture": slot.picture} for slot in settings.slots],
    }


def games_to_json(settings: GameSettings) -> dict[str, Any]:
    return {
        "daily_minutes": settings.daily_minutes,
        "dance_tile": settings.dance_tile,
        "items": {
            game: {"enabled": item.enabled, "level": item.level}
            for game, item in settings.items.items()
        },
    }


def validate_volume(max_volume: object, step: object) -> tuple[int, int]:
    # bool is an int in Python, and JSON 25.0 arrives as a float: both refused.
    if type(max_volume) is not int or not 1 <= max_volume <= 100:
        raise SettingsError("max_volume_invalid")
    if type(step) is not int or not 1 <= step <= max_volume:
        raise SettingsError("volume_step_invalid")
    return max_volume, step


# -- PIN hashing -------------------------------------------------------------------


def hash_pin(pin: str, params: dict[str, int] | None = None, generated: bool = False) -> PinRecord:
    params = params or SCRYPT
    salt = secrets.token_bytes(16)
    digest = _scrypt(pin, salt, params)
    return PinRecord(
        salt=salt.hex(),
        hash=digest.hex(),
        n=params["n"],
        r=params["r"],
        p=params["p"],
        generated=pin if generated else None,
    )


def check_pin(record: PinRecord, pin: str) -> bool:
    if not _encodable(pin):
        return False  # e.g. a lone surrogate from JSON: can never be the PIN
    digest = _scrypt(pin, bytes.fromhex(record.salt), {"n": record.n, "r": record.r, "p": record.p})
    return hmac.compare_digest(digest.hex(), record.hash)


def _scrypt(pin: str, salt: bytes, params: dict[str, int]) -> bytes:
    with _hashing:
        return hashlib.scrypt(
            pin.encode("utf-8"),
            salt=salt,
            n=params["n"],
            r=params["r"],
            p=params["p"],
            maxmem=64 * 1024 * 1024,
            dklen=32,
        )


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def generate_pin() -> str:
    return f"{secrets.randbelow(10**GENERATED_PIN_DIGITS):0{GENERATED_PIN_DIGITS}d}"


# -- reading the file ---------------------------------------------------------------

_KNOWN_SECTIONS = frozenset(
    {
        "schema",
        "sonos",
        "volume",
        "admin",
        "time",
        "schedule",
        "sleep_timer",
        "games",
        "controls",
        "hue",
    }
)


def _parse(data: dict[str, Any]) -> StoredSettings:
    """Check every value, so a hand-edited file can never loosen the limit."""
    try:
        sonos, volume, admin = data["sonos"], data["volume"], data["admin"]
        pin = admin["pin"] if isinstance(admin, dict) else None
        if not all(isinstance(part, dict) for part in (sonos, volume, admin, pin)):
            raise ValueError("unexpected structure")
        room = sonos.get("room")
        room = None if room is None else validate_room(room)
        room_uid = sonos.get("room_uid")
        if room_uid is not None and (
            not isinstance(room_uid, str)
            or not 1 <= len(room_uid) <= 64
            or _CONTROL_RE.search(room_uid)
        ):
            raise ValueError("invalid room_uid")
        seed_ip = validate_seed_ip(sonos.get("seed_ip"))
        max_volume, step = validate_volume(volume.get("max"), volume.get("step"))
        generated = admin.get("generated_pin")
        if generated is not None:
            generated = validate_pin(generated)
        record = PinRecord(
            salt=pin.get("salt"),
            hash=pin.get("hash"),
            n=pin.get("n"),
            r=pin.get("r"),
            p=pin.get("p"),
            generated=generated,
        )
    except (KeyError, SettingsError) as exc:
        raise ValueError(f"invalid settings ({exc})") from None
    _check_record(record)
    try:
        schedule = validate_schedule(data.get("schedule"))
        sleep_timer = validate_sleep_timer(data.get("sleep_timer", {}))
        games = validate_games(data.get("games", {}))
        controls = validate_controls(data.get("controls", {}))
        hue = validate_hue(data.get("hue", {}))
    except SettingsError as exc:
        raise ValueError(f"invalid settings ({exc})") from None
    return StoredSettings(
        room,
        room_uid,
        seed_ip,
        max_volume,
        step,
        record,
        time_zone=_parse_time_zone(data),
        schedule=schedule,
        sleep_timer=sleep_timer,
        games=games,
        controls=controls,
        hue=hue,
    )


def _parse_time_zone(data: dict[str, Any]) -> str | None:
    section = data.get("time")
    zone = section.get("zone") if isinstance(section, dict) else None
    # A zone this system does not know (e.g. from a newer database) is not a
    # reason to reject the whole file: TZ or the system zone apply instead.
    return zone if isinstance(zone, str) and load_zone(zone) is not None else None


def _check_record(record: PinRecord) -> None:
    for text in (record.salt, record.hash):
        if not isinstance(text, str) or not _HEX_RE.match(text):
            raise ValueError("invalid PIN hash")
    for value, low, high in ((record.n, 2, 2**20), (record.r, 1, 32), (record.p, 1, 16)):
        if type(value) is not int or not low <= value <= high:
            raise ValueError("invalid PIN hash parameters")
    # A power of two, and well within the memory limit of _scrypt().
    if record.n & (record.n - 1) or 128 * record.r * record.n > 32 * 1024 * 1024:
        raise ValueError("invalid PIN hash parameters")


# -- the store ------------------------------------------------------------------------


class SettingsStore:
    """Reads and writes the settings file; hands out immutable snapshots."""

    def __init__(
        self,
        data_dir: Path,
        *,
        scrypt: dict[str, int] | None = None,
        clock: Callable[[], float] = time.monotonic,
        create: bool = True,
    ) -> None:
        self.path = data_dir / FILE_NAME
        self._lock_path = data_dir / LOCK_NAME
        self._scrypt = scrypt or SCRYPT
        self._clock = clock
        self._mutex = threading.RLock()
        self._signature: tuple[int, int, int] | None = None
        self._failed_signature: tuple[int, int, int] | None = None
        # Sections this version does not know (written by a newer Muckebox):
        # kept as they are, so that going back and forth loses nothing.
        self._unknown_sections: dict[str, Any] = {}
        self._checked_at = 0.0
        #: Set when the file could not be read at startup and was moved aside.
        self.load_problem: str | None = None
        self._current = self._load(create)

    # -- reading ------------------------------------------------------------

    def current(self) -> StoredSettings:
        """The settings, re-read if another process changed the file."""
        now = self._clock()
        if now - self._checked_at >= RELOAD_INTERVAL:
            self._checked_at = now
            with self._mutex:
                signature = self._file_signature()
                if signature not in (self._signature, self._failed_signature):
                    try:
                        self._current = self._read()
                    except (OSError, ValueError, SettingsFileError) as exc:
                        # Logged once per version of the file, not every second.
                        self._failed_signature = signature
                        log.error(
                            "Could not re-read %s (%s); keeping the last settings", self.path, exc
                        )
                    else:
                        self._failed_signature = None
        return self._current

    def verify_pin(self, pin: object) -> bool:
        return self.check(pin) is not None

    def check(self, pin: object) -> str | None:
        """The version of the PIN that ``pin`` matched, or None.

        Callers bind sessions to this version, not to whatever is current
        after the (slow) check: a PIN change meanwhile must end them too.
        """
        if not isinstance(pin, str):
            return None
        record = self.current().pin
        return record.version if check_pin(record, pin.strip()) else None

    # -- changing -----------------------------------------------------------

    def set_room(self, room: str, room_uid: str | None, seed_ip: str | None) -> StoredSettings:
        room = validate_room(room)
        seed_ip = validate_seed_ip(seed_ip)
        return self._update(lambda s: replace(s, room=room, room_uid=room_uid, seed_ip=seed_ip))

    def set_time_zone(self, name: object) -> StoredSettings:
        zone = validate_time_zone(name)
        return self._update(lambda s: replace(s, time_zone=zone))

    def set_schedule(self, data: object) -> StoredSettings:
        schedule = validate_schedule(data)
        return self._update(lambda s: replace(s, schedule=schedule))

    def set_sleep_timer(self, data: object) -> StoredSettings:
        sleep_timer = validate_sleep_timer(data)
        return self._update(lambda s: replace(s, sleep_timer=sleep_timer))

    def set_games(self, data: object) -> StoredSettings:
        games = validate_games(data)
        return self._update(lambda s: replace(s, games=games))

    def set_controls(self, data: object) -> StoredSettings:
        controls = validate_controls(data)
        return self._update(lambda s: replace(s, controls=controls))

    def set_hue_bridge(self, bridge: HueBridge | None) -> StoredSettings:
        """A newly paired bridge (another bridge: the room and buttons start empty)."""

        def change(settings: StoredSettings) -> StoredSettings:
            same = bridge is not None and settings.hue.bridge is not None
            same = same and settings.hue.bridge.id == bridge.id
            hue = replace(settings.hue, bridge=bridge) if same else HueSettings(bridge=bridge)
            return replace(settings, hue=hue)

        return self._update(change)

    def set_hue(self, data: object) -> StoredSettings:
        room, slots = validate_hue_choice(data)
        return self._update(lambda s: replace(s, hue=replace(s.hue, room=room, slots=slots)))

    def set_volume(self, max_volume: object, step: object) -> StoredSettings:
        max_volume, step = validate_volume(max_volume, step)
        return self._update(lambda s: replace(s, max_volume=max_volume, volume_step=step))

    def change_pin(self, new_pin: object, expected_version: str | None = None) -> StoredSettings:
        """Set a new PIN; with ``expected_version``, only if the PIN is still that one."""
        pin = validate_pin(new_pin)
        record = hash_pin(pin, self._scrypt)

        def change(settings: StoredSettings) -> StoredSettings:
            if expected_version is not None and settings.pin.version != expected_version:
                raise SettingsError("pin_changed")  # e.g. reset-pin ran meanwhile
            return replace(settings, pin=record)

        return self._update(change)

    def reset_pin(self) -> str:
        """Set a new random PIN (shown in the log until changed) and return it."""
        pin = generate_pin()
        record = hash_pin(pin, self._scrypt, generated=True)
        self._update(lambda s: replace(s, pin=record))
        return pin

    def _update(self, change: Callable[[StoredSettings], StoredSettings]) -> StoredSettings:
        with self._mutex, self._file_lock():
            # Start from the file, not from memory: another process may have
            # changed it (e.g. a PIN reset from the command line).
            base = self._current
            if self.path.exists():
                try:
                    base = self._read()
                except ValueError as exc:
                    # Unusable (e.g. a broken hand edit): keep a copy, then
                    # replace it with the last good settings plus this change.
                    self._move_aside(exc)
            updated = change(base)
            self._write(updated)
            self._current = updated
            return updated

    # -- file handling ----------------------------------------------------------

    def _load(self, create: bool) -> StoredSettings:
        with self._mutex, self._file_lock(create):
            if self.path.exists():
                try:
                    return self._read()
                except OSError as exc:
                    # Not corrupt, just not ours to read (e.g. after "user:" was
                    # changed): never throw the settings away for that.
                    raise SettingsFileError(
                        f"{self.path} cannot be read ({exc.strerror or exc}); make sure it "
                        "belongs to the user Muckebox runs as"
                    ) from exc
                except ValueError as exc:
                    if not create:
                        raise SettingsFileError(f"{self.path} cannot be read: {exc}") from exc
                    self._move_aside(exc)
            if not create:
                raise SettingsFileError(f"{self.path} does not exist yet")
            pin = generate_pin()
            fresh = StoredSettings(
                room=None,
                room_uid=None,
                seed_ip=None,
                max_volume=DEFAULT_MAX_VOLUME,
                volume_step=DEFAULT_VOLUME_STEP,
                pin=hash_pin(pin, self._scrypt, generated=True),
            )
            self._write(fresh)
            return fresh

    def _read(self) -> StoredSettings:
        """Read and check the file; raises ValueError if its content is unusable."""
        signature = self._file_signature()
        data = json.loads(self.path.read_bytes())
        schema = data.get("schema") if isinstance(data, dict) else None
        if type(schema) is int and schema > SCHEMA:
            raise SettingsFileError(
                f"{self.path} was written by a newer Muckebox (schema {schema}); "
                "update Muckebox or restore a backup"
            )
        if schema != SCHEMA:
            raise ValueError(f"unsupported schema {schema!r}")
        settings = _parse(data)
        self._unknown_sections = {k: v for k, v in data.items() if k not in _KNOWN_SECTIONS}
        self._signature = signature
        return settings

    def _move_aside(self, reason: Exception) -> None:
        broken = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
        shutil.move(self.path, broken)
        self.load_problem = "settings_corrupt"
        log.error("%s could not be read (%s); moved to %s", self.path, reason, broken)

    def _write(self, settings: StoredSettings) -> None:
        pin = settings.pin
        data: dict[str, Any] = {
            **self._unknown_sections,
            "schema": SCHEMA,
            "sonos": {
                "room": settings.room,
                "room_uid": settings.room_uid,
                "seed_ip": settings.seed_ip,
            },
            "volume": {"max": settings.max_volume, "step": settings.volume_step},
            "admin": {
                "pin": {"salt": pin.salt, "hash": pin.hash, "n": pin.n, "r": pin.r, "p": pin.p},
                "generated_pin": pin.generated,
            },
            "time": {"zone": settings.time_zone},
            "schedule": schedule_to_json(settings.schedule),
            "sleep_timer": sleep_timer_to_json(settings.sleep_timer),
            "games": games_to_json(settings.games),
            "controls": controls_to_json(settings.controls),
            "hue": hue_to_json(settings.hue, secrets=True),
        }
        owner = self._owner()
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        atomic_write(self.path, payload, mode=0o600)
        self._keep_owner(self.path, owner)
        self._signature = self._file_signature()

    def _file_signature(self) -> tuple[int, int, int] | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_ino, st.st_mtime_ns, st.st_size)

    def _owner(self) -> tuple[int, int] | None:
        """Who should own the file: its current owner, else the data folder's."""
        for candidate in (self.path, self.path.parent):
            try:
                st = candidate.stat()
            except OSError:
                continue
            return st.st_uid, st.st_gid
        return None

    @staticmethod
    def _keep_owner(path: Path, owner: tuple[int, int] | None) -> None:
        # When root writes (e.g. `docker exec -u 0 ... reset-pin`), hand the
        # file back to the server's user so that the server can still read it.
        if owner is None or os.geteuid() != 0 or owner == (0, 0):
            return
        with contextlib.suppress(OSError):
            os.chown(path, *owner)

    @contextlib.contextmanager
    def _file_lock(self, create: bool = True) -> Iterator[None]:
        if not create:
            # Read-only use (diagnostics): never create or change files, and
            # work without write access (e.g. a read-only mount).
            try:
                fd = os.open(self._lock_path, os.O_RDONLY)
            except OSError:
                yield
                return
            try:
                fcntl.flock(fd, fcntl.LOCK_SH)
                yield
            finally:
                os.close(fd)  # also releases the lock
            return
        owner = self._owner()
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        self._keep_owner(self._lock_path, owner)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
