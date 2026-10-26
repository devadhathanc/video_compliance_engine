import os
import time
import logging
import uuid
import requests
import yt_dlp
import boto3

logger = logging.getLogger("video-indexer")

class VideoIndexerService:
    """
    AWS-powered Video Indexer replacing Azure Video Indexer.
    Uses:
      - Amazon S3 for video storage
      - Amazon Transcribe for Speech-to-Text
      - Amazon Rekognition for On-Screen Text Detection (OCR)
    """

    def __init__(self):
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.bucket = os.getenv("AWS_S3_BUCKET")
        if not self.bucket:
            raise ValueError("AWS_S3_BUCKET is not set in environment variables.")

        # Initialize AWS clients
        self.s3_client = boto3.client("s3", region_name=self.region)
        self.transcribe_client = boto3.client("transcribe", region_name=self.region)
        self.rekognition_client = boto3.client("rekognition", region_name=self.region)

    def download_youtube_video(self, url: str, output_path: str = "temp_video.mp4") -> str:
        """Downloads a YouTube video to a local MP4 file."""
        logger.info(f"Downloading YouTube video: {url}")
        
        ydl_opts = {
            "format": "best[ext=mp4]/best",
            "outtmpl": output_path,
            "quiet": False,
            "no_warnings": False,
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            logger.info("Download complete.")
            return output_path
        except Exception as e:
            raise Exception(f"YouTube Download Failed: {str(e)}")

    def upload_video(self, video_path: str, video_name: str) -> dict:
        """
        Uploads local video to S3 and kicks off both Transcribe and Rekognition jobs.
        Returns a dict tracking the job identifiers.
        """
        unique_suffix = uuid.uuid4().hex[:8]
        s3_key = f"incoming/{video_name}_{unique_suffix}.mp4"
        transcribe_job_name = f"transcribe_{video_name}_{unique_suffix}"

        logger.info(f"Uploading {video_path} to s3://{self.bucket}/{s3_key}...")
        self.s3_client.upload_file(video_path, self.bucket, s3_key)
        logger.info("S3 upload complete.")

        s3_uri = f"s3://{self.bucket}/{s3_key}"

        # 1. Start Amazon Transcribe job
        logger.info(f"Starting AWS Transcribe job: {transcribe_job_name}...")
        self.transcribe_client.start_transcription_job(
            TranscriptionJobName=transcribe_job_name,
            Media={"MediaFileUri": s3_uri},
            MediaFormat="mp4",
            IdentifyLanguage=True,  # Auto-detect language (or defaults to en-US)
        )

        # 2. Start Amazon Rekognition Text Detection
        logger.info(f"Starting AWS Rekognition OCR detection for {s3_key}...")
        rekognition_resp = self.rekognition_client.start_text_detection(
            Video={"S3Object": {"Bucket": self.bucket, "Name": s3_key}}
        )
        rekognition_job_id = rekognition_resp["JobId"]

        return {
            "s3_key": s3_key,
            "transcribe_job_name": transcribe_job_name,
            "rekognition_job_id": rekognition_job_id,
        }

    def wait_for_processing(self, job_info: dict) -> dict:
        """
        Polls both Transcribe and Rekognition until processing completes.
        """
        transcribe_job_name = job_info["transcribe_job_name"]
        rekognition_job_id = job_info["rekognition_job_id"]
        s3_key = job_info["s3_key"]

        logger.info("Waiting for AWS Transcribe and Rekognition jobs to finish...")

        # Poll Transcribe
        transcript_text = ""
        while True:
            t_status = self.transcribe_client.get_transcription_job(
                TranscriptionJobName=transcribe_job_name
            )
            state = t_status["TranscriptionJob"]["TranscriptionJobStatus"]
            if state == "COMPLETED":
                transcript_url = t_status["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
                response = requests.get(transcript_url)
                transcript_json = response.json()
                transcripts = transcript_json.get("results", {}).get("transcripts", [])
                transcript_text = " ".join([t.get("transcript", "") for t in transcripts])
                logger.info("AWS Transcribe completed successfully.")
                break
            elif state == "FAILED":
                reason = t_status["TranscriptionJob"].get("FailureReason", "Unknown")
                raise Exception(f"AWS Transcribe job failed: {reason}")
            
            logger.info(f"Transcribe status: {state}... waiting 10s")
            time.sleep(10)

        # Poll Rekognition
        ocr_lines = []
        while True:
            r_status = self.rekognition_client.get_text_detection(JobId=rekognition_job_id)
            state = r_status["JobStatus"]
            if state == "SUCCEEDED":
                # Collect distinct detected texts with confidence > 80%
                seen = set()
                for item in r_status.get("TextDetections", []):
                    text_detection = item.get("TextDetection", {})
                    confidence = text_detection.get("Confidence", 0.0)
                    detected = text_detection.get("DetectedText", "").strip()
                    if detected and confidence >= 80.0:
                        lower_text = detected.lower()
                        if lower_text not in seen:
                            seen.add(lower_text)
                            ocr_lines.append(detected)

                # Check if there are paginated results
                next_token = r_status.get("NextToken")
                while next_token:
                    paged = self.rekognition_client.get_text_detection(
                        JobId=rekognition_job_id, NextToken=next_token
                    )
                    for item in paged.get("TextDetections", []):
                        text_detection = item.get("TextDetection", {})
                        confidence = text_detection.get("Confidence", 0.0)
                        detected = text_detection.get("DetectedText", "").strip()
                        if detected and confidence >= 80.0:
                            lower_text = detected.lower()
                            if lower_text not in seen:
                                seen.add(lower_text)
                                ocr_lines.append(detected)
                    next_token = paged.get("NextToken")

                logger.info(f"AWS Rekognition completed. Found {len(ocr_lines)} text snippets.")
                break
            elif state == "FAILED":
                status_msg = r_status.get("StatusMessage", "Unknown")
                logger.warning(f"AWS Rekognition OCR failed: {status_msg}. Continuing without OCR.")
                break

            logger.info(f"Rekognition status: {state}... waiting 10s")
            time.sleep(10)

        # Optional S3 cleanup of uploaded video
        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=s3_key)
            logger.info(f"Cleaned up temporary video s3://{self.bucket}/{s3_key}")
        except Exception as e:
            logger.warning(f"Failed to delete S3 temporary file: {e}")

        return {
            "transcript": transcript_text,
            "ocr_text": ocr_lines,
            "video_metadata": {"platform": "youtube"}
        }

    def extract_data(self, processed_data: dict) -> dict:
        """Returns the processed data in the state format."""
        return {
            "transcript": processed_data.get("transcript", ""),
            "ocr_text": processed_data.get("ocr_text", []),
            "video_metadata": processed_data.get("video_metadata", {"platform": "youtube"})
        }