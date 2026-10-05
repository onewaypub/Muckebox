# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the games' sounds and pictures and the fonts come from, and under
which licence.

The sounds are short excerpts of recordings from Wikimedia Commons,
converted to MP3 (mono, faded, loudness-normalised); the pictures are
Twemoji graphics. REUSE.toml states the same licences per file, and a test
keeps this list, the files and REUSE.toml in step.
"""

from __future__ import annotations

from dataclasses import dataclass

TWEMOJI_AUTHOR = "Twitter, Inc and other contributors (Twemoji)"
TWEMOJI_LICENSE = "CC-BY-4.0"
TWEMOJI_SOURCE = "https://github.com/jdecked/twemoji"
FONT_LICENSE = "OFL-1.1"
#: The two typefaces in muckebox/static/fonts/ (Latin subsets, as is).
FONTS = {
    "font_bricolage": (
        "The Bricolage Grotesque Project Authors",
        "https://github.com/ateliertriay/bricolage",
    ),
    "font_figtree": ("The Figtree Project Authors", "https://github.com/erikdkennedy/figtree"),
}


@dataclass(frozen=True)
class Sound:
    author: str
    license: str  # SPDX identifier
    source: str  # the file's page on Wikimedia Commons


SOUNDS = {
    "cat": Sound(
        author="freemaster2",
        license="CC0-1.0",
        source="https://commons.wikimedia.org/wiki/File:Meow_of_a_Siamese_cat_-_freemaster2.wav",
    ),
    "rooster": Sound(
        author="alys",
        license="LicenseRef-Public-Domain",
        source="https://commons.wikimedia.org/wiki/File:Small_rooster_crowing.ogg",
    ),
    "sheep": Sound(
        author="earthcalling",
        license="LicenseRef-Public-Domain",
        source="https://commons.wikimedia.org/wiki/File:Sheep_bleating.ogg",
    ),
    "horse": Sound(
        author="Hü.",
        license="LicenseRef-Public-Domain",
        source="https://commons.wikimedia.org/wiki/File:Wiehern.ogg",
    ),
    "elephant": Sound(
        author="தகவலுழவன்",
        license="CC0-1.0",
        source="https://commons.wikimedia.org/wiki/File:Elephant_voice_-_trumpeting.ogg",
    ),
    "lion": Sound(
        author="த*உழவன்",
        license="LicenseRef-Public-Domain",
        source="https://commons.wikimedia.org/wiki/File:Lion_raring-sound1TamilNadu178.ogg",
    ),
    "donkey": Sound(
        author="felix-blume",
        license="CC0-1.0",
        source="https://commons.wikimedia.org/wiki/File:157763_felix-blume_a-donkey-is-braying-in-his-enclosure-in-south-of-france.wav",
    ),
    "dog": Sound(
        author="Amada44",
        license="CC-BY-SA-3.0",
        source="https://commons.wikimedia.org/wiki/File:Barking_of_a_dog_2.ogg",
    ),
    "doorbell": Sound(
        author="Amada44",
        license="CC0-1.0",
        source="https://commons.wikimedia.org/wiki/File:Sound_Effect_-_Door_Bell.ogg",
    ),
    "car_horn": Sound(
        author="15HPanska_Ruttner_Jan",
        license="CC0-1.0",
        source="https://commons.wikimedia.org/wiki/File:Car_Horn.wav",
    ),
    "train": Sound(
        author="Alex Alex Lep",
        license="LicenseRef-Public-Domain",
        source="https://commons.wikimedia.org/wiki/File:Parovoz_sound.ogg",
    ),
    "clock": Sound(
        author="natalie",
        license="LicenseRef-Public-Domain",
        source="https://commons.wikimedia.org/wiki/File:Clock_ticking.ogg",
    ),
    "water": Sound(
        author="stephan",
        license="LicenseRef-Public-Domain",
        source="https://commons.wikimedia.org/wiki/File:Faucet_water_into_sink.ogg",
    ),
    "bicycle_bell": Sound(
        author="Soundscape_Leuphana",
        license="CC0-1.0",
        source="https://commons.wikimedia.org/wiki/File:Bicycle-bell-1.wav",
    ),
    "church_bells": Sound(
        author="Nicor",
        license="CC0-1.0",
        source="https://commons.wikimedia.org/wiki/File:Glocken_St._Josef_Eschelbronn.ogg",
    ),
    "rain": Sound(
        author="ezwa",
        license="LicenseRef-Public-Domain",
        source="https://commons.wikimedia.org/wiki/File:Rain_(1).ogg",
    ),
}

#: Pictures in static/pictures (all Twemoji).
PICTURES = (
    "bear",
    "bicycle_bell",
    "bird",
    "book",
    "breathing",
    "bulb",
    "butterfly",
    "car_horn",
    "cat",
    "church_bells",
    "clock",
    "dog",
    "donkey",
    "doorbell",
    "elephant",
    "freeze_dance",
    "frog",
    "horse",
    "lion",
    "moon",
    "mouse",
    "move_like",
    "penguin",
    "rabbit",
    "rain",
    "rooster",
    "sheep",
    "snake",
    "star",
    "sound_quiz",
    "sun",
    "train",
    "turtle",
    "water",
)

#: Licences for the credits list (SPDX identifier -> name and link).
LICENCES = {
    "CC0-1.0": ("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "LicenseRef-Public-Domain": ("gemeinfrei", "https://en.wikipedia.org/wiki/Public_domain"),
    "CC-BY-4.0": ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
    "CC-BY-SA-3.0": ("CC BY-SA 3.0", "https://creativecommons.org/licenses/by-sa/3.0/"),
    "OFL-1.1": ("SIL OFL 1.1", "https://openfontlicense.org/"),
}


def credits() -> list[dict[str, str]]:
    """For the parents' page: what, by whom, under which licence."""
    items = [
        {
            "name": key,
            "author": sound.author,
            "license": LICENCES[sound.license][0],
            "license_url": LICENCES[sound.license][1],
            "source": sound.source,
        }
        for key, sound in SOUNDS.items()
    ]
    items.append(
        {
            "name": "pictures",
            "author": TWEMOJI_AUTHOR,
            "license": LICENCES[TWEMOJI_LICENSE][0],
            "license_url": LICENCES[TWEMOJI_LICENSE][1],
            "source": TWEMOJI_SOURCE,
        }
    )
    items.extend(
        {
            "name": key,
            "author": author,
            "license": LICENCES[FONT_LICENSE][0],
            "license_url": LICENCES[FONT_LICENSE][1],
            "source": source,
        }
        for key, (author, source) in FONTS.items()
    )
    return items
