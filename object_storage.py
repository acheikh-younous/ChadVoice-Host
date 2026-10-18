"""Private S3-compatible audio storage: Neon Object Storage or Cloudflare R2.
Local filesystem remains available only for local development.
"""
import os
from pathlib import Path
from functools import lru_cache

MODE=os.getenv('CHADVOICE_AUDIO_STORAGE','local').strip().lower()

def bucket_name():
    return os.environ.get('S3_BUCKET') or os.environ.get('R2_BUCKET') or ''

@lru_cache(maxsize=1)
def r2_client():
    """Legacy function name; returns a generic private S3 client."""
    import boto3
    from botocore.config import Config
    if MODE=='s3':
        endpoint=os.getenv('AWS_ENDPOINT_URL_S3') or os.getenv('S3_ENDPOINT_URL')
        access=os.getenv('AWS_ACCESS_KEY_ID')
        secret=os.getenv('AWS_SECRET_ACCESS_KEY')
        region=os.getenv('AWS_REGION','us-east-2')
    elif MODE=='r2':
        account=os.getenv('R2_ACCOUNT_ID','')
        endpoint=os.getenv('R2_ENDPOINT_URL') or (f'https://{account}.r2.cloudflarestorage.com' if account else None)
        access=os.getenv('R2_ACCESS_KEY_ID')
        secret=os.getenv('R2_SECRET_ACCESS_KEY')
        region='auto'
    else:
        raise RuntimeError('S3 storage mode is not configured')
    if not all((endpoint,access,secret,bucket_name())):
        raise RuntimeError('Private object storage is missing endpoint, key, secret or bucket.')
    return boto3.client('s3',endpoint_url=endpoint,
        region_name=region,aws_access_key_id=access,aws_secret_access_key=secret,
        config=Config(signature_version='s3v4',s3={'addressing_style':'path'},
                      retries={'max_attempts':3,'mode':'standard'}))

def object_key(filename):return 'audio/'+filename

def store_audio_bytes(dest:Path,filename,content:bytes,mime:str):
    if MODE in ('r2','s3'):
        r2_client().put_object(Bucket=bucket_name(),Key=object_key(filename),Body=content,ContentType=mime)
    else:
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(content)

def remove_audio_file(dest:Path,filename):
    if MODE in ('r2','s3'):
        r2_client().delete_object(Bucket=bucket_name(),Key=object_key(filename))
    else:dest.unlink(missing_ok=True)

def audio_response_payload(filename,range_header=None):
    if MODE not in ('r2','s3'):raise ValueError('Not in object storage mode')
    args={'Bucket':bucket_name(),'Key':object_key(filename)}
    if range_header:
        import re
        if not re.fullmatch(r'bytes=\d*-\d*',range_header):return None
        args['Range']=range_header
    try:
        res=r2_client().get_object(**args)
    except Exception as exc:
        if 'InvalidRange' in str(exc):return None
        raise
    return res

def iter_body(body):
    try:
        while True:
            block=body.read(256*1024)
            if not block: break
            yield block
    finally:body.close()

def copy_audio_to_zip(archive,filename,zip_name,local_root):
    if MODE in ('r2','s3'):
        body=r2_client().get_object(Bucket=bucket_name(),Key=object_key(filename))['Body']
        with archive.open(zip_name,'w') as out:
            for block in iter_body(body):out.write(block)
    else:
        fp=local_root/filename
        if fp.is_file():archive.write(fp,zip_name)
