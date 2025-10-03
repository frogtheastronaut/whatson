import json
import os

class SitesInformation:
    def __init__(self, data_file_path=None):
        if not data_file_path:
            data_file_path = os.path.join(os.path.dirname(__file__), "resources", "data.json")
            false_positives_file_path = os.path.join(os.path.dirname(__file__), "resources", "false_positives.txt")
        
        with open(data_file_path, "r", encoding="utf-8") as file:
            site_data = json.load(file)
        # remove all false positives from site_data
        with open(false_positives_file_path, "r", encoding="utf-8") as fp_file:
            false_positives = [line.strip() for line in fp_file]
        for site in false_positives:
            if site in site_data:
                del site_data[site]
        self.sites = site_data