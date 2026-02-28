import argparse
import base64
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

try:
    import pymysql
except ImportError:
    pymysql = None

CHROME_BINARY = None
HEADLESS = False
FINGERPRINT_FILE = '1.txt'
USE_FINGERPRINT = False

DEFAULT_ZHILIAN_URL = 'https://www.zhaopin.com/sou/jl538/kw01L00O80EO062/p1?srccode=401801'
SKILLS_DIR = 'skills'


def read_fingerprint(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            text = file.read()
    except FileNotFoundError:
        return {}

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    def find_value(key):
        key_lower = key.lower()
        for index, line in enumerate(lines):
            if line.lower() == key_lower and index + 1 < len(lines):
                return lines[index + 1]
        return None

    user_agent = find_value('user-agent')
    cookie = find_value('cookie')
    xsrf_token = find_value('x-xsrf-token')

    # Fallback: parse from markdown/raw header dump (like 任务流程.md).
    if not user_agent:
        m = re.search(r'(?im)^user-agent\\s*\\r?\\n(.+)$', text)
        if m:
            user_agent = m.group(1).strip()

    if not cookie:
        m = re.search(
            r'(?is)\\bcookie\\s*\\r?\\n(.+?)\\r?\\n(?:priority|referer|sec-ch-ua|user-agent|upgrade-insecure-requests|\\})',
            text,
        )
        if m:
            cookie = m.group(1).strip()

    if not xsrf_token:
        m = re.search(r'(?im)^x-xsrf-token\\s*\\r?\\n(.+)$', text)
        if m:
            xsrf_token = m.group(1).strip()

    return {
        'user_agent': user_agent,
        'cookie': cookie,
        'xsrf_token': xsrf_token,
    }


def apply_cookies(driver, cookie_string, domain='.zhaopin.com'):
    if not cookie_string:
        return
    cookies = [c.strip() for c in cookie_string.split(';') if c.strip()]
    for cookie in cookies:
        if '=' not in cookie:
            continue
        name, value = cookie.split('=', 1)
        try:
            driver.add_cookie(
                {
                    'name': name.strip(),
                    'value': value.strip(),
                    'domain': domain,
                }
            )
        except Exception:
            continue


def create_driver(fingerprint):
    chrome_options = Options()
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    if HEADLESS:
        chrome_options.add_argument('--headless=new')
    if CHROME_BINARY:
        chrome_options.binary_location = CHROME_BINARY

    if USE_FINGERPRINT and fingerprint.get('user_agent'):
        chrome_options.add_argument(f"--user-agent={fingerprint['user_agent']}")

    chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    driver = webdriver.Chrome(options=chrome_options)
    driver.implicitly_wait(8)
    driver.set_page_load_timeout(35)
    driver.execute_cdp_cmd('Network.enable', {})

    return driver


def safe_get(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def pick_value(obj, paths):
    for path in paths:
        if isinstance(path, (list, tuple)):
            value = safe_get(obj, *path)
        else:
            value = obj.get(path) if isinstance(obj, dict) else None
        if value not in (None, '', []):
            return value
    return None


def normalize_text(value):
    if value is None:
        return ''
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip()


def normalize_company_logo_url(value):
    raw = normalize_text(value)
    if not raw:
        return ''
    if raw.startswith('data:'):
        return ''
    if raw.startswith('http://') or raw.startswith('https://'):
        return raw
    if raw.startswith('//'):
        return 'https:' + raw

    cleaned = raw.lstrip('/')
    # Already like image2.lietou-static.com/xxx or img01.zhaopin.cn/xxx
    if re.match(r'^[A-Za-z0-9.-]+\.[A-Za-z]{2,}/', cleaned):
        return 'https://' + cleaned

    # Fallback to zhilian image host
    return f'https://img01.zhaopin.cn/{cleaned}'


def normalize_skills(value):
    if not value:
        return []
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict):
                text = item.get('name') or item.get('label') or item.get('value')
            else:
                text = str(item)
            if text:
                result.append(text.strip())
        return [x for x in result if x]
    if isinstance(value, str):
        return [x.strip() for x in re.split(r'[、,/|;；\n]+', value) if x.strip()]
    return [str(value).strip()]


def parse_salary(salary_text):
    if not salary_text:
        return None, None, None
    text = str(salary_text).replace(' ', '')
    if '面议' in text:
        return None, None, None
    if '·' in text:
        text = text.split('·', 1)[0]

    is_year = '年' in text
    unit = None
    if '万' in text:
        unit = 'wan'
    elif '千' in text:
        unit = 'qian'
    elif re.search(r'[kK]', text):
        unit = 'k'

    numbers = re.findall(r'\d+(?:\.\d+)?', text)
    if not numbers:
        return None, None, None

    min_value = float(numbers[0])
    max_value = float(numbers[1]) if len(numbers) > 1 else min_value

    if unit == 'wan':
        min_value *= 10
        max_value *= 10
    elif unit == 'qian':
        min_value *= 1
        max_value *= 1

    if is_year:
        min_value /= 12
        max_value /= 12

    avg_value = (min_value + max_value) / 2
    return round(min_value, 2), round(max_value, 2), round(avg_value, 2)


def build_search_url(base_url, page):
    # Priority 1: explicit placeholder
    if '{page}' in base_url:
        return base_url.format(page=page)

    # Priority 2: replace path /pN
    if re.search(r'/p\d+(?=\?|$)', base_url):
        return re.sub(r'/p\d+(?=\?|$)', f'/p{page}', base_url)

    # Priority 3: set/override query param p
    parsed = urlparse(base_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params['p'] = [str(page)]
    query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=query))


def is_security_page(html_text):
    html_text = html_text or ''
    html_low = html_text.lower()

    # Normal zhilian result pages usually contain embedded state with positionURL.
    if '__initial_state__' in html_low and 'positionurl' in html_low:
        return False

    markers = [
        'security verification',
        'captcha.eo.gtimg.com',
        'teocaptchawidget',
        'cap_union_prehandle',
        '请完成安全验证',
        '安全验证',
    ]
    return any(marker.lower() in html_low for marker in markers)


def iter_dicts(obj):
    stack = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            for value in current.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            for item in current:
                if isinstance(item, (dict, list)):
                    stack.append(item)


def extract_initial_state(page_source):
    anchor = '__INITIAL_STATE__'
    idx = page_source.find(anchor)
    if idx < 0:
        return None

    start = page_source.find('{', idx)
    if start < 0:
        return None

    level = 0
    in_string = False
    escaped = False
    end = -1

    for i in range(start, len(page_source)):
        ch = page_source[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == '\\\\':
                escaped = True
            elif ch == '\"':
                in_string = False
            continue

        if ch == '\"':
            in_string = True
        elif ch == '{':
            level += 1
        elif ch == '}':
            level -= 1
            if level == 0:
                end = i + 1
                break

    if end < 0:
        return None

    text = page_source[start:end]
    try:
        return json.loads(text)
    except Exception:
        # Mild cleanup for trailing commas in rare pages.
        text = re.sub(r',\\s*}', '}', text)
        text = re.sub(r',\\s*]', ']', text)
        try:
            return json.loads(text)
        except Exception:
            return None


def extract_jobs_from_initial_state(page_source):
    state = extract_initial_state(page_source)
    if not isinstance(state, dict):
        return []

    result = []
    seen = set()
    for obj in iter_dicts(state):
        rec = extract_from_object(obj)
        if not rec:
            continue
        job_url = rec.get('job_url')
        if not job_url or job_url in seen:
            continue
        seen.add(job_url)
        result.append(rec)
    return result


def extract_from_object(obj):
    job_url = normalize_text(
        pick_value(
            obj,
            [
                'positionURL',
                'positionUrl',
                'jobUrl',
                'detailUrl',
                'positionDetailUrl',
                ('job', 'positionURL'),
                ('job', 'positionUrl'),
                ('job', 'jobUrl'),
            ],
        )
    )

    if not job_url or 'zhaopin' not in job_url.lower():
        return None

    title = normalize_text(
        pick_value(
            obj,
            ['name', 'positionName', 'jobName', 'title', ('job', 'name'), ('job', 'positionName'), ('job', 'title')],
        )
    )
    company = normalize_text(
        pick_value(
            obj,
            [
                'companyName',
                ('company', 'name'),
                ('company', 'companyName'),
                ('comp', 'name'),
                ('comp', 'compName'),
            ],
        )
    )
    salary = normalize_text(
        pick_value(obj, ['salary60', 'salary', 'salaryDesc', 'salaryReal', ('job', 'salary')])
    )
    location = normalize_text(
        pick_value(
            obj,
            ['workingCity', 'cityName', 'city', 'cityDistrict', 'workCity', ('job', 'cityName')],
        )
    )
    experience = normalize_text(
        pick_value(obj, ['workingExp', 'workExp', 'experience', ('job', 'workingExp')])
    )
    education = normalize_text(pick_value(obj, ['education', 'eduLevel', ('job', 'education')]))
    industry = normalize_text(
        pick_value(obj, ['industryName', ('company', 'industryName'), ('comp', 'compIndustry')])
    )
    job_type = normalize_text(pick_value(obj, ['jobType', 'positionType', ('job', 'jobType')]))
    company_nature = normalize_text(
        pick_value(obj, ['companyType', 'companyNature', ('company', 'typeName')])
    )
    company_size = normalize_text(
        pick_value(obj, ['companySize', ('company', 'sizeName'), ('comp', 'compScale')])
    )
    company_logo = normalize_company_logo_url(
        pick_value(
            obj,
            [
                'companyLogo',
                'logo',
                'logoUrl',
                ('company', 'logo'),
                ('company', 'logoUrl'),
                ('comp', 'compLogo'),
            ],
        )
    )

    skills_raw = pick_value(
        obj,
        [
            'skills',
            'skillLabel',
            'skillLabels',
            'jobSkillTags',
            'welfareTag',
            ('job', 'skills'),
            ('job', 'skillLabels'),
        ],
    )
    skills = normalize_skills(skills_raw)

    description = normalize_text(
        pick_value(
            obj,
            [
                'jobSummary',
                'jobDescription',
                'positionDetail',
                'description',
                ('job', 'jobSummary'),
                ('job', 'description'),
            ],
        )
    )

    return {
        'title': title,
        'company': company,
        'salary': salary,
        'location': location,
        'experience': experience,
        'education': education,
        'industry': industry,
        'job_type': job_type,
        'company_nature': company_nature,
        'company_size': company_size,
        'job_url': job_url,
        'skills_list': skills,
        'description': description,
        'source': 'zhilian',
        'company_logo': company_logo,
    }


def extract_jobs_from_performance(driver):
    result = []
    seen = set()

    try:
        logs = [json.loads(log['message'])['message'] for log in driver.get_log('performance')]
    except Exception:
        return result

    for log in logs:
        if log.get('method') != 'Network.responseReceived':
            continue

        params = log.get('params', {})
        response = params.get('response', {})
        url = response.get('url', '')
        mime = (response.get('mimeType') or '').lower()

        if 'zhaopin' not in url.lower():
            continue
        if 'json' not in mime and '/api/' not in url.lower() and 'search' not in url.lower():
            continue

        request_id = params.get('requestId')
        if not request_id:
            continue

        try:
            body_data = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': request_id})
            body = body_data.get('body', '')
            if body_data.get('base64Encoded'):
                body = base64.b64decode(body).decode('utf-8', errors='ignore')
            parsed = json.loads(body)
        except Exception:
            continue

        for obj in iter_dicts(parsed):
            rec = extract_from_object(obj)
            if not rec:
                continue
            job_url = rec.get('job_url')
            if not job_url or job_url in seen:
                continue
            seen.add(job_url)
            result.append(rec)

    return result


def extract_jobs_from_dom(driver):
    result = []
    seen = set()

    selectors = [
        "a[href*='jobs.zhaopin.com']",
        "a[href*='job_detail']",
        "a[href*='/job/']",
    ]

    links = []
    for selector in selectors:
        try:
            links.extend(driver.find_elements(By.CSS_SELECTOR, selector))
        except Exception:
            continue

    for elem in links:
        try:
            href = normalize_text(elem.get_attribute('href'))
            title = normalize_text(elem.text)
        except Exception:
            continue

        if not href or href in seen:
            continue
        if 'zhaopin' not in href.lower():
            continue
        if len(title) < 2:
            continue

        seen.add(href)

        company = ''
        salary = ''
        location = ''
        company_logo = ''

        try:
            card = elem.find_element(By.XPATH, './ancestor::*[self::li or self::div][1]')
            card_text = normalize_text(card.text)

            # salary guess
            m_salary = re.search(r'\d+(?:\.\d+)?(?:k|K|千|万)\s*[-~]\s*\d+(?:\.\d+)?(?:k|K|千|万)', card_text)
            if m_salary:
                salary = m_salary.group(0)

            # company guess
            for csel in ["[class*='company']", "a[href*='company']", "span"]:
                nodes = card.find_elements(By.CSS_SELECTOR, csel)
                for node in nodes:
                    t = normalize_text(node.text)
                    if t and t != title and len(t) <= 40:
                        company = t
                        break
                if company:
                    break

            # logo guess
            imgs = card.find_elements(By.CSS_SELECTOR, 'img')
            for img in imgs:
                src = normalize_company_logo_url(img.get_attribute('src'))
                if src:
                    company_logo = src
                    break

            # location guess
            m_loc = re.search(r'(北京|上海|广州|深圳|杭州|成都|武汉|西安|南京|苏州|重庆|天津|长沙|郑州|青岛|厦门)', card_text)
            if m_loc:
                location = m_loc.group(1)
        except Exception:
            pass

        result.append(
            {
                'title': title,
                'company': company,
                'salary': salary,
                'location': location,
                'experience': '',
                'education': '',
                'industry': '',
                'job_type': '',
                'company_nature': '',
                'company_size': '',
                'job_url': href,
                'skills_list': [],
                'description': '',
                'source': 'zhilian',
                'company_logo': company_logo,
            }
        )

    return result


def extract_description_from_html(html):
    if not html:
        return ''

    # JSON keys from script
    json_keys = ['jobSummary', 'jobDescription', 'positionDetail', 'description', 'duty']
    for key in json_keys:
        pattern = rf'"{key}"\s*:\s*"(.*?)"'
        m = re.search(pattern, html, flags=re.I | re.S)
        if m:
            raw = m.group(1)
            try:
                text = json.loads('"' + raw.replace('"', '\\"') + '"')
            except Exception:
                text = raw
            text = normalize_text(text)
            if len(text) >= 20:
                return text

    # fallback by label block
    for label in ['职位描述', '岗位职责', '任职要求']:
        p = re.search(label + r'.{0,2500}', html, flags=re.S)
        if p:
            text = re.sub(r'<[^>]+>', ' ', p.group(0))
            text = re.sub(r'\s+', ' ', text)
            text = normalize_text(text)
            if len(text) >= 20:
                return text

    return ''


def extract_description_from_page(driver):
    selectors = [
        "[data-selector='job-intro-content']",
        "[class*='job-summary']",
        "[class*='describ']",
        "[class*='detail-content']",
        "[class*='job-detail']",
    ]

    best = ''
    for selector in selectors:
        try:
            nodes = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            nodes = []
        for node in nodes:
            text = normalize_text(node.text)
            if len(text) > len(best):
                best = text

    if len(best) >= 20:
        return best

    return extract_description_from_html(driver.page_source)


COMMON_SKILLS = [
    'Java',
    'Spring',
    'Spring Boot',
    'Spring Cloud',
    'MySQL',
    'Redis',
    'Kafka',
    'RocketMQ',
    'RabbitMQ',
    'Oracle',
    'SQL',
    'Linux',
    'Docker',
    'Kubernetes',
    'JVM',
    'Go',
    'Python',
    'C++',
    'JavaScript',
    'TypeScript',
    'Vue',
    'React',
    '微服务',
    '分布式',
]


def extract_skills_from_description(description):
    text = normalize_text(description)
    if not text:
        return []

    found = []
    low = text.lower()
    for item in COMMON_SKILLS:
        if item.lower() in low:
            found.append(item)

    # dedupe keep order
    dedup = []
    seen = set()
    for item in found:
        if item not in seen:
            seen.add(item)
            dedup.append(item)

    if dedup:
        return dedup[:8]

    # fallback from description lines
    lines = [line.strip(' ：:;；-') for line in text.splitlines() if line.strip()]
    compact = [line for line in lines if 2 <= len(line) <= 60]
    return compact[:4]


def load_skills_library(skills_dir):
    lib = {}
    base = Path(skills_dir)
    if not base.exists() or not base.is_dir():
        return lib

    for file in base.glob('*.json'):
        data = None
        for enc in ('utf-8', 'gbk', 'utf-8-sig'):
            try:
                with open(file, 'r', encoding=enc) as f:
                    data = json.load(f)
                break
            except Exception:
                continue
        if not isinstance(data, list):
            continue

        values = [normalize_text(x) for x in data if normalize_text(x)]
        if values:
            lib[file.stem.lower()] = values

    return lib


def pick_fallback_skills(skill_lib, keyword, title, count=4):
    if not skill_lib:
        return []

    key = normalize_text(keyword).lower()
    title_low = normalize_text(title).lower()

    pool = []
    for name, values in skill_lib.items():
        if (key and (key in name or name in key)) or (title_low and name in title_low):
            pool.extend(values)

    if not pool:
        for values in skill_lib.values():
            pool.extend(values)

    pool = [x for x in pool if x]
    if not pool:
        return []

    if len(pool) <= count:
        random.shuffle(pool)
        return pool

    return random.sample(pool, count)


def finalize_record(record, driver, keyword, skill_lib, with_detail=True, wait_seconds=2):
    title = normalize_text(record.get('title'))
    company = normalize_text(record.get('company'))
    salary = normalize_text(record.get('salary'))

    salary_min, salary_max, salary_avg = parse_salary(salary)
    salary_min = salary_min if salary_min is not None else 0
    salary_max = salary_max if salary_max is not None else 0
    salary_avg = salary_avg if salary_avg is not None else 0

    skills = record.get('skills_list') or []
    description = normalize_text(record.get('description'))

    if with_detail and (not skills):
        job_url = normalize_text(record.get('job_url'))
        if job_url:
            try:
                driver.get(job_url)
                time.sleep(wait_seconds)
                if not is_security_page(driver.page_source):
                    description = extract_description_from_page(driver)
            except Exception:
                pass

    if not skills:
        skills = extract_skills_from_description(description)

    if not skills:
        skills = pick_fallback_skills(skill_lib, keyword=keyword, title=title, count=4)

    return {
        'title': title,
        'company': company,
        'salary': salary,
        'salary_min': salary_min,
        'salary_max': salary_max,
        'salary_avg': salary_avg,
        'location': normalize_text(record.get('location')),
        'experience': normalize_text(record.get('experience')),
        'education': normalize_text(record.get('education')),
        'industry': normalize_text(record.get('industry')),
        'job_type': normalize_text(record.get('job_type')),
        'company_nature': normalize_text(record.get('company_nature')),
        'company_size': normalize_text(record.get('company_size')),
        'job_url': normalize_text(record.get('job_url')),
        'skills': json.dumps(skills, ensure_ascii=False),
        'source': 'zhilian',
        'company_logo': normalize_company_logo_url(record.get('company_logo')),
        'crawl_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def get_data(driver, url, seen_urls, keyword, skill_lib, detail_wait=2):
    try:
        driver.get_log('performance')
    except Exception:
        pass

    driver.get(url)
    time.sleep(4)

    html_text = driver.page_source
    if is_security_page(html_text):
        print(f'security verification triggered on search page: {url}')
        return []

    raw_records = extract_jobs_from_initial_state(html_text)
    if not raw_records:
        raw_records = extract_jobs_from_performance(driver)
    if not raw_records:
        raw_records = extract_jobs_from_dom(driver)

    page_records = []
    for item in raw_records:
        job_url = normalize_text(item.get('job_url'))
        if not job_url:
            continue
        if job_url in seen_urls:
            continue
        seen_urls.add(job_url)

        final = finalize_record(
            item,
            driver=driver,
            keyword=keyword,
            skill_lib=skill_lib,
            with_detail=True,
            wait_seconds=detail_wait,
        )

        if not final.get('job_url'):
            continue

        print({'title': final.get('title'), 'company': final.get('company'), 'job_url': final.get('job_url')})
        page_records.append(final)

    return page_records


def get_db_connection():
    if pymysql is None:
        raise RuntimeError('Missing dependency: pymysql. Install via pip install pymysql')

    return pymysql.connect(
        host='127.0.0.1',
        port=3306,
        user='root',
        password='root',
        database='recruitment_system',
        charset='utf8mb4',
        autocommit=True,
    )


def save_to_mysql(connection, records):
    if not records:
        return 0

    sql = (
        "INSERT INTO jobs (title, company, salary, salary_min, salary_max, salary_avg, "
        "location, experience, education, industry, job_type, company_nature, company_size, "
        "job_url, skills, source, company_logo, crawl_date) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "title=VALUES(title), company=VALUES(company), salary=VALUES(salary), "
        "salary_min=VALUES(salary_min), salary_max=VALUES(salary_max), salary_avg=VALUES(salary_avg), "
        "location=VALUES(location), experience=VALUES(experience), education=VALUES(education), "
        "industry=VALUES(industry), job_type=VALUES(job_type), company_nature=VALUES(company_nature), "
        "company_size=VALUES(company_size), skills=VALUES(skills), source=VALUES(source), "
        "company_logo=VALUES(company_logo), crawl_date=VALUES(crawl_date), "
        "updated_at=CURRENT_TIMESTAMP"
    )

    values = []
    for item in records:
        values.append(
            (
                item['title'],
                item['company'],
                item['salary'],
                item['salary_min'],
                item['salary_max'],
                item['salary_avg'],
                item['location'],
                item['experience'],
                item['education'],
                item['industry'],
                item['job_type'],
                item['company_nature'],
                item['company_size'],
                item['job_url'],
                item['skills'],
                item['source'],
                item['company_logo'],
                item['crawl_date'],
            )
        )

    with connection.cursor() as cursor:
        cursor.executemany(sql, values)
    return len(values)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Zhilian job crawler')
    parser.add_argument('--base-url', default=DEFAULT_ZHILIAN_URL, help='Search page URL, supports /p1 page pattern')
    parser.add_argument('--key', default='java', help='Keyword for fallback skills matching')
    parser.add_argument('--pages', type=int, default=1, help='Number of pages to crawl')
    parser.add_argument('--detail-wait', type=float, default=2, help='Seconds to wait on job detail page')
    parser.add_argument('--skills-dir', default=SKILLS_DIR, help='Directory for fallback skills json files')
    parser.add_argument('--headless', action='store_true', help='Run chrome in headless mode')
    parser.add_argument('--use-fingerprint', action='store_true', help='Use fingerprint file user-agent/cookie on zhilian')
    parser.add_argument('--fingerprint-file', default=FINGERPRINT_FILE, help='Fingerprint file path')
    parser.add_argument('--cookie', default='', help='Raw cookie string for zhaopin.com')
    parser.add_argument('--user-agent', default='', help='Custom user-agent string')
    args = parser.parse_args()

    HEADLESS = bool(args.headless)
    USE_FINGERPRINT = bool(args.use_fingerprint or args.cookie or args.user_agent)

    fingerprint = read_fingerprint(args.fingerprint_file) if args.use_fingerprint else {}
    if args.cookie:
        fingerprint['cookie'] = args.cookie
    if args.user_agent:
        fingerprint['user_agent'] = args.user_agent
    skill_lib = load_skills_library(args.skills_dir)

    driver = create_driver(fingerprint)
    connection = get_db_connection()

    try:
        if USE_FINGERPRINT:
            driver.get('https://www.zhaopin.com/')
            apply_cookies(driver, fingerprint.get('cookie'), domain='.zhaopin.com')

        seen_urls = set()

        for page in range(1, args.pages + 1):
            url = build_search_url(args.base_url, page)
            records = get_data(
                driver,
                url,
                seen_urls=seen_urls,
                keyword=args.key,
                skill_lib=skill_lib,
                detail_wait=args.detail_wait,
            )
            saved = save_to_mysql(connection, records)
            print(f'page {page} saved {saved} records')
            time.sleep(2)
    finally:
        driver.quit()
        connection.close()
