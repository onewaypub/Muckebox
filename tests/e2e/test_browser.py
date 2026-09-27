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

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from waitress import create_server  # noqa: E402

from muckebox.config import load_settings  # noqa: E402
from muckebox.covers import CoverStore  # noqa: E402
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


class Server:
    def __init__(self, tmp_path, set_up=True):
        household = self.household = FakeHousehold()
        self.fake = household.speaker("Kinderzimmer")
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
    expect(page.locator("#volume i.on")).to_have_count(9)
    louder.tap()
    expect(page.locator("#volume i.on")).to_have_count(10)
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
    server.server.close()
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
    boxes = [tiles.nth(i).bounding_box() for i in range(14)]
    assert all(box["width"] >= 100 for box in boxes)
    for a, b in itertools.combinations(boxes, 2):  # no two tiles may overlap
        overlap_x = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
        overlap_y = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
        assert overlap_x <= 0 or overlap_y <= 0


def test_parents_add_a_favorite_for_the_kids(page, server):
    page.goto(server.url + "/admin")
    page.locator("#pin").fill("2468")
    page.locator("#login button").tap()
    row = page.locator(".favorite-row", has_text="Kinderradio")
    row.locator("button").tap()
    expect(row.locator("button")).to_have_text("Schon als Kachel da")
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
    expect(page.locator("#favorites-no-room")).to_be_visible()
    rooms = page.locator(".room-row")
    expect(rooms).to_have_count(2)  # searched without asking
    rooms.filter(has_text="Kinderzimmer").locator("button").tap()
    expect(page.locator("#room-current")).to_have_text("Gewählter Raum: Kinderzimmer")
    expect(rooms.filter(has_text="Kinderzimmer").locator("button")).to_have_text("Gewählt")
    expect(page.locator(".favorite-row", has_text="Kinderradio")).to_be_visible()

    page.locator("#max-volume").fill("18")
    page.locator("#volume-step").fill("2")
    page.locator("#volume button").tap()
    expect(page.locator("#flash")).to_have_text("Gespeichert")
    assert new_server.store.current().max_volume == 18

    page.locator("#pin-current").fill(new_server.store.current().pin.generated)
    page.locator("#pin-new").fill("9753")
    page.locator("#pin-repeat").fill("9753")
    page.locator("#pin-form button").tap()
    expect(page.locator("#pin-banner")).to_be_hidden()
    assert new_server.store.verify_pin("9753")

    page.goto(new_server.url)
    expect(page.locator("#empty")).to_be_visible()
    expect(overlay).to_be_hidden()


def log_in(page, server):
    page.goto(server.url + "/admin")
    page.locator("#pin").fill("2468")
    page.locator("#login button").tap()
    expect(page.locator("#room-current")).to_have_text("Gewählter Raum: Kinderzimmer")


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
    log_in(page, server)
    page.locator("#schedule-enabled").check()
    monday = page.locator(".day-row").first
    monday.locator(".from").fill("06:30")
    monday.locator(".to").fill("00:00")  # until midnight
    page.locator("#copy-monday").tap()
    page.locator(".day-row").nth(6).locator(".free").check()  # Sunday is free
    page.locator("#fade-minutes").fill("5")
    page.locator("#schedule button[type=submit]").tap()
    expect(page.locator("#flash")).to_have_text("Gespeichert")
    schedule = server.store.current().schedule
    assert schedule.enabled and schedule.fade_minutes == 5
    assert schedule.days[1].start.strftime("%H:%M") == "06:30"
    assert schedule.days[1].end is None  # 24:00
    assert schedule.days[6] is None
    expect(page.locator("#override-buttons")).to_be_visible()
    page.locator("[data-override='30']").tap()
    expect(page.locator("#schedule-status")).to_contain_text("Freigabe bis")
    page.locator("#override-end").tap()
    expect(page.locator("#override-end")).to_be_hidden()
