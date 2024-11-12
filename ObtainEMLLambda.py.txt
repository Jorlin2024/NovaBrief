import boto3
import email
import email.utils
import logging
import base64
import os
from urllib.parse import unquote_plus
from datetime import datetime

def extract_email(from_header):
    """Extracts the email address from the 'From' header."""
    name, email_address = email.utils.parseaddr(from_header)
    return email_address

def decode_filename_and_extension(encoded_filename):
    """Decodes the file name and gets the clean extension"""
    try:
        # Decode the file name
        decoded_parts = email.header.decode_header(encoded_filename)
        filename_parts = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                if charset:
                    filename_parts.append(part.decode(charset))
                else:
                    filename_parts.append(part.decode())
            else:
                filename_parts.append(part)
        
        decoded_filename = ''.join(filename_parts)
        # Get the extension from the decoded name
        _, extension = os.path.splitext(decoded_filename)
        return extension[1:].lower() if extension else ''
    except Exception as e:
        logging.error(f"Error decoding file name: {str(e)}")
        return ''

def get_attachments_info(msg):
    """Extracts information about all attachments in the email."""
    attachments = []
    
    for part in msg.walk():
        if part.get_content_maintype() == 'multipart':
            continue
            
        filename = part.get_filename()
        if filename:
            # Use the new function to get the clean extension
            extension = decode_filename_and_extension(filename)
            attachments.append({
                'filename': filename,
                'extension': extension,
                'part': part
            })
    
    return attachments

def lambda_handler(event, context):
    s3 = boto3.client("s3")

    # Set up logging
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    ALLOWED_EXTENSIONS = {'docx', 'csv', 'html', 'txt', 'pdf', 'md', 'doc', 'xlsx', 'xls'}

    if event: 
        print("My Event is : ", event)
        file_obj = event["Records"][0]
        filename = str(file_obj["s3"]['object']['key'])
        bucket_name = file_obj["s3"]["bucket"]["name"]
        logger.info(f"Processing file: {filename} from bucket: {bucket_name}")

        file_key_original = unquote_plus(filename)
        logger.info(f"File name: {file_key_original}")
        fileObj = s3.get_object(Bucket = bucket_name, Key=file_key_original)
        
        msg = email.message_from_bytes(fileObj['Body'].read())
        email_from = extract_email(msg['From'])
        logger.info(f"From: {email_from}")

        # Get information about all attachments
        attachments = get_attachments_info(msg)

        # Log information about the found attachments
        logger.info(f"Attachments found: {len(attachments)}")
        for att in attachments:
            logger.info(f"Attachment: {att['filename']} - Extension: {att['extension']}")

        # Filter only the attachments with allowed extensions
        valid_attachments = [att for att in attachments if att['extension'] in ALLOWED_EXTENSIONS]
        logger.info(f"Valid attachments: {len(valid_attachments)}")

        # Currently date
        timestamp = datetime.now().strftime("%Y-%b-%d")

        # Process the valid attachments
        for idx, attachment in enumerate(valid_attachments):
            # Decode the attachment content
            file_data = attachment['part'].get_payload(decode=True)
            
            # Temporary file path
            file_path = f'/tmp/{filename}'
            
            # Upload the attachment to another bucket 
            target_bucket = os.environ['target_bucket']
                        
            try:
                # Save the file temporarily
                with open(file_path, 'wb') as f:
                    f.write(file_data)

                # Construct the S3 path
                # If there are multiple valid attachments, add an index to the name
                if len(valid_attachments) > 1:
                    s3_path = f"{email_from}/{timestamp}_{idx + 1}.{attachment['extension']}"
                else:
                    s3_path = f"{email_from}/{timestamp}.{attachment['extension']}"
                
                # Upload to S3
                s3.put_object(
                    Bucket=target_bucket, 
                    Key=s3_path, 
                    Body=file_data
                )

                logger.info(f'Attachment processed: {s3_path}')
            
            except Exception as e:
                logger.error(f'Error processing attachment {attachment["filename"]}: {str(e)}')
            
            # Clean up the temporary file
            if os.path.exists(file_path):
                os.remove(file_path)
        return {
            'statusCode': 200,
            'body': f'Processing completed. {len(valid_attachments)} files processed.'
        }