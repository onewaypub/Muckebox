# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""German user interface texts."""

MESSAGES: dict[str, str] = {
    # Generic API errors
    "error.bad_request": "Die Anfrage ist ungültig.",
    "error.csrf_header_missing": "Die Anfrage wurde aus Sicherheitsgründen abgelehnt.",
    "error.not_found": "Nicht gefunden.",
    "error.method_not_allowed": "Diese Aktion ist hier nicht möglich.",
    "error.request_too_large": "Die Anfrage ist zu groß.",
    "error.internal_error": "Ein unerwarteter Fehler ist aufgetreten.",
    # Configuration problems (shown to parents; the kids view shows a symbol)
    "error.sonos_not_configured": (
        "Es ist kein Sonos-Lautsprecher eingestellt. Bitte SONOS_ROOM oder SONOS_IP setzen."
    ),
    "error.sonos_ip_invalid": (
        "SONOS_IP ist ungültig. Erlaubt sind eine IPv4-Adresse oder ein Hostname."
    ),
    "error.max_volume_invalid": "MAX_VOLUME muss eine ganze Zahl von 1 bis 100 sein.",
    "error.volume_step_invalid": ("VOLUME_STEP muss eine ganze Zahl von 1 bis MAX_VOLUME sein."),
    "error.admin_pin_missing": (
        "Es ist keine ADMIN_PIN gesetzt. Die Eltern-Seite bleibt gesperrt."
    ),
    "error.admin_pin_too_short": (
        "Die ADMIN_PIN muss mindestens {min_length} Zeichen lang sein. "
        "Die Eltern-Seite bleibt gesperrt."
    ),
}
