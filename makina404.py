import asyncio
import subprocess
import httpx
from playwright.async_api import async_playwright, Error as PlaywrightError
import aiohttp
import os
import sys
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
    load_dotenv() # Load variables from .env file if it exists
    print("[*] Loaded environment variables from .env file (if present).")
except ImportError:
    print("[*] python-dotenv not installed, relying on system environment variables.")

# --- Configuration ---
INPUT_FILE = "domains.txt"
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_SCANNER_WEBHOOK')
# Path to rapiddns executable if not in PATH, otherwise leave as 'rapiddns'
RAPIDDNS_PATH = 'rapiddns'
# Limit concurrent browser operations (screenshots)
SCREENSHOT_CONCURRENCY = 5
# Limit concurrent subdomain checks (HTTP requests)
HTTPX_CONCURRENCY = 100
# User-Agent for requests
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36 SubdomainScanner/1.0"
# HTTPX request timeout (seconds)
HTTP_TIMEOUT = 10
# Playwright navigation timeout (milliseconds)
BROWSER_TIMEOUT = 15000 # 15 seconds

# --- Global Semaphore for Browser Operations ---
browser_semaphore = asyncio.Semaphore(SCREENSHOT_CONCURRENCY)
httpx_semaphore = asyncio.Semaphore(HTTPX_CONCURRENCY)

# --- Helper Functions ---

async def run_rapiddns(domain: str) -> list[str]:
    """Runs the rapiddns tool asynchronously to get subdomains."""
    subdomains = set()
    command = [RAPIDDNS_PATH, "-s", domain]
    print(f"[*] Enumerating subdomains for: {domain}")
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            print(f"[!] Error running rapiddns for {domain}: {stderr.decode(errors='ignore').strip()}", file=sys.stderr)
            return []

        output = stdout.decode(errors='ignore').strip()
        if output:
            # rapiddns output is one subdomain per line
            found = output.splitlines()
            subdomains.update(sub.strip() for sub in found if sub.strip())
            print(f"[*] Found {len(found)} potential subdomains for {domain}")
        else:
            print(f"[*] No subdomains found by rapiddns for {domain}")

    except FileNotFoundError:
        print(f"[!] Error: '{RAPIDDNS_PATH}' command not found. Make sure rapiddns is installed and in your PATH.", file=sys.stderr)
        # Exit if rapiddns isn't found, as the script can't proceed
        sys.exit(1)
    except Exception as e:
        print(f"[!] Exception running rapiddns for {domain}: {e}", file=sys.stderr)

    return list(subdomains)

async def send_to_discord(webhook_url: str, subdomain_url: str, screenshot_bytes: bytes):
    """Sends a message with a screenshot to the Discord webhook."""
    if not webhook_url or not screenshot_bytes:
        return

    try:
        # Use aiohttp for multipart/form-data upload needed by Discord webhooks
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field('payload_json', f'{{"content": "Potential Subdomain Takeover Detected (404): `{subdomain_url}`"}}')
            # Discord expects the file field to be named 'file', 'file1', etc.
            form.add_field('file1', screenshot_bytes, filename='screenshot.png', content_type='image/png')

            async with session.post(webhook_url, data=form) as response:
                if 200 <= response.status < 300:
                    print(f"[+] Successfully sent notification for {subdomain_url} to Discord.")
                else:
                    response_text = await response.text()
                    print(f"[!] Failed to send notification for {subdomain_url} to Discord. Status: {response.status}, Response: {response_text}", file=sys.stderr)
    except aiohttp.ClientError as e:
        print(f"[!] aiohttp Error sending Discord notification for {subdomain_url}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[!] Unexpected Error sending Discord notification for {subdomain_url}: {e}", file=sys.stderr)


async def take_screenshot(browser, url: str) -> bytes | None:
    """Takes a screenshot of a given URL using Playwright."""
    screenshot_bytes = None
    # Limit concurrent browser page creation/navigation/screenshotting
    async with browser_semaphore:
        print(f"[*] Attempting screenshot for {url} (semaphore acquired)")
        page = None
        try:
            context = await browser.new_context(
                user_agent=USER_AGENT,
                ignore_https_errors=True, # Important for potentially misconfigured subdomains
                java_script_enabled=True  # Some 404 pages might need JS
            )
            page = await context.new_page()
            await page.goto(url, timeout=BROWSER_TIMEOUT, wait_until='domcontentloaded') # Wait until DOM is loaded
            # Wait a tiny bit for any dynamic rendering if needed, adjust if necessary
            # await asyncio.sleep(0.5)
            screenshot_bytes = await page.screenshot(type='png', full_page=False) # Capture viewport
            print(f"[*] Screenshot captured for {url}")
            await page.close()
            await context.close() # Close context to free up resources
        except PlaywrightError as e:
            print(f"[!] Playwright Error taking screenshot for {url}: {e}", file=sys.stderr)
            if "net::ERR_NAME_NOT_RESOLVED" in str(e):
                print(f"[*] Note: DNS resolution failed for {url} (common)")
            elif "Timeout" in str(e):
                print(f"[*] Note: Timeout during navigation/screenshot for {url}")
        except Exception as e:
            print(f"[!] Unexpected Error taking screenshot for {url}: {e}", file=sys.stderr)
        finally:
            if page and not page.is_closed():
                await page.close()
            # Context is closed within the try block if successful
            # print(f"[*] Released semaphore for {url}") # Optional debug print
    return screenshot_bytes


async def check_subdomain(subdomain: str, client: httpx.AsyncClient, browser, webhook_url: str):
    """Checks HTTP status of a subdomain and triggers screenshot/alert if 404."""
    # Try HTTPS first, then HTTP
    protocols = ['https', 'http']
    for proto in protocols:
        url = f"{proto}://{subdomain}"
        async with httpx_semaphore: # Limit concurrent httpx requests
            try:
                response = await client.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
                # print(f"[*] Checked {url} - Status: {response.status_code}") # Debug

                if response.status_code == 404:
                    print(f"[!] Potential Takeover: {url} responded with 404")
                    screenshot = await take_screenshot(browser, url)
                    if screenshot:
                        await send_to_discord(webhook_url, url, screenshot)
                    # If HTTPS gave 404, no need to check HTTP
                    return # Exit function after handling 404

                # Optional: Handle other interesting status codes?
                # elif response.status_code in [500, 502, 503]:
                #     print(f"[*] Interesting Status {response.status_code} for {url}")

            except httpx.HTTPStatusError as e:
                # This catches non-2xx responses if raise_for_status() were used, but we handle 404 explicitly
                if e.response.status_code == 404:
                     print(f"[!] Potential Takeover: {url} responded with 404 (caught via HTTPStatusError)")
                     screenshot = await take_screenshot(browser, url)
                     if screenshot:
                         await send_to_discord(webhook_url, url, screenshot)
                     return # Exit function after handling 404
                else:
                    print(f"[*] HTTP Error for {url}: Status {e.response.status_code}", file=sys.stderr)
                    # Don't proceed to screenshot on non-404 server errors usually
            except (httpx.TimeoutException):
                print(f"[*] Timeout checking {url}")
            except (httpx.ConnectError, httpx.NetworkError):
                print(f"[*] Connection Error checking {url}")
                # If HTTPS fails connection, we might still want to try HTTP (handled by loop)
                continue # Try next protocol (HTTP)
            except (httpx.TooManyRedirects):
                 print(f"[*] Too many redirects for {url}")
            except Exception as e:
                print(f"[!] Unexpected Error checking {url}: {type(e).__name__} - {e}", file=sys.stderr)
                # If HTTPS had an unexpected error, maybe still try HTTP?
                continue # Try next protocol (HTTP)

        # If we get here (after potentially checking both http/https without a 404), the subdomain is likely okay or had other issues.
        # A small delay can prevent overwhelming some servers, but slows things down.
        # await asyncio.sleep(0.05) # Optional small delay

# --- Main Execution ---

async def main():
    """Main function to coordinate the scanning process."""
    try:
        with open(INPUT_FILE, 'r') as f:
            target_domains = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    except FileNotFoundError:
        print(f"[!] Error: Input file '{INPUT_FILE}' not found.", file=sys.stderr)
        return

    if not target_domains:
        print("[!] No domains found in input file.")
        return

    if not DISCORD_WEBHOOK_URL or not urlparse(DISCORD_WEBHOOK_URL).scheme in ['http', 'https']:
        print("[!] Invalid or missing DISCORD_WEBHOOK_URL.", file=sys.stderr)
        # Continue without Discord notifications if desired, or exit
        # return

    print(f"--- Starting Subdomain Takeover Scan ---")
    print(f"Targets: {len(target_domains)} domains from {INPUT_FILE}")
    print(f"Screenshot Concurrency: {SCREENSHOT_CONCURRENCY}")
    print(f"HTTP Request Concurrency: {HTTPX_CONCURRENCY}")
    print(f"Discord Webhook Configured: {'Yes' if DISCORD_WEBHOOK_URL else 'No'}")
    print("-" * 40)


    # Setup Playwright and HTTPX Client
    async with async_playwright() as p, \
               httpx.AsyncClient(verify=False, headers={'User-Agent': USER_AGENT}, timeout=HTTP_TIMEOUT, limits=httpx.Limits(max_connections=HTTPX_CONCURRENCY, max_keepalive_connections=20)) as client: # Disable SSL verify for flexibility

        browser = None
        try:
            # Launch browser once
            browser = await p.chromium.launch() # headless=True is default
            print("[*] Browser launched successfully.")

            all_subdomain_check_tasks = []
            for domain in target_domains:
                subdomains = await run_rapiddns(domain)
                if subdomains:
                    # Create check tasks for each subdomain found for this domain
                    for sub in subdomains:
                        # Basic validation - skip if it doesn't look like a valid hostname part
                        if not sub or '.' not in sub or sub.startswith('.') or sub.endswith('.'):
                             # print(f"[*] Skipping invalid subdomain format: {sub}")
                             continue
                        task = asyncio.create_task(check_subdomain(sub, client, browser, DISCORD_WEBHOOK_URL))
                        all_subdomain_check_tasks.append(task)

            # Wait for all subdomain check tasks to complete
            if all_subdomain_check_tasks:
                print(f"\n[*] Checking {len(all_subdomain_check_tasks)} total subdomains concurrently...")
                await asyncio.gather(*all_subdomain_check_tasks)
            else:
                print("\n[*] No valid subdomains found across all targets to check.")

        except PlaywrightError as e:
             print(f"[!] Failed to launch browser: {e}", file=sys.stderr)
             print("[!] Make sure you have run 'playwright install chromium'", file=sys.stderr)
        except Exception as e:
            print(f"[!] An unexpected error occurred during main execution: {e}", file=sys.stderr)
        finally:
            if browser:
                await browser.close()
                print("[*] Browser closed.")

    print("\n--- Scan Complete ---")


if __name__ == "__main__":
    # On Windows, the default event loop policy might cause issues with subprocesses
    # If you encounter Proactor event loop errors, uncomment the following line:
    # if sys.platform == "win32":
    #    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
