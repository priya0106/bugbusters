import os
from typing import List, Dict, Any
from urllib.parse import urljoin
from sentence_transformers import SentenceTransformer
from together import Together
from pymongo import MongoClient
import pandas as pd
import numpy as np
from datetime import datetime
import re
import markdown2

class DataBase:
    def __init__(self):
        self.client = self._initialize_db()
        self.defect_data = self._load_defect_data()

    def _initialize_db(self) -> MongoClient:
        try:
            conn =  MongoClient(f"mongodb+srv://{os.environ['USER_NAME']}:{os.environ['PASSWORD']}@bugbusters.exmbwtk.mongodb.net/?retryWrites=true&w=majority&appName=bugbusters")
            return conn[os.environ['DB_NAME']]
        except Exception as e:
            raise ConnectionError(f"Failed to connect to MongoDB: {e}")

    def _load_defect_data(self) -> List[Dict]:
        defects = list(self.client['defect_cause'].find())
        incidents = list(self.client['servicenow_incidents'].find())
        for inc in incidents:
            inc['bug_id'] = inc.get('incident_id', 'UNKNOWN')
            inc['Defect Summary'] = inc.get('short_description', 'No summary')
            inc['owner'] = inc.get('assigned_to', 'Unassigned')
            inc['solution'] = inc.get('description', 'No solution provided')
            inc['rootCause'] = {
                'description': inc.get('description', ''),
                'analysis': {'logs': ""}
            }
            inc['url'] = f"{os.environ['SERVICENOW_URL']}/nav_to.do?uri=incident.do?sys_id={inc.get('sys_id', '')}"
        return defects + incidents

    def get_defects_by_indices(self, indices: List[int]) -> List[Dict]:
        return [self.defect_data[i] for i in indices]

    def get_defects_by_indices_with_scores(self, indices_scores: List[tuple]) -> List[Dict]:
        defects = []
        for idx, score in indices_scores:
            defect = self.defect_data[idx].copy()  # Make a copy of the defect data
            defect['relevance_score'] = round(score * 100, 2)  # Convert to percentage
            defects.append(defect)
        return defects

    def cleanup(self):
        if hasattr(self, 'client') and self.client:
            self.client.close()

class FAISS:
    def __init__(self):
        self.encoder = SentenceTransformer("all-MiniLM-L6-v2")
        self.defect_embeddings = None
        self.defect_data = None

    @classmethod
    def initialize(cls):
        return cls()

    def add_documents(self, db_instance: 'DataBase' = None):
        if db_instance is None:
            db_instance = DataBase()
        
        self.defect_data = db_instance.defect_data
        if self.defect_data:
            summaries = [d['Defect Summary'] for d in self.defect_data]
            self.create_embeddings(summaries)
        
        return {
            "embed_model": self.encoder,
            "index": self.defect_embeddings,
            "data": self.defect_data
        }

    def create_embeddings(self, texts: List[str]):
        self.defect_embeddings = self.encoder.encode(texts)
        
    def semantic_search(self, query: str, top_k: int = 10, threshold: float = 0.3) -> List[tuple]:
        query_embedding = self.encoder.encode([query])[0]
        similarities = np.dot(self.defect_embeddings, query_embedding)
        
        # Get indices and scores for all results above threshold
        indices_scores = [(idx, float(score)) for idx, score in enumerate(similarities) if score > threshold]
        
        # Sort by score in descending order and take top_k
        indices_scores.sort(key=lambda x: x[1], reverse=True)
        return indices_scores[:top_k]

    def cleanup(self):
        self.defect_embeddings = None
        self.defect_data = None
        if hasattr(self, 'encoder'):
            del self.encoder

class LLM:
    def __init__(self):
        self.llm = Together(api_key=os.environ["TOGETHER_API_KEY"])
        self.context_window = []
        self.jira_base_url = os.environ.get('JIRA_BASE_URL', 'https://nish09.atlassian.net/browse/')
        self.system_prompt = """You are Bugbuster, a friendly AI assistant that helps with defect analysis.

Keep your responses simple and clear, like this:
1. Start with a direct answer to the question
2. Add relevant details or examples if needed
3. End with "Summary: " followed by 1-2 sentences highlighting key points

Format:
- Use bullet points for lists
- Keep sentences short
- Highlight important terms in **bold**
- Link Jira tickets like [SCRUM-7](https://nish09.atlassian.net/browse/SCRUM-7)

Example response:
The login issue in [SCRUM-15] is caused by **event handling problems** in the mobile UI.

- Issue affects mobile users only
- Root cause: Touch events not being captured
- Solution: Update event listeners

Summary: Mobile login needs UI event handling fixes. Team should focus on touch event listeners."""
        self.query_types = {
            'description': ['what is', 'describe', 'explain', 'tell me about'],
            'error': ['error', 'log', 'exception', 'payload'],
            'analysis': ['analyze', 'check', 'investigate', 'debug'],
            'impact': ['impact', 'affect', 'consequence', 'result'],
            'status': ['status', 'state', 'progress', 'current'],
            'validation': ['test', 'verify', 'validate', 'qa'],
            'service': ['service', 'kafka', 'mongodb', 'api', 'downstream']
        }

    def _format_conversation_history(self) -> str:
        if not self.context_window:
            return ""
        history = "\n".join([f"User: {turn['user']}\nAssistant: {turn['assistant']}" 
                           for turn in self.context_window[-3:]])
        return f"\nRecent conversation:\n{history}"

    def _get_query_type(self, query: str) -> str:
        query_lower = query.lower()
        for qtype, keywords in self.query_types.items():
            if any(keyword in query_lower for keyword in keywords):
                return qtype
        return 'general'

    def _create_prompt(self, query: str, relevant_defects: List[Dict]) -> str:
        query_type = self._get_query_type(query)
        
        # Handle service-specific queries
        if query_type == 'service':
            service_summary = self._format_service_analysis(relevant_defects)
            if service_summary:
                return service_summary

        # Handle error log queries
        if query_type == 'error':
            error_summary = self._format_error_logs(relevant_defects)
            if error_summary:
                return error_summary

        # Handle summary/details queries
        if any(word.upper().startswith(('SCRUM-', 'INC')) for word in query.split()):
            defect_id = next((word.upper() for word in query.split() if word.upper().startswith('SCRUM-')), None)
            defect = next((d for d in relevant_defects if d['bug_id'] == defect_id), None)
            
            if defect:
                root_cause = defect.get('rootCause', {})
                root_cause_desc = root_cause.get('description') if isinstance(root_cause, dict) else root_cause
                
                return f"""Defect Details for [{defect_id}]({urljoin(self.jira_base_url, defect_id)}):

Summary: {defect['Defect Summary']}

Root Cause: {root_cause_desc if root_cause_desc else 'No root cause specified'}

Solution: {defect.get('solution', 'No solution specified')}

Owner: {defect.get('owner', 'Unassigned')}

Status: {defect.get('status', 'Unknown')}"""

        # Handle solution queries
        if any(keyword in query.lower() for keyword in ['solution', 'fix', 'resolve']):
            mentioned_ids = [word.upper() for word in query.split() if word.upper().startswith('SCRUM-')]
            if mentioned_ids:
                defect_id = mentioned_ids[0]
                defect = next((d for d in relevant_defects if d['bug_id'] == defect_id), None)
                
                if defect:
                    return f"""Solution Details for [{defect_id}]({urljoin(self.jira_base_url, defect_id)}):

Defect Summary: {defect['Defect Summary']}

Solution: {defect.get('solution', 'No solution specified')}

Root Cause: {defect.get('rootCause', {}).get('description', 'N/A')}
Owner: {defect.get('owner', 'Unassigned')}"""

        # Handle root cause queries
        elif any(keyword in query.lower() for keyword in ['root', 'cause', 'why']):
            # Extract defect ID from query
            mentioned_ids = [word.upper() for word in query.split() if word.upper().startswith('SCRUM-')]
            if mentioned_ids:
                defect_id = mentioned_ids[0]
                defect = next((d for d in relevant_defects if d['bug_id'] == defect_id), None)
                
                if defect:
                    # Extract root cause data properly
                    root_cause = defect.get('rootCause', {})
                    root_cause_desc = root_cause.get('description') if isinstance(root_cause, dict) else root_cause
                    solution = defect.get('solution', 'No solution provided')
                    
                    return f"""Root Cause Analysis for [{defect_id}]({urljoin(self.jira_base_url, defect_id)}):

Defect Summary: {defect['Defect Summary']}

Root Cause: {root_cause_desc if root_cause_desc else 'No root cause specified'}

Solution: {solution}

Owner: {defect.get('owner', 'Unassigned')}"""

        table_html = """
        <div class="defect-table">
            <table border="1">
                <thead>
                    <tr>
                        <th>Defect ID</th>
                        <th>Summary</th>
                        <th>Owner</th>
                    </tr>
                </thead>
                <tbody>
        """
        
        for defect in relevant_defects:
            bug_id = defect['bug_id']
            jira_url = defect.get('url', urljoin(self.jira_base_url, bug_id))
            table_html += f"""
                <tr>
                    <td><a href="{jira_url}" target="_blank">{bug_id}</a></td>
                    <td>{defect['Defect Summary']}</td>
                    <td>{defect.get('owner', 'Unassigned')}</td>
                </tr>
            """
            
        table_html += """
                </tbody>
            </table>
        </div>
        """

        if "list" in query.lower() or "all" in query.lower():
            return f"""Here are all the currently active defects in the system:
            {table_html}
            
            Note: Only these defects are currently in our database. If you're looking for other defect IDs, they may have been resolved or not yet added."""
        else:
            defect_context = "\n\n".join([
                f"Defect: {d['Defect Summary']}\n"
                f"ID: [{d['bug_id']}]({urljoin(self.jira_base_url, d['bug_id'])})\n"
                f"Root Cause: {d.get('rootCause', {}).get('description', 'N/A')}\n"
                f"Solution: {d.get('solution', 'N/A')}\n"
                f"Owner: {d.get('owner', 'N/A')}"
                for d in relevant_defects
            ])

            conversation_history = self._format_conversation_history()

            prompt = f"""{self.system_prompt}

    Available defect information:
    {defect_context}

    {conversation_history}

    User Query: {query}

    Provide a clear, focused answer based on the available information. If the query is about specific aspects (owner, root cause, solution), only include that information."""

            return prompt

    def _format_response(self, response: str) -> Dict[str, Any]:
        # Generate summary if not found
        if "Summary:" not in response:
            # Split response into lines and extract key information
            lines = response.split('\n')
            key_points = []
            
            # Get first line as main point
            if lines:
                first_line = lines[0].strip()
                if first_line:
                    key_points.append(first_line)
            
            # Look for important keywords
            for line in lines:
                line = line.lower().strip()
                if any(key in line for key in ['root cause:', 'solution:', 'status:', 'owner:', 'impact:']):
                    key_points.append(line)
            
            # Create summary from key points or use default
            if key_points:
                summary = " ".join(key_points[:3])  # Use first 3 key points
            else:
                summary = "Key points from the analysis"
                
            response += f"\n\nSummary: {summary}"

        # Add visual separation for the summary
        response = response.replace("Summary:", "\n---\n**Summary:**")

        # Convert markdown and add styling
        html_response = f"""
        <div class="response-card">
            <div class="response-content">
                {markdown2.markdown(response, extras=['fenced-code-blocks'])}
            </div>
        </div>
        """
        
        return {
            "message": html_response,
            "content_type": "html"
        }

    def get_response(self, query: str, relevant_defects: List[Dict]) -> Dict[str, Any]:
        # Add debug logging for all queries
        if any(word.upper().startswith(('SCRUM-', 'INC')) for word in query.split()):
            defect_id = next((word.upper() for word in query.split() if word.upper().startswith('SCRUM-')), None)
            print(f"Processing query for defect {defect_id}")
            defect = next((d for d in relevant_defects if d['bug_id'] == defect_id), None)
            if defect:
                print(f"Found defect data: {defect}")
            else:
                print(f"No defect found with ID {defect_id}")
        
        prompt = self._create_prompt(query, relevant_defects)
        response = self.llm.chat.completions.create(
            model=os.environ["MODEL"],
            messages=[{"role": "user", "content": prompt}]
        )

        answer = response.choices[0].message.content
        self.context_window.append({
            'user': query,
            'assistant': answer,
            'timestamp': datetime.now()
        })

        if len(self.context_window) > 5:
            self.context_window.pop(0)

        return self._format_response(answer)

    def _format_service_analysis(self, defects: List[Dict]) -> str:
        services = {
            'kafka': [],
            'mongodb': [],
            'notification': [],
            'login': [],
            'policy': []
        }
        
        for defect in defects:
            summary = defect['Defect Summary'].lower()
            for service in services.keys():
                if service in summary:
                    services[service].append(defect)
        
        active_services = {k: v for k, v in services.items() if v}
        if not active_services:
            return None

        response = "Service-related Issues Analysis:\n\n"
        for service, issues in active_services.items():
            response += f"\n{service.upper()} Service Issues:\n"
            for issue in issues:
                response += f"- [{issue['bug_id']}]({urljoin(self.jira_base_url, issue['bug_id'])}): {issue['Defect Summary']}\n"
                if issue.get('rootCause', {}).get('description'):
                    response += f"  Root Cause: {issue['rootCause']['description']}\n"
                
        return response

    def _format_error_logs(self, defects: List[Dict]) -> str:
        errors = []
        for defect in defects:
            if 'Error log' in defect or 'rootCause' in defect and 'analysis' in defect['rootCause']:
                error_log = defect.get('Error log', defect['rootCause'].get('analysis', {}).get('logs', ''))
                if error_log:
                    errors.append({
                        'id': defect['bug_id'],
                        'summary': defect['Defect Summary'],
                        'log': error_log
                    })
        
        if not errors:
            return None

        response = "Error Log Analysis:\n\n"
        for error in errors:
            response += f"[{error['id']}]({urljoin(self.jira_base_url, error['id'])}):\n"
            response += f"Summary: {error['summary']}\n"
            response += f"Log: ```\n{error['log']}\n```\n\n"
        
        return response

    def cleanup(self):
        self.context_window.clear()
        if hasattr(self, 'llm'):
            del self.llm