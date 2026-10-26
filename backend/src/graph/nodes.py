import json
import os
import logging
import re
from typing import Dict, Any

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_aws import AmazonKnowledgeBasesRetriever
from langchain_core.messages import SystemMessage, HumanMessage

from backend.src.graph.state import VideoAuditState, ComplianceIssue
from backend.src.services.video_indexer import VideoIndexerService

logger = logging.getLogger("brand-guardian")
logging.basicConfig(level=logging.INFO)


# --- NODE 1: THE INDEXER ---
def index_video_node(state: VideoAuditState) -> Dict[str, Any]:
    """
    Downloads YouTube video, uploads to S3, and extracts:
      - Transcript via AWS Transcribe
      - On-screen text (OCR) via AWS Rekognition
    """
    video_url = state.get("video_url")
    video_id_input = state.get("video_id", "vid_demo")

    logger.info(f"--- [Node: Indexer] Processing: {video_url} ---")

    local_filename = "temp_audit_video.mp4"

    try:
        vi_service = VideoIndexerService()

        # 1. DOWNLOAD from YouTube
        if "youtube.com" in video_url or "youtu.be" in video_url:
            local_path = vi_service.download_youtube_video(video_url, output_path=local_filename)
        else:
            raise Exception("Please provide a valid YouTube URL for this test.")

        # 2. UPLOAD to S3 and start Transcribe + Rekognition jobs
        job_info = vi_service.upload_video(local_path, video_name=video_id_input)
        logger.info(f"Upload Success. Jobs started: {job_info}")

        # 3. CLEANUP local file
        if os.path.exists(local_path):
            os.remove(local_path)
            logger.info("Cleaned up local temp video file.")

        # 4. WAIT for both AWS jobs to complete
        processed_data = vi_service.wait_for_processing(job_info)

        # 5. EXTRACT into graph state format
        clean_data = vi_service.extract_data(processed_data)

        logger.info("--- [Node: Indexer] Extraction Complete ---")
        return clean_data

    except Exception as e:
        logger.error(f"Video Indexer Failed: {e}")
        return {
            "errors": [str(e)],
            "final_status": "FAIL",
            "transcript": "",
            "ocr_text": []
        }


# --- NODE 2: THE COMPLIANCE AUDITOR ---
def audit_content_node(state: VideoAuditState) -> Dict[str, Any]:
    """
    Performs Retrieval-Augmented Generation (RAG) to audit the video content.
    - Retrieves relevant brand/compliance rules from AWS Bedrock Knowledge Base.
    - Uses Google Gemini LLM to reason about violations.
    """
    logger.info("--- [Node: Auditor] Querying Knowledge Base & LLM ---")

    transcript = state.get("transcript", "")

    if not transcript:
        logger.warning("No transcript available. Skipping Audit.")
        return {
            "final_status": "FAIL",
            "final_report": "Audit skipped because video processing failed (No Transcript)."
        }

    # --- Step 1: Initialize Google Gemini LLM ---
    llm = ChatGoogleGenerativeAI(
        model=os.getenv("GOOGLE_MODEL", "gemini-2.0-flash"),
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        temperature=0.0
    )

    # --- Step 2: Retrieve relevant compliance rules from AWS Bedrock Knowledge Base ---
    retriever = AmazonKnowledgeBasesRetriever(
        knowledge_base_id=os.getenv("AWS_BEDROCK_KNOWLEDGE_BASE_ID"),
        retrieval_config={"managedSearchConfiguration": {"numberOfResults": 3}},
        region_name=os.getenv("AWS_REGION", "us-east-1")
    )

    ocr_text = state.get("ocr_text", [])
    query_text = f"{transcript} {' '.join(ocr_text)}"

    logger.info("Retrieving compliance rules from Bedrock Knowledge Base...")
    docs = retriever.invoke(query_text)
    retrieved_rules = "\n\n".join([doc.page_content for doc in docs])
    logger.info(f"Retrieved {len(docs)} rule document(s).")

    # --- Step 3: Build RAG Prompt ---
    system_prompt = f"""
    You are a Senior Brand Compliance Auditor.

    OFFICIAL REGULATORY RULES:
    {retrieved_rules}

    INSTRUCTIONS:
    1. Analyze the Transcript and OCR text below.
    2. Identify ANY violations of the rules above.
    3. Return STRICTLY valid JSON in the following format (no markdown, no code fences):

    {{
        "compliance_results": [
            {{
                "category": "Claim Validation",
                "severity": "CRITICAL",
                "description": "Explanation of the violation..."
            }}
        ],
        "status": "FAIL",
        "final_report": "Summary of findings..."
    }}

    If no violations are found, set "status" to "PASS" and "compliance_results" to [].
    """

    user_message = f"""
    VIDEO METADATA: {state.get('video_metadata', {})}
    TRANSCRIPT: {transcript}
    ON-SCREEN TEXT (OCR): {ocr_text}
    """

    # --- Step 4: Invoke LLM ---
    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message)
        ])

        # Gemini may return a list of content blocks — extract plain text
        content = response.content
        if isinstance(content, list):
            content = next(
                (block["text"] for block in content if isinstance(block, dict) and "text" in block),
                ""
            )

        content_str = str(content).strip()
        if content_str.startswith("```"):
            content_str = re.sub(r"^```(?:json)?\s*", "", content_str)
            content_str = re.sub(r"\s*```$", "", content_str)

        audit_data = json.loads(content_str.strip())

        logger.info(f"Audit complete. Status: {audit_data.get('status')}")

        return {
            "compliance_results": audit_data.get("compliance_results", []),
            "final_status": audit_data.get("status", "FAIL"),
            "final_report": audit_data.get("final_report", "No report generated.")
        }

    except Exception as e:
        logger.error(f"System Error in Auditor Node: {str(e)}")
        raw = response.content if "response" in locals() else "None"
        logger.error(f"Raw LLM Response: {raw}")
        return {
            "errors": [str(e)],
            "final_status": "FAIL"
        }