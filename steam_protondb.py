#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Steam / ProtonDB checker.

Run with: uv run steam_protondb.py
Or:       python steam_protondb.py

Uses only Python's standard library. If saved, credentials are stored in
steam_protondb.env in the current working directory.
"""

from __future__ import annotations

import getpass
import html
import json
import os
import re
import select
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


APP_NAME = "Steam / ProtonDB Checker"
ENV_PATH = Path.cwd() / "steam_protondb.env"
USER_AGENT = "steam-protondb-cli/2.2 (Python urllib)"

STEAM_OWNED_GAMES_URL = (
    "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
)
STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
STEAM_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
PROTONDB_SUMMARY_URL = (
    "https://www.protondb.com/api/v1/reports/summaries/{appid}.json"
)

TIER_ORDER = {
    "native": 0,
    "platinum": 1,
    "gold": 2,
    "silver": 3,
    "bronze": 4,
    "borked": 5,
    "unknown": 6,
    "error": 7,
}

TIER_LABELS = {
    "native": "Native Linux",
    "platinum": "Platinum",
    "gold": "Gold",
    "silver": "Silver",
    "bronze": "Bronze",
    "borked": "Borked",
    "unknown": "No reports / unknown",
    "error": "Lookup error",
}

ANSI = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[97m",
}


@dataclass
class GameResult:
    appid: int
    name: str
    tier: str = "unknown"
    total: int = 0
    confidence: str = ""
    trending_tier: str = ""
    score: float | None = None
    error: str = ""
    playtime_minutes: int | None = None

    @property
    def protondb_url(self) -> str:
        return f"https://www.protondb.com/app/{self.appid}"

    @property
    def steam_url(self) -> str:
        return f"https://store.steampowered.com/app/{self.appid}/"

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(self.tier, self.tier.title())


def color(text: str, name: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR") is not None:
        return text

    return f"{ANSI.get(name, '')}{text}{ANSI['reset']}"


def print_header() -> None:
    subtitle = "Library scan  |  Single-game lookup  |  HTML reports"
    width = max(len(APP_NAME), len(subtitle)) + 4
    border = "─" * width

    print()
    print(color("┌" + border + "┐", "cyan"))
    print(color("│" + APP_NAME.center(width) + "│", "cyan"))
    print(color("│" + subtitle.center(width) + "│", "dim"))
    print(color("└" + border + "┘", "cyan"))
    print(color(f"Credentials file: {ENV_PATH}", "dim"))


def _read_keypress() -> str:
    """Read a key without Python text-stream buffering arrow-key bytes."""

    if os.name == "nt":
        import msvcrt

        key = msvcrt.getwch()

        if key in ("\x00", "\xe0"):
            return {
                "H": "UP",
                "P": "DOWN",
                "K": "LEFT",
                "M": "RIGHT",
            }.get(msvcrt.getwch(), "OTHER")

        if key in ("\r", "\n"):
            return "ENTER"

        if key == "\x03":
            raise KeyboardInterrupt

        return key

    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    def ready(timeout: float) -> bool:
        return bool(select.select([fd], [], [], timeout)[0])

    try:
        tty.setraw(fd)

        first = os.read(fd, 1)

        if first == b"\x03":
            raise KeyboardInterrupt

        if first in (b"\r", b"\n"):
            return "ENTER"

        if first != b"\x1b":
            try:
                return first.decode("utf-8")
            except UnicodeDecodeError:
                return "OTHER"

        # Escape can be a standalone key or the start of an arrow sequence.
        if not ready(0.12):
            return "ESC"

        second = os.read(fd, 1)

        if second not in (b"[", b"O"):
            return "ESC"

        sequence = bytearray()
        deadline = time.monotonic() + 0.12

        while time.monotonic() < deadline:
            if not ready(max(0.0, deadline - time.monotonic())):
                break

            char = os.read(fd, 1)

            if not char:
                break

            sequence.extend(char)

            # The final byte of a terminal escape sequence is 0x40 to 0x7e.
            if 0x40 <= char[0] <= 0x7E:
                break

            if len(sequence) >= 16:
                break

        if sequence:
            return {
                "A": "UP",
                "B": "DOWN",
                "C": "RIGHT",
                "D": "LEFT",
            }.get(chr(sequence[-1]), "OTHER")

        return "OTHER"

    finally:
        termios.tcsetattr(
            fd,
            termios.TCSADRAIN,
            old_settings,
        )


def ask_choice(prompt: str, choices: dict[str, str]) -> str:
    """Arrow-key menu that updates in place without clearing earlier output."""

    entries = list(choices.items())

    if not entries:
        raise ValueError("Menu needs at least one choice")

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        while True:
            print(f"\n{prompt}")

            for key, label in entries:
                print(f"  {key}) {label}")

            answer = input("Choice: ").strip().lower()

            if answer in choices:
                return answer

            print(
                color(
                    "Please choose one of: " + ", ".join(choices),
                    "yellow",
                )
            )

    selected = 0
    previous_lines = 0
    guide = "Use ↑/↓ or j/k, then Enter. Number keys select directly."

    while True:
        lines = [
            color(prompt, "white"),
            color(guide, "dim"),
        ]

        for index, (key, label) in enumerate(entries):
            marker = " ❯ " if index == selected else "   "
            text = f"{marker}{key}) {label}"

            if index == selected:
                lines.append(f"\033[1;36;7m{text}\033[0m")
            else:
                lines.append(color(text, "dim"))

        if previous_lines:
            # The cursor rests on the blank line following the menu.
            # Return to the first line and overwrite only the menu.
            sys.stdout.write(f"\033[{previous_lines + 1}A")

        for line in lines:
            sys.stdout.write("\r\033[2K" + line + "\n")

        sys.stdout.write("\r\033[2K\n")
        sys.stdout.flush()

        previous_lines = len(lines)
        keypress = _read_keypress()

        if keypress == "UP" or keypress in ("k", "K"):
            selected = (selected - 1) % len(entries)

        elif keypress == "DOWN" or keypress in ("j", "J"):
            selected = (selected + 1) % len(entries)

        elif keypress == "ENTER":
            return entries[selected][0]

        elif keypress in choices:
            return keypress


def load_saved_credentials() -> tuple[str, str] | None:
    if not ENV_PATH.is_file():
        return None

    values: dict[str, str] = {}

    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            name, value = line.split("=", 1)
            values[name.strip()] = value.strip().strip('"').strip("'")

    except OSError:
        return None

    steam_id = values.get("STEAM_ID64", "")
    api_key = values.get("STEAM_API_KEY", "")

    if re.fullmatch(r"\d{17}", steam_id) and api_key:
        return steam_id, api_key

    return None


def save_credentials(steam_id: str, api_key: str) -> None:
    # The file is local to the current working directory.
    ENV_PATH.write_text(
        "# Steam credentials for steam_protondb.py. Keep private.\n"
        f"STEAM_ID64={steam_id}\n"
        f"STEAM_API_KEY={api_key}\n",
        encoding="utf-8",
    )

    if os.name != "nt":
        try:
            ENV_PATH.chmod(0o600)
        except OSError:
            pass


def request_json(
    url: str,
    params: dict[str, Any] | None = None,
    timeout: int = 20,
) -> Any:
    if params:
        separator = "&" if "?" in url else "?"
        url = url + separator + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def input_steam_credentials() -> tuple[str, str]:
    saved = load_saved_credentials()

    if saved:
        print(color(f"Saved credentials found in {ENV_PATH}", "cyan"))

        choice = ask_choice(
            "How should credentials be handled?",
            {
                "1": "Use saved SteamID64 and API key",
                "2": "Enter different credentials",
                "q": "Cancel",
            },
        )

        if choice == "1":
            return saved

        if choice == "q":
            raise KeyboardInterrupt

    print(
        "\nSteam library access requires a SteamID64 "
        "and Steam Web API key.\n"
    )

    print("SteamID64:")
    print(
        "  • Open your Steam profile. "
        "A numeric profile URL often contains the ID."
    )
    print(
        "  • For a custom profile URL, try "
        "https://steamid.io/lookup"
    )
    print(
        "  • Enter the 17-digit SteamID64 below, "
        "not your account password.\n"
    )

    while True:
        steam_id = input("SteamID64: ").strip()

        if re.fullmatch(r"\d{17}", steam_id):
            break

        print(color("Expected a 17-digit SteamID64. Try again.", "yellow"))

    print("\nSteam Web API key:")
    print(
        "  • Sign in to Steam and open "
        "https://steamcommunity.com/dev/apikey"
    )
    print(
        "  • Create or copy your key. It is hidden while you type.\n"
    )

    while True:
        api_key = getpass.getpass("Steam Web API key: ").strip()

        if api_key:
            break

        print(color("The API key cannot be empty.", "yellow"))

    choice = ask_choice(
        "Save credentials for future runs? "
        "The env file stores the key as plain text.",
        {
            "1": f"Yes, save to {ENV_PATH}",
            "2": "No, use only for this run",
        },
    )

    if choice == "1":
        try:
            save_credentials(steam_id, api_key)

            print(color(f"Credentials saved to {ENV_PATH}", "green"))
            print(
                color(
                    "Keep this file private and do not commit it to Git.",
                    "yellow",
                )
            )

        except OSError as exc:
            print(color(f"Could not save credentials: {exc}", "yellow"))
            print("The credentials will still be used for this run.")

    return steam_id, api_key


def fetch_owned_games(
    steam_id: str,
    api_key: str,
) -> list[dict[str, Any]]:
    payload = request_json(
        STEAM_OWNED_GAMES_URL,
        {
            "key": api_key,
            "steamid": steam_id,
            "include_appinfo": 1,
            "include_played_free_games": 1,
            "format": "json",
        },
    )

    response = (
        payload.get("response", {})
        if isinstance(payload, dict)
        else {}
    )

    if "games" not in response:
        raise RuntimeError(
            "Steam returned no game list. Check the SteamID64 and API key, "
            "and make sure the Steam profile's Game details privacy setting "
            "is Public."
        )

    games = response.get("games") or []

    if not games:
        raise RuntimeError(
            "Steam returned an empty library. Game details may be private, "
            "or Steam may not expose any games for this account. Set Profile > "
            "Edit Profile > Privacy > Game details to Public and try again."
        )

    return games


def protondb_lookup(appid: int) -> dict[str, Any]:
    url = PROTONDB_SUMMARY_URL.format(appid=appid)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected response from ProtonDB")

        return payload

    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {
                "tier": "unknown",
                "total": 0,
                "confidence": "",
            }

        if exc.code == 429:
            time.sleep(2)

            try:
                return request_json(url, timeout=25)
            except Exception as retry_exc:
                raise RuntimeError(
                    "ProtonDB rate limit or request failure"
                ) from retry_exc

        raise RuntimeError(f"ProtonDB returned HTTP {exc.code}") from exc

    except urllib.error.URLError as exc:
        raise RuntimeError("Could not connect to ProtonDB") from exc

    except json.JSONDecodeError as exc:
        raise RuntimeError("ProtonDB returned invalid JSON") from exc


def check_game(
    appid: int,
    name: str,
    playtime_minutes: int | None = None,
) -> GameResult:
    result = GameResult(
        appid=appid,
        name=name,
        playtime_minutes=playtime_minutes,
    )

    try:
        summary = protondb_lookup(appid)

        tier = str(
            summary.get("tier")
            or summary.get("provisionalTier")
            or "unknown"
        ).lower()

        result.tier = tier if tier in TIER_ORDER else "unknown"

        try:
            result.total = int(summary.get("total") or 0)
        except (TypeError, ValueError):
            result.total = 0

        result.confidence = str(summary.get("confidence") or "")
        result.trending_tier = str(summary.get("trendingTier") or "")

        try:
            score = summary.get("score")
            result.score = float(score) if score is not None else None
        except (TypeError, ValueError):
            result.score = None

    except Exception as exc:
        result.tier = "error"
        result.error = str(exc)

    return result


def tier_color(tier: str) -> str:
    return {
        "native": "green",
        "platinum": "green",
        "gold": "yellow",
        "silver": "cyan",
        "bronze": "magenta",
        "borked": "red",
        "unknown": "dim",
        "error": "red",
    }.get(tier, "dim")


def _animate_progress(
    stop: threading.Event,
    label: str,
    future: Any,
) -> None:
    frames = [
        "[■□□□□]",
        "[■■□□□]",
        "[■■■□□]",
        "[■■■■□]",
        "[■■■■■]",
        "[□■■■■]",
        "[□□■■■]",
        "[□□□■■]",
        "[□□□□■]",
        "[□□□□□]",
    ]

    index = 0

    while not stop.is_set() and not future.done():
        text = f"  {frames[index % len(frames)]} Checking {label[:48]}"

        print("\r" + text.ljust(75), end="", flush=True)

        index += 1
        stop.wait(0.09)

    print("\r" + (" " * 78) + "\r", end="", flush=True)


def check_with_progress(
    appid: int,
    name: str,
    playtime_minutes: int | None = None,
) -> GameResult:
    stop = threading.Event()
    worker = ThreadPoolExecutor(max_workers=1)

    future = worker.submit(
        check_game,
        appid,
        name,
        playtime_minutes,
    )

    try:
        if sys.stdout.isatty():
            _animate_progress(stop, name, future)

        result = future.result()

    finally:
        stop.set()
        worker.shutdown(wait=True)

    status = result.tier_label

    if result.total:
        status += (
            f" · {result.total} report"
            f"{'s' if result.total != 1 else ''}"
        )
    elif result.tier == "unknown":
        status += " · no summary available"

    if result.error:
        status += f" · {result.error}"

    icon = (
        color("✓", "green")
        if result.tier != "error"
        else color("!", "red")
    )

    print(
        f"  {icon} {result.name} ({result.appid}) — "
        f"{color(status, tier_color(result.tier))}"
    )

    return result


def parse_appid(user_input: str) -> int | None:
    value = user_input.strip()

    match = re.search(r"/app/(\d+)(?:/|$|\?)", value)

    if match:
        return int(match.group(1))

    if re.fullmatch(r"\d+", value):
        return int(value)

    return None


def search_steam_games(query: str) -> list[dict[str, Any]]:
    payload = request_json(
        STEAM_SEARCH_URL,
        {
            "term": query,
            "l": "english",
            "cc": "US",
        },
    )

    items = (
        payload.get("items", [])
        if isinstance(payload, dict)
        else []
    )

    return [
        item
        for item in items
        if (
            isinstance(item, dict)
            and item.get("id")
            and item.get("name")
        )
    ]


def get_game_name(appid: int) -> str:
    try:
        payload = request_json(
            STEAM_DETAILS_URL,
            {
                "appids": str(appid),
                "filters": "basic",
                "l": "english",
                "cc": "US",
            },
        )

        entry = (
            payload.get(str(appid), {})
            if isinstance(payload, dict)
            else {}
        )

        data = (
            entry.get("data", {})
            if isinstance(entry, dict) and entry.get("success")
            else {}
        )

        if isinstance(data, dict) and data.get("name"):
            return str(data["name"])

    except Exception:
        pass

    return f"Steam app {appid}"


def resolve_single_game(query: str) -> tuple[int, str] | None:
    """Resolve one game query. The caller splits comma-separated batches."""

    query = query.strip()

    if not query:
        return None

    print(color(f"\nSearching: {query}", "cyan"))

    appid = parse_appid(query)

    if appid is not None:
        return appid, get_game_name(appid)

    try:
        items = search_steam_games(query)

    except Exception as exc:
        print(color(f"Steam search failed for '{query}': {exc}", "red"))
        return None

    if not items:
        print(color(f"No Steam games found for '{query}'.", "yellow"))
        return None

    if len(items) == 1:
        return int(items[0]["id"]), str(items[0]["name"])

    candidates = items[:8]

    options = {
        str(index): f"{item['name']} (App ID {item['id']})"
        for index, item in enumerate(candidates, start=1)
    }

    options["0"] = "Skip this game"

    selection = ask_choice(
        f"Select the matching game for '{query}':",
        options,
    )

    if selection == "0":
        return None

    item = candidates[int(selection) - 1]

    return int(item["id"]), str(item["name"])


def _truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""

    if len(text) <= width:
        return text

    if width == 1:
        return "…"

    return text[: width - 1] + "…"


def _playtime_text(minutes: int | None) -> str:
    if minutes is None:
        return "—"

    if minutes < 60:
        return f"{minutes}m"

    return f"{minutes / 60:.1f}h"


def print_results_table(
    results: list[GameResult],
    *,
    sort_by_tier: bool = False,
) -> None:
    """Print a terminal table that stays visible when the program exits."""

    if not results:
        print(color("No results to display.", "yellow"))
        return

    items = list(results)

    if sort_by_tier:
        items.sort(
            key=lambda result: (
                TIER_ORDER.get(result.tier, 99),
                result.name.casefold(),
            )
        )

    terminal_width = shutil.get_terminal_size((100, 24)).columns

    widths = [
        max(12, min(40, terminal_width - 49)),
        7,
        20,
        7,
        9,
    ]

    headers = [
        "GAME",
        "APP ID",
        "PROTONDB CATEGORY",
        "REPORTS",
        "PLAYTIME",
    ]

    overhead = len(widths) * 3 + 1
    available = max(12, terminal_width - overhead)
    fixed_width = sum(widths[1:])

    widths[0] = max(
        12,
        min(widths[0], available - fixed_width),
    )

    def border(left: str, middle: str, right: str) -> str:
        return (
            left
            + middle.join("─" * (width + 2) for width in widths)
            + right
        )

    def row(
        values: list[str],
        tier: str | None = None,
    ) -> str:
        parts = []

        for index, value in enumerate(values):
            shown = _truncate(value, widths[index])
            padded = shown.ljust(widths[index])

            if index == 2 and tier is not None:
                padded = color(padded, tier_color(tier))

            parts.append(f" {padded} ")

        return "│" + "│".join(parts) + "│"

    print()
    print(color(f"Results: {len(items)} game(s)", "white"))
    print(color(border("┌", "┬", "┐"), "cyan"))
    print(color(row(headers), "white"))
    print(color(border("├", "┼", "┤"), "cyan"))

    for result in items:
        print(
            row(
                [
                    result.name,
                    str(result.appid),
                    result.tier_label,
                    str(result.total),
                    _playtime_text(result.playtime_minutes),
                ],
                result.tier,
            )
        )

    print(color(border("└", "┴", "┘"), "cyan"))

    print("Game links can be opened using the App ID:")
    print("  Steam:    https://store.steampowered.com/app/APP_ID/")
    print("  ProtonDB: https://www.protondb.com/app/APP_ID")

    errors = [result for result in items if result.error]

    if errors:
        print(color("Lookup errors:", "red"))

        for result in errors:
            print(f"  {result.name} ({result.appid}): {result.error}")

    print()


def html_report(
    results: list[GameResult],
    title: str = "Steam / ProtonDB Library Report",
) -> Path:
    now = datetime.now().astimezone()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S %Z")

    output = (
        Path.cwd()
        / f"steam_protondb_report_{now.strftime('%Y%m%d_%H%M%S')}.html"
    )

    rows: list[str] = []

    for result in sorted(
        results,
        key=lambda item: (
            TIER_ORDER.get(item.tier, 99),
            item.name.casefold(),
        ),
    ):
        tier_class = (
            result.tier
            if result.tier in TIER_ORDER
            else "unknown"
        )

        score = (
            f"{result.score:g}"
            if result.score is not None
            else "—"
        )

        playtime = _playtime_text(result.playtime_minutes)

        rows.append(
            f'<tr data-tier="{tier_class}">'
            f'<td class="game"><a href="{html.escape(result.steam_url, quote=True)}" target="_blank" rel="noopener">{html.escape(result.name)}</a>'
            f'<small>App ID {result.appid} · <a href="{html.escape(result.protondb_url, quote=True)}" target="_blank" rel="noopener">ProtonDB details</a></small></td>'
            f'<td><span class="badge {tier_class}">{html.escape(result.tier_label)}</span></td>'
            f"<td>{result.total}</td><td>{score}</td>"
            f"<td>{html.escape(result.confidence or '—')}</td>"
            f"<td>{html.escape(playtime)}</td>"
            f'<td class="error">{html.escape(result.error)}</td></tr>'
        )

    counts = {
        tier: sum(1 for result in results if result.tier == tier)
        for tier in TIER_ORDER
    }

    summary_cards = "".join(
        f'<div class="summary-card"><span>{html.escape(TIER_LABELS[tier])}</span><strong>{count}</strong></div>'
        for tier, count in counts.items()
        if count
    )

    tier_options = "".join(
        f'<option value="{tier}">{html.escape(TIER_LABELS[tier])}</option>'
        for tier in TIER_ORDER
    )

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{
 color-scheme:dark;
 --bg:#11151c;
 --panel:#1b222c;
 --line:#303b49;
 --text:#e6edf5;
 --muted:#9eacbd;
 --accent:#76b7ff;
}}
* {{ box-sizing:border-box; }}
body {{
 margin:0;
 background:var(--bg);
 color:var(--text);
 font:15px/1.5 system-ui,-apple-system,sans-serif;
}}
main {{ max-width:1400px; margin:0 auto; padding:32px 22px 60px; }}
h1 {{ margin:0 0 6px; font-size:clamp(1.7rem,3vw,2.4rem); }}
.muted {{ color:var(--muted); }}
.summary {{
 display:grid;
 grid-template-columns:repeat(auto-fit,minmax(145px,1fr));
 gap:10px;
 margin:24px 0;
}}
.summary-card {{
 background:var(--panel);
 border:1px solid var(--line);
 border-radius:12px;
 padding:14px;
}}
.summary-card span {{ display:block; color:var(--muted); font-size:.86rem; }}
.summary-card strong {{ display:block; font-size:1.6rem; margin-top:3px; }}
.controls {{ display:flex; flex-wrap:wrap; gap:10px; margin:20px 0; }}
input,select {{
 background:var(--panel);
 color:var(--text);
 border:1px solid var(--line);
 border-radius:8px;
 padding:10px 12px;
 font:inherit;
}}
input {{ flex:1; min-width:220px; }}
.table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:12px; }}
table {{ width:100%; border-collapse:collapse; min-width:920px; }}
th,td {{ text-align:left; padding:12px 14px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ position:sticky; top:0; background:#202936; cursor:pointer; white-space:nowrap; }}
tbody tr:hover {{ background:#1b2532; }}
a {{ color:var(--accent); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
.game {{ min-width:220px; font-weight:600; }}
small {{ display:block; color:var(--muted); font-weight:400; margin-top:3px; }}
.badge {{ display:inline-block; border-radius:999px; padding:3px 9px; font-size:.82rem; white-space:nowrap; }}
.native,.platinum {{ color:#b7f5c5; background:#153b2a; }}
.gold {{ color:#ffe6a0; background:#4a3913; }}
.silver {{ color:#c8e5ff; background:#1b3b54; }}
.bronze {{ color:#ffd0b0; background:#4c2d1e; }}
.borked,.error {{ color:#ffb4b4; }}
.unknown {{ color:#d2dae5; background:#343d49; }}
footer {{ margin-top:20px; color:var(--muted); font-size:.86rem; }}
@media(max-width:600px) {{ main {{ padding:20px 12px 40px; }} }}
</style>
</head>
<body>
<main>
<h1>{html.escape(title)}</h1>
<div class="muted">
Generated {html.escape(timestamp)} · {len(results)} games checked.
ProtonDB tiers are community summaries, not guarantees for every setup.
</div>
<section class="summary" aria-label="Category totals">{summary_cards}</section>
<div class="controls">
<input id="search" type="search" placeholder="Filter games by name or App ID…" aria-label="Search games">
<select id="tier" aria-label="Filter by tier">
<option value="all">All tiers</option>{tier_options}
</select>
</div>
<div class="table-wrap">
<table id="games">
<thead><tr>
<th data-sort="text">Game / links</th>
<th data-sort="text">ProtonDB category</th>
<th data-sort="number">Reports</th>
<th data-sort="number">Score</th>
<th data-sort="text">Confidence</th>
<th data-sort="text">Playtime</th>
<th>Notes</th>
</tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</div>
<footer>
ProtonDB is community-reported. “No reports / unknown” means no
summary was available; it does not itself mean a game is unplayable.
This report was generated locally.
</footer>
</main>
<script>
const search=document.getElementById('search'), tier=document.getElementById('tier');
function filterRows() {{
 const q=search.value.toLowerCase();
 for(const row of document.querySelectorAll('#games tbody tr')) {{
  const text=row.innerText.toLowerCase();
  row.hidden=!(text.includes(q)&&(tier.value==='all'||row.dataset.tier===tier.value));
 }}
}}
search.addEventListener('input',filterRows);
tier.addEventListener('change',filterRows);
for(const th of document.querySelectorAll('th[data-sort]')) {{
 th.addEventListener('click',()=>{{
  const table=th.closest('table'),body=table.querySelector('tbody');
  const idx=[...th.parentNode.children].indexOf(th);
  const rows=[...body.rows],dir=th.dataset.dir==='asc'?'desc':'asc';
  th.dataset.dir=dir;
  rows.sort((a,b)=>{{
   const x=a.cells[idx].innerText.trim(),y=b.cells[idx].innerText.trim();
   const numeric=th.dataset.sort==='number';
   const cmp=numeric?(parseFloat(x)||0)-(parseFloat(y)||0):x.localeCompare(y);
   return dir==='asc'?cmp:-cmp;
  }});
  rows.forEach(row=>body.appendChild(row));
 }});
}}
</script>
</body>
</html>"""

    output.write_text(document, encoding="utf-8")
    return output


def print_clickable_html_path(path: Path) -> None:
    """Print a terminal hyperlink where OSC 8 is supported."""

    resolved = path.resolve()

    if sys.stdout.isatty():
        link = (
            f"\033]8;;{resolved.as_uri()}\033\\"
            f"{resolved}"
            f"\033]8;;\033\\"
        )

        print(f"HTML report: {link}")
        print(
            color(
                "Ctrl+click the path if your terminal supports hyperlinks.",
                "dim",
            )
        )

    else:
        print(f"HTML report: {resolved}")
        print(f"File URI: {resolved.as_uri()}")


def offer_html_report(
    results: list[GameResult],
    title: str = "Steam / ProtonDB Library Report",
) -> None:
    choice = ask_choice(
        "Generate an HTML report?",
        {
            "1": "Yes, generate HTML report",
            "2": "No, skip HTML export",
        },
    )

    if choice != "1":
        print("HTML report skipped.")
        return

    try:
        path = html_report(results, title)

    except OSError as exc:
        print(color(f"Could not write the HTML report: {exc}", "red"))
        print("Check that the current directory is writable.")
        return

    print_clickable_html_path(path)

    open_choice = ask_choice(
        "Open the report in your default browser now?",
        {
            "1": "Yes, open in browser",
            "2": "No, leave it for later",
        },
    )

    if open_choice == "1":
        try:
            if webbrowser.open(path.resolve().as_uri()):
                print(
                    color(
                        "Opened the report in your default browser.",
                        "green",
                    )
                )
            else:
                print(
                    color(
                        "Could not launch a browser automatically. "
                        "Use the path above.",
                        "yellow",
                    )
                )

        except Exception as exc:
            print(color(f"Could not open the browser: {exc}", "yellow"))


def run_library_scan() -> None:
    """Scan a library, optionally export HTML, print results, and exit."""

    try:
        steam_id, api_key = input_steam_credentials()

    except KeyboardInterrupt:
        print("Cancelled.")
        return

    print("\nFetching your Steam library…")

    try:
        games = fetch_owned_games(steam_id, api_key)

    except Exception as exc:
        print(color(f"\nCould not fetch the Steam library: {exc}", "red"))
        return

    mode = ask_choice(
        f"Found {len(games)} games. What should be scanned?",
        {
            "1": "Only games with playtime",
            "2": "Full Steam library",
        },
    )

    if mode == "1":
        games = [
            game
            for game in games
            if int(game.get("playtime_forever") or 0) > 0
        ]

    games.sort(
        key=lambda game: str(
            game.get("name") or f"App {game.get('appid', '')}"
        ).casefold()
    )

    if not games:
        print(
            color(
                "No games match that selection. "
                "The account may not have any playtime yet.",
                "yellow",
            )
        )
        return

    print(
        f"\nScanning {len(games)} games. "
        "Results appear as each lookup finishes.\n"
    )

    results: list[GameResult] = []

    for index, game in enumerate(games, start=1):
        try:
            appid = int(game["appid"])

        except (KeyError, TypeError, ValueError):
            continue

        name = str(game.get("name") or f"Steam app {appid}")
        playtime = int(game.get("playtime_forever") or 0)

        print(color(f"[{index}/{len(games)}]", "dim"), end=" ")

        results.append(
            check_with_progress(
                appid,
                name,
                playtime,
            )
        )

        # A small delay reduces the rate of requests to ProtonDB.
        if index < len(games):
            time.sleep(0.15)

    print("\nScan complete.")

    offer_html_report(results)

    # The final table is printed after the HTML question.
    # Choosing "No" never clears or hides the scan results.
    print_results_table(results, sort_by_tier=True)


def run_single_games() -> None:
    """Look up multiple games, separated by commas, then display one table."""

    results: list[GameResult] = []
    seen_appids: set[int] = set()

    while True:
        print(
            "\nEnter a game name, Steam store URL, or App ID."
            "\nSeparate multiple games with commas."
            "\nExample: RimWorld, PEAK, Mirror, Portal 2"
            "\nEnter q to finish and show results."
        )

        raw = input("Games: ").strip()

        if raw.lower() in {"q", "quit", "exit"}:
            break

        queries = [
            part.strip()
            for part in raw.split(",")
            if part.strip()
        ]

        if not queries:
            print(color("Enter at least one game.", "yellow"))
            continue

        for query in queries:
            resolved = resolve_single_game(query)

            if resolved is None:
                continue

            appid, name = resolved

            if appid in seen_appids:
                print(
                    color(
                        f"Already checked {name} ({appid}); "
                        "skipping duplicate.",
                        "dim",
                    )
                )
                continue

            seen_appids.add(appid)

            results.append(
                check_with_progress(
                    appid,
                    name,
                )
            )

        if results:
            next_step = ask_choice(
                "What next?",
                {
                    "1": "Enter more games",
                    "2": "Finish and show results",
                },
            )

            if next_step == "2":
                break

    if not results:
        print(color("No games were checked.", "yellow"))
        return

    offer_html_report(
        results,
        "Steam / ProtonDB Game Lookup Results",
    )

    # Always display final results after the optional HTML export.
    print_results_table(results)


def main() -> int:
    try:
        print_header()

        choice = ask_choice(
            "What do you want to check?",
            {
                "1": "Scan a Steam library",
                "2": "Look up one or more games",
                "q": "Quit",
            },
        )

        if choice == "1":
            run_library_scan()

        elif choice == "2":
            run_single_games()

        return 0

    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130

    except EOFError:
        print("\nInput closed.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())