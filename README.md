# Bugbusters - AI-Powered Defect Analysis System

A comprehensive system for analyzing and managing software defects using AI, with integrated UV Rules support.

## Prerequisites

- Python 3.8+
- Node.js 14+
- MongoDB Atlas account
- Together AI API key
- Jira API access

## Installation

### Backend Setup

1. Create and activate virtual environment:
    ```bash
    python -m venv venv
    source venv/bin/activate  # Linux/Mac
    venv\Scripts\activate     # Windows
    ```

2. Install Python dependencies:
    ```bash
    pip install -r requirements.txt
    ```

3. Create .env file in root_cause_identification folder:
    ```env
    USER_NAME=your_mongodb_username
    PASSWORD=your_mongodb_password
    DB_NAME=your_database_name
    TOGETHER_API_KEY=your_together_ai_key
    JIRA_URL=your_jira_url
    JIRA_EMAIL=your_jira_email
    JIRA_API_TOKEN=your_jira_api_token
    ```

4. Start the FastAPI backend:
    ```bash
    cd root_cause_identification
    python app.py
    ```
    Backend will run on http://localhost:8000

### Frontend Setup

1. Install Node.js dependencies:
    ```bash
    cd frontend_final
    npm install
    ```

2. Start the React frontend:
    ```bash
    npm start
    ```
    Frontend will run on http://localhost:3000

## API Endpoints

### Defect Analysis API

#### Get Defect Response
```http
POST /defects/response
Content-Type: application/json

{
    "prompt": "string",
    "conversation_id": "string (optional)"
}
```

Example queries:
- `list all defects`
- `what is the root cause of SCRUM-7`
- `show solution for SCRUM-13`
- `show all kafka service issues`

### Response Formats

#### Success Response
    ```json
    {
        "response": {
            "message": "string",
            "content_type": "html|text",
            "results": [
                {
                    "defectSummary": "string",
                    "relevance": "number",
                    "analysis": "string"
                }
            ]
        }
    }
    ```

#### Error Response
    ```json
    {
        "response": {
            "message": "error message",
            "content_type": "text"
        }
    }
    ```

## Features

- AI-powered defect analysis
- Root cause identification
- Solution recommendations
- Service impact analysis
- Semantic search capabilities
- UV Rules integration
- Real-time chat interface

## Technology Stack

- **Backend**: FastAPI, MongoDB, Together AI
- **Frontend**: React, Axios
- **AI/ML**: Sentence Transformers, FAISS
- **Documentation**: JIRA integration
