import os
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

# MongoDB Setup
mongo_client =  MongoClient(f"mongodb+srv://{os.environ['USER_NAME']}:{os.environ['PASSWORD']}@bugbusters.exmbwtk.mongodb.net/?retryWrites=true&w=majority&appName=bugbusters")

db = mongo_client[os.environ['DB_NAME']]
collection = db['servicenow_incidents']

def get_servicenow_incidents():
    url = f"{os.environ['SERVICENOW_URL']}/api/now/table/incident?sysparm_query=caller_id.user_name=TestUser&sysparm_limit=100"
    auth = (os.environ['SERVICENOW_USER'], os.environ['SERVICENOW_PASS'])
    headers = {"Accept": "application/json"}

    response = requests.get(url, auth=auth, headers=headers)
    response.raise_for_status()

    result = response.json()
    if isinstance(result, dict) and "result" in result:
        return result["result"]
    else:
        raise ValueError("Unexpected response format from ServiceNow")

def transform_incident_data(raw_incident):
    # Transform the raw data to a consistent MongoDB schema
    return {
        "incident_id": raw_incident.get("number"),
        "short_description": raw_incident.get("short_description"),
        "description": raw_incident.get("description", ""),
        "state": raw_incident.get("state"),
        "assigned_to": raw_incident.get("assigned_to", {}).get("display_value") if isinstance(raw_incident.get("assigned_to"), dict) else raw_incident.get("assigned_to"),
        "opened_by": raw_incident.get("opened_by", {}).get("display_value") if isinstance(raw_incident.get("opened_by"), dict) else raw_incident.get("opened_by"),
        "created_on": raw_incident.get("sys_created_on"),
        "url": f"{os.environ['SERVICENOW_URL']}/nav_to.do?uri=incident.do?sys_id={raw_incident.get('sys_id')}"
    }

def load_data_from_servicenow():
    try:
        print("Fetching incidents from ServiceNow...")
        incidents = get_servicenow_incidents()
        print(f"Found {len(incidents)} incidents.")

        inserted_count = 0
        for inc in incidents:
            transformed = transform_incident_data(inc)
            if not collection.find_one({"incident_id": transformed["incident_id"]}):
                collection.insert_one(transformed)
                inserted_count += 1

        print(f"Inserted {inserted_count} new incidents into MongoDB.")
    except Exception as e:
        print(f"Error loading ServiceNow data: {e}")
