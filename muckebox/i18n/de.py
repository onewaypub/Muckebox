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
        "Der Raum wurde nicht gefunden. SONOS_ROOM prüfen oder SONOS_IP setzen "
        "(nötig, wenn Sonos in einem anderen Netzwerk/VLAN ist)."
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
    "error.pin_changed": "Die PIN wurde gerade woanders geändert. Bitte neu anmelden.",
    "error.settings_save_failed": (
        "Die Einstellungen konnten nicht gespeichert werden. Ist der Datenordner beschreibbar?"
    ),
}
