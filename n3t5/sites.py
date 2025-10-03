import json
import os




class SitesInformation:
    def __init__(self, data_file_path=None):
        if not data_file_path:
            data_file_path = os.path.join(os.path.dirname(__file__), "resources", "data.json")
        
        with open(data_file_path, "r", encoding="utf-8") as file:
            site_data = json.load(file)
        
        site_data.pop('$schema', None)
        self.sites = site_data



