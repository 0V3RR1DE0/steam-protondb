# Steam / ProtonDB Checker

A lightweight command-line tool for checking Steam games against [ProtonDB](https://www.protondb.com/) to see how well they run on Linux through Steam Play and Proton.

The tool provides an interactive terminal menu, individual game lookups, Steam library scanning, and optional HTML reports.

## Features

- Interactive terminal menus with arrow-key navigation.
- Look up games by name, Steam App ID, or Steam store URL.
- Search for multiple games in one session using comma-separated names.
- Select the correct game when Steam returns multiple search results.
- Scan a Steam library using your SteamID64 and Steam Web API key.
- Scan games with playtime or the full library.
- Display ProtonDB categories and community report counts in the terminal.
- Optionally export results to an HTML report with search, sorting, filters, and links to Steam and ProtonDB.
- Open generated HTML reports in your default browser.
- Optionally save Steam credentials locally for future runs.
- Uses Python's standard library without third-party Python dependencies.

## Requirements

- Python 3.10 or newer.
- An internet connection.
- `uv` is recommended, but regular Python also works.
- A SteamID64 and Steam Web API key are required for library scans.

Individual game lookups do not require Steam API credentials.

## Running the tool

Run these commands from the directory containing `steam_protondb.py`.

### Using uv

```sh
uv run steam_protondb.py
```

### Using Python

```sh
python steam_protondb.py
```

On some systems, use `python3` instead:

```sh
python3 steam_protondb.py
```

## Checking individual games

Choose the individual game lookup option from the menu.

You can enter a game name, Steam App ID, or Steam store URL.

Examples:

```text
RimWorld
294100
https://store.steampowered.com/app/294100/RimWorld/
```

You can also enter multiple games separated by commas:

```text
RimWorld, PEAK, Portal 2
```

Each game is searched separately. When multiple matches are found, select the correct game from the interactive menu.

Game names containing commas may need to be searched individually.

## Scanning a Steam library

Choose the library scan option.

The tool asks for your SteamID64 and Steam Web API key, then lets you choose between:

- Games with playtime.
- Your full Steam library.

For your library to be available through Steam's owned-games API, your Steam profile's **Game details** privacy setting may need to be Public.

### Finding your SteamID64

Your SteamID64 is a 17-digit numeric identifier. If your profile URL uses a custom name, you can look it up at:

https://steamid.io/lookup

### Getting a Steam Web API key

Sign in to Steam and visit:

https://steamcommunity.com/dev/apikey

Never share your API key or Steam account password.

## HTML reports

After a lookup or library scan, you can choose whether to generate an HTML report.

The report provides a searchable and sortable table with ProtonDB categories, report counts, playtime where available, and links to individual games.

Reports are saved in the current working directory. You can open them in your default browser when prompted or open the HTML file manually later.

## Credentials and privacy

If you choose to save your credentials, they are stored in:

```text
steam_protondb.env
```

This file is located in the current working directory from which the script is launched.

The file stores the Steam API key as plain text. On Linux, the tool attempts to restrict its permissions to the current user.

**Keep this file private. Never commit it to Git or upload it to a public repository.**

The included `.gitignore` excludes the credentials file and generated reports.

## ProtonDB categories

ProtonDB reports reflect community experiences and are not guarantees of performance on every Linux distribution or hardware configuration.

The tool displays categories such as:

- Native Linux
- Platinum
- Gold
- Silver
- Bronze
- Borked
- No reports / unknown

An unknown category does not necessarily mean a game is unplayable.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
