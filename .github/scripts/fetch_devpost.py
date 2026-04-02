#!/usr/bin/env python3
"""
fetch_devpost.py
Scrapes https://devpost.com/thinhtn3 for hackathon projects,
then fetches each project page for hackathon name and prize details.
Replaces content between <!-- DEVPOST:START --> and <!-- DEVPOST:END -->
in README.md.
"""

import re
import sys
import time
import pathlib
import requests
from bs4 import BeautifulSoup

PROFILE_URL = "https://devpost.com/thinhtn3"
README_PATH = pathlib.Path(__file__).resolve().parents[2] / "README.md"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; profile-readme-bot/1.0; "
        "+https://github.com/thinhtn3)"
    )
}

INTER_REQUEST_DELAY = 1.5

START_MARKER = "<!-- DEVPOST:START -->"
END_MARKER = "<!-- DEVPOST:END -->"


def fetch_html(url: str, retries: int = 3, backoff: float = 4.0):
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except requests.RequestException as exc:
            wait = backoff * (2 ** attempt)
            print(f"  [warn] {url} attempt {attempt + 1} failed: {exc}. "
                  f"Retrying in {wait:.0f}s...", file=sys.stderr)
            time.sleep(wait)
    print(f"  [error] Gave up fetching {url}", file=sys.stderr)
    return None


def scrape_profile(profile_url: str) -> list:
    soup = fetch_html(profile_url)
    if soup is None:
        return []

    cards = soup.find_all("a", class_="link-to-software")
    projects = []
    for card in cards:
        href = card.get("href", "")
        if href.startswith("/"):
            href = "https://devpost.com" + href
        name_tag = card.find("h5") or card.find("h3")
        name = name_tag.get_text(strip=True) if name_tag else href.rstrip("/").split("/")[-1]
        is_winner = card.find("img", alt="Winner") is not None
        projects.append({"name": name, "url": href, "is_winner": is_winner})

    return projects


def scrape_project_page(project_url: str) -> dict:
    result = {"hackathon_name": None, "hackathon_url": None, "prizes": []}
    soup = fetch_html(project_url)
    if soup is None:
        return result

    # Attempt 1: <a class="challenge-link">
    hackathon_anchor = soup.find("a", class_="challenge-link")
    if hackathon_anchor:
        result["hackathon_name"] = hackathon_anchor.get_text(strip=True)
        result["hackathon_url"] = hackathon_anchor.get("href", "")

    # Attempt 2: "Built at" text node near an anchor
    if not result["hackathon_name"]:
        for tag in soup.find_all(string=re.compile(r"Built at", re.I)):
            parent = tag.parent
            anchor = parent.find("a") if parent else None
            if anchor:
                result["hackathon_name"] = anchor.get_text(strip=True)
                result["hackathon_url"] = anchor.get("href", "")
                break

    # Attempt 3: #submissions anchor
    if not result["hackathon_name"]:
        submission_div = soup.find("div", id="submissions")
        if submission_div:
            anchor = submission_div.find("a")
            if anchor:
                result["hackathon_name"] = anchor.get_text(strip=True)
                result["hackathon_url"] = anchor.get("href", "")

    # Prizes
    prize_section = (
        soup.find("div", id="prizes")
        or soup.find("section", id="prizes")
        or soup.find(id="app-prizes")
    )
    if prize_section:
        for item in prize_section.find_all(["li", "h4", "h5"]):
            text = item.get_text(strip=True)
            if text:
                result["prizes"].append(text)

    if not result["prizes"]:
        for img in soup.find_all("img", alt=re.compile(r"winner|prize|award", re.I)):
            alt = img.get("alt", "").strip()
            if alt:
                result["prizes"].append(alt)

    return result


def build_markdown(projects: list) -> str:
    if not projects:
        return "_No hackathon projects found. Check back later!_\n"

    lines = [
        "| Project | Hackathon | Award |",
        "|---------|-----------|-------|",
    ]

    for p in projects:
        name = p["name"]
        project_url = p["url"]
        hackathon = p.get("hackathon_name") or "—"
        hackathon_url = p.get("hackathon_url", "")
        prizes = p.get("prizes", [])
        is_winner = p.get("is_winner", False)

        project_cell = f"[{name}]({project_url})"

        if hackathon_url and hackathon != "—":
            hackathon_cell = f"[{hackathon}]({hackathon_url})"
        else:
            hackathon_cell = hackathon

        if prizes:
            award_cell = " · ".join(prizes[:2])
        elif is_winner:
            award_cell = "🏆 Winner"
        else:
            award_cell = "—"

        lines.append(f"| {project_cell} | {hackathon_cell} | {award_cell} |")

    timestamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    lines.append(f"\n_Last updated: {timestamp}_\n")

    return "\n".join(lines) + "\n"


def update_readme(new_content: str, project_count: int) -> bool:
    readme_text = README_PATH.read_text(encoding="utf-8")

    pattern = re.compile(
        r"(" + re.escape(START_MARKER) + r"\n)"
        r".*?"
        r"(" + re.escape(END_MARKER) + r")",
        re.DOTALL,
    )

    replacement = r"\g<1>" + new_content + r"\g<2>"
    updated_text, count = pattern.subn(replacement, readme_text)

    if count == 0:
        print("[error] Sentinel markers not found in README.md.", file=sys.stderr)
        sys.exit(1)

    if updated_text == readme_text:
        print("[info] README.md content unchanged — no write needed.")
        return False

    README_PATH.write_text(updated_text, encoding="utf-8")
    print(f"[info] README.md updated with {project_count} project(s).")
    return True


def main() -> None:
    print(f"[info] Fetching Devpost profile: {PROFILE_URL}")
    projects = scrape_profile(PROFILE_URL)
    print(f"[info] Found {len(projects)} project(s).")

    if not projects:
        print("[warn] No projects found. Writing placeholder.")
        update_readme("_No hackathon projects found. Check back later!_\n", 0)
        return

    for i, project in enumerate(projects):
        print(f"[info] Fetching project page {i + 1}/{len(projects)}: {project['url']}")
        details = scrape_project_page(project["url"])
        project.update(details)
        if i < len(projects) - 1:
            time.sleep(INTER_REQUEST_DELAY)

    markdown = build_markdown(projects)
    update_readme(markdown, len(projects))


if __name__ == "__main__":
    main()
