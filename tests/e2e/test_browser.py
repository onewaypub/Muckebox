# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end tests in real browser engines.

WebKit stands in for Safari on an iPad, Chromium for an Android tablet. The
server runs in-process on loopback with a simulated speaker, so tests can
switch the "speaker" off and on.

Needs Playwright browsers (`python -m playwright install webkit chromium`).
Without them the tests are skipped, unless MUCKEBOX_REQUIRE_E2E=1 (CI).
"""

import itertools
import os
import re
import threading
import time

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from waitress import create_server  # noqa: E402

from muckebox.config import load_settings  # noqa: E402
from muckebox.covers import CoverStore  # noqa: E402
from muckebox.hue.fake import FakeHue  # noqa: E402
from muckebox.library import Library, favorite_source  # noqa: E402
from muckebox.runtime.service import Runtime  # noqa: E402
from muckebox.settings import SettingsStore  # noqa: E402
from muckebox.sonos.fake import FakeHousehold  # noqa: E402
from muckebox.web import create_app  # noqa: E402
from muckebox.web.app import Services  # noqa: E402

pytestmark = pytest.mark.e2e
expect = playwright_api.expect
REQUIRED = os.environ.get("MUCKEBOX_REQUIRE_E2E") == "1"
DEVICES = {"webkit": "iPad (gen 7) landscape", "chromium": "Galaxy Tab S4 landscape"}


class ShiftedClock:
    """Real time, shifted to a chosen local time (for usage-time tests)."""

    def __init__(self, local):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        target = datetime.fromisoformat(local).replace(tzinfo=ZoneInfo("Europe/Berlin"))
        self.offset = target.timestamp() - time.time()

    def monotonic(self):
        return time.monotonic()

    def time(self):
        return time.time() + self.offset


class Server:
    def __init__(self, tmp_path, set_up=True, clock=None):
        household = self.household = FakeHousehold()
        self.fake = household.speaker("Kinderzimmer")
        self.hue = FakeHue()
        settings = load_settings({"DATA_DIR": str(tmp_path)})
        store = self.store = SettingsStore(tmp_path, scrypt={"n": 2**4, "r": 8, "p": 1})
        if set_up:
            store.set_room("Kinderzimmer", household.uid("Kinderzimmer"), None)
            store.change_pin("2468")
        self.library = Library(tmp_path / "library.json")
        self.runtime = Runtime(
            store,
            self.library,
            tmp_path,
            backend_factory=household.backend,
            room_finder=household.find_rooms,
            clock=clock,
            hue=self.hue,
        )
        services = Services(
            settings=settings,
            store=store,
            runtime=self.runtime,
            library=self.library,
            covers=CoverStore(tmp_path / "c"),
            secret_key=b"k" * 32,
        )
        self.server = create_server(create_app(services), host="127.0.0.1", port=0, threads=4)
        self.url = f"http://127.0.0.1:{self.server.effective_port}"
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.runtime.start()
        self.thread.start()

    def stop(self):
        self.server.close()
        self.runtime.stop()

    def vanish(self):
        """Like a NAS that was switched off: also end open keep-alive connections,
        which waitress would otherwise keep serving after close()."""
        self.server.close()
        for channel in list(self.server._map.values()):
            channel.close()

    def add_tiles(self, *indexes):
        tiles = []
        for index in indexes:
            favorite = self.fake.favorites[index]
            source = favorite_source(
                favorite.item_id, favorite.ref, favorite.route, favorite.description
            )
            tiles.append(self.library.add(favorite.title, source))
        return tiles


@pytest.fixture(scope="module")
def playwright():
    with playwright_api.sync_playwright() as p:
        yield p


@pytest.fixture(params=["webkit", "chromium"])
def page(request, playwright):
    engine = request.param
    try:
        browser = getattr(playwright, engine).launch()
    except Exception as exc:
        if REQUIRED:
            raise
        pytest.skip(f"{engine} cannot run here: {str(exc).splitlines()[0]}")
    context = browser.new_context(**playwright.devices[DEVICES[engine]])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    context.close()
    browser.close()
    assert not errors, f"JavaScript errors: {errors}"


@pytest.fixture
def server(tmp_path):
    server = Server(tmp_path)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def new_server(tmp_path):
    """A Muckebox on its first start: no room, a generated PIN."""
    server = Server(tmp_path, set_up=False)
    server.start()
    yield server
    server.stop()


def test_tap_plays_and_highlights_the_tile(page, server):
    _radio, playlist = server.add_tiles(2, 0)
    page.goto(server.url)
    tiles = page.locator(".tile")
    expect(tiles).to_have_count(2)
    tiles.nth(1).tap()
    expect(tiles.nth(1)).to_have_class(re.compile(r"\bplaying\b"))
    expect(page.locator("#toggle")).to_have_attribute("aria-label", "Pause")
    assert server.fake.queue == [playlist.source["uri"]]
    page.locator("#toggle").tap()
    expect(tiles.nth(1)).to_have_class(re.compile(r"\bpaused\b"))
    expect(page.locator("#toggle")).to_have_attribute("aria-label", "Abspielen")


def test_louder_stops_at_the_maximum(page, server):
    server.add_tiles(0)
    server.fake.volume = 22
    page.goto(server.url)
    louder = page.locator("#louder")
    expect(page.locator("#volume i.on")).to_have_count(4)  # five dots: 22 of 25
    louder.tap()
    expect(page.locator("#volume i.on")).to_have_count(5)
    expect(louder).to_be_disabled()
    assert server.fake.volume == 25


def test_sleeping_speaker_and_recovery(page, server):
    server.add_tiles(0)
    page.goto(server.url)
    expect(page.locator(".tile")).to_have_count(1)
    server.fake.reachable = False
    overlay = page.locator("#overlay")
    expect(overlay).to_be_visible(timeout=10_000)
    expect(overlay).to_contain_text("Der Lautsprecher schläft gerade")
    server.fake.reachable = True
    expect(overlay).to_be_hidden(timeout=20_000)


def test_server_gone_shows_offline_overlay(page, server):
    page.goto(server.url)
    expect(page.locator("#empty")).to_be_visible()
    server.vanish()
    overlay = page.locator("#overlay")
    expect(overlay).to_be_visible(timeout=10_000)
    expect(overlay).to_contain_text("Keine Verbindung zur Muckebox")


@pytest.mark.parametrize(
    "size", [None, (640, 1000), (390, 844)], ids=["device", "tablet-portrait", "phone-portrait"]
)
def test_layout_fits_the_screen_with_big_targets(page, server, size):
    server.add_tiles(*[i % 5 for i in range(14)])
    if size:
        page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(server.url)
    tiles = page.locator(".tile")
    expect(tiles).to_have_count(14)
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= page.viewport_size["width"]
    louder = page.locator("#louder").bounding_box()
    assert louder["x"] + louder["width"] <= page.viewport_size["width"]
    assert louder["width"] >= 40 and louder["height"] >= 40
    assert_tiles_fit(page, [tiles.nth(i).bounding_box() for i in range(6)])  # the first page


def assert_tiles_fit(page, boxes, minimum=100):
    viewport = page.viewport_size
    for box in boxes:
        assert box["width"] >= minimum
        assert box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"]
        assert box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"]
    for a, b in itertools.combinations(boxes, 2):  # no two tiles may overlap
        overlap_x = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
        overlap_y = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
        assert overlap_x <= 0 or overlap_y <= 0


def test_small_layout_turns_pages_and_follows_the_playing_tile(page, server):
    tiles = server.add_tiles(*[i % 5 for i in range(8)])
    page.goto(server.url)
    expect(page.locator(".tile")).to_have_count(8)
    expect(page.locator("#page-dots i")).to_have_count(2)
    expect(page.locator("#page-prev")).to_be_disabled()
    expect(page.locator("#prev")).to_be_hidden()  # previous/next only if the parents want them
    page.locator("#page-next").tap()
    expect(page.locator("#page-dots i").nth(1)).to_have_class(re.compile(r"\bon\b"))
    last = page.locator(".tile").nth(7)
    expect(last).to_be_in_viewport()
    last.tap()
    expect(last).to_have_class(re.compile(r"\bplaying\b"))
    page.locator("#page-prev").tap()
    expect(page.locator(".tile").nth(0)).to_be_in_viewport()
    assert tiles[7].id == last.get_attribute("data-id")


def test_big_layout_shows_titles_and_what_is_playing(page, server):
    server.store.set_controls({"profile": "big"})
    server.fake.albums[server.fake.favorites[1].ref.uri] = ["x-file:1", "x-file:2", "x-file:3"]
    server.add_tiles(*[i % 5 for i in range(9)])
    page.goto(server.url)
    tiles = page.locator(".tile")
    expect(tiles).to_have_count(9)
    expect(tiles.nth(1).locator(".title")).to_have_text("Hörspiel Folge 1")
    expect(page.locator("#page-next")).to_be_hidden()  # no pages: the grid scrolls
    expect(page.locator("#now-title")).to_have_text("Tippe auf eine Kachel")
    tiles.nth(1).tap()
    expect(page.locator("#now-title")).to_have_text("Hörspiel Folge 1")
    expect(page.locator("#now-label")).to_have_text("Läuft gerade")
    expect(page.locator("#now-sub")).to_contain_text("Titel 1 von 3", timeout=15_000)
    expect(page.locator("#now-progress")).to_be_visible()
    expect(page.locator("#prev")).to_be_visible()
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= page.viewport_size["width"]


def open_page(page, name):
    page.locator(f"#nav a[data-page='{name}']").tap()
    expect(page.locator(f"#page-{name}")).to_be_visible()


def test_parents_add_a_favorite_for_the_kids(page, server):
    page.goto(server.url + "/admin")
    page.locator("#pin").fill("2468")
    page.locator("#login button").tap()
    expect(page.locator("#greeting")).to_contain_text("Alles läuft.")
    open_page(page, "add")
    row = page.locator(".favorite-row", has_text="Kinderradio")
    row.locator("button").tap()
    expect(row.locator("button")).to_have_text("Schon als Kachel da")
    open_page(page, "tiles")
    expect(page.locator(".tile-row input[name=title]")).to_have_value("Kinderradio")
    page.goto(server.url)
    expect(page.locator(".tile")).to_have_count(1)
    expect(page.locator(".tile")).to_have_attribute("aria-label", "Kinderradio")


def test_first_start_setup(page, new_server):
    page.goto(new_server.url)
    overlay = page.locator("#overlay")
    expect(overlay).to_contain_text("noch nicht fertig eingerichtet")

    page.goto(new_server.url + "/admin")
    expect(page.locator("#login .hint")).to_contain_text("reset-pin")
    page.locator("#pin").fill(new_server.store.current().pin.generated)
    page.locator("#login button").tap()
    expect(page.locator("#pin-banner")).to_be_visible()
    expect(page.locator("#page-setup")).to_be_visible()  # no room yet: straight to the setup
    rooms = page.locator(".room-row")
    expect(rooms).to_have_count(2)  # searched without asking
    rooms.filter(has_text="Kinderzimmer").locator("button").tap()
    expect(page.locator("#room-current")).to_have_text("Gewählter Raum: Kinderzimmer")
    expect(rooms.filter(has_text="Kinderzimmer").locator("button")).to_have_text("Gewählt")
    open_page(page, "add")
    expect(page.locator(".favorite-row", has_text="Kinderradio")).to_be_visible()

    open_page(page, "volume")
    page.locator("#max-volume").evaluate(
        "(range) => { range.value = '18';"
        " range.dispatchEvent(new Event('input', { bubbles: true })); }"
    )
    expect(page.locator("#max-volume-value")).to_have_text("18")
    page.locator("#volume-step").fill("2")
    page.locator("#page-volume .profile", has_text="7–14 Jahre").tap()
    page.locator("#volume button[type=submit]").tap()
    expect(page.locator("#flash")).to_have_text("Gespeichert")
    assert new_server.store.current().max_volume == 18
    assert new_server.store.current().controls.profile == "big"

    open_page(page, "pin")
    page.locator("#pin-current").fill(new_server.store.current().pin.generated)
    page.locator("#pin-new").fill("9753")
    page.locator("#pin-repeat").fill("9753")
    page.locator("#pin-form button").tap()
    expect(page.locator("#pin-banner")).to_be_hidden()
    assert new_server.store.verify_pin("9753")

    page.goto(new_server.url)
    expect(page.locator("#empty")).to_be_visible()
    expect(page.locator("body")).to_have_attribute("data-profile", "big")
    expect(overlay).to_be_hidden()


def log_in(page, server, to="setup"):
    page.goto(server.url + "/admin")
    page.locator("#pin").fill("2468")
    page.locator("#login button").tap()
    expect(page.locator("#room-current")).to_have_text("Gewählter Raum: Kinderzimmer")
    open_page(page, to)


def test_the_chosen_room_can_get_a_speaker_address(page, server):
    log_in(page, server)
    page.locator("#seed-ip").fill("192.0.2.10")
    page.locator("#room-search button").tap()
    button = page.locator(".room-row", has_text="Kinderzimmer").locator("button")
    expect(button).to_have_text("Übernehmen")
    button.tap()
    expect(button).to_have_text("Gewählt")
    expect(button).to_be_disabled()
    assert server.store.current().seed_ip == "192.0.2.10"


def test_a_failed_room_search_leaves_a_hint(page, server):
    log_in(page, server)
    server.household.reachable = False
    page.locator("#room-search button").tap()
    expect(page.locator("#rooms")).to_contain_text("nicht erreichbar")
    page.wait_for_timeout(5500)  # longer than a flash message stays
    expect(page.locator("#rooms")).to_contain_text("nicht erreichbar")


def test_parents_set_usage_times_and_allow_more(page, server):
    log_in(page, server, to="schedule")
    page.locator("#schedule-enabled").check()
    monday = page.locator(".day-row").first
    monday.locator(".from").fill("06:30")
    monday.locator(".to").fill("00:00")  # until midnight
    page.locator("#copy-monday").tap()
    sunday = page.locator(".day-row").nth(6)
    sunday.locator(".free-chip").tap()  # Sunday is free
    expect(sunday.locator(".free")).to_be_checked()
    expect(sunday.locator(".from")).to_be_disabled()
    page.locator("#fade-minutes").fill("6")
    page.locator("#page-schedule .stepper .dec").tap()  # back to 5
    page.locator("#schedule button[type=submit]").tap()
    expect(page.locator("#flash")).to_have_text("Gespeichert")
    schedule = server.store.current().schedule
    assert schedule.enabled and schedule.fade_minutes == 5
    assert schedule.days[1].start.strftime("%H:%M") == "06:30"
    assert schedule.days[1].end is None  # 24:00
    assert schedule.days[6] is None
    open_page(page, "overview")
    expect(page.locator("#today-bar")).to_be_visible()
    expect(page.locator("#override-buttons")).to_be_visible()
    page.locator("[data-override='30']").tap()
    expect(page.locator("#schedule-status")).to_contain_text("Freigabe bis")
    page.locator("#override-end").tap()
    expect(page.locator("#override-end")).to_be_hidden()


DAILY = {
    "enabled": True,
    "fade_minutes": 10,
    "days": {
        day: {"from": "07:00", "to": "19:00"}
        for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    },
}


@pytest.fixture
def evening_server(tmp_path):
    """A Muckebox at 20:00 with usage times until 19:00: bedtime."""
    server = Server(tmp_path, clock=ShiftedClock("2026-09-28 20:00"))
    server.store.set_time_zone("Europe/Berlin")
    server.store.set_schedule(DAILY)
    server.start()
    yield server
    server.stop()


def hold(page, selector, seconds=3.2):
    page.locator(selector).dispatch_event("pointerdown", {"isPrimary": True, "pointerId": 1})
    page.wait_for_timeout(seconds * 1000)
    page.locator(selector).dispatch_event("pointerup")


def type_pin(page, pin):
    for digit in pin:
        page.locator(".pad-key", has_text=digit).tap()


def test_bedtime_moon_and_the_parents_pin_pad(page, evening_server):
    evening_server.add_tiles(0)
    page.goto(evening_server.url)
    bedtime = page.locator("#bedtime")
    expect(bedtime).to_be_visible()
    expect(bedtime).to_contain_text("Schlafenszeit")
    expect(bedtime).to_contain_text("Wieder ab 07:00 Uhr")
    expect(page.locator("#tiles")).to_be_hidden()
    expect(page.locator("#louder")).to_be_disabled()
    page.locator("#moon").tap()  # a short tap does nothing
    expect(page.locator("#pin-pad")).to_be_hidden()
    hold(page, "#moon")
    pad = page.locator("#pin-pad")
    expect(pad).to_be_visible()
    expect(pad.locator("[data-choice='15']")).to_be_disabled()
    type_pin(page, "1111")
    pad.locator("[data-choice='15']").tap()
    expect(page.locator("#pad-message")).to_have_text("Falsche PIN")
    type_pin(page, "2468")
    pad.locator("[data-choice='15']").tap()
    expect(pad).to_be_hidden()
    expect(bedtime).to_be_hidden(timeout=5000)
    expect(page.locator(".tile")).to_have_count(1)


def test_kids_start_the_sleep_timer_with_the_moon(page, server):
    server.store.set_sleep_timer({"enabled": True, "minutes": 30, "wake": "07:00"})
    server.add_tiles(0)
    page.goto(server.url)
    moon = page.locator("#small-moon")
    expect(moon).to_have_attribute("data-mode", "start")
    moon.tap()
    expect(moon).to_have_attribute("data-mode", "running")
    expect(page.locator(".small-moon .ring")).to_be_visible()
    timer = server.runtime.keeper.timers.state.sleep
    assert timer is not None and timer.ends_at > time.time() + 29 * 60
    moon.tap()  # a second tap changes nothing
    assert server.runtime.keeper.timers.state.sleep.ends_at == timer.ends_at


def test_parents_see_and_reset_where_an_album_stopped(page, server):
    from muckebox.sonos.model import Position

    album, _radio = server.add_tiles(1, 2)
    server.runtime.resume.record(album.id, Position(3, 760, 1200, "x"), 5)
    log_in(page, server, to="tiles")
    row = page.locator(".tile-row").nth(0)  # tiles in the order they were added
    expect(row.locator(".resume-toggle")).to_be_checked()
    expect(row.locator(".resume-position")).to_have_text("Stand: Titel 3, 12:40")
    row.locator(".resume-restart").tap()
    expect(row.locator(".resume-position")).to_be_hidden()
    assert server.runtime.resume.get(album.id) is None
    radio_row = page.locator(
        ".tile-row", has=page.locator("input[name=title][value='Kinderradio']")
    )
    expect(radio_row.locator(".resume")).to_be_hidden()  # radio has no positions


# -- games ----------------------------------------------------------------------------

RECORD_AUDIO = """
window.__played = [];
window.__spoken = [];
window.Audio = class {
  constructor() { this.src = ""; }
  play() {
    window.__played.push(this.src);
    setTimeout(() => this.onended && this.onended(), 10);
    return Promise.resolve();
  }
  pause() {}
};
window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
Object.defineProperty(window, "speechSynthesis", { value: {
  speak(utterance) {
    window.__spoken.push(utterance.text);
    setTimeout(() => utterance.onend && utterance.onend(), 10);
  },
  cancel() {},
  getVoices() { return []; },
}});
"""


def enable_games(server, *games, level=1, dance_tile=None):
    server.store.set_games(
        {
            "daily_minutes": 30,
            "dance_tile": dance_tile,
            "items": {game: {"enabled": True, "level": level} for game in games},
        }
    )


def open_game(page, game):
    page.locator("#games-button").tap()
    page.locator(f".game-card[data-game='{game}']").tap()


def test_sound_quiz(page, server):
    page.add_init_script(RECORD_AUDIO)
    enable_games(server, "sound_quiz", level=1)
    page.goto(server.url)
    expect(page.locator("#games-button")).to_be_visible()
    open_game(page, "sound_quiz")
    for _ in range(5):
        expect(page.locator(".quiz-choice")).to_have_count(2)
        played = None
        deadline = time.time() + 5
        while not played and time.time() < deadline:  # (the page's CSP forbids wait_for_function)
            played = page.evaluate(
                "window.__played.filter((src) => !src.includes('silence')).at(-1)"
            )
            time.sleep(0.05)
        answer = played.rsplit("/", 1)[1].removesuffix(".mp3")
        page.evaluate("window.__played = []")
        page.locator(f".quiz-choice[data-id='{answer}']").tap()
    expect(page.locator(".game-stage")).to_contain_text("Fertig! Toll zugehört.")
    expect(page.locator("#game-layer")).to_be_hidden(timeout=5000)
    spoken = page.evaluate("window.__spoken")
    assert sum(text.startswith("Richtig, ") for text in spoken) == 5
    assert server.runtime.games.active() is None
    assert 0 < server.runtime.games.used_today() < 60  # the unused time came back


def test_freeze_dance_mutes_the_speaker(page, server):
    page.add_init_script(RECORD_AUDIO)
    (tile,) = server.add_tiles(0)
    enable_games(server, "freeze_dance", level=3, dance_tile=tile.id)
    page.clock.install()
    page.goto(server.url)
    open_game(page, "freeze_dance")
    expect(page.locator(".game-stage")).to_have_attribute("data-phase", "dance")
    assert server.fake.state == "playing"  # the dance music
    page.clock.run_for(12_500)  # the first dance lasts at least 12 s
    expect(page.locator(".game-stage")).to_have_attribute("data-phase", "freeze")
    expect(page.locator(".game-stage")).to_contain_text("Stopp!")
    deadline = time.time() + 5
    while not server.fake.muted and time.time() < deadline:
        time.sleep(0.05)
    assert server.fake.muted
    page.locator("#game-close").tap()
    expect(page.locator("#game-layer")).to_be_hidden()
    deadline = time.time() + 5
    while (server.fake.muted or server.fake.state != "paused") and time.time() < deadline:
        time.sleep(0.05)
    assert (server.fake.muted, server.fake.state) == (False, "paused")


def test_move_like(page, server):
    page.add_init_script(RECORD_AUDIO)
    enable_games(server, "move_like", level=1)
    page.goto(server.url)
    open_game(page, "move_like")
    expect(page.locator(".move-holder .game-text")).to_contain_text("Beweg dich wie")
    page.locator("#game-close").tap()
    expect(page.locator("#game-layer")).to_be_hidden()
    assert server.runtime.games.active() is None


def test_breathing_at_bedtime(page, evening_server):
    page.add_init_script(RECORD_AUDIO)
    enable_games(evening_server, "breathing", level=1)
    page.clock.install()
    page.goto(evening_server.url)
    button = page.locator("#bedtime-breathing")
    expect(button).to_be_visible()
    button.tap()
    expect(page.locator(".breathing-circle")).to_be_visible()
    page.clock.run_for(4 * 60 * 1000)
    expect(page.locator(".game-picture.night")).to_be_visible()
    page.locator("#game-stage").tap()
    expect(page.locator("#game-layer")).to_be_hidden()
    assert "Gute Nacht." in page.evaluate("window.__spoken")


def test_a_double_tap_on_the_pin_pad_gives_the_time_once(page, evening_server):
    page.goto(evening_server.url)
    hold(page, "#moon")
    type_pin(page, "2468")
    choice = page.locator("[data-choice='15']")
    choice.dispatch_event("click")
    choice.dispatch_event("click")
    expect(page.locator("#pin-pad")).to_be_hidden()
    override = evening_server.runtime.timers.state.override
    assert override[1] - override[0] == 15 * 60


def test_a_failed_sleep_timer_start_can_be_tried_again(page, server):
    server.store.set_sleep_timer({"enabled": True, "minutes": 30, "wake": "07:00"})
    page.route("**/api/sleep-timer/start", lambda route: route.abort())
    page.goto(server.url)
    moon = page.locator("#small-moon")
    expect(moon).to_have_attribute("data-mode", "start")
    moon.tap()
    expect(moon).to_have_attribute("data-mode", "start")  # not stuck in "running"
    page.unroute("**/api/sleep-timer/start")
    moon.tap()
    expect(moon).to_have_attribute("data-mode", "running")


# -- toddler taps: long, wobbly, slightly beside the tile -------------------------------------


def touch(page, points, hold_ms=0, wobble=0):
    """A real touch through the browser's input pipeline (Chromium only): put
    a finger down at the first point, wobble while holding, lift it at the last."""
    cdp = page.context.new_cdp_session(page)
    send = lambda kind, at: cdp.send(  # noqa: E731
        "Input.dispatchTouchEvent",
        {"type": kind, "touchPoints": [] if at is None else [{"x": at[0], "y": at[1]}]},
    )
    x, y = points[0]
    send("touchStart", (x, y))
    for step in range(max(1, hold_ms // 100)):
        page.wait_for_timeout(100)
        if wobble:
            sign = 1 if step % 2 else -1
            send("touchMove", (x + sign * wobble, y - sign * wobble))
    for point in points[1:]:
        page.wait_for_timeout(16)
        send("touchMove", point)
    send("touchEnd", None)


def chromium_only(page):
    if page.context.browser.browser_type.name != "chromium":
        pytest.skip("real touch input is emulated through Chromium's DevTools protocol")


def middle(box):
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


@pytest.mark.parametrize("how", ["long_wobbly", "in_the_gap"])
def test_toddler_taps_start_the_tile(page, server, how):
    chromium_only(page)
    server.add_tiles(0, 2, 3, 1, 4, 0)
    server.store.set_controls({"tap_cooldown": 0})
    page.goto(server.url)
    tiles = page.locator(".tile")
    expect(tiles).to_have_count(6)
    first, second = tiles.nth(0).bounding_box(), tiles.nth(1).bounding_box()
    x, y = middle(first)
    if how == "long_wobbly":
        touch(page, [(x, y)], hold_ms=1500, wobble=12)
    else:  # just right of the tile, in the gap before the next one
        gap_x = first["x"] + first["width"] + (second["x"] - first["x"] - first["width"]) * 0.3
        touch(page, [(gap_x, y)])
    expect(tiles.nth(0)).to_have_class(re.compile(r"\bplaying\b"))


def test_a_swipe_turns_the_page_and_starts_nothing(page, server):
    chromium_only(page)
    server.add_tiles(*[i % 5 for i in range(8)])
    page.goto(server.url)
    tiles = page.locator(".tile")
    expect(tiles).to_have_count(8)
    x, y = middle(tiles.nth(4).bounding_box())
    touch(page, [(x, y), (x - 100, y), (x - 250, y)])
    expect(page.locator("#page-dots i").nth(1)).to_have_class(re.compile(r"\bon\b"))
    assert server.fake.state != "playing"


def test_a_finger_resting_on_the_tile_starts_it_before_it_is_lifted(page, server):
    chromium_only(page)
    server.add_tiles(0, 2)
    page.goto(server.url)
    tile = page.locator(".tile").nth(0)
    expect(tile).to_be_visible()
    cdp = page.context.new_cdp_session(page)
    x, y = middle(tile.bounding_box())
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    expect(tile).to_have_class(re.compile(r"\bplaying\b"))  # the finger is still down
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    page.wait_for_timeout(500)
    plays = [call for call in server.fake.calls if call[0] == "play_favorite"]
    assert len(plays) == 1  # lifting the finger does not start it again


# -- lights -------------------------------------------------------------------------------------


def set_up_lights(server):
    bridge = server.hue.bridge
    from muckebox.settings import HueBridge

    server.store.set_hue_bridge(
        HueBridge(bridge.ip, bridge.id, bridge.name, bridge.key, bridge.fingerprint)
    )
    server.store.set_hue(
        {
            "room": "room-kids",
            "slots": [
                {"scene": "scene-bright", "picture": "sun"},
                {"scene": "scene-night", "picture": "moon"},
            ],
        }
    )


@pytest.mark.parametrize("layout", ["small", "big"])
def test_kids_switch_the_lights(page, server, layout):
    server.store.set_controls({"profile": layout})
    set_up_lights(server)
    server.add_tiles(0, 2)
    page.goto(server.url)
    buttons = page.locator("#lights .light")
    expect(buttons).to_have_count(2)
    expect(buttons.nth(1)).to_be_enabled(timeout=10_000)  # after the first poll
    buttons.nth(1).tap()
    expect(buttons.nth(1)).to_have_class(re.compile(r"\bactive\b"))
    assert "group-kids" in server.hue.bridge.lit
    expect(buttons.nth(1)).not_to_have_class(re.compile(r"\bwaiting\b"), timeout=5000)
    buttons.nth(1).tap()
    expect(buttons.nth(1)).not_to_have_class(re.compile(r"\bactive\b"))
    assert "group-kids" not in server.hue.bridge.lit


def test_parents_connect_the_bridge_and_choose_buttons(page, server):
    server.hue.bridge.button_pressed = False
    log_in(page, server, to="lights")
    page.locator("#hue-search button").tap()
    row = page.locator(".bridge-row", has_text="Hue Bridge")
    row.locator("button").tap()
    expect(page.locator("#hue-pairing")).to_be_visible()
    server.hue.bridge.button_pressed = True  # the parent pressed the button
    expect(page.locator("#hue-paired")).to_be_visible(timeout=10_000)
    expect(page.locator("#hue-bridge-name")).to_contain_text("Hue Bridge")
    page.locator("#hue-room").select_option("room-kids")
    first = page.locator("#hue-slots .slot").nth(0)
    first.locator(".slot-scene").select_option("scene-night")
    first.locator(".slot-pictures label").nth(3).tap()  # the moon
    page.locator("#hue-form button[type=submit]").tap()
    expect(page.locator("#flash")).to_have_text("Gespeichert")
    hue = server.store.current().hue
    assert hue.room == "room-kids"
    assert [(s.scene, s.picture) for s in hue.slots] == [("scene-night", "moon")]
    open_page(page, "sleep")
    expect(page.locator("#sleep-lights-field")).to_be_visible()
    page.locator("#sleep-lights").select_option("off")
    page.locator("#sleep-timer button[type=submit]").tap()
    expect(page.locator("#flash")).to_have_text("Gespeichert")
    assert server.store.current().sleep_timer.lights == "off"


# -- touch diagnosis --------------------------------------------------------------------------


def test_touch_diagnosis_shows_what_the_tablet_receives(page, server):
    chromium_only(page)
    server.add_tiles(0, 2, 3)
    server.runtime.touchlog.start()
    page.goto(server.url)
    tiles = page.locator(".tile")
    expect(tiles).to_have_count(3)
    expect(page.locator(".touch-diag")).to_be_attached(timeout=10_000)
    x, y = middle(tiles.nth(0).bounding_box())
    touch(page, [(x, y)], hold_ms=600)  # a resting finger: started while still down
    expect(page.locator(".touch-dot.down")).to_have_count(1)
    expect(tiles.nth(0)).to_have_class(re.compile(r"\bplaying\b"))
    deadline = time.time() + 6
    while time.time() < deadline and not any(
        e["type"] == "tile-hold" for e in server.runtime.touchlog.events()
    ):
        time.sleep(0.1)
    types = [e["type"] for e in server.runtime.touchlog.events()]
    assert "pointerdown" in types and "pointerup" in types and "tile-hold" in types
    down = next(e for e in server.runtime.touchlog.events() if e["type"] == "pointerdown")
    assert down["target"] == "tile:1" and down["pt"] == "touch"

    log_in(page, server, to="volume")
    rows = page.locator("#touch-rows tr")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("tile:1")
    expect(rows.first).to_contain_text("gestartet")
    page.locator("#touch-stop").tap()
    expect(page.locator("#touch-status")).to_have_text("Aus")
    assert not server.runtime.touchlog.active()
