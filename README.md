# makina404
A Python tool that enumerates subdomains,domains and checks if they return a 404 status code, takes a screenshot and sends an alert with the screenshot to a specified Discord webhook. This helps me discover subdomain takeovers. Unlike many others i know for a fact that a lot of people already automates subdomain takeovers with nuclei so i do things a little bit different. I look for subdomains manually, which takes a lot of time of course but i was still manage to get some success with this method. Not all providers have templates for nuclei and some are marked as Not vulnerable on [can-i-take-over-xyz](https://github.com/EdOverflow/can-i-take-over-xyz) so they don't make templates. I still look at those providers and see if i can takeover which i was able to.

## Installation
```
git clone https://github.com/firaterror/makina404.git
cd makina404
pip3.10 install httpx httpx[http2] asyncio playwright aiohttp python-dotenv
playwright install chromium
```
**Input Domains (`domains.txt`):**
    Create a file named `domains.txt` in the same directory as `makina404.py`. List the root domains you want to scan, one per line.

**Discord Webhook (Environment Variable):**
    Set the `DISCORD_SCANNER_WEBHOOK` environment variable to your Discord webhook URL.
      
      *   Create a file named `.env` in the project directory.
      
      *   Add the line: `DISCORD_SCANNER_WEBHOOK="YOUR_WEBHOOK_URL_HERE"`

**Script Constants (Optional Fine-tuning):**
    You can adjust these constants directly within the `makina404.py` script:
    
    *   `SCREENSHOT_CONCURRENCY`: Max number of parallel browser operations (default: 5).
    
    *   `HTTPX_CONCURRENCY`: Max number of parallel HTTP checks (default: 100).
    
    *   `HTTP_TIMEOUT`: Timeout for HTTP requests in seconds (default: 10).
    
    *   `BROWSER_TIMEOUT`: Timeout for Playwright page navigation in milliseconds (default: 15000).
    
    *   `RAPIDDNS_PATH`: Path to `rapiddns` if not in system PATH (default: 'rapiddns').

## Usage

1.  Ensure your `domains.txt` file is populated.
2.  Ensure the `DISCORD_SCANNER_WEBHOOK` environment variable is set (or the `.env` file exists).
3.  Run it:
    ```python3.10 makina404.py```
