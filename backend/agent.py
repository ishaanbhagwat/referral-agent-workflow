import redis
import logging
import json
from openai import OpenAI, APIConnectionError, APIStatusError, RateLimitError, APITimeoutError
import os
from dotenv import load_dotenv
from prompts import OCR_Extraction_Prompt
import datetime

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define redis client
redis_client = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

# Define OpenAI client
try:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    logger.info("Key Extracted: %r", OPENAI_API_KEY)
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    logger.info("OpenAI Client Created Successfully")
except (APIConnectionError, APIStatusError, RateLimitError, APITimeoutError) as e:
    logger.error("OpenAI client initialization failed: %s", str(e))
    logger.exception(e)


# Attempt redis connection
try:
    redis_client.ping()
    logger.info("Connected to Redis successfully")
except redis.ConnectionError:
    logger.error("Failed to connect to Redis")
    raise


def check_redis():
    """Check Redis for a new document processing job."""
    logger.debug("Checking Redis for new document processing job.")
    queue_key = "document_processing_queue"
    job_json = redis_client.rpop(queue_key)
    if job_json is not None:
        # Redis may return bytes, so decode if needed
        if isinstance(job_json, bytes):
            job_json = job_json.decode("utf-8")
        logger.debug("Job found in Redis queue.")
        return job_json
    logger.debug("No job found in Redis queue.")
    return None


def chat_with_gpt4(messages, model="gpt-4.1-nano"):
    """Send messages to GPT-4 model and return the response content."""
    logger.info("Calling OpenAI GPT-4 model with messages.")
    response = openai_client.chat.completions.create(
        model=model,
        messages=messages
    )
    logger.info("Received response from GPT-4 model.")
    return response.choices[0].message.content


def llm_assistend_extraction(raw_ocr_text):
    """Extract structured data from raw OCR text using LLM."""
    logger.info("Starting LLM-assisted extraction.")
    messages = [
        {"role": "system", "content": OCR_Extraction_Prompt},
        {"role": "user", "content": raw_ocr_text}
    ]
    
    response = chat_with_gpt4(messages=messages)
    try:
        if response != None:
            data = json.loads(response)
        else:
            data = None
    except json.JSONDecodeError:
        logger.error("LLM response is not valid JSON:")
        logger.exception(json.JSONDecodeError)
        logger.info("Returned Text: %s", response)
        data = None
    logger.info("LLM-assisted extraction complete.")
    return data

def check_required_fields(data):
    """Check if all required fields are present and not empty."""
    logger.debug("Checking for required fields in extracted data.")
    if not data:
        return False, ["All fields - invalid JSON response"]
    
    required_fields = [
        "referring_provider.name", 
        "referring_provider.contact",
        "receiving_provider.name",
        "receiving_provider.contact",
        "patient.name",
        "patient.date_of_birth",
        "reason_for_referral",
        "requested_action"
    ]
    
    missing_fields = []
    
    for field in required_fields:
        if "." in field:
            # Handle nested fields
            parts = field.split(".")
            current = data
            field_missing = False
            
            for part in parts:
                if not isinstance(current, dict) or part not in current:
                    field_missing = True
                    break
                current = current[part]
            
            if field_missing or not current or current == "":
                missing_fields.append(field)
            elif field.endswith(".contact"):
                # Special check for contact fields - at least one contact method should be present
                contact = current
                if not any([contact.get("phone"), contact.get("email"), contact.get("address")]):
                    missing_fields.append(field + " (phone, email, or address)")
        else:
            # Handle top-level fields
            if field not in data or not data[field] or data[field] == "":
                missing_fields.append(field)
    
    logger.debug("Required fields check complete. Missing fields: %s", missing_fields)
    return len(missing_fields) == 0, missing_fields


def draft_request_email(missing_fields, referring_provider, receiving_provider, referral_id):
    logger.info("Drafting request email for missing fields: %s", missing_fields)
    email_prompt = f"""
    You are a medical administrative assistant. Draft a professional email requesting missing information for a medical referral.

    Context:
    - Referring Provider: {referring_provider.get('name', 'Unknown')}
    - Receiving Provider: {receiving_provider.get('name', 'Unknown')}
    - Missing Fields: {', '.join(missing_fields)}

    Please draft a concise, professional email that:
    1. Explains that we received a referral but some required information is missing
    2. Lists the specific missing fields in human readable form and it should absolutely not be json/computer form
    3. Requests the information be provided to complete the referral
    4. Maintains a professional and courteous tone

    Return the email in the following JSON format:
    {{
        "subject": "email subject line",
        "body": "email body content",
        "recipient": "email address or contact method"
    }}
    """
    
    messages = [
        {"role": "system", "content": "You are a professional medical administrative assistant."},
        {"role": "user", "content": email_prompt}
    ]
    
    response = chat_with_gpt4(messages=messages)
    try:
        if response is not None:
            logger.info("Email draft received from LLM.")
            return json.loads(response)
        else:
            logger.warning("No response received from LLM for email draft.")
            return None
    except json.JSONDecodeError:
        logger.error("Failed to parse email draft response")
        return None


def mock_send_email(email_data, contact_info):
    logger.info("To: %s", contact_info)
    logger.info("Subject: %s", email_data.get('subject', 'Missing Referral Information'))
    logger.info("Body: %s", email_data.get('body', 'No body content'))
    logger.info("%s", "=" * 50)
    
    # Simulate successful email sending
    logger.info("Mock email send complete.")
    return {
        "status": "sent",
        "timestamp": datetime.datetime.now().isoformat(),
        "recipient": contact_info
    }


def mock_emr_sync(referral_data):
    logger.info("Syncing referral ID: %s", referral_data.get('referral_id', 'Unknown'))
    logger.info("Patient: %s", referral_data.get('patient', {}).get('name', 'Unknown'))
    logger.info("Referring Provider: %s", referral_data.get('referring_provider', {}).get('name', 'Unknown'))
    logger.info("Receiving Provider: %s", referral_data.get('receiving_provider', {}).get('name', 'Unknown'))
    logger.info("EMR Sync completed successfully")
    logger.info("%s", "=" * 50)
    logger.info("Mock EMR sync complete.")
    return {
        "status": "synced",
        "timestamp": datetime.datetime.now().isoformat(),
        "referral_id": referral_data.get('referral_id')
    }


def update_redis_status(doc_id, status, additional_info=None, structured_data=None):
    """Update the document processing status in Redis."""
    logger.info("Updating Redis status for document %s to %s", doc_id, status)
    doc_key = f"document:{doc_id}"
    status_data = {
        "status": status,
        "timestamp": datetime.datetime.now().isoformat(),
        "additional_info": additional_info,
        "structured_data": structured_data
    }
    redis_client.setex(doc_key, 3600, json.dumps(status_data))
    logger.info("Redis status update complete for document %s.", doc_id)


def get_contact_info(provider_data):
    """Extract the best available contact information from provider data."""
    logger.debug("Extracting contact info from provider data.")
    contact = provider_data.get('contact', {})
    
    if contact.get('email'):
        logger.debug("Contact info extraction complete: %s", contact)
        return contact['email']
    elif contact.get('phone'):
        logger.debug("Contact info extraction complete: %s", contact)
        return contact['phone']
    elif contact.get('address'):
        logger.debug("Contact info extraction complete: %s", contact)
        return contact['address']
    else:
        logger.debug("Contact info extraction complete: %s", contact)
        return "No contact information available"



def main():
    """Main loop for processing documents from the Redis queue."""
    logger.info("Agent main loop started.")
    while True:
        logger.debug("Polling for new document in Redis queue.")
        document_data = check_redis()
        if document_data:
            logger.info("Document data found. Beginning processing.")
            try:
                # Parse the document data
                logger.debug("Parsing document data from Redis.")
                job_data = json.loads(document_data)
                doc_id = job_data.get('document_id', 'unknown')
                raw_ocr_text = job_data.get('extracted_text', '')
                
                logger.info("Processing document %s", doc_id)
                
                # Extract structured data using LLM
                logger.info("Extracting structured data for document %s", doc_id)
                structured_data = llm_assistend_extraction(raw_ocr_text)
                
                if structured_data is None:
                    logger.error("Failed to extract structured data for document %s", doc_id)
                    update_redis_status(doc_id, "extraction_failed", "LLM extraction failed", structured_data=None)
                    continue
                
                # Check for missing required fields
                logger.info("Checking required fields for document %s", doc_id)
                is_complete, missing_fields = check_required_fields(structured_data)
                
                if is_complete:
                    # All required fields are present - sync to EMR
                    logger.info("All required fields present for document %s. Syncing to EMR...", doc_id)
                    emr_result = mock_emr_sync(structured_data)
                    update_redis_status(doc_id, "emr_synced", emr_result, structured_data=structured_data)
                    logger.info("EMR sync complete for document %s", doc_id)
                else:
                    # Missing fields detected - request additional information
                    logger.info("Missing fields detected for document %s: %s", doc_id, missing_fields)
                    
                    # Draft email requesting missing information
                    logger.info("Drafting email for missing fields for document %s", doc_id)
                    email_draft = draft_request_email(
                        missing_fields,
                        structured_data.get('referring_provider', {}),
                        structured_data.get('receiving_provider', {}),
                        structured_data.get('referral_id', doc_id)
                    )
                    
                    if email_draft:
                        # Determine who to contact (prefer referring provider)
                        referring_provider = structured_data.get('referring_provider', {})
                        receiving_provider = structured_data.get('receiving_provider', {})
                        
                        contact_info = get_contact_info(referring_provider)
                        if contact_info == "No contact information available":
                            contact_info = get_contact_info(receiving_provider)
                        
                        # Send email
                        logger.info("Sending email for missing fields for document %s", doc_id)
                        email_result = mock_send_email(email_draft, contact_info)
                        
                        update_redis_status(doc_id, "missing_fields_email_sent", {
                            "missing_fields": missing_fields,
                            "email_result": email_result,
                            "contact_info": contact_info
                        }, structured_data=structured_data)
                        logger.info("Missing fields email sent for document %s", doc_id)
                    else:
                        logger.error("Failed to draft email for document %s", doc_id)
                        update_redis_status(doc_id, "email_draft_failed", {"missing_fields": missing_fields}, structured_data=structured_data)
                
                print(f"Processed document {doc_id}")
                print(f"Structured data: {json.dumps(structured_data, indent=2)}")
                logger.info("Processing complete for document %s", doc_id)
                
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.error("Error processing document (JSON or dict error): %s", str(e))
                logger.exception(e)
            except Exception as e:
                logger.error("Unexpected error processing document: %s", str(e))
                logger.exception(e)
                raise
            
            # For now, break after handling current document
            logger.info("Breaking after processing current document.")
        
        # Continue checking Redis (in production, you might want to add a small delay)
        logger.debug("No document found, continuing to poll Redis queue.")
        continue
    logger.info("Agent main loop ended.")
    
if __name__ == "__main__":
    main()