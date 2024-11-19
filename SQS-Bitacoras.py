import json
import os
import boto3
import botocore.config
import tempfile
import logging
from urllib.parse import unquote_plus
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

# Configure the AWS SDK to retry operations up to 5 times
custom_config = botocore.config.Config(
    retries={
        'max_attempts': 5,
        'mode': 'standard'
    }
)

# Configure logging to see retry operations
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('botocore')

# Set up AWS clients
s3 = boto3.client('s3')
bedrock = boto3.client('bedrock-runtime',config=custom_config)

# Get the prompt from the environment variable
prompt = os.environ['prompt_informe']

# List of allowed file extensions
allowed_extensions = ['docx', 'csv', 'html', 'txt', 'pdf', 'md', 'doc', 'xlsx', 'xls']

# Debugging logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def send_email(recipient_email, temp_file_path, bucket_key, summary):
    """Sends an email with the generated summary and the original file attached."""
    
    # SMTP server and credentials
    smtp_server = os.environ['smtp_server']
    smtp_port = os.environ['smtp_port']  
    smtp_user = os.environ['smtp_user']
    smtp_password = os.environ['smtp_password']
    sender_name = os.environ['sender_name']
    cc_email = os.environ['cc_email']

    # Create the email message
    msg = MIMEMultipart()
    msg['From'] = f"{sender_name} <{smtp_user}>"
    msg['To'] = recipient_email
    msg['Cc'] = cc_email
    msg['Subject'] = "Resumen de Documento Procesado"

    # Add the summary to the email body
    msg.attach(MIMEText(summary, 'plain'))

    # Attach the original file if it exists
    if temp_file_path:
        with open(temp_file_path, 'rb') as file:
            file_data = file.read()
            part = MIMEApplication(file_data)
            part.add_header('Content-Disposition', 'attachment', 
                            filename=bucket_key)
            msg.attach(part)
            logger.info(f"Attached file: {bucket_key}")
    
    # Send the email
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()  # Iniciar conexión segura
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
            logger.info("Email sent")
    except Exception as e:
        print(f"Error sending email: {e}")

def lambda_handler(event, context):
    # Receive a message from the SQS queue
    for record in event['Records']:
        body = json.loads(record['body'])

        bucket_name = body['Records'][0]['s3']['bucket']['name']
        file_key = body['Records'][0]['s3']['object']['key']

        file_key_original = unquote_plus(file_key)

        logger.info(f"Bucket : {bucket_name}")
        logger.info(f"Bucket key : {file_key}")

        # Check the file type against the allowed extensions [docx, csv, html, txt, pdf, md, doc, xlsx, xls]
        extension = file_key_original.split('.')[-1]
        logger.info(f"Tipo de archivo: {extension}")

        # If the file type is not allowed, send an error email and return
        if extension.lower() not in allowed_extensions:
            logger.error(f"Tipo de archivo no permitido: {extension}")

            error_message = f"""
Lo sentimos, pero el archivo que intentó procesar no está en un formato permitido.
                        
Formatos de archivo permitidos: {', '.join(allowed_extensions)}
                        
Archivo enviado: {file_key_original}                  
Extensión detectada: {extension}

Por favor, intente nuevamente con un archivo en alguno de los formatos permitidos.
Atentamente, El equipo de Novacomp"""
            send_email(
                recipient_email= os.environ['recipient_email'],
                temp_file_path=None,
                bucket_key = file_key_original,
                summary = error_message
            )
            return {
                'statusCode': 400,
                'body': json.dumps({'error': f'Unsupported file type: {extension}'}),
            }


        # Download the file from S3 to a temporary file
        logger.info(f"Downloading file from S3: {bucket_name}/{file_key}")
        with tempfile.NamedTemporaryFile(delete=False, dir='/tmp') as temp_file:
            temp_file_path = temp_file.name
        s3.download_file(bucket_name, file_key_original, temp_file_path)
    
        # Read the content of the downloaded file
        with open(temp_file_path, 'rb') as file:
            file_content = file.read()
    
        logger.info("Calling Bedrock API")

        # Use the Converse API to summarize the file content
        model_id = os.environ['model_id']
        
        response = bedrock.converse(
            modelId=model_id,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'text': prompt,
                    },
                    {
                        'document': {
                            'name': "Resumen",
                            'format': extension,
                            'source': {
                                'bytes': file_content
                            }
                        }
                    }
                ]
            }]
        )
        # Store the model's response in a variable
        summary = response['output']['message']['content'][0]['text']
        logger.info(f"Document summary for {file_key_original}: {summary}")

        success_message = f"""Estimado/a miembro del equipo:

A continuación encontrará el resumen generado del documento:

{summary}

Se adjunta el documento original para su referencia.

Atentamente,
El equipo de Novacomp"""

       
        send_email(
            recipient_email= os.environ['recipient_email'],
            temp_file_path=temp_file_path,
            bucket_key = file_key_original,
            summary = success_message
        )
        
        # Delete the temporary file
        os.remove(temp_file_path)