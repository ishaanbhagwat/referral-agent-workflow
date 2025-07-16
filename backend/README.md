# Agentic Referral Workflow Backend

A FastAPI-based backend service for processing medical referral documents using OCR, LLM extraction, and automated workflow management.

## Prerequisites

- Python 3.8+
- Docker
- Redis (via Docker)
- OpenAI API key

## Setup Instructions

### 1. Redis Setup with Docker

First, ensure Docker is running on your system.

#### Download and Run Redis Container

```bash
# Pull the official Redis image
docker pull redis:latest

# Run Redis container on port 6379
docker run -d --name redis-server -p 6379:6379 redis:latest

# Verify Redis is running
docker ps
```

##### (Optional) Test Redis Connection

```bash
# Connect to Redis CLI
docker exec -it redis-server redis-cli

# Test connection
ping
# Should return: PONG

# Exit Redis CLI
exit
```

### 2. Python Environment Setup

```bash
# Clone the repository (if not already done)
git clone <your-repo-url>
cd agentic-referral-workflow/backend

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On macOS/Linux:
source .venv/bin/activate
# On Windows:
# .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration

Create a `.env` file in the backend directory:

```bash
# OpenAI API Configuration
OPENAI_API_KEY=your_openai_api_key_here

# Redis Configuration (default values)
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
```

### 4. Running the Application

#### Start the FastAPI Server with Uvicorn

```bash
# Development mode with auto-reload
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The application will be available at:
- **API**: http://localhost:8000

#### What Happens on Startup

1. **FastAPI Server**: Starts on port 8000
2. **Agent Manager**: Automatically launches 1 agent worker process
3. **Redis Connection**: Establishes connection to Redis on localhost:6379
4. **CORS**: Configured for frontend on http://localhost:3000

## API Endpoints

### Document Management
- `POST /upload-document` - Upload and process a medical document
- `GET /document/{document_id}/status` - Get processing status of a specific document
- `GET /documents/status/all` - Get status of all documents in the system

## Workflow Overview

1. **Document Upload**: User uploads medical document via API
2. **OCR Processing**: Document text is extracted using Tesseract
3. **Queue Addition**: Document is added to Redis processing queue
4. **Agent Processing**: Background agent picks up document and:
   - Extracts structured data using LLM
   - Validates required fields
   - Either syncs to EMR (if complete) or sends email for missing info
5. **Status Updates**: Processing status is stored in Redis

## Development

### Running Agent Separately (Optional)

You can also run the agent independently for development:

```bash
python agent.py
```