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
    "admin.locked": (
        "Die Eltern-Seite ist gesperrt. Setze die Umgebungsvariable ADMIN_PIN "
        "(mindestens 4 Zeichen) und starte die Muckebox neu."
    ),
    "admin.pin": "PIN",
    "admin.login": "Anmelden",
    "admin.logout": "Abmelden",
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
    "admin.max_volume": "Maximale Lautstärke",
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
    "error.admin_locked": "Die Eltern-Seite ist gesperrt (keine ADMIN_PIN gesetzt).",
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
    "error.sonos_not_configured": (
        "Es ist kein Sonos-Lautsprecher eingestellt. Bitte SONOS_ROOM oder SONOS_IP setzen."
    ),
    "error.sonos_ip_invalid": (
        "SONOS_IP ist ungültig. Erlaubt sind eine IPv4-Adresse oder ein Hostname."
    ),
    "error.max_volume_invalid": "MAX_VOLUME muss eine ganze Zahl von 1 bis 100 sein.",
    "error.volume_step_invalid": "VOLUME_STEP muss eine ganze Zahl von 1 bis MAX_VOLUME sein.",
    "error.admin_pin_missing": "Es ist keine ADMIN_PIN gesetzt. Die Eltern-Seite bleibt gesperrt.",
    "error.admin_pin_placeholder": (
        "Die ADMIN_PIN ist noch ein Beispielwert. Bitte eine eigene PIN setzen; "
        "bis dahin bleibt die Eltern-Seite gesperrt."
    ),
    "error.admin_pin_too_short": (
        "Die ADMIN_PIN muss mindestens {min_length} Zeichen lang sein. "
        "Die Eltern-Seite bleibt gesperrt."
    ),
}
