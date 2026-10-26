import json
import os
import logging
from backend.src.graph.workflow import app as compliance_graph

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """
    AWS Lambda entry point for Brand Guardian AI.
    Handles events from:
    1. Amazon API Gateway (HTTP POST /audit)
    2. Amazon S3 PutObject events (auto-audit newly uploaded videos)
    """
    logger.info(f"Received Lambda event: {json.dumps(event)}")
    
    # 1. Parse incoming event
    if "body" in event:
        body = event["body"]
        if isinstance(body, str):
            body = json.loads(body)
        video_url = body.get("video_url")
    elif "Records" in event:
        # Triggered by S3 event
        record = event["Records"][0]
        s3_bucket = record["s3"]["bucket"]["name"]
        s3_key = record["s3"]["object"]["key"]
        video_url = f"s3://{s3_bucket}/{s3_key}"
    else:
        video_url = event.get("video_url")
        
    if not video_url:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing video_url in request"})
        }
        
    try:
        request_id = getattr(context, "aws_request_id", "local_req")
        initial_inputs = {
            "video_url": video_url,
            "video_id": f"vid_{request_id[:8]}",
            "compliance_results": [],
            "errors": []
        }
        
        final_state = compliance_graph.invoke(initial_inputs)
        
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "video_id": final_state.get("video_id"),
                "status": final_state.get("final_status"),
                "final_report": final_state.get("final_report"),
                "compliance_results": final_state.get("compliance_results", [])
            })
        }
    except Exception as e:
        logger.error(f"Error in Lambda execution: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
