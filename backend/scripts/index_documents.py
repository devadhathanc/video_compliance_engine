import os
import glob
import logging
import boto3
from dotenv import load_dotenv

load_dotenv(override=True)

# Document Loaders and Splitters
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Setup Logging & Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("indexer")

def index_docs():
    """
    Reads PDFs from backend/data, verifies chunks, uploads them to AWS S3,
    and initiates an ingestion job in AWS Bedrock Knowledge Base.
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    data_folder = os.path.join(current_dir, "../../backend/data")
    
    region = os.getenv("AWS_REGION", "us-east-1")
    s3_bucket = os.getenv("AWS_S3_BUCKET")
    kb_id = os.getenv("AWS_BEDROCK_KNOWLEDGE_BASE_ID")
    data_source_id = os.getenv("AWS_BEDROCK_DATA_SOURCE_ID")
    s3_prefix = os.getenv("AWS_S3_KB_PREFIX", "knowledge_base/")

    logger.info("=" * 60)
    logger.info("AWS Knowledge Base Indexer Configuration:")
    logger.info(f"AWS_REGION: {region}")
    logger.info(f"AWS_S3_BUCKET: {s3_bucket}")
    logger.info(f"AWS_BEDROCK_KNOWLEDGE_BASE_ID: {kb_id}")
    logger.info(f"S3 Destination Prefix: {s3_prefix}")
    logger.info("=" * 60)

    if not s3_bucket:
        logger.error("Missing required environment variable: AWS_S3_BUCKET")
        return

    # 1. Find PDF Files
    pdf_files = glob.glob(os.path.join(data_folder, "*.pdf"))
    if not pdf_files:
        logger.warning(f"No PDFs found in {data_folder}. Please add files.")
        return

    logger.info(f"Found {len(pdf_files)} PDFs to process: {[os.path.basename(f) for f in pdf_files]}")

    # 2. Local Chunking Inspection
    all_splits = []
    for pdf_path in pdf_files:
        try:
            filename = os.path.basename(pdf_path)
            logger.info(f"Loading and chunking: {filename}...")
            loader = PyPDFLoader(pdf_path)
            raw_docs = loader.load()

            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=1000,
                chunk_overlap=200
            )
            splits = text_splitter.split_documents(raw_docs)
            for split in splits:
                split.metadata["source"] = filename
            all_splits.extend(splits)
            logger.info(f" -> Generated {len(splits)} text chunks from {filename}.")
        except Exception as e:
            logger.error(f"Failed to process {pdf_path}: {e}")

    logger.info(f"Total document chunks prepared: {len(all_splits)}")

    # 3. Upload Source Documents to AWS S3 Knowledge Base Bucket
    s3_client = boto3.client("s3", region_name=region)
    uploaded_keys = []

    for pdf_path in pdf_files:
        filename = os.path.basename(pdf_path)
        s3_key = f"{s3_prefix}{filename}"
        try:
            logger.info(f"Uploading {filename} to s3://{s3_bucket}/{s3_key}...")
            s3_client.upload_file(pdf_path, s3_bucket, s3_key)
            uploaded_keys.append(s3_key)
            logger.info(f"✓ Uploaded {filename} successfully.")
        except Exception as e:
            logger.error(f"Failed to upload {filename} to S3: {e}")

    # 4. Sync / Ingestion into AWS Bedrock Knowledge Base
    if kb_id:
        try:
            bedrock_agent = boto3.client("bedrock-agent", region_name=region)
            
            # If data_source_id is not specified, list data sources for the knowledge base
            if not data_source_id:
                logger.info(f"Listing data sources for Bedrock Knowledge Base: {kb_id}...")
                ds_resp = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id, maxResults=5)
                data_sources = ds_resp.get("dataSourceSummaries", [])
                if data_sources:
                    data_source_id = data_sources[0]["dataSourceId"]
                    logger.info(f"Found active data source: {data_source_id}")

            if data_source_id:
                logger.info(f"Starting Bedrock Knowledge Base Ingestion Job for KB '{kb_id}' and Data Source '{data_source_id}'...")
                job = bedrock_agent.start_ingestion_job(
                    knowledgeBaseId=kb_id,
                    dataSourceId=data_source_id,
                    description="Automated sync from index_documents script"
                )
                job_id = job.get("ingestionJob", {}).get("ingestionJobId")
                logger.info(f"✓ Ingestion job started successfully! Job ID: {job_id}")
            else:
                logger.warning("No Bedrock Data Source ID found. Ensure the S3 bucket is linked as a data source in Bedrock console.")
        except Exception as e:
            logger.warning(f"Could not trigger Bedrock automatic sync job: {e}")
            logger.info("You can sync the S3 data source manually in the AWS Bedrock Console.")

    logger.info("=" * 60)
    logger.info("✅ Document Indexing & S3 Knowledge Base Sync Completed!")
    logger.info("=" * 60)

if __name__ == "__main__":
    index_docs()