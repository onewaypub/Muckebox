# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""German user interface texts."""

MESSAGES: dict[str, str] = {
    # -- kids view ------------------------------------------------------------
    "kids.play": "Abspielen",
    "kids.pause": "Pause",
    "kids.next": "Nächstes Lied",
    "kids.previous": "Vorheriges Lied",
    "kids.louder": "Lauter",
    "kids.quieter": "Leiser",
    "kids.volume": "Lautstärke",
    "kids.empty": "Hier ist noch keine Musik. Die Eltern können welche hinzufügen.",
    "kids.offline": "Keine Verbindung zur Muckebox",
    "kids.sleeping": "Der Lautsprecher schläft gerade",
    "kids.config": "Die Muckebox ist noch nicht fertig eingerichtet",
    "kids.error": "Das hat gerade nicht geklappt",
    "kids.bedtime": "Schlafenszeit",
    "kids.bedtime_until": "Wieder ab {time} Uhr",
    "kids.sleep_timer": "Einschlaf-Timer",
    "kids.games": "Spiele",
    "kids.game_close": "Spiel beenden",
    "kids.replay": "Noch einmal hören",
    "game.done": "Fertig!",
    "game.breathing_intro": "Mach es dir gemütlich. Wir atmen zusammen, ganz langsam.",
    "game.breathe_in": "Einatmen",
    "game.breathe_out": "Ausatmen",
    "game.good_night": "Gute Nacht.",
    "game.quiz_listen": "Hör mal gut zu! Was ist das?",
    "game.quiz_right": "Richtig, {name}!",
    "game.quiz_again": "Hör noch mal.",
    "game.quiz_done": "Fertig! Toll zugehört.",
    "game.move_say": "Beweg dich {move}!",
    "game.move_calm": "Und jetzt ganz ruhig. Atme tief ein und aus.",
    "game.freeze_ready": (
        "Gleich geht's los. Tanz, wenn die Musik spielt, und erstarre, wenn sie stoppt!"
    ),
    "game.freeze_stop": "Stopp!",
    "game.freeze_go": "Weiter!",
    "kids.pad_title": "Eltern-Freigabe: PIN eingeben",
    "kids.pad_delete": "Löschen",
    "kids.pad_15": "+15 Min.",
    "kids.pad_30": "+30 Min.",
    "kids.pad_60": "+1 Std.",
    "kids.pad_morning": "Bis morgen früh",
    "kids.pad_close": "Abbrechen",
    "kids.pad_wrong": "Falsche PIN",
    "kids.pad_locked": "Zu viele Versuche. Bitte später noch einmal.",
    # -- parents' page -----------------------------------------------------------
    "admin.title": "Muckebox – Eltern",
    "admin.pin": "PIN",
    "admin.login": "Anmelden",
    "admin.logout": "Abmelden",
    "admin.login_hint": (
        "Erster Start oder PIN vergessen? Die Start-PIN steht im Log der Muckebox. "
        "Eine neue PIN erzeugt dieser Befehl im Container:"
    ),
    "admin.pin_banner": "Du nutzt noch die Start-PIN aus dem Log. Bitte lege eine eigene PIN fest.",
    "admin.pin_banner_link": "PIN ändern",
    "admin.setup": "Einrichtung",
    "admin.room_current": "Gewählter Raum: {room}",
    "admin.room_none": (
        "Noch kein Raum gewählt. Wähle unten den Raum, den die Muckebox steuern soll."
    ),
    "admin.seed_ip": "IP-Adresse eines Lautsprechers (optional)",
    "admin.seed_ip_hint": (
        "Nur nötig, wenn die Suche nichts findet, z. B. wenn die Lautsprecher in einem "
        "anderen Netz (VLAN) sind oder mehrere Sonos-Systeme im Netz laufen."
    ),
    "admin.seed_ip_placeholder": "z. B. 192.0.2.10",
    "admin.search_rooms": "Räume suchen",
    "admin.searching": "Suche läuft … (bis zu 20 Sekunden)",
    "admin.rooms_none": (
        "Keine Räume gefunden. Trage die IP-Adresse eines Lautsprechers ein und suche erneut."
    ),
    "admin.room_grouped": "gruppiert",
    "admin.choose": "Wählen",
    "admin.apply": "Übernehmen",
    "admin.chosen": "Gewählt",
    "admin.room_saved": "Die Muckebox steuert jetzt „{room}“.",
    "admin.room_hint": (
        "Muckebox prüft die Verbindung, bevor sie den Raum speichert. "
        "Das Lautstärke-Limit gilt im gewählten Raum sofort."
    ),
    "admin.favorites_no_room": "Wähle zuerst einen Raum, dann erscheinen hier seine Favoriten.",
    "admin.volume": "Lautstärke",
    "admin.volume_step": "Schrittweite der Lauter/Leiser-Tasten",
    "admin.volume_hint": (
        "Das Limit gilt sofort (1 bis 100). Stelle zusätzlich in der Sonos-App ein "
        "Lautstärke-Limit für den Raum ein."
    ),
    "admin.controls": "Bedienung",
    "admin.tap_cooldown": "Kacheln nach einem Tipp sperren (Sekunden)",
    "admin.idle_minutes": "Ohne Tippen pausieren nach (Minuten)",
    "admin.controls_hint": (
        "Gegen Dauertippen: Nach dem Start einer Kachel startet so lange keine andere "
        "(0 = aus); Vor/Zurück warten immer 3 Sekunden, Pause/Weiter 1 Sekunde. "
        "Läuft eine Kachel so lange, ohne dass jemand auf dem Tablet tippt, wird sie in "
        "einer Minute leiser und pausiert (0 = aus). Danach geht alles wie gewohnt weiter."
    ),
    "admin.idle_paused": "Zuletzt ohne Tippen pausiert",
    "admin.resume": "Weiterhören",
    "admin.resume_position": "Stand: Titel {track}, {time}",
    "admin.resume_restart": "Von vorn",
    "admin.schedule": "Nutzungszeiten",
    "admin.schedule_enabled": "Nutzungszeiten einschalten",
    "admin.day_mon": "Montag",
    "admin.day_tue": "Dienstag",
    "admin.day_wed": "Mittwoch",
    "admin.day_thu": "Donnerstag",
    "admin.day_fri": "Freitag",
    "admin.day_sat": "Samstag",
    "admin.day_sun": "Sonntag",
    "admin.from": "Von",
    "admin.to": "Bis",
    "admin.day_free": "ganzer Tag frei",
    "admin.copy_monday": "Zeiten vom Montag für alle Tage übernehmen",
    "admin.fade_minutes": "Vor dem Ende leiser werden (Minuten)",
    "admin.schedule_hint": (
        "Außerhalb der Zeiten sind die Kacheln gesperrt; Pause und Leiser gehen weiter. "
        "Am Ende pausiert die Musik einmal, danach bleibt die Sonos-App frei. "
        "Ein Tag ohne Zeitfenster ist frei; „Bis 00:00“ heißt bis Mitternacht. "
        "Für einen langen Abend gibt es die Freigabe."
    ),
    "admin.phase_off": "Keine Nutzungszeiten eingestellt: Muckebox ist immer erlaubt.",
    "admin.phase_open": "Jetzt erlaubt bis {time}.",
    "admin.phase_open_free": "Jetzt erlaubt.",
    "admin.phase_fading": "Klingt gerade aus, Ende um {time}.",
    "admin.phase_closed": "Schlafenszeit bis {time}.",
    "admin.phase_closed_open_end": "Schlafenszeit.",
    "admin.override_until": "Freigabe bis {time}.",
    "admin.override_15": "+15 Min.",
    "admin.override_30": "+30 Min.",
    "admin.override_60": "+1 Std.",
    "admin.override_morning": "Bis morgen früh",
    "admin.override_end": "Freigabe beenden",
    "admin.sleep_timer": "Einschlaf-Timer",
    "admin.sleep_enabled": "Mond-Knopf für die Kinder zeigen",
    "admin.sleep_minutes": "Dauer (Minuten)",
    "admin.sleep_wake": "Gesperrt bis (an Tagen ohne Nutzungszeit)",
    "admin.sleep_hint": (
        "Das Kind tippt auf den Mond und die Musik läuft noch so lange. Zum Schluss wird sie "
        "leiser und pausiert; danach bleiben die Kacheln bis zur nächsten erlaubten Zeit "
        "gesperrt. Eine Freigabe hebt das auf."
    ),
    "admin.sleep_running": "Läuft bis {time}.",
    "admin.sleep_cancel": "Timer beenden",
    "admin.games": "Spiele",
    "admin.games_status": "Heute gespielt: {used} von {limit} Minuten.",
    "admin.games_minutes": "Spielzeit pro Tag für alle Spiele (Minuten)",
    "admin.dance_tile": "Musik für den Stopptanz",
    "admin.dance_current": "– die Musik, die gerade läuft –",
    "admin.games_hint": (
        "Jedes Spiel ist erst nach dem Einschalten für die Kinder sichtbar. Runden sind kurz "
        "und enden ruhig, ohne Punkte. Die Atemübung zählt nicht zur Spielzeit und geht auch "
        "zur Schlafenszeit."
    ),
    "admin.game_level": "Schwierigkeit",
    "admin.credits": "Quellen der Klänge und Bilder",
    "admin.credit_pictures": "Bilder",
    "admin.level_1": "klein (2–3 Jahre)",
    "admin.level_2": "mittel (4–5 Jahre)",
    "admin.level_3": "groß (ab 6 Jahren)",
    "admin.game_about_freeze_dance": (
        "Musik läuft auf dem Sonos und stoppt immer wieder: alle erstarren. Der Bildschirm "
        "zeigt nur eine ruhige Farbe."
    ),
    "admin.game_about_sound_quiz": (
        "Das Tablet spielt ein Geräusch, und das Kind tippt auf das passende Bild."
    ),
    "admin.game_about_move_like": (
        "„Beweg dich wie ein Elefant!“: Bild, Stimme und Geräusch, das Kind bewegt sich dazu."
    ),
    "admin.game_about_breathing": "Ruhige Atemübung zum Einschlafen; der Bildschirm wird dunkel.",
    "sound.dog": "der Hund",
    "sound.cat": "die Katze",
    "sound.rooster": "der Hahn",
    "sound.sheep": "das Schaf",
    "sound.horse": "das Pferd",
    "sound.elephant": "der Elefant",
    "sound.lion": "der Löwe",
    "sound.donkey": "der Esel",
    "sound.doorbell": "die Türklingel",
    "sound.car_horn": "die Autohupe",
    "sound.train": "der Zug",
    "sound.clock": "die Uhr",
    "sound.water": "das Wasser",
    "sound.bicycle_bell": "die Fahrradklingel",
    "sound.church_bells": "die Kirchenglocken",
    "sound.rain": "der Regen",
    "move.elephant": "wie ein Elefant",
    "move.cat": "wie eine Katze",
    "move.frog": "wie ein Frosch",
    "move.horse": "wie ein Pferd",
    "move.lion": "wie ein Löwe",
    "move.rooster": "wie ein Hahn",
    "move.bird": "wie ein Vogel",
    "move.snake": "wie eine Schlange",
    "move.turtle": "wie eine Schildkröte",
    "move.rabbit": "wie ein Hase",
    "move.mouse": "wie eine Maus",
    "move.bear": "wie ein Bär",
    "move.butterfly": "wie ein Schmetterling",
    "move.penguin": "wie ein Pinguin",
    "game.freeze_dance": "Stopptanz",
    "game.sound_quiz": "Geräusche-Rätsel",
    "game.move_like": "Beweg dich wie …",
    "game.breathing": "Atemübung",
    "admin.pin_change": "PIN ändern",
    "admin.pin_current": "Aktuelle PIN",
    "admin.pin_new": "Neue PIN",
    "admin.pin_repeat": "Neue PIN wiederholen",
    "admin.pin_hint": (
        "Mindestens 4 Zeichen. Nach dem Ändern sind alle anderen Geräte abgemeldet."
    ),
    "admin.pin_mismatch": "Die beiden neuen PINs stimmen nicht überein.",
    "admin.pin_changed": "PIN geändert. Andere Geräte sind jetzt abgemeldet.",
    "admin.kids_view": "Zur Kinderansicht",
    "admin.tiles": "Kacheln",
    "admin.tiles_empty": "Noch keine Kacheln. Füge unten Favoriten oder Links hinzu.",
    "admin.rename": "Umbenennen",
    "admin.save": "Speichern",
    "admin.up": "Nach oben",
    "admin.down": "Nach unten",
    "admin.delete": "Entfernen",
    "admin.delete_confirm": "Kachel „{title}“ wirklich entfernen?",
    "admin.cover": "Eigenes Bild",
    "admin.favorites": "Sonos-Favoriten",
    "admin.favorites_hint": (
        "Alles, was in der Sonos-App als Favorit gespeichert ist, kann eine Kachel werden."
    ),
    "admin.refresh": "Neu laden",
    "admin.add": "Hinzufügen",
    "admin.added": "Schon als Kachel da",
    "admin.link": "Link hinzufügen",
    "admin.link_hint": (
        "Teilen-Link aus Apple Music, Spotify, TIDAL oder Deezer einfügen. "
        "Sonos spielt ihn mit dem in der Sonos-App verknüpften Konto."
    ),
    "admin.link_placeholder": "https://music.apple.com/…",
    "admin.status": "Status",
    "admin.room": "Raum",
    "admin.connection": "Verbindung",
    "admin.max_volume": "Lautstärke-Limit",
    "admin.corrections": "Lautstärke-Korrekturen",
    "admin.clock": "Uhrzeit der Muckebox",
    "admin.zone_differs": (
        "Die Muckebox rechnet mit der Zeitzone {server}, dieses Gerät mit {browser}. "
        "Nutzungszeiten richten sich nach der Muckebox."
    ),
    "admin.zone_adopt": "Zeitzone {browser} übernehmen",
    "admin.version": "Version",
    "admin.source": "Quellcode (AGPL-3.0)",
    "admin.saved": "Gespeichert",
    "admin.loading": "Lädt …",
    # Why a favorite cannot be played
    "reason.no_resource": "Verknüpfung ohne abspielbaren Inhalt (z. B. Künstler)",
    "reason.tv_input": "TV-Eingang",
    "reason.broken_metadata": "Sonos liefert keine vollständigen Daten zu diesem Favoriten",
    # Warnings after adding a tile
    "warning.cover_missing": "Kein Bild gefunden – du kannst ein eigenes hochladen.",
    "warning.title_missing": "Kein Titel gefunden – bitte umbenennen.",
    "warning.sharelink_experimental": (
        "Links von diesem Dienst sind noch nicht auf echter Hardware getestet."
    ),
    # Connection status
    "status.ok": "Verbunden",
    "status.starting": "Verbindet …",
    "status.not_configured": "Noch kein Raum gewählt",
    # -- errors (API codes) --------------------------------------------------------
    "error.bad_request": "Die Anfrage ist ungültig.",
    "error.csrf_header_missing": "Die Anfrage wurde aus Sicherheitsgründen abgelehnt.",
    "error.origin_mismatch": "Die Anfrage wurde aus Sicherheitsgründen abgelehnt.",
    "error.not_found": "Nicht gefunden.",
    "error.method_not_allowed": "Diese Aktion ist hier nicht möglich.",
    "error.request_too_large": "Die Anfrage ist zu groß.",
    "error.internal_error": "Ein unerwarteter Fehler ist aufgetreten.",
    "error.offline": "Keine Verbindung zur Muckebox.",
    "error.timeout": (
        "Die Muckebox hat nicht rechtzeitig geantwortet. Die Änderung ist eventuell trotzdem "
        "gespeichert – bitte die Liste prüfen."
    ),
    "error.library_corrupt": (
        "Die Kachel-Liste war beschädigt. Sie wurde als {file} im Datenordner beiseitegelegt; "
        "es geht mit einer leeren Liste weiter."
    ),
    "error.busy": "Einen Moment, die Muckebox ist noch beschäftigt.",
    "error.tile_not_found": "Diese Kachel gibt es nicht mehr.",
    "error.favorite_not_found": "Diesen Favoriten gibt es nicht mehr. Bitte neu laden.",
    "error.rev_conflict": "Die Kacheln wurden gerade woanders geändert. Bitte neu laden.",
    "error.title_invalid": "Der Titel muss 1 bis 60 Zeichen lang sein.",
    "error.library_error": "Die Kacheln konnten nicht gespeichert werden.",
    "error.upload_not_image": "Das ist kein Bild, das die Muckebox lesen kann (JPEG oder PNG).",
    "error.upload_too_large": "Das Bild ist zu groß (höchstens 10 MB).",
    "error.not_playable": "Dieser Favorit lässt sich nicht abspielen.",
    "error.sharelink_unsupported": (
        "Dieser Link wird nicht unterstützt. Speichere den Inhalt in der Sonos-App als "
        "Favorit und füge ihn hier als Favorit hinzu."
    ),
    "error.sharelink_unresolvable": "Der Link konnte nicht geöffnet werden.",
    "error.login_required": "Bitte zuerst anmelden.",
    "error.pin_wrong": "Die PIN ist falsch.",
    "error.pin_rate_limited": "Zu viele Versuche. Bitte in {retry_in} Sekunden erneut versuchen.",
    # Sonos
    "error.sonos_error": "Der Lautsprecher meldet einen Fehler.",
    "error.sonos_unreachable": (
        "Der Sonos-Lautsprecher ist nicht erreichbar. Ist er eingeschaltet und im Netzwerk?"
    ),
    "error.upnp_disabled": (
        "Sonos lehnt die Steuerung ab. In der Sonos-App unter Konto → Datenschutz & "
        "Sicherheit → Verbindungssicherheit „UPnP“ einschalten."
    ),
    "error.room_not_found": (
        "Der Raum wurde nicht gefunden. Ist der Lautsprecher an? Wenn Sonos in einem anderen "
        "Netz (VLAN) ist: auf der Eltern-Seite unter „Einrichtung“ die IP-Adresse eines "
        "Lautsprechers eintragen und neu suchen."
    ),
    "error.sonos_timeout": "Der Lautsprecher hat zu lange nicht geantwortet.",
    "error.group_problem": "Mit der Gruppierung der Räume stimmt etwas nicht.",
    "error.service_unavailable": (
        "Sonos konnte den Inhalt nicht laden. Ist der Musikdienst in der Sonos-App angemeldet?"
    ),
    "error.playback_failed": "Der Lautsprecher hat die Wiedergabe nicht gestartet.",
    "error.command_rejected": "Der Lautsprecher hat den Befehl abgelehnt.",
    "error.action_not_available": "Das geht gerade nicht.",
    "error.volume_unknown": "Die Lautstärke konnte nicht gelesen werden.",
    "error.not_configured": (
        "Die Muckebox ist noch nicht eingerichtet. Bitte auf der Eltern-Seite einen Raum wählen."
    ),
    # Configuration problems (shown to parents)
    "error.pin_generated": (
        "Die Eltern-PIN ist noch die erzeugte Start-PIN aus dem Log. "
        "Bitte eine eigene PIN festlegen."
    ),
    "error.settings_corrupt": (
        "Die gespeicherten Einstellungen waren beschädigt und wurden beiseitegelegt. "
        "Bitte Raum, Lautstärke und PIN neu festlegen."
    ),
    # Settings on the parents' page
    "error.room_invalid": "Bitte einen Raum wählen (höchstens 100 Zeichen).",
    "error.seed_ip_invalid": (
        "Die Lautsprecher-Adresse ist ungültig. Erlaubt sind eine IPv4-Adresse oder ein Hostname "
        "(ohne Port und ohne http://)."
    ),
    "error.max_volume_invalid": "Das Lautstärke-Limit muss eine ganze Zahl von 1 bis 100 sein.",
    "error.volume_step_invalid": (
        "Die Schrittweite muss eine ganze Zahl von 1 bis zum Lautstärke-Limit sein."
    ),
    "error.pin_too_short": "Die PIN muss mindestens 4 Zeichen lang sein.",
    "error.pin_placeholder": "Diese PIN ist zu leicht zu erraten. Bitte eine andere wählen.",
    "error.pin_invalid": (
        "Die PIN darf höchstens 64 Zeichen lang sein und keine Steuerzeichen enthalten."
    ),
    "error.bedtime": "Jetzt ist Schlafenszeit.",
    "error.game_unavailable": "Dieses Spiel geht gerade nicht.",
    "error.game_running": "Es läuft schon ein Spiel.",
    "error.games_limit_reached": "Die Spielzeit für heute ist vorbei.",
    "error.dance_music_missing": (
        "Für den Stopptanz fehlt die Musik: Eltern wählen sie auf der Eltern-Seite."
    ),
    "error.schedule_off": "Es sind keine Nutzungszeiten eingestellt.",
    "error.sleep_timer_off": "Der Einschlaf-Timer ist ausgeschaltet.",
    "error.schedule_invalid": "Die Nutzungszeiten sind ungültig.",
    "error.schedule_order_invalid": (
        "Das Ende muss am selben Tag nach dem Beginn liegen. Für einen langen Abend gibt es "
        "die Freigabe."
    ),
    "error.sleep_timer_invalid": (
        "Der Einschlaf-Timer braucht eine Dauer von 5 bis 90 Minuten und eine Aufwachzeit."
    ),
    "error.games_invalid": "Die Einstellungen der Spiele sind ungültig.",
    "error.controls_invalid": (
        "Die Kachel-Sperre darf 0 bis 30 Sekunden dauern, die Pause ohne Tippen 0 bis 240 Minuten."
    ),
    "error.cooling_down": "Einen Moment, gleich geht es wieder.",
    "error.time_zone_invalid": "Diese Zeitzone kennt die Muckebox nicht.",
    "error.pin_changed": "Die PIN wurde gerade woanders geändert. Bitte neu anmelden.",
    "error.settings_save_failed": (
        "Die Einstellungen konnten nicht gespeichert werden. Ist der Datenordner beschreibbar?"
    ),
}
