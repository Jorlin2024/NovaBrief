import boto3
import email
import email.utils
import logging
import os
import base64
from urllib.parse import unquote_plus
from datetime import datetime

# Configurar logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def extract_email(from_header):
    """Extracts the email address from the 'From' header."""
    name, email_address = email.utils.parseaddr(from_header)
    return email_address

def extract_html_with_embedded_images(msg):
    """
    Extrae el cuerpo HTML y procesa imágenes incrustadas preservando el formato
    """
    html_body = None
    embedded_images = {}

    # Lista de codificaciones a intentar (ordenadas de más general a más específica)
    encodings = [
        'utf-8', 'utf-8-sig',  # UTF-8 primero
        'cp1252',  # Windows
        'latin-1', 'iso-8859-1', 'iso-8859-15',  # Latín
        'ascii'  # Último recurso
    ]

    # Función auxiliar para intentar decodificar
    def safe_decode(payload, encodings_to_try):
        for encoding in encodings_to_try:
            try:
                # Intentar decodificar con la codificación actual
                decoded = payload.decode(encoding)
                logger.info(f"Decodificación exitosa con {encoding}")
                return decoded
            except (UnicodeDecodeError, LookupError):
                continue
        
        # Si ninguna codificación funciona, usar decodificación con reemplazo
        try:
            return payload.decode('utf-8', errors='replace')
        except Exception:
            # Último recurso: decodificación forzada
            return payload.decode('latin-1')

    # Buscar cuerpo HTML
    for part in msg.walk():
        if part.get_content_type() == 'text/html':
            # Obtener payload
            payload = part.get_payload(decode=True)
            
            # Intentar decodificar
            html_body = safe_decode(payload, encodings)
            break
    
    # Si no hay HTML, buscar texto plano
    if not html_body:
        for part in msg.walk():
            if part.get_content_type() == 'text/plain':
                # Obtener payload
                payload = part.get_payload(decode=True)
                
                # Intentar decodificar
                plain_text = safe_decode(payload, encodings)
                
                # Convertir texto plano a HTML simple
                html_body = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                </head>
                <body>
                    <pre>{plain_text}</pre>
                </body>
                </html>
                """
                break

    # Procesar imágenes incrustadas
    if html_body:
        for part in msg.walk():
            if part.get_content_maintype() == 'image':
                # Obtener Content-ID o nombre de archivo
                content_id = part.get('Content-ID', '').strip('<>')
                filename = part.get_filename()

                # Decodificar imagen
                image_data = part.get_payload(decode=True)
                image_base64 = base64.b64encode(image_data).decode('utf-8')
                mime_type = part.get_content_type()

                # Generar identificador único
                img_key = content_id or filename or f'image_{len(embedded_images)}'

                # Guardar imagen
                embedded_images[img_key] = {
                    'data': image_base64,
                    'mime_type': mime_type,
                    'filename': filename or img_key
                }

        # Reemplazar referencias de imágenes
        for key, img_info in embedded_images.items():
            # Reemplazar referencias de Content-ID o nombre de archivo
            html_body = html_body.replace(f'cid:{key}', 
                f'data:{img_info["mime_type"]};base64,{img_info["data"]}')
            html_body = html_body.replace(key, 
                f'data:{img_info["mime_type"]};base64,{img_info["data"]}')

    # Agregar encabezado HTML si no existe
    if not html_body.strip().lower().startswith('<!doctype') and not html_body.strip().lower().startswith('<html'):
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body>
            {html_body}
        </body>
        </html>
        """

    return html_body

def lambda_handler(event, context):
    s3 = boto3.client("s3")

    # Configurar logging
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    if event: 
        print("My Event is : ", event)
        file_obj = event["Records"][0]
        filename = str(file_obj["s3"]['object']['key'])
        bucket_name = file_obj["s3"]["bucket"]["name"]
        logger.info(f"Processing file: {filename} from bucket: {bucket_name}")

        file_key_original = unquote_plus(filename)
        logger.info(f"File name: {file_key_original}")
        
        # Obtener el objeto del archivo EML
        fileObj = s3.get_object(Bucket=bucket_name, Key=file_key_original)
        
        # Convertir a objeto de mensaje de correo
        msg = email.message_from_bytes(fileObj['Body'].read())
        
        # Extraer información del remitente
        email_from = extract_email(msg['From'])
        logger.info(f"From: {email_from}")

        # Extraer cuerpo HTML
        html_body = extract_html_with_embedded_images(msg)
        
        if html_body:
            # Generar marca de tiempo
            timestamp = datetime.now().strftime("%Y-%b-%d %H:%M:%S")
            
            # Bucket de destino
            target_bucket = os.environ['target_bucket']
            
            try:
                # Ruta para guardar en S3
                s3_path = f"{email_from}/{timestamp}.html"
                
                # Guardar en S3
                s3.put_object(
                    Bucket=target_bucket, 
                    Key=s3_path, 
                    Body=html_body,
                    ContentType='text/html; charset=utf-8'
                )

                logger.info(f'HTML body processed and saved: {s3_path}')
                
                return {
                    'statusCode': 200,
                    'body': 'HTML body processed successfully.'
                }
            
            except Exception as e:
                logger.error(f'Error processing HTML body: {str(e)}')
                return {
                    'statusCode': 500,
                    'body': f'Error processing HTML body: {str(e)}'
                }
        else:
            logger.warning('No HTML body found in the email.')
            return {
                'statusCode': 404,
                'body': 'No HTML body found in the email.'
            }
