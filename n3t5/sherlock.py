import sys
import re
import requests
from time import monotonic
from n3t5.result import QueryStatus, QueryResult
from n3t5.sites import SitesInformation

def interpolate_string(input_object, username):
    if isinstance(input_object, str):
        return input_object.replace("{}", username)
    elif isinstance(input_object, dict):
        return {key: interpolate_string(value, username) for key, value in input_object.items()}
    elif isinstance(input_object, list):
        return [interpolate_string(item, username) for item in input_object]
    else:
        return input_object

def sherlock(username, site_data):
    results = {}
    
    for social_network, net_info in site_data.items():
        results[social_network] = {}
        results[social_network]["url_main"] = net_info.get("urlMain")
        
        url = interpolate_string(net_info["url"], username.replace(' ', '%20'))
        
        regex_check = net_info.get("regexCheck")
        if regex_check and re.search(regex_check, username) is None:
            results[social_network]["status"] = QueryResult(username, social_network, url, QueryStatus.ILLEGAL)
            results[social_network]["url_user"] = ""
            results[social_network]["http_status"] = ""
            results[social_network]["response_text"] = ""
            continue
            
        results[social_network]["url_user"] = url
        
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:129.0) Gecko/20100101 Firefox/129.0"}
        if "headers" in net_info:
            headers.update(net_info["headers"])
        
        try:
            start_time = monotonic()
            r = requests.get(url, headers=headers, timeout=60, allow_redirects=False)
            query_time = monotonic() - start_time
            
            error_type = net_info["errorType"]
            
            if error_type == "message":
                error_flag = True
                errors = net_info.get("errorMsg")
                if isinstance(errors, str):
                    if errors in r.text:
                        error_flag = False
                else:
                    for error in errors:
                        if error in r.text:
                            error_flag = False
                            break
                if error_flag:
                    query_status = QueryStatus.CLAIMED
                else:
                    query_status = QueryStatus.AVAILABLE
                    
            elif error_type == "status_code":
                error_codes = net_info.get("errorCode")
                query_status = QueryStatus.CLAIMED
                if isinstance(error_codes, int):
                    error_codes = [error_codes]
                if error_codes is not None and r.status_code in error_codes:
                    query_status = QueryStatus.AVAILABLE
                elif r.status_code >= 300 or r.status_code < 200:
                    query_status = QueryStatus.AVAILABLE
                    
            elif error_type == "response_url":
                if 200 <= r.status_code < 300:
                    query_status = QueryStatus.CLAIMED
                else:
                    query_status = QueryStatus.AVAILABLE
            else:
                query_status = QueryStatus.UNKNOWN
            
            results[social_network]["status"] = QueryResult(username, social_network, url, query_status, query_time)
            results[social_network]["http_status"] = r.status_code
            results[social_network]["response_text"] = r.text
            
        except Exception:
            results[social_network]["status"] = QueryResult(username, social_network, url, QueryStatus.UNKNOWN)
            results[social_network]["http_status"] = ""
            results[social_network]["response_text"] = ""
    
    return results

def main():
    if len(sys.argv) < 2:
        print("Usage: python -m net5 <username>")
        sys.exit(1)
    
    username = sys.argv[1]
    
    try:
        sites = SitesInformation()
        site_data = sites.sites
        
        print(f"Searching for username '{username}'...")
        results = sherlock(username, site_data)
        
        found_count = 0
        for site_name in results:
            if results[site_name]["status"].status == QueryStatus.CLAIMED:
                found_count += 1
                print(f"[+] {site_name}: {results[site_name]['url_user']}")
        
        print(f"\nTotal found: {found_count} sites")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()