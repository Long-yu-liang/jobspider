from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import argparse
import time


def build_driver(headless):
    chrome_options = Options()
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    if headless:
        chrome_options.add_argument('--headless=new')
    return webdriver.Chrome(options=chrome_options)


def write_fingerprint(output_path, user_agent, cookies, xsrf_token):
    with open(output_path, 'w', encoding='utf-8') as file:
        file.write('user-agent\n')
        file.write(f"{user_agent}\n")
        file.write('cookie\n')
        file.write(f"{cookies}\n")
        file.write('x-xsrf-token\n')
        file.write(f"{xsrf_token}\n")


def main():
    parser = argparse.ArgumentParser(description='Generate Liepin fingerprint file')
    parser.add_argument('--output', default='1.txt', help='Output file path')
    parser.add_argument('--headless', action='store_true', help='Run Chrome headless')
    parser.add_argument('--wait', type=int, default=5, help='Seconds to wait for page load')
    args = parser.parse_args()

    driver = build_driver(args.headless)
    driver.get('https://www.liepin.com/')
    time.sleep(args.wait)

    if not args.headless:
        input('If you need to login or search, do it now, then press Enter... ')

    user_agent = driver.execute_script('return navigator.userAgent')
    cookie_items = driver.get_cookies()
    cookie_string = '; '.join([f"{item['name']}={item['value']}" for item in cookie_items])
    xsrf_token = ''
    for item in cookie_items:
        if item.get('name') == 'XSRF-TOKEN':
            xsrf_token = item.get('value', '')
            break

    write_fingerprint(args.output, user_agent, cookie_string, xsrf_token)
    driver.quit()

    print(f"Fingerprint saved to {args.output}")


if __name__ == '__main__':
    main()
