import os
import requests
import json
from dotenv import load_dotenv
from pymongo import MongoClient
from io import BytesIO
from docx import Document

load_dotenv()

JQL_QUERY = 'issuetype = Bug AND status = Done'

mongo_client =  MongoClient(f"mongodb+srv://{os.environ['USER_NAME']}:{os.environ['PASSWORD']}@bugbusters.exmbwtk.mongodb.net/?retryWrites=true&w=majority&appName=bugbusters")
db = mongo_client[os.environ['DB_NAME']]
collection = db['defect_cause']

# def get_done_bugs():
#     url = f"{os.environ['JIRA_URL']}/rest/api/3/search"
#     headers = {"Content-Type": "application/json"}
#     auth = (os.environ['JIRA_EMAIL'], os.environ['JIRA_API_TOKEN'])

#     payload = {
#         "jql": JQL_QUERY,
#         "fields": ["summary", "attachment", "assignee", "description", "comment"],
#         "maxResults": 1000
#     }

#     response = requests.post(url, json=payload, headers=headers, auth=auth)


#     response.raise_for_status()
#     return response.json()["issues"]

def parse_rca_to_json(rca_response):
    text = rca_response.get("text", "")
    owner = rca_response.get("assignee", "Unassigned")
    bug_id = rca_response.get("bug_id", "")
    bug_url = rca_response.get("bug_url", "")

    sections = {}
    current_section = None

    for line in text.splitlines():
        if line.strip() == "":
            continue
        if line.endswith(":") or line.lower() in ["defect summary", "description", "detailed root cause", "error logs", "analysis artifacts", "detailed solution"]:
            current_section = line.strip(":").strip()
            sections[current_section] = []
        elif current_section:
            sections[current_section].append(line.strip())

    for key in sections:
        sections[key] = " ".join(sections[key])

    parsed_json = {}
    for section, content in sections.items():
        if "logs" in section.lower():
            parsed_json.setdefault("rootCause", {}).setdefault("analysis", {})["logs"] = content
        elif "xml" in section.lower() or "artifacts" in section.lower():
            parsed_json.setdefault("rootCause", {}).setdefault("analysis", {}).setdefault("xml_files", []).append({
                "name": "Example.xml",
                "content": content
            })
        elif "root cause" in section.lower():
            parsed_json.setdefault("rootCause", {})["description"] = content
        elif "solution" in section.lower():
            parsed_json["solution"] = content
        else:
            parsed_json[section] = content

    parsed_json["owner"] = owner
    parsed_json["bug_id"] = bug_id
    parsed_json["bug_url"] = bug_url

    return parsed_json

def process_and_store_rca(issue_key, bug_url, assignee, attachment):
    print(f"Processing {attachment['filename']} from {issue_key}...")
    response = requests.get(attachment["content"], auth=(os.environ['JIRA_EMAIL'], os.environ['JIRA_API_TOKEN']))
    response.raise_for_status()

    if attachment["filename"].endswith(".docx"):
        try:
            file_stream = BytesIO(response.content)
            doc = Document(file_stream)
            rca_content = {
                "text": "\n".join([paragraph.text for paragraph in doc.paragraphs]),
                "bug_id": issue_key,
                "bug_url": bug_url,
                "assignee": assignee
            }
        except Exception as e:
            print(f"Failed to process {attachment['filename']} as .docx: {e}")
            return None
    else:
        try:
            rca_content = {
                "text": response.text,
                "bug_id": issue_key,
                "bug_url": bug_url,
                "assignee": assignee
            }
        except Exception as e:
            print(f"Failed to parse {attachment['filename']} as JSON: {e}")
            return None

    parsed_json = parse_rca_to_json(rca_content)

    existing_document = collection.find_one({"bug_id": issue_key})
    if existing_document:
        print(f"Document for bug_id {issue_key} already exists in MongoDB. Skipping.")
        return None

    collection.insert_one(parsed_json)
    print(f"Stored RCA document for {issue_key} in MongoDB.")
    return parsed_json

def extract_text_from_jira_content(content) -> str:
    """Extract plain text from Jira's structured content"""
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if 'content' in content:
            text = []
            for item in content.get('content', []):
                if item.get('type') == 'text':
                    text.append(item.get('text', ''))
                elif item.get('type') == 'paragraph':
                    for child in item.get('content', []):
                        if child.get('type') == 'text':
                            text.append(child.get('text', ''))
            return ' '.join(text)
    return ""

def extract_rca_from_text(text) -> dict:
    """Extract RCA information from text content."""
    # Convert input to plain text
    text = extract_text_from_jira_content(text)
    
    rca_info = {
        "description": "",
        "solution": "",
        "analysis": {"logs": ""}
    }
    
    if not text:
        return rca_info
        
    text_lower = text.lower()
    
    # Look for common RCA markers
    rca_markers = [
        "root cause:", "cause:", "reason:",
        "solution:", "fix:", "resolution:",
        "error log:", "stack trace:", "exception:"
    ]
    
    lines = text.split('\n')
    current_section = None
    
    for line in lines:
        line_lower = line.lower().strip()
        
        # Detect section based on markers
        for marker in rca_markers:
            if line_lower.startswith(marker):
                if "cause" in marker or "reason" in marker:
                    current_section = "description"
                elif "solution" in marker or "fix" in marker or "resolution" in marker:
                    current_section = "solution"
                elif "log" in marker or "trace" in marker or "exception" in marker:
                    current_section = "logs"
                continue
        
        # Add content to appropriate section
        if current_section:
            if current_section == "logs":
                rca_info["analysis"]["logs"] += line + "\n"
            else:
                rca_info[current_section] += line + "\n"
    
    # Strip whitespace from text values while preserving structure
    stripped_info = {}
    for k, v in rca_info.items():
        if isinstance(v, str):
            stripped_info[k] = v.strip()
        elif isinstance(v, dict):
            stripped_info[k] = {k2: v2.strip() if isinstance(v2, str) else v2 
                              for k2, v2 in v.items()}
        else:
            stripped_info[k] = v
            
    return stripped_info

def analyze_summary(summary: str) -> dict:
    """Analyze bug summary to generate initial RCA information."""
    # Common patterns for different types of issues
    patterns = {
        'ui': ['button', 'click', 'tap', 'interface', 'unresponsive', 'display', 'screen', 'mobile'],
        'api': ['endpoint', 'request', 'response', 'api', 'service'],
        'data': ['database', 'data', 'record', 'null', 'missing'],
        'auth': ['login', 'authentication', 'password', 'credential', 'session'],
        'performance': ['slow', 'timeout', 'performance', 'latency', 'loading']
    }
    
    summary_lower = summary.lower()
    issue_type = None
    
    # Determine issue type
    for type_key, keywords in patterns.items():
        if any(keyword in summary_lower for keyword in keywords):
            issue_type = type_key
            break
    
    # Generate analysis based on issue type
    analysis = {
        'ui': {
            'description': "Initial analysis indicates a UI/UX issue affecting user interaction with the interface.",
            'analysis': {'logs': "User interaction events not being captured or processed correctly."},
            'solution': "Investigate event handling and touch listeners on the affected UI elements."
        },
        'api': {
            'description': "Potential API integration or service communication issue.",
            'analysis': {'logs': "API endpoint communication needs to be verified."},
            'solution': "Check API endpoints and request/response handling."
        },
        'data': {
            'description': "Data handling or database interaction issue.",
            'analysis': {'logs': "Data flow and database operations need verification."},
            'solution': "Verify data persistence and retrieval operations."
        },
        'auth': {
            'description': "Authentication or session management issue.",
            'analysis': {'logs': "Authentication flow and session handling require investigation."},
            'solution': "Review authentication process and session management."
        },
        'performance': {
            'description': "Performance optimization required.",
            'analysis': {'logs': "Performance metrics indicate optimization needed."},
            'solution': "Conduct performance profiling and optimization."
        }
    }
    
    if issue_type and issue_type in analysis:
        return analysis[issue_type]
    
    return {
        'description': "Initial analysis pending. Bug reported for investigation.",
        'analysis': {'logs': "No specific error patterns identified yet."},
        'solution': "Investigation needed to determine root cause and solution."
    }

def create_basic_rca(issue_key: str, bug_url: str, assignee_name: str, bug_summary: str, description: str = "", comments: list = None) -> dict:
    """Create basic RCA document from issue fields"""
    
    # Extract RCA info from description
    desc_rca = extract_rca_from_text(description) if description else {}
    
    # Extract RCA info from comments
    comment_rca = {}
    if comments:
        for comment in comments:
            comment_text = extract_text_from_jira_content(comment.get('body', ''))
            if any(marker in comment_text.lower() for marker in ['root cause', 'rca', 'fixed', 'solution']):
                comment_rca = extract_rca_from_text(comment_text)
                if comment_rca.get('description') or comment_rca.get('solution'):
                    break
    
    # Get intelligent analysis from summary if no RCA found
    summary_analysis = analyze_summary(bug_summary)
    
    # Combine RCA information with priority to description/comments over automated analysis
    root_cause_desc = desc_rca.get('description') or comment_rca.get('description') or summary_analysis['description']
    solution = desc_rca.get('solution') or comment_rca.get('solution') or summary_analysis['solution']
    logs = (desc_rca.get('analysis', {}).get('logs') or 
            comment_rca.get('analysis', {}).get('logs') or 
            summary_analysis['analysis']['logs'])

    return {
        "bug_id": issue_key,
        "bug_url": bug_url,
        "owner": assignee_name,
        "Defect Summary": bug_summary,
        "rootCause": {
            "description": root_cause_desc,
            "analysis": {"logs": logs} if logs else {}
        },
        "solution": solution,
    }

def load_data_from_jira():
    # bugs = get_done_bugs()
    bugs = []
    print(f"Found {len(bugs)} bugs with status 'Done'.")

    for bug in bugs:
        issue_key = bug["key"]
        bug_url = f"{os.environ['JIRA_URL']}/browse/{issue_key}"
        assignee = bug["fields"].get("assignee", {})
        assignee_name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
        summary = bug["fields"].get("summary", "No summary available")
        description = bug["fields"].get("description", "")
        comments = bug["fields"].get("comment", {}).get("comments", [])
        attachments = bug["fields"].get("attachment", [])

        rca_processed = False
        for attachment in attachments:
            if "RCA" in attachment["filename"]:
                process_and_store_rca(issue_key, bug_url, assignee_name, attachment)
                rca_processed = True
                break
        
        if not rca_processed:
            # Create and store basic RCA if none exists
            basic_rca = create_basic_rca(issue_key, bug_url, assignee_name, summary, description, comments)
            existing_document = collection.find_one({"bug_id": issue_key})
            if not existing_document:
                collection.insert_one(basic_rca)
                print(f"Stored basic RCA document for {issue_key} in MongoDB.")

    print("Processing complete.")