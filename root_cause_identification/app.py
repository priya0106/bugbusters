from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from llm import FAISS, DataBase, LLM
from contextlib import asynccontextmanager
from pathlib import Path
import os
from dotenv import load_dotenv
import uvicorn
from pydantic import BaseModel
from jira_data_loader import load_data_from_jira
from fastapi.middleware.cors import CORSMiddleware
import signal
import markdown2
import bleach  # Add this import
import requests
from requests.auth import HTTPBasicAuth
from service_now_loader import load_data_from_servicenow

defects_llm = {}
cleanup_done = False
valid_defect_ids = set()  # Will be populated during startup

def cleanup_resources():
    global cleanup_done
    if not cleanup_done:
        if defects_llm:
            defects_llm.clear()
        cleanup_done = True

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        BASE_DIR = Path(__file__).absolute().parent
        ENV_PATH = os.path.join(BASE_DIR, ".env")
        load_dotenv(ENV_PATH)
        load_data_from_jira()
        load_data_from_servicenow()
        
        vs = FAISS.initialize()
        db = DataBase()
        faiss_data = vs.add_documents(db)
        defects_llm.update(faiss_data)
        
        # Get valid defect IDs from database
        global valid_defect_ids
        valid_defect_ids = {str(d['bug_id']) for d in db.defect_data}
        print(f"Loaded valid defect IDs: {valid_defect_ids}")
        
        yield
    except Exception as e:
        print(f"Error during startup: {e}")
        cleanup_resources()
        raise
    finally:
        cleanup_resources()

def handle_exit(signum, frame):
    cleanup_resources()
    raise KeyboardInterrupt()

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

app  = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    prompt: str
    conversation_id: str = None

@app.post("/defects/response")
async def defects_response(chat_request: ChatRequest):
    llm = LLM()
    query = chat_request.prompt.lower()
    db = DataBase()
    
    # Check if query mentions invalid defect IDs using dynamic set
    # mentioned_ids = set([word.upper() for word in query.split() if word.upper().startswith('SCRUM-')])
    mentioned_ids = set([
    word.upper() for word in query.split()
    if word.upper().startswith('SCRUM-') or word.upper().startswith('INC')
    ])
    invalid_ids = mentioned_ids - valid_defect_ids
    if invalid_ids:
        return JSONResponse(content={
            "response": {
                "message": f"""**Invalid Defect IDs**

The following defect IDs are not in the current database:
- {', '.join(invalid_ids)}

**Currently Active Defects:**
- {', '.join(sorted(valid_defect_ids))}

---
**Summary:**
Please check the list of active defects above and try your query with a valid defect ID.""",
                "content_type": "markdown"
            }
        })

    vs = FAISS.initialize()
    vs.defect_embeddings = defects_llm["index"]
    
    # Initialize relevant_defects with all defects as default
    relevant_defects = db.defect_data

    # Special handling for root cause and solution queries
    if any(keyword in query for keyword in ['root', 'cause', 'why', 'solution', 'fix', 'resolve']):
        mentioned_ids = [word.upper() for word in query.split() if word.upper().startswith('SCRUM-')]
        if mentioned_ids and mentioned_ids[0] in valid_defect_ids:
            # Get the specific defect directly from database
            relevant_defects = [d for d in db.defect_data if d['bug_id'] == mentioned_ids[0]]
            # Add debug logging
            print(f"Found defect details: {relevant_defects[0] if relevant_defects else 'Not found'}")
    elif not any(keyword in query for keyword in ['owner', 'who', 'list', 'all defect']):
        # For specific queries that aren't about listing all defects
        relevant_indices_scores = vs.semantic_search(query, top_k=10)
        relevant_defects = db.get_defects_by_indices_with_scores(relevant_indices_scores)
        relevant_defects.sort(key=lambda x: x['relevance_score'], reverse=True)
    
    response = llm.get_response(query, relevant_defects)
    
    # Only sanitize if content type is HTML
    if response.get("content_type") == "html":
        allowed_tags = ['a', 'p', 'br', 'li', 'ul', 'ol', 'table', 'tr', 'td', 'th', 'thead', 'tbody']
        allowed_attrs = {'a': ['href', 'target']}
        response["message"] = bleach.clean(
            response["message"],
            tags=allowed_tags,
            attributes=allowed_attrs,
            protocols=['http', 'https']
        )
    
    return JSONResponse(
        content={"response": response},
        headers={"Content-Type": "application/json"}
    )

class UVRuleRequest(BaseModel):
    user_request: str

@app.post("/proxy/uvrules")
async def proxy_uvrules(request: UVRuleRequest):
    """Proxy endpoint for UV Rules service"""
    if not request.user_request.strip():
        return JSONResponse(content={
            "message": "Please provide a policy number and rule code (e.g., E101)."
        })

    try:
        # For now, return a mock response until UV Rules service is available
        return JSONResponse(content={
            "message": "I understand you're asking about UV rules. " + 
                      "To help you better, please provide:\n" +
                      "1. Policy Number\n" +
                      "2. Rule Code (e.g., E101)\n\n" +
                      "For example: 'Why is rule E101 triggered for policy 12345?'"
        })
    except Exception as e:
        return JSONResponse(
            content={
                "message": f"Error processing UV rule request: {str(e)}"
            },
            status_code=500
        )

if __name__ == "__main__":
    try:
        uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
    except KeyboardInterrupt:
        cleanup_resources()