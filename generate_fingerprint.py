import argparse
from pathlib import Path
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


def build_driver(headless):
    chrome_options = Options()
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    if headless:
        chrome_options.add_argument('--headless=new')
    return webdriver.Chrome(options=chrome_options)


def write_legacy_fingerprint(output_path, user_agent, cookies, xsrf_token):
    with open(output_path, 'w', encoding='utf-8') as file:
        file.write('user-agent\n')
        file.write(f'{user_agent}\n')
        file.write('cookie\n')
        file.write(f'{cookies}\n')
        file.write('x-xsrf-token\n')
        file.write(f'{xsrf_token}\n')


def load_env(env_path):
    data = {}
    path = Path(env_path)
    if not path.exists():
        return data

    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        data[key.strip()] = value.strip()
    return data


def write_env(env_path, data):
    lines = ['# Fingerprints']
    ordered_keys = [
        'LIEPIN_USER_AGENT',
        'LIEPIN_COOKIE',
        'LIEPIN_XSRF_TOKEN',
        'ZHILIAN_USER_AGENT',
        'ZHILIAN_COOKIE',
        'ZHILIAN_XSRF_TOKEN',
    ]

    for key in ordered_keys:
        if key in data:
            lines.append(f'{key}={data[key]}')

    # Keep unknown keys too.
    for key in sorted(data.keys()):
        if key not in ordered_keys:
            lines.append(f'{key}={data[key]}')

    Path(env_path).write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='Generate Liepin fingerprint and write to .env')
    parser.add_argument('--env', default='.env', help='Env file path')
    parser.add_argument('--output', default='1.txt', help='Legacy output file path')
    parser.add_argument('--no-legacy', action='store_true', help='Do not write legacy 1.txt format')
    parser.add_argument('--headless', action='store_true', help='Run Chrome headless')
    parser.add_argument('--wait', type=int, default=5, help='Seconds to wait for page load')
    args = parser.parse_args()

    driver = build_driver(args.headless)
    try:
        driver.get('https://www.liepin.com/')
        time.sleep(args.wait)

        if not args.headless:
            input('If you need to login/search on Liepin, do it now, then press Enter... ')

        user_agent = driver.execute_script('return navigator.userAgent')
        cookie_items = driver.get_cookies()
        cookie_string = '; '.join([f"{item['name']}={item['value']}" for item in cookie_items])
        xsrf_token = ''
        for item in cookie_items:
            if item.get('name') == 'XSRF-TOKEN':
                xsrf_token = item.get('value', '')
                break
    finally:
        driver.quit()

    env_data = load_env(args.env)
    env_data['LIEPIN_USER_AGENT'] = user_agent
    env_data['LIEPIN_COOKIE'] = cookie_string
    env_data['LIEPIN_XSRF_TOKEN'] = xsrf_token
    write_env(args.env, env_data)

    if not args.no_legacy:
        write_legacy_fingerprint(args.output, user_agent, cookie_string, xsrf_token)

    print(f'Liepin fingerprint saved to {args.env}')
    if not args.no_legacy:
        print(f'Legacy fingerprint also saved to {args.output}')


if __name__ == '__main__':
    main()
