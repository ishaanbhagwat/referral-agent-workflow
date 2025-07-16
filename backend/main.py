from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import redis
import json
import uuid
from datetime import datetime
import logging
from typing import cast, List, Dict, Any
from DocumentProcessor import doc_processor
import asyncio
import threading
import subprocess
import time
import sys
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Referral Queuing Service")

# CORS middleware for NextJS frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # NextJS default port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Redis connection
redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

# Test Redis connection
try:
    redis_client.ping()
    logger.info("Connected to Redis successfully")
except redis.ConnectionError:
    logger.error("Failed to connect to Redis")
    raise

# --- Agent Manager for running agent.py as a worker thread ---
class AgentManager:
    def __init__(self, num_workers=1):
        self.num_workers = num_workers
        self.workers = []
        self.running = False

    def start_agents(self):
        self.running = True
        for i in range(self.num_workers):
            t = threading.Thread(target=self._run_agent, args=(i,), daemon=True)
            t.start()
            self.workers.append(t)

    def _run_agent(self, worker_id):
        while self.running:
            try:
                print(f"Starting agent worker {worker_id}")
                proc = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "agent.py")])
                proc.wait()
                print(f"Agent worker {worker_id} exited with code {proc.returncode}, restarting in 2s...")
                time.sleep(2)
            except Exception as e:
                print(f"Agent worker {worker_id} crashed: {e}, restarting in 2s...")
                time.sleep(2)

    def stop_agents(self):
        self.running = False
        # Optionally: send a signal to subprocesses to terminate

# Instantiate and start the agent manager on FastAPI startup
agent_manager = AgentManager(num_workers=1)

def start_agent_manager():
    agent_manager.start_agents()

@app.on_event("startup")
def on_startup():
    start_agent_manager()

@app.post("/upload-document")
async def upload_document(file: UploadFile = File(...)):
    """
    Upload and process a medical document
    """
    try:
        # Generate unique document ID
        doc_id = str(uuid.uuid4())
        
        # Read file content
        file_content = await file.read()
        
        # Process document with OCR
        logger.info(f"Processing document: {file.filename}")
        filename = file.filename if file.filename is not None else ""
        processing_result = doc_processor.process_document(file_content, filename)
        
        # Create job data for Redis queue
        job_data = {
            "document_id": doc_id,
            "filename": processing_result["filename"],
            "file_size": processing_result["file_size"],
            "extracted_text": processing_result["extracted_text"],
            "text_length": processing_result["text_length"],
            "upload_timestamp": datetime.now().isoformat(),
            "status": "ocr_complete",
            "next_step": "agent_processing"
        }
        
        # Store in Redis queue for agent processing
        queue_key = "document_processing_queue"
        redis_client.lpush(queue_key, json.dumps(job_data))
        
        # Also store document metadata for status tracking
        doc_key = f"document:{doc_id}"
        redis_client.setex(doc_key, 3600, json.dumps(job_data))  # Expire in 1 hour
        
        logger.info(f"Document {doc_id} queued for agent processing")
        
        return {
            "document_id": doc_id,
            "status": "uploaded_and_queued",
            "message": "Document processed with OCR and queued for agent",
            "extracted_text_preview": processing_result["extracted_text"][:200] + "..." if len(processing_result["extracted_text"]) > 200 else processing_result["extracted_text"],
            "text_length": processing_result["text_length"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing document: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

@app.get("/document/{document_id}/status")
async def get_document_status(document_id: str):
    """
    Get the processing status of a document, including structured data if available.
    """
    try:
        doc_key = f"document:{document_id}"
        doc_data = redis_client.get(doc_key)
        # If doc_data is awaitable, resolve it
        if hasattr(doc_data, "__await__"):
            doc_data = asyncio.get_event_loop().run_until_complete(doc_data)
        if not isinstance(doc_data, str):
            logger.error(f"doc_data for {doc_key} is not a string: {type(doc_data)}")
            raise HTTPException(status_code=500, detail="Corrupt document data in Redis")
        data = json.loads(doc_data)
        response = {
            "status": data.get("status"),
            "timestamp": data.get("timestamp"),
            "additional_info": data.get("additional_info"),
            "structured_data": data.get("structured_data")
        }
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving document status: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve document status")

@app.get("/queue/status")
async def get_queue_status():
    """
    Get the current status of the processing queue
    """
    try:
        queue_key = "document_processing_queue"
        queue_length = cast(int, redis_client.llen(queue_key))
        
        return {
            "queue_length": queue_length,
            "status": "active" if queue_length > 0 else "empty"
        }
        
    except Exception as e:
        logger.error(f"Error retrieving queue status: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve queue status")

@app.get("/documents/all")
def get_all_documents_in_queue():
    """
    Get all documents currently in the Redis queue and their status.
    """
    try:
        queue_key = "document_processing_queue"
        queue_items = redis_client.lrange(queue_key, 0, -1)
        # If queue_items is awaitable, resolve it
        import asyncio
        if not isinstance(queue_items, list) and hasattr(queue_items, "__await__"):
            queue_items = asyncio.get_event_loop().run_until_complete(queue_items)
        if not isinstance(queue_items, list):
            logger.error(f"queue_items is not a list after resolving: {type(queue_items)}")
            return {"documents": [], "count": 0}
        documents: List[Dict[str, Any]] = []
        for item in queue_items:
            try:
                if not isinstance(item, str):
                    logger.error(f"Queue item is not a string: {type(item)}")
                    continue
                job_data = json.loads(item)
                doc_id = job_data.get("document_id")
                # Try to get status from the status key if available
                doc_key = f"document:{doc_id}"
                status_data = redis_client.get(doc_key)
                if hasattr(status_data, "__await__"):
                    status_data = asyncio.get_event_loop().run_until_complete(status_data)
                if not isinstance(status_data, str):
                    logger.error(f"Status data for {doc_key} is not a string: {type(status_data)}")
                    status = None
                else:
                    try:
                        status = json.loads(status_data).get("status")
                    except Exception:
                        status = None
                job_data["current_status"] = status
                documents.append(job_data)
            except Exception as e:
                logger.error(f"Error parsing job data from queue: {str(e)}")
                continue
        return {"documents": documents, "count": len(documents)}
    except Exception as e:
        logger.error(f"Error retrieving all documents in queue: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve all documents in queue")

@app.get("/documents/status/all")
def get_all_document_statuses():
    """
    Get the status and metadata for all documents in the system.
    """
    try:
        doc_keys = redis_client.keys("document:*")
        if hasattr(doc_keys, "__await__"):
            doc_keys = asyncio.get_event_loop().run_until_complete(doc_keys)
        if not isinstance(doc_keys, list):
            logger.error(f"doc_keys is not a list after resolving: {type(doc_keys)}")
            return {"documents": [], "count": 0}
        documents = []
        for key in doc_keys:
            value = redis_client.get(key)
            if hasattr(value, "__await__"):
                value = asyncio.get_event_loop().run_until_complete(value)
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            if not isinstance(value, str):
                logger.error(f"Value for {key} is not a string: {type(value)}")
                continue
            try:
                doc_data = json.loads(value)
                doc_data["document_id"] = key.split("document:")[1]
                documents.append(doc_data)
            except Exception as e:
                logger.error(f"Error parsing document data for {key}: {str(e)}")
                continue
        return {"documents": documents, "count": len(documents)}
    except Exception as e:
        logger.error(f"Error retrieving all document statuses: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve all document statuses")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)