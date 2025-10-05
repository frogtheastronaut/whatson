import sys
import os
import re
import asyncio
import httpx
import logging
import time
from whatson.result import QueryStatus, QueryResult
from whatson.sites import SitesInformation

MAX_CONCURRENT_REQUESTS = 600
REQUEST_TIMEOUT = 5

logging.basicConfig(level=logging.WARNING, format="%(message)s")

def interpolate_string(input_object, username):
    if isinstance(input_object, str):
        return input_object.replace("{}", username)
    elif isinstance(input_object, dict):
        return {key: interpolate_string(value, username) for key, value in input_object.items()}
    elif isinstance(input_object, list):
        return [interpolate_string(item, username) for item in input_object]
    else:
        return input_object

async def check_site(session, semaphore, username, site_name, site_info, keywords=None):
    if keywords is None:
        keywords = []
    async with semaphore:
        try:
            url = interpolate_string(site_info["url"], username.replace(' ', '%20'))
            
            # Skip if username doesn't match site's regex requirements
            regex_check = site_info.get("regexCheck")
            if regex_check and re.search(regex_check, username) is None:
                return site_name, {
                    "status": QueryResult(username, site_name, url, QueryStatus.ILLEGAL),
                    "url_main": site_info.get("urlMain"),
                    "url_user": "",
                    "http_status": "",
                    "response_text": ""
                }
            
            # Set realistic browser headers
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1"
            }
            
            if "headers" in site_info:
                headers.update(site_info["headers"])
            
            # Use special probe URL if specified, otherwise use main URL
            url_probe = site_info.get("urlProbe")
            if url_probe:
                url_probe = interpolate_string(url_probe, username)
            else:
                url_probe = url
            
            # Disable redirects for response_url detection, enable for others
            allow_redirects = site_info["errorType"] != "response_url"
            
            start_time = time.time()
            resp = await session.get(url_probe, headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=allow_redirects)
            query_time = time.time() - start_time
            
            # Get the expected error type
            error_type = site_info["errorType"]
            if isinstance(error_type, str):
                error_type = [error_type]
            
            # Always try to get response text for keyword checking
            try:
                response_text = resp.text
            except Exception:
                response_text = ""
            
            query_status = QueryStatus.UNKNOWN
            
            # Check for WAF/bot detection patterns
            waf_patterns = [
                r'.loading-spinner{visibility:hidden}body.no-js .challenge-running{display:none}',
                r'<span id="challenge-error-text">',
                r'AwsWafIntegration.forceRefreshToken',
                r'{return l.onPageView}}),Object.defineProperty(r,"perimeterxIdentifiers"'
            ]
            
            if any(pattern in response_text for pattern in waf_patterns):
                query_status = QueryStatus.UNKNOWN
            else:
                if "message" in error_type:
                    error_flag = True
                    errors = site_info.get("errorMsg")
                    
                    if isinstance(errors, str):
                        if errors in response_text:
                            error_flag = False
                    else:
                        for error in errors:
                            if error in response_text:
                                error_flag = False
                                break
                    
                    query_status = QueryStatus.CLAIMED if error_flag else QueryStatus.AVAILABLE
                
                if "status_code" in error_type and query_status != QueryStatus.AVAILABLE:
                    error_codes = site_info.get("errorCode")
                    query_status = QueryStatus.CLAIMED
                    
                    if isinstance(error_codes, int):
                        error_codes = [error_codes]
                    
                    if error_codes and resp.status_code in error_codes:
                        query_status = QueryStatus.AVAILABLE
                    elif resp.status_code >= 300 or resp.status_code < 200:
                        query_status = QueryStatus.AVAILABLE
                
                if "response_url" in error_type and query_status != QueryStatus.AVAILABLE:
                    query_status = QueryStatus.CLAIMED if 200 <= resp.status_code < 300 else QueryStatus.AVAILABLE
            
            # Print results
            if query_status == QueryStatus.CLAIMED:
                if keywords and response_text:
                    keyword_matches = sum(1 for keyword in keywords if keyword.lower() in response_text.lower())
                    if keyword_matches > 0:
                        print(f"[+] [Keyword]   {site_name}: {url}")
                elif not keywords:
                    print(f"[+] [No Keyword] {site_name}: {url}")
            
            return site_name, {
                "status": QueryResult(username, site_name, url, query_status, query_time),
                "url_main": site_info.get("urlMain"),
                "url_user": url,
                "http_status": resp.status_code,
                "response_text": response_text
            }
                
        except Exception as e:
            logging.debug(f"Error checking {site_name}: {e}")
            return site_name, {
                "status": QueryResult(username, site_name, url, QueryStatus.UNKNOWN),
                "url_main": site_info.get("urlMain"),
                "url_user": url,
                "http_status": "",
                "response_text": ""
            }

async def whatson_async(username, site_data, keywords=None):
    if keywords is None:
        keywords = []
    start_time = time.time()
    results = {}
    semaphore = asyncio.Semaphore(100)  # Balanced concurrency
    
    # Configure httpx with HTTP/2, custom limits
    limits = httpx.Limits(
        max_keepalive_connections=MAX_CONCURRENT_REQUESTS,
        max_connections=MAX_CONCURRENT_REQUESTS,
        keepalive_expiry=30.0
    )
    
    async with httpx.AsyncClient(
        limits=limits,
        http2=True,  # Enable HTTP/2 for better performance
        timeout=httpx.Timeout(REQUEST_TIMEOUT),
        follow_redirects=True,  # Follow redirects to get final content
        verify=False  # Skip SSL verification for speed
    ) as session:
        tasks = []
        for site_name, site_info in site_data.items():
            task = asyncio.create_task(check_site(session, semaphore, username, site_name, site_info, keywords))
            tasks.append(task)
        
        print(f"Checking {len(tasks)} sites..")
        completed_tasks = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in completed_tasks:
            if isinstance(result, tuple):
                site_name, site_result = result
                results[site_name] = site_result

    
    print(f"\n{'='*50}")
    print("SUMMARY:")
    
    found_count = 0
    keyword_matched_count = 0
    
    for site_result in results.values():
        if site_result["status"].status == QueryStatus.CLAIMED:
            found_count += 1
            if keywords and site_result["response_text"]:
                response_lower = site_result["response_text"].lower()
                if any(keyword.lower() in response_lower for keyword in keywords):
                    keyword_matched_count += 1
    
    elapsed = time.time() - start_time
    print(f"\nCompleted in {elapsed:.2f} seconds")
    print(f"Total found: {found_count} sites")
    if keywords:
        print(f"With keyword matches: {keyword_matched_count} sites")
    print("\nAll scans finished. Closing connections")
    os._exit(0)
    
    return results
def whatson(username, site_data, keywords=None):
    if keywords is None:
        keywords = []
    return asyncio.run(whatson_async(username, site_data, keywords))

def main():
    if len(sys.argv) < 2:
        print("Usage: python -m whatson <username> [keywords...]")
        sys.exit(1)
    
    username = sys.argv[1]
    keywords = sys.argv[2:] if len(sys.argv) > 2 else []
    
    try:
        sites = SitesInformation()
        site_data = sites.sites
        
        if keywords:
            print(f"Searching for username '{username}' across {len(site_data)} sites with keywords: {', '.join(keywords)}...")
        else:
            print(f"Searching for username '{username}' across {len(site_data)} sites...")
        whatson(username, site_data, keywords)
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()