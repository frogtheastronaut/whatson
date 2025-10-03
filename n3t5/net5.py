import sys
import re
import asyncio
import aiohttp
from aiohttp import ClientTimeout, TCPConnector
import logging
import signal
import time
from n3t5.result import QueryStatus, QueryResult
from n3t5.sites import SitesInformation

# High-performance settings from crawler
MAX_CONCURRENT_REQUESTS = 300
MAX_WORKERS = 50
REQUEST_TIMEOUT = 5

logging.basicConfig(level=logging.INFO, format="%(message)s")

def interpolate_string(input_object, username):
    if isinstance(input_object, str):
        return input_object.replace("{}", username)
    elif isinstance(input_object, dict):
        return {key: interpolate_string(value, username) for key, value in input_object.items()}
    elif isinstance(input_object, list):
        return [interpolate_string(item, username) for item in input_object]
    else:
        return input_object

async def check_site(session, semaphore, username, site_name, site_info):
    async with semaphore:
        try:
            url = interpolate_string(site_info["url"], username.replace(' ', '%20'))
            
            # Check regex validation
            regex_check = site_info.get("regexCheck")
            if regex_check and re.search(regex_check, username) is None:
                return site_name, {
                    "status": QueryResult(username, site_name, url, QueryStatus.ILLEGAL),
                    "url_main": site_info.get("urlMain"),
                    "url_user": "",
                    "http_status": "",
                    "response_text": ""
                }
            
            # Prepare headers
            headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:129.0) Gecko/20100101 Firefox/129.0"}
            if "headers" in site_info:
                headers.update(site_info["headers"])
            
            start_time = time.time()
            async with session.get(url, headers=headers, timeout=ClientTimeout(total=REQUEST_TIMEOUT), allow_redirects=False) as resp:
                query_time = time.time() - start_time
                response_text = await resp.text()
                
                # Determine status based on error type
                error_type = site_info["errorType"]
                query_status = QueryStatus.UNKNOWN
                
                if error_type == "message":
                    # Check if error message appears in response text
                    error_flag = True
                    errors = site_info.get("errorMsg")
                    if isinstance(errors, str):
                        if errors in response_text:
                            error_flag = False
                    elif isinstance(errors, list):
                        for error in errors:
                            if error in response_text:
                                error_flag = False
                                break
                    query_status = QueryStatus.CLAIMED if error_flag else QueryStatus.AVAILABLE
                    
                elif error_type == "status_code":
                    # Check HTTP status codes
                    error_codes = site_info.get("errorCode")
                    if isinstance(error_codes, int):
                        error_codes = [error_codes]
                    
                    # Default logic: 2xx = found, others = not found
                    if 200 <= resp.status < 300:
                        # But check if this success code should indicate "not found"
                        if error_codes and resp.status in error_codes:
                            query_status = QueryStatus.AVAILABLE
                        else:
                            query_status = QueryStatus.CLAIMED
                    else:
                        # 4xx, 5xx = not found (unless specifically listed as success)
                        if error_codes and resp.status in error_codes:
                            query_status = QueryStatus.AVAILABLE
                        else:
                            query_status = QueryStatus.AVAILABLE
                        
                elif error_type == "response_url":
                    # Check if we got redirected
                    if 200 <= resp.status < 300:
                        query_status = QueryStatus.CLAIMED
                    else:
                        query_status = QueryStatus.AVAILABLE
                
                elif error_type == "errorUrl":
                    # Check if response URL matches error URL pattern
                    error_url = site_info.get("errorUrl", "")
                    if error_url and error_url in str(resp.url):
                        query_status = QueryStatus.AVAILABLE
                    elif 200 <= resp.status < 300:
                        query_status = QueryStatus.CLAIMED
                    else:
                        query_status = QueryStatus.AVAILABLE
                
                else:
                    # Unknown error type - default to conservative approach
                    if 200 <= resp.status < 300:
                        query_status = QueryStatus.CLAIMED
                    else:
                        query_status = QueryStatus.AVAILABLE
                
                # Print results as we find them
                if query_status == QueryStatus.CLAIMED:
                    print(f"[+] {site_name}: {url}")
                elif query_status == QueryStatus.AVAILABLE:
                    #print(f"[-] {site_name}: Not found")
                    pass
                else:
                    # print(f"[?] {site_name}: Unknown status")
                    pass
                
                return site_name, {
                    "status": QueryResult(username, site_name, url, query_status, query_time),
                    "url_main": site_info.get("urlMain"),
                    "url_user": url,
                    "http_status": resp.status,
                    "response_text": response_text
                }
                
        except Exception as e:
            logging.debug(f"Error checking {site_name}: {e}")
            # print(f"[!] {site_name}: Error - {str(e)[:50]}...")
            return site_name, {
                "status": QueryResult(username, site_name, url, QueryStatus.UNKNOWN),
                "url_main": site_info.get("urlMain"),
                "url_user": url,
                "http_status": "",
                "response_text": ""
            }

async def sherlock_async(username, site_data):
    results = {}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    
    connector = TCPConnector(
        limit=MAX_CONCURRENT_REQUESTS,
        force_close=True,  # force close connections because we aren't wasting time
        enable_cleanup_closed=True
    )
    
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for site_name, site_info in site_data.items():
            task = asyncio.create_task(check_site(session, semaphore, username, site_name, site_info))
            tasks.append(task)
        
        print(f"Checking {len(tasks)} sites concurrently...")
        completed_tasks = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in completed_tasks:
            if isinstance(result, tuple):
                site_name, site_result = result
                results[site_name] = site_result

    print("\nAll scans finished. Closing connections. This may take some time...")
    if not connector.closed:
        await connector.close()
    
    return results

def sherlock(username, site_data):
    return asyncio.run(sherlock_async(username, site_data))

def main():
    if len(sys.argv) < 2:
        print("Usage: python -m net5 <username>")
        sys.exit(1)
    
    username = sys.argv[1]
    start_time = time.time()
    
    try:
        sites = SitesInformation()
        site_data = sites.sites
        
        print(f"Searching for username '{username}' across {len(site_data)} sites...")
        results = sherlock(username, site_data)
        
        print(f"\n" + "="*50)
        print("SUMMARY:")
        found_count = 0
        for site_name in results:
            if results[site_name]["status"].status == QueryStatus.CLAIMED:
                found_count += 1
        
        elapsed = time.time() - start_time
        print(f"\nCompleted in {elapsed:.2f} seconds")
        print(f"Total found: {found_count} sites")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()