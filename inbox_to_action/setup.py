"""One-time setup, run in YOUR terminal (secrets never appear on screen or in chat):

    python3 -m inbox_to_action.setup

Creates the Notion database and saves every setting to Vercel (production + preview).
"""
import getpass
import re
import subprocess
import sys

from .notion import setup_database


def page_id_from(text: str) -> str:
    m = re.search(r"([0-9a-f]{32})(?:\?|$)", text.replace("-", "").strip(), re.I)
    if not m:
        raise ValueError("no Notion page id found")
    return m.group(1).lower()


def run_setup(ask, ask_secret, create_db, set_env, say=print) -> None:
    say("Step 1/4  Notion: create an integration at https://www.notion.so/my-integrations,")
    say("          then open an empty page, ••• → Connections → add your integration.")
    token = ask_secret("Notion integration token: ").strip()
    while True:
        try:
            page = page_id_from(ask("Link to that Notion page: "))
            break
        except ValueError:
            say("That doesn't look like a Notion page link. Copy it from Share → Copy link.")
    db = create_db(token, page)
    say("          Notion database created.")
    say("Step 2/4  Anthropic: create a key at https://console.anthropic.com/settings/keys")
    key = ask_secret("Anthropic API key: ").strip()
    say("Step 3/4  Choose the password for your Today page (12+ characters).")
    while True:
        pw = ask_secret("Password: ")
        if len(pw) < 12:
            say("Use at least 12 characters.")
            continue
        if ask_secret("Same password again: ") != pw:
            say("Those didn't match, try again.")
            continue
        break
    vips = ",".join(v.strip().lower() for v in ask("Step 4/4  Important senders, comma-separated (Enter to skip): ").split(",") if v.strip())
    say("Saving to Vercel…")
    settings = {"NOTION_TOKEN": token, "NOTION_DATABASE_ID": db, "ANTHROPIC_API_KEY": key, "APP_PASSWORD": pw}
    if vips:
        settings["VIP_EMAILS"] = vips
    for name, value in settings.items():
        set_env(name, value)
        say(f"          {name} saved")
    say("Done. Tell Claude: \"setup finished\".")


def vercel_set(name: str, value: str) -> None:
    sensitive = name != "VIP_EMAILS"
    for target in ("production", "preview"):
        subprocess.run(["npx", "vercel", "env", "rm", name, target, "--yes"], capture_output=True)
        cmd = ["npx", "vercel", "env", "add", name, target] + (["--sensitive"] if sensitive else [])
        r = subprocess.run(cmd, input=value, text=True, capture_output=True)
        if r.returncode != 0:
            sys.exit(f"Vercel refused {name} ({target}). Run `npx vercel login` and try again.")


if __name__ == "__main__":
    run_setup(ask=input, ask_secret=getpass.getpass, create_db=setup_database, set_env=vercel_set)
