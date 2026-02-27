from __future__ import annotations

import argparse
import html
import json
import re
import time
from dataclasses import dataclass
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

try:
    import pymysql
except ImportError:
    pymysql = None

FINGERPRINT_FILE = "1.txt"


@dataclass
class Fingerprint:
    user_agent: str = ""
    cookie: str = ""
    xsrf_token: str = ""


def read_fingerprint(file_path: str) -> Fingerprint:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return Fingerprint()

    def find_value(key: str) -> str:
        key_lower = key.lower()
        for i, line in enumerate(lines):
            if line.lower() == key_lower and i + 1 < len(lines):
                return lines[i + 1]
        return ""

    return Fingerprint(
        user_agent=find_value("user-agent"),
        cookie=find_value("cookie"),
        xsrf_token=find_value("x-xsrf-token"),
    )


def apply_cookies(driver: webdriver.Chrome, cookie_string: str) -> None:
    if not cookie_string:
        return
    pairs = [c.strip() for c in cookie_string.split(";") if c.strip()]
    for pair in pairs:
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        try:
            driver.add_cookie(
                {
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": ".liepin.com",
                }
            )
        except Exception:
            continue


def create_driver(headless: bool, use_fingerprint: bool, fp: Fingerprint) -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    if headless:
        opts.add_argument("--headless=new")
    if use_fingerprint and fp.user_agent:
        opts.add_argument(f"--user-agent={fp.user_agent}")

    driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(6)
    driver.set_page_load_timeout(35)

    if use_fingerprint and fp.xsrf_token:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd(
            "Network.setExtraHTTPHeaders",
            {"headers": {"x-xsrf-token": fp.xsrf_token}},
        )

    return driver


def get_db_connection(host: str, port: int, user: str, password: str, database: str):
    if pymysql is None:
        raise RuntimeError("Missing dependency: pymysql. Install via pip install pymysql")

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=True,
    )


def load_rows(connection, limit: int, only_empty: bool):
    sql = (
        "SELECT id, job_url, skills FROM jobs "
        "WHERE job_url IS NOT NULL AND TRIM(job_url) <> '' "
        "AND job_url LIKE 'https://www.liepin.com/%%' "
    )
    if only_empty:
        sql += (
            "AND (skills IS NULL OR TRIM(skills) = '' OR TRIM(skills) = '[]' "
            "OR LOWER(TRIM(skills)) = 'null') "
        )
    sql += "ORDER BY id ASC LIMIT %s"

    with connection.cursor() as cursor:
        cursor.execute(sql, (limit,))
        return cursor.fetchall()


def normalize_text(text: str) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if lines and lines[0] == "职位介绍":
        lines = lines[1:]
    return "\n".join(lines).strip()


def parse_desc_from_html(page_source: str) -> str:
    # Fallback when Selenium element lookup fails.
    m = re.search(r"data-selector=\"job-intro-content\"[^>]*>(.*?)</dd>", page_source, re.S | re.I)
    if not m:
        return ""
    raw = m.group(1)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", "", raw)
    return normalize_text(html.unescape(raw))


def fetch_desc(driver: webdriver.Chrome, url: str, wait: float, retries: int = 2) -> Optional[str]:
    selectors = [
        "dd[data-selector='job-intro-content']",
        ".job-intro-container dd[data-selector='job-intro-content']",
        ".job-intro-container .paragraph dd",
    ]

    for attempt in range(1, retries + 1):
        try:
            driver.get(url)
            time.sleep(wait)

            source = driver.page_source
            if "该职位已下线" in source or "职位不存在" in source or "页面不存在" in source:
                return None

            for selector in selectors:
                nodes = driver.find_elements(By.CSS_SELECTOR, selector)
                if not nodes:
                    continue
                text = normalize_text(nodes[0].text)
                if text:
                    return text

            parsed = parse_desc_from_html(source)
            if parsed:
                return parsed
        except TimeoutException:
            if attempt == retries:
                return None
        except WebDriverException:
            if attempt == retries:
                return None
        except Exception:
            if attempt == retries:
                return None

    return None


def update_skills(connection, job_id: int, desc: str) -> None:
    payload = json.dumps([desc], ensure_ascii=False)
    sql = "UPDATE jobs SET skills=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s"
    with connection.cursor() as cursor:
        cursor.execute(sql, (payload, job_id))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill jobs.skills from Liepin job detail by job_url")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="root")
    parser.add_argument("--database", default="recruitment_system")

    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--all", action="store_true", help="Process all rows, not only empty skills")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--wait", type=float, default=1.5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--use-fingerprint", action="store_true")

    args = parser.parse_args()

    fp = read_fingerprint(FINGERPRINT_FILE) if args.use_fingerprint else Fingerprint()
    connection = get_db_connection(args.host, args.port, args.user, args.password, args.database)
    driver = create_driver(args.headless, args.use_fingerprint, fp)

    try:
        if args.use_fingerprint:
            driver.get("https://www.liepin.com/")
            apply_cookies(driver, fp.cookie)

        rows = load_rows(connection, limit=args.limit, only_empty=(not args.all))
        total = len(rows)
        print(f"loaded {total} rows")

        updated = 0
        skipped = 0
        failed = 0

        for idx, (job_id, job_url, _skills) in enumerate(rows, start=1):
            desc = fetch_desc(driver, job_url, wait=args.wait)
            if not desc:
                skipped += 1
                print(f"[{idx}/{total}] skip id={job_id} url={job_url}")
                continue

            if args.dry_run:
                updated += 1
                print(f"[{idx}/{total}] dry-run id={job_id}, desc_len={len(desc)}")
                continue

            try:
                update_skills(connection, job_id, desc)
                updated += 1
                print(f"[{idx}/{total}] updated id={job_id}, desc_len={len(desc)}")
            except Exception as exc:
                failed += 1
                print(f"[{idx}/{total}] update failed id={job_id}, error={exc}")

        print(f"done: total={total}, updated={updated}, skipped={skipped}, failed={failed}")
    finally:
        driver.quit()
        connection.close()


if __name__ == "__main__":
    main()
