from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import pandas as pd
import json
import time
import base64
import re
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import argparse

try:
    import pymysql
except ImportError:
    pymysql = None

CHROME_BINARY = None
HEADLESS = False
FINGERPRINT_FILE = '1.txt'
USE_FINGERPRINT = False


def read_fingerprint(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            lines = [line.strip() for line in file if line.strip()]
    except FileNotFoundError:
        return {}

    def find_value(key):
        key_lower = key.lower()
        for index, line in enumerate(lines):
            if line.lower() == key_lower and index + 1 < len(lines):
                return lines[index + 1]
        return None

    return {
        'user_agent': find_value('user-agent'),
        'cookie': find_value('cookie'),
        'xsrf_token': find_value('x-xsrf-token'),
    }


def update_fingerprint_from_browser(file_path):
    chrome_options = Options()
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    if HEADLESS:
        chrome_options.add_argument('--headless=new')
    if CHROME_BINARY:
        chrome_options.binary_location = CHROME_BINARY

    chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    driver = webdriver.Chrome(options=chrome_options)
    driver.implicitly_wait(8)
    driver.execute_cdp_cmd('Network.enable', {})

    driver.get('https://www.liepin.com/')
    time.sleep(3)

    user_agent = driver.execute_script('return navigator.userAgent')
    cookies = driver.get_cookies()
    cookie_string = '; '.join([f"{item['name']}={item['value']}" for item in cookies])
    xsrf_token = ''
    for item in cookies:
        if item.get('name') == 'XSRF-TOKEN':
            xsrf_token = item.get('value', '')
            break

    with open(file_path, 'w', encoding='utf-8') as file:
        file.write('user-agent\n')
        file.write(f"{user_agent}\n")
        file.write('cookie\n')
        file.write(f"{cookie_string}\n")
        file.write('x-xsrf-token\n')
        file.write(f"{xsrf_token}\n")

    driver.quit()


def apply_cookies(driver, cookie_string):
    if not cookie_string:
        return
    cookies = [c.strip() for c in cookie_string.split(';') if c.strip()]
    for cookie in cookies:
        if '=' not in cookie:
            continue
        name, value = cookie.split('=', 1)
        driver.add_cookie({
            'name': name.strip(),
            'value': value.strip(),
            'domain': '.liepin.com',
        })


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
    driver.execute_cdp_cmd('Network.enable', {})

    extra_headers = {}
    if USE_FINGERPRINT and fingerprint.get('xsrf_token'):
        extra_headers['x-xsrf-token'] = fingerprint['xsrf_token']
    if extra_headers:
        driver.execute_cdp_cmd('Network.setExtraHTTPHeaders', {'headers': extra_headers})

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


def normalize_skills(value):
    if not value:
        return []
    if isinstance(value, list):
        skills = []
        for item in value:
            if isinstance(item, dict):
                text = item.get('name') or item.get('label') or item.get('value')
            else:
                text = str(item)
            if text:
                skills.append(text.strip())
        return [skill for skill in skills if skill]
    if isinstance(value, str):
        return [skill.strip() for skill in re.split(r"[、,/|]+", value) if skill.strip()]
    return [str(value).strip()]


def normalize_text(value):
    if value is None:
        return ''
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip()


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
    elif re.search(r"[kK]", text):
        unit = 'k'

    numbers = re.findall(r"\d+(?:\.\d+)?", text)
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


def build_job_url(job_item):
    job = job_item.get('job', {}) if isinstance(job_item, dict) else {}
    job_url = pick_value(job, ['jobUrl', 'jobLink', 'link', 'detailUrl'])
    if job_url:
        return job_url
    job_id = pick_value(job, ['jobId', 'job_id', 'id'])
    if job_id:
        return f"https://www.liepin.com/job/{job_id}.shtml"
    return None


def extract_job_item(job_item):
    job = job_item.get('job', {}) if isinstance(job_item, dict) else {}
    comp = job_item.get('comp', {}) if isinstance(job_item, dict) else {}

    title = normalize_text(pick_value(job, ['title', 'jobName']))
    company = normalize_text(pick_value(comp, ['compName', 'name']))
    salary = normalize_text(pick_value(job, ['salary', 'salaryDesc', 'salaryRange']))
    salary_min, salary_max, salary_avg = parse_salary(salary)
    location = normalize_text(pick_value(job, ['dq', 'city', 'workPlace', 'workCity']))
    experience = normalize_text(pick_value(job, ['requireWorkYears', 'workYear', 'workYearDesc']))
    education = normalize_text(pick_value(job, ['requireEduLevel', 'eduLevel', 'education']))
    industry = normalize_text(pick_value(comp, ['compIndustry', 'industry', 'industryName']))
    job_type = normalize_text(pick_value(job, ['jobType', 'jobKind', 'workType']))
    company_nature = normalize_text(
        pick_value(comp, ['compKind', 'compType', 'compNature', 'compProperty', 'compStage'])
    )
    company_size = normalize_text(pick_value(comp, ['compScale', 'scale', 'compSize']))
    job_url = build_job_url(job_item)
    skills_raw = pick_value(job, ['skills', 'skill', 'labels', 'tagList', 'keyLabels', 'keySkills'])
    skills = normalize_skills(skills_raw)
    source = 'liepin'
    company_logo = normalize_text(pick_value(comp, ['compLogo', 'logo', 'logoUrl', 'compLogoUrl']))

    salary_min = salary_min if salary_min is not None else 0
    salary_max = salary_max if salary_max is not None else 0
    salary_avg = salary_avg if salary_avg is not None else 0

    if not salary:
        salary = ''

    return {
        'title': title,
        'company': company,
        'salary': salary,
        'salary_min': salary_min,
        'salary_max': salary_max,
        'salary_avg': salary_avg,
        'location': location,
        'experience': experience,
        'education': education,
        'industry': industry,
        'job_type': job_type,
        'company_nature': company_nature,
        'company_size': company_size,
        'job_url': job_url,
        'skills': json.dumps(skills, ensure_ascii=False),
        'source': source,
        'company_logo': company_logo,
        'crawl_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def get_data(driver, url, seen_urls):
    try:
        driver.get_log('performance')
    except Exception:
        pass

    driver.get(url)
    time.sleep(3)

    page_records = []

    logs = [json.loads(log['message'])['message'] for log in driver.get_log('performance')]
    static_js_403 = False
    for log in logs:
        if log.get('method') != 'Network.responseReceived':
            continue
        response = log.get('params', {}).get('response', {})
        response_url = response.get('url', '')
        if (
            response.get('status') == 403
            and 'concat.lietou-static.com/fe-www-pc/v6/js' in response_url
        ):
            static_js_403 = True
        if 'https://api-c.liepin.com/api/com.liepin.searchfront4c.pc-search-job' not in response_url:
            continue

        request_id = log.get('params', {}).get('requestId')
        if not request_id:
            continue

        try:
            response_dict = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': request_id})
            body = response_dict.get('body', '')
            if response_dict.get('base64Encoded'):
                body = base64.b64decode(body).decode('utf-8', errors='ignore')
            body_dict = json.loads(body)
        except Exception:
            continue

        job_card_list = safe_get(body_dict, 'data', 'data', 'jobCardList') or []
        for item in job_card_list:
            job_data = extract_job_item(item)
            job_url = job_data.get('job_url')
            if not job_url:
                continue
            if job_url in seen_urls:
                continue
            seen_urls.add(job_url)
            print(job_data)
            page_records.append(job_data)

    if not page_records and static_js_403:
        print('Detected 403 on Liepin JS assets. Disable fingerprint/cookie injection and retry.')

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
        values.append((
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
        ))

    with connection.cursor() as cursor:
        cursor.executemany(sql, values)
    return len(values)


def build_search_url(base_url, current_page, page_size=None, key=None):
    parsed = urlparse(base_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params['currentPage'] = [str(current_page)]
    if page_size is not None:
        params['pageSize'] = [str(page_size)]
    if key:
        params['key'] = [key]
    query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=query))


def to_excel(data_list):
    if not data_list:
        print('没有获取到数据')
        return
    df = pd.DataFrame(data_list)
    df = df.drop_duplicates()
    df.to_excel('招聘信息.xlsx', index=False)
    print(f'数据已保存到 招聘信息.xlsx，共 {len(df)} 条记录')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Liepin job crawler')
    parser.add_argument('--key', default='java', help='Search keyword, e.g. java')
    parser.add_argument('--pages', type=int, default=1, help='Number of pages to crawl')
    args = parser.parse_args()

    key = args.key
    base_search_url = 'https://www.liepin.com/zhaopin/?city=410&currentPage=0&pageSize=40'
    page_num = args.pages
    page_size = 40

    fingerprint = read_fingerprint(FINGERPRINT_FILE)
    driver = create_driver(fingerprint)

    driver.get('https://www.liepin.com/')
    if USE_FINGERPRINT:
        apply_cookies(driver, fingerprint.get('cookie'))

    seen_urls = set()
    connection = get_db_connection()

    for current_page in range(0, page_num):
        url = build_search_url(base_search_url, current_page, page_size=page_size, key=key)
        records = get_data(driver, url, seen_urls)
        saved = save_to_mysql(connection, records)
        print(f'page {current_page} saved {saved} records')
        time.sleep(2)

    driver.quit()
    connection.close()
