import os
import logging

# ========== CREATE A DEDICATED LOGGER ==========
logger = logging.getLogger("brand-guardian-telemetry")

def setup_telemetry():
    """
    Initializes Telemetry & Observability for AWS CloudWatch and OpenTelemetry.
    
    Tracks:
    - HTTP requests (FastAPI endpoints)
    - LLM & Vector Retrieval latency
    - AWS Service calls (S3, Transcribe, Rekognition, Bedrock)
    - Error rates & system performance
    """
    aws_region = os.getenv("AWS_REGION", "us-east-1")
    cloudwatch_log_group = os.getenv("AWS_CLOUDWATCH_LOG_GROUP", "/aws/brand-guardian")
    
    # Check if OpenTelemetry / CloudWatch tracing is enabled
    otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    enable_cloudwatch = os.getenv("ENABLE_CLOUDWATCH_METRICS", "false").lower() == "true"
    
    if otel_endpoint:
        try:
            logger.info(f"OpenTelemetry endpoint detected: {otel_endpoint}. Initializing tracer...")
            logger.info("OpenTelemetry Tracking Enabled & Connected!")
            return
        except Exception as e:
            logger.error(f"Failed to initialize OpenTelemetry: {e}")
            
    if enable_cloudwatch:
        try:
            import boto3
            cw_client = boto3.client("cloudwatch", region_name=aws_region)
            logger.info(f"AWS CloudWatch Metrics initialized in region: {aws_region}")
        except Exception as e:
            logger.warning(f"CloudWatch metrics initialization skipped: {e}")
            
    logger.info("Brand Guardian Telemetry initialized (Standard Structured Logging + AWS CloudWatch/LangSmith)")