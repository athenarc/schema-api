import datetime
import os

import boto3
from botocore.exceptions import ClientError
from django.conf import settings

from api_auth.constants import AuthEntityType
from api_auth.models import AuthEntity
from files.models import Directory, File, FileMetadata
from util.exceptions import ApplicationError, ApplicationNotFoundError, ApplicationDuplicateError

import zipfile  # File unzip functionality
import tempfile  # File unzip functionality
import shutil  # File unzip functionality
import logging  # File unzip functionality
from zipfile import BadZipFile  # Catch malformed zip files

logger = logging.getLogger(
    __name__)  # File unzip -> logging progress functionality


class S3BucketService:

    def __init__(self, auth_entity: AuthEntity):
        if auth_entity.entity_type != AuthEntityType.USER:
            raise ApplicationError(
                f'{self.__class__.__name__} depends on an AuthEntity of type {AuthEntityType.USER}'
            )
        self.auth_entity = auth_entity
        self.bucket = str(self.auth_entity.uuid)

        self.s3_client = boto3.client(
            's3',
            endpoint_url=settings.S3['URL'],
            aws_access_key_id=settings.S3['ACCESS_KEY_ID'],
            aws_secret_access_key=settings.S3['SECRET_ACCESS_KEY'],
            config=boto3.session.Config(signature_version='s3v4'),
            verify=settings.S3['USE_SSL'],
            use_ssl=settings.S3['USE_SSL'])
        self._create_bucket_if_not_exists()

    def _create_bucket_if_not_exists(self):
        try:
            self.s3_client.head_bucket(Bucket=self.bucket)
        except ClientError as ex:
            if ex.response['Error']['Code'] == '404':
                self.s3_client.create_bucket(Bucket=self.bucket)
            else:
                raise

    def _normalize_path(self, path: str) -> str:
        normalized_path = os.path.normpath(path).lstrip('/')
        return '' if normalized_path == '.' else normalized_path

    def _stat_object(self, key: str) -> File:
        try:
            response = self.s3_client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as ce:
            if ce.response['Error']['Code'] == '404':
                raise ApplicationNotFoundError(
                    f'File `{key}` does not exist') from ce
            raise
        d = Directory()
        metadata = FileMetadata(size=response['ContentLength'],
                                ts_modified=response['LastModified'])
        return d.create_entity_on_path(File, key, metadata=metadata)

    def issue_upload_urls(self, size: int, file_path: str):
        key = self._normalize_path(file_path)

        validity_period_seconds = settings.S3['VALIDITY_PERIOD_SECONDS']
        validity_period = datetime.timedelta(seconds=validity_period_seconds)

        current_ref_ts = datetime.datetime.now()
        expiry = current_ref_ts + validity_period

        max_part_size = settings.S3['MAX_PART_SIZE_BYTES']

        if size > max_part_size:

            part_sizes = [max_part_size] * (size // max_part_size) + [
                size % max_part_size
            ]

            # Issue new multipart upload creation=
            response = self.s3_client.create_multipart_upload(
                Bucket=self.bucket, Key=key, Expires=expiry)

            upload_id = response['UploadId']

            urls = []
            for i in range(len(part_sizes)):
                url = self.s3_client.generate_presigned_url(
                    ClientMethod='upload_part',
                    Params={
                        'Bucket': self.bucket,
                        'Key': key,
                        'PartNumber': i + 1,
                        'UploadId': upload_id,
                        'ContentLength': part_sizes[i]
                    },
                    ExpiresIn=validity_period_seconds)
                urls.append({
                    'part': i + 1,
                    'url': url,
                    'n_bytes': part_sizes[i]
                })

            complete_url = self.s3_client.generate_presigned_url(
                ClientMethod='complete_multipart_upload',
                Params={
                    'Bucket': self.bucket,
                    'Key': key,
                    'UploadId': upload_id
                },
                ExpiresIn=validity_period_seconds)
            return {
                'type': 'multipart',
                'expiry': expiry,
                'urls': {
                    'parts': urls,
                    'finalize': complete_url
                }
            }
        url = self.s3_client.generate_presigned_url(
            ClientMethod='put_object',
            Params={
                'Bucket': self.bucket,
                'Key': key,
                'ContentLength': size
            },
            ExpiresIn=validity_period_seconds)
        return {'type': 'simple', 'expiry': expiry, 'url': url}

    def issue_download_urls(self, file_path: str):
        key = self._normalize_path(file_path)
        validity_period_seconds = settings.S3['VALIDITY_PERIOD_SECONDS']
        validity_period = datetime.timedelta(seconds=validity_period_seconds)
        current_ref_ts = datetime.datetime.now()
        expiry = current_ref_ts + validity_period
        self._stat_object(key)
        return {
            'expiry':
            expiry,
            'url':
            self.s3_client.generate_presigned_url(
                ClientMethod='get_object',
                Params={
                    'Bucket': self.bucket,
                    'Key': key
                },
                ExpiresIn=validity_period_seconds)
        }

    def list_objects(self, subdir: str = '.') -> Directory:
        prefix = self._normalize_path(subdir)
        if prefix:
            prefix += '/'

        paginator = self.s3_client.get_paginator('list_objects_v2')
        operation_parameters = {
            'Bucket': self.bucket,
            'Prefix': prefix,
        }

        page_iterator = paginator.paginate(**operation_parameters)

        directory = Directory()
        for page in page_iterator:
            for obj in page.get('Contents', []):
                metadata = FileMetadata(size=obj['Size'],
                                        ts_modified=obj['LastModified'])
                directory.create_entity_on_path(
                    File,
                    obj['Key'][len(prefix):] if prefix != ''
                    and obj['Key'].startswith(prefix) else obj['Key'],
                    metadata=metadata)

        return directory

    # - same as copy and delete
    def move_object(self,
                    old_path: str,
                    new_path: str,
                    overwrite=False) -> File:
        old_key = self._normalize_path(old_path)
        new_key = self._normalize_path(new_path)

        file = self.copy_object(old_key, new_key, overwrite=overwrite)
        try:
            self.delete_object(old_path)
        except ApplicationNotFoundError:
            # Don't care if previous object has been already erased (perhaps from a race condition)
            pass
        return file

    def retrieve_object(self, path: str) -> FileMetadata:
        key = self._normalize_path(path)
        return self._stat_object(key).metadata

    def retrieve_object_bytes(self, path: str) -> bytes:
        key = self._normalize_path(path)
        response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
        return response['Body'].read()

    def delete_object(self, path: str) -> None:
        key = self._normalize_path(path)
        self._stat_object(key)
        self.s3_client.delete_object(Bucket=self.bucket, Key=key)

    def copy_object(self,
                    source_path: str,
                    destination_path: str,
                    overwrite: bool = False) -> File:
        source_key = self._normalize_path(source_path)
        destination_key = self._normalize_path(destination_path)

        if not overwrite:
            found = False
            try:
                self._stat_object(destination_key)
                found = True
            except ApplicationNotFoundError:
                pass
            if found:
                raise ApplicationDuplicateError({
                    'destination':
                    f'File `{destination_key}` already exists'
                })

        try:
            self.s3_client.copy_object(Bucket=self.bucket,
                                       CopySource={
                                           'Bucket': self.bucket,
                                           'Key': source_key
                                       },
                                       Key=destination_key)
        except ClientError as ce:
            if ce.response['Error']['Code'] == 'NoSuchKey':
                raise ApplicationNotFoundError(
                    f'File `{source_key}` does not exist') from ce
            raise

        return Directory().create_entity_on_path(File, destination_key)


    def unzip_file_to_s3_folder(self,
                                zip_path: str,
                                destination_folder: str = "",
                                progress_callback=None) -> list[dict]:
        """
        Unzips a zip file stored in S3 to a temporary directory and uploads its contents to S3.
        Returns metadata of uploaded files. Optionally accepts a progress callback.

        :param zip_path: Path to the .zip file in S3
        :param destination_folder: Destination path prefix in S3
        :param progress_callback: Optional callable(metadata_dict, index, total)
        :return: List of metadata dicts with 's3_path', 'filename', 'size'
        """
        key = self._normalize_path(zip_path)
        destination_folder = self._normalize_path(destination_folder)

        uploaded_files_metadata = []

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_tmp_path = os.path.join(tmpdir, "archive.zip")

            logger.info(f"Downloading zip from S3: {zip_path}")
            try:
                with open(zip_tmp_path, "wb") as f:
                    response = self.s3_client.get_object(Bucket=self.bucket,
                                                        Key=key)
                    shutil.copyfileobj(response["Body"], f)
            except ClientError as ce:
                if ce.response["Error"]["Code"] == "NoSuchKey":
                    raise ApplicationNotFoundError(
                        f"File `{key}` does not exist") from ce
                raise

            logger.info(f"Extracting zip file to temp dir: {tmpdir}")
            try:
                with zipfile.ZipFile(zip_tmp_path, 'r') as zip_ref:
                    zip_ref.extractall(tmpdir)
            except BadZipFile as e:
                logger.error(f"File at {zip_path} is not a valid ZIP archive: {e}")
                raise ValueError(
                    "The provided file is not a valid zip archive.") from e

            # Collect all files first
            extracted_files = []
            for root, _, files in os.walk(tmpdir):
                for file_name in files:
                    abs_file_path = os.path.join(root, file_name)
                    if abs_file_path != zip_tmp_path:
                        extracted_files.append(abs_file_path)

            total_files = len(extracted_files)
            logger.info(f"Uploading {total_files} extracted files to S3...")

            for index, abs_file_path in enumerate(extracted_files, start=1):
                rel_path = os.path.relpath(abs_file_path,
                                        tmpdir).replace("\\", "/")
                s3_key = os.path.join(destination_folder,
                                    rel_path).replace("\\", "/")

                with open(abs_file_path, "rb") as f:
                    file_size = os.path.getsize(abs_file_path)
                    self.s3_client.put_object(Bucket=self.bucket,
                                            Key=s3_key,
                                            Body=f)

                metadata = {
                    "s3_path": s3_key,
                    "filename": os.path.basename(abs_file_path),
                    "size": file_size
                }
                uploaded_files_metadata.append(metadata)

                logger.info(
                    f"[{index}/{total_files}] Uploaded: {s3_key} ({file_size} bytes)"
                )

                if progress_callback:
                    try:
                        progress_callback(metadata, index, total_files)
                    except Exception as e:
                        logger.warning(f"Progress callback failed: {e}")

        logger.info("Unzip and upload complete.")
        return uploaded_files_metadata
