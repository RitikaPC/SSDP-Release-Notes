#!/usr/bin/env python3
"""
check_gaps.py — Check for unpublished weeks with updates between last published and target week

This script:
1. Reads weekly_stopper.json to find all weeks with data
2. Queries Confluence to find which weeks have been published
3. Returns list of weeks that need to be published before the target week
"""

import os
import sys
import re
import json
import datetime
import requests

CONFLUENCE_BASE_URL = "https://eng-stla.atlassian.net/wiki"
USERNAME = os.getenv("CONFLUENCE_USERNAME")
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")
SPACE_KEY = os.getenv("CONFLUENCE_SPACE_KEY")
WEEKLY_STOPPER = os.getenv("WEEKLY_STOPPER", "weekly_stopper.json")

forced_week_raw = None
forced_year = None
forced_week = None

if "--week" in sys.argv:
    forced_week_raw = sys.argv[sys.argv.index("--week") + 1]

def parse_week_arg(raw: str):
    if not raw:
        return None, None
    raw = raw.strip()
    m = re.match(r"^(?P<year>\d{4})[-_ ]*W?(?P<week>\d{1,2})$", raw)
    if m:
        return int(m.group("year")), int(m.group("week"))
    m2 = re.match(r"^W?(?P<week>\d{1,2})$", raw)
    if m2:
        return None, int(m2.group("week"))
    return None, None

if forced_week_raw:
    forced_year, forced_week = parse_week_arg(forced_week_raw)

today = datetime.date.today()
current_year = today.isocalendar()[0]
target_year = forced_year if forced_year else current_year
target_week = forced_week if forced_week else today.isocalendar()[1]

def parse_stopper_key(k: str):
    """Return (year, week, display_str)."""
    m = re.match(r"^(?P<y>\d{4})-W?(?P<w>\d{1,2})$", k)
    if m:
        return int(m.group("y")), int(m.group("w")), k
    m2 = re.match(r"^(?P<w>\d{1,2})$", k)
    if m2:
        w = int(m2.group("w"))
        # Infer year: high week numbers when target is low likely mean previous year
        if target_week <= 10 and w > 40:
            return target_year - 1, w, k
        else:
            return target_year, w, k
    return None, None, k

def get_page_title_for_week(year, week, display_key):
    """Generate Confluence page title with year prefix when needed.
    
    Use year prefix (YYYY-Www) when:
    - Week is from a different calendar year than today
    - Or when the display_key already has a year format
    """
    if '-' in display_key or 'W' in display_key:
        # Already has year format
        return f"SSDP Release Notes Week {display_key}"
    
    # Check if this week is from current calendar year
    today = datetime.date.today()
    current_calendar_year = today.year
    
    # For weeks near year boundary, determine calendar year
    # Week 1 of ISO year can start in previous December
    # Week 52/53 belong to their ISO year
    if week >= 52:
        # High weeks belong to their year
        week_calendar_year = year
    elif week == 1:
        # Week 1 can span years - use ISO year
        week_calendar_year = year
    else:
        week_calendar_year = year
    
    # Add year prefix if not current calendar year
    if week_calendar_year != current_calendar_year:
        return f"SSDP Release Notes Week {year}-W{week:02d}"
    else:
        return f"SSDP Release Notes Week {display_key}"

def load_stopper():
    if not os.path.exists(WEEKLY_STOPPER):
        return {}
    try:
        with open(WEEKLY_STOPPER, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def has_updates(week_data):
    """Check if a week has any non-null updates."""
    if not week_data:
        return False
    for key, value in week_data.items():
        if value not in (None, "", "None"):
            return True
    return False

def confluence_page_exists(title):
    """Check if a page with given title exists in Confluence."""
    if not USERNAME or not API_TOKEN or not SPACE_KEY:
        # Can't check Confluence - fall back to known published list
        # Based on user's screenshot: weeks 45-51 are published
        known_published = [
            "SSDP Release Notes Week 45",
            "SSDP Release Notes Week 46", 
            "SSDP Release Notes Week 47",
            "SSDP Release Notes Week 48",
            "SSDP Release Notes Week 49",
            "SSDP Release Notes Week 50",
            "SSDP Release Notes Week 51",
            "SSDP Release Notes Week 2025-W45",
            "SSDP Release Notes Week 2025-W46",
            "SSDP Release Notes Week 2025-W47",
            "SSDP Release Notes Week 2025-W48",
            "SSDP Release Notes Week 2025-W49",
            "SSDP Release Notes Week 2025-W50",
            "SSDP Release Notes Week 2025-W51",
        ]
        return title in known_published
    
    url = f"{CONFLUENCE_BASE_URL}/rest/api/content"
    params = {"spaceKey": SPACE_KEY, "title": title}
    try:
        r = requests.get(url, auth=(USERNAME, API_TOKEN), params=params, timeout=10)
        if r.status_code == 200:
            results = r.json().get("results", [])
            return len(results) > 0
    except Exception as e:
        print(f"Warning: Could not check Confluence for '{title}': {e}", file=sys.stderr)
    return False

def main():
    stopper_data = load_stopper()
    
    # Parse all weeks with data
    weeks_with_data = []
    for key in stopper_data.keys():
        year, week, display = parse_stopper_key(key)
        if year is not None and week is not None:
            if has_updates(stopper_data[key]):
                weeks_with_data.append((year, week, display))
    
    # Sort chronologically
    weeks_with_data.sort()
    
    # Also check for recent weeks that might not be in stopper yet
    # Check last 4 weeks before target
    today = datetime.date.today()
    check_weeks = []
    for days_back in range(28, 0, -7):  # 4 weeks
        check_date = today - datetime.timedelta(days=days_back)
        iso_year, iso_week, _ = check_date.isocalendar()
        check_weeks.append((iso_year, iso_week))
    
    # Add these to weeks_with_data if not already there
    for year, week in check_weeks:
        if (year, week) < (target_year, target_week):
            # Check if this week is already in our list
            if not any(y == year and w == week for y, w, _ in weeks_with_data):
                # Create display format
                if year != today.year:
                    display = f"{year}-W{week:02d}"
                else:
                    display = str(week)
                weeks_with_data.append((year, week, display))
    
    # Re-sort
    weeks_with_data.sort()
    
    # Find weeks that are before target and have updates but not published
    unpublished_weeks = []
    for year, week, display in weeks_with_data:
        if (year, week) < (target_year, target_week):
            # Format title with year-aware logic
            title = get_page_title_for_week(year, week, display)
            if not confluence_page_exists(title):
                unpublished_weeks.append((year, week, display))
    
    # Output results as JSON for easy parsing
    # Format unpublished weeks with year context
    unpublished_list = []
    for year, week, display in unpublished_weeks:
        # Use year-prefixed format for weeks from different year
        today = datetime.date.today()
        if year != today.year:
            unpublished_list.append(f"{year}-W{week:02d}")
        else:
            unpublished_list.append(display)
    
    result = {
        "target_week": f"{target_year}-W{target_week:02d}" if forced_year else str(target_week),
        "unpublished_weeks": unpublished_list
    }
    
    print(json.dumps(result, indent=2))
    
    # Exit code: 0 if no gaps, 1 if gaps found
    sys.exit(0 if not unpublished_list else 1)

if __name__ == "__main__":
    main()
