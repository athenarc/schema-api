from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse, OpenApiExample
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api_auth.auth import ApiTokenAuthentication
from api_auth.permissions import IsActive, IsUser
from files.serializers import FilesListQPSerializer, FileSerializer, \
    FileNamedSerializer, FileCreateSerializer, FileDetailsQPSerializer, FileRefSerializer, FileCreateQPSerializer, \
    FileMetadataSerializer
from files.services import S3BucketService

import csv, io, mimetypes  # File preview functionality
from rest_framework.exceptions import NotFound, ValidationError  # File preview functionality
from util.exceptions import ApplicationNotFoundError  # File preview functionality


class FilesListAPIView(APIView):
    authentication_classes = [ApiTokenAuthentication
                              ] if settings.USE_AUTH else []
    permission_classes = [IsAuthenticated, IsUser, IsActive
                          ] if settings.USE_AUTH else []

    @extend_schema(
        summary='List directory contents',
        description=
        'Endpoint that allows to list directories and files under a specific sub-directory. Also may list'
        'all files that are recursively found in the specified subdirectory\'s tree',
        tags=['Files'],
        parameters=[
            OpenApiParameter('subdir',
                             OpenApiTypes.STR,
                             OpenApiParameter.QUERY,
                             required=False,
                             allow_blank=False,
                             many=False),
            OpenApiParameter('recursive',
                             OpenApiTypes.STR,
                             OpenApiParameter.QUERY,
                             required=False,
                             allow_blank=True,
                             many=False)
        ],
        responses={
            200:
            OpenApiResponse(
                description=
                'Stderr of the task specified by the UUID are returned',
                response=FileNamedSerializer,
                examples=[
                    OpenApiExample(
                        'directories-and-files',
                        summary='Listing of files and directories',
                        value=[{
                            "type": "file",
                            "metadata": {
                                "size": 1258291200,
                                "ts_modified": "2024-04-13T20:41:08.419000Z"
                            },
                            "name": "bigfile.bin"
                        }, {
                            "type": "directory",
                            "metadata": {},
                            "name": "img/"
                        }, {
                            "type": "file",
                            "metadata": {
                                "size": 95,
                                "ts_modified": "2024-06-26T11:37:56.492000Z"
                            },
                            "name": "report.txt"
                        }, {
                            "type": "directory",
                            "metadata": {},
                            "name": "bin/"
                        }],
                        request_only=False,
                        response_only=True,
                    ),
                    OpenApiExample(
                        'recursive-listing-of-files',
                        summary='Recursive listing of files',
                        value=[{
                            "path": "directory0/directory1/stderr.log",
                            "metadata": {
                                "size": 27,
                                "ts_modified": "2024-07-04T07:57:19.087000Z"
                            }
                        }, {
                            "path": "directory0/output.txt",
                            "metadata": {
                                "size": 1196,
                                "ts_modified": "2024-06-25T09:05:23.853000Z"
                            }
                        }, {
                            "path": "upload.csv",
                            "metadata": {
                                "size": 21330,
                                "ts_modified": "2024-02-19T14:49:32.209000Z"
                            }
                        }],
                        request_only=False,
                        response_only=True,
                    )
                ]),
            400:
            OpenApiResponse(
                description=
                'Request was invalid. Response will contain information about potential errors in the '
                'request.'),
            401:
            OpenApiResponse(
                description=
                'Authentication failed. Perhaps no API token was provided in the `Authorization` header, '
                'or the API token was invalid.')
        })
    def get(self, request):
        qp_serializer = FilesListQPSerializer(data=request.query_params)
        qp_serializer.is_valid(raise_exception=True)
        qp_serializer_validated_data = qp_serializer.validated_data
        recursive = bool(qp_serializer_validated_data.pop('recursive', False))
        print(recursive)

        s3_service = S3BucketService(request.user)

        directory = s3_service.list_objects(**qp_serializer_validated_data)

        if recursive:
            return Response(data=FileSerializer(directory.walk(),
                                                many=True).data,
                            status=status.HTTP_200_OK)
        else:
            return Response(data=FileNamedSerializer(directory.contents,
                                                     many=True).data,
                            status=status.HTTP_200_OK)

    @extend_schema(
        summary='Create new file',
        description=
        'Endpoint that allows a new file to be created on the remote storage. This can be either through a '
        'file upload or copying an existing file',
        tags=['Files'],
        request=FileCreateSerializer,
        parameters=[
            OpenApiParameter('overwrite',
                             OpenApiTypes.BOOL,
                             OpenApiParameter.QUERY,
                             allow_blank=True,
                             required=False,
                             many=False)
        ],
        examples=[
            OpenApiExample(
                'copy',
                summary='Copy',
                description=
                'In this example a new file at `dir0/file1.txt` is created by requesting the copy of a '
                'file at `dir1/file0.txt`.',
                value={
                    "source": "dir1/file0.txt",
                    "path": "dir0/file1.txt"
                },
                request_only=True,
                response_only=False),
            OpenApiExample(
                'upload-small',
                summary='Simple upload request',
                description=
                'A request to upload a small file with a single upload URL',
                value={
                    "size": 21330,
                    "path": "upload.csv"
                },
                request_only=True,
                response_only=False),
            OpenApiExample(
                'upload-big',
                summary='Multi-part big upload request',
                description='A request to upload a big file in multiple parts',
                value={
                    "size": 1423053023,
                    "path": "data.bin"
                },
                request_only=True,
                response_only=False)
        ],
        responses={
            201:
            OpenApiResponse(
                description='New file successfully created',
                response=FileRefSerializer,
                examples=[
                    OpenApiExample(
                        'copied_file',
                        summary='Copied file path returned',
                        value={"path": "dir0/file1.txt"},
                        request_only=False,
                        response_only=True,
                    ),
                    OpenApiExample(
                        'single-part',
                        summary='Single file upload URL',
                        value={
                            "size": 21330,
                            "path": "files/upload.csv",
                            "upload_info": {
                                "type":
                                "simple",
                                "expiry":
                                "2024-01-01T14:46:24.804622",
                                "url":
                                "https://s3.hypatia-comp.athenarc.gr/9cb85312-cfd0-48d4-8839-f36b51265e10/files/"
                                "upload.csv?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=zYdRmGyKqKZrRd2W%2"
                                "F20240219%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20240219T144625Z&X-Amz-Ex"
                                "pires=86400&X-Amz-SignedHeaders=content-length%3Bhost&X-Amz-Signature=6e6de51d0"
                                "c13569282cec13c70d8b50b4088b137f4baee6efab57274f912b7fc"
                            }
                        },
                        request_only=False,
                        response_only=True),
                    OpenApiExample(
                        'multi-part',
                        summary='Multi-part upload URLs',
                        value={
                            "size": 230686720,
                            "path": "files/big_file.dat",
                            "upload_info": {
                                "type": "multipart",
                                "expiry": "2024-01-01T14:52:37.290554",
                                "urls": {
                                    "parts": [{
                                        "part":
                                        1,
                                        "url":
                                        "https://s3.hypatia-comp.athenarc.gr/9cb85312-cfd0-48d4-8839-f36b512"
                                        "65e10/files/big_file.dat?partNumber=1&uploadId=OWFkYTZiMTEtMDlmMi00"
                                        "NGMxLTg4MjgtNWFmNmMzZmMzNGFkLjM3MTI5YjE0LWJhZGEtNGVlZS05YjQzLWM3MTM"
                                        "zZjljY2EwOQ&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=zYdRm"
                                        "GyKqKZrRd2W%2F20240219%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=2"
                                        "0240219T145237Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=content-len"
                                        "gth%3Bhost&X-Amz-Signature=1ab96c54effa5618fc736a8033477a775ea998be"
                                        "aa9c678a231adab75a690ade",
                                        "n_bytes":
                                        104857600
                                    }, {
                                        "part":
                                        2,
                                        "url":
                                        "https://s3.hypatia-comp.athenarc.gr/9cb85312-cfd0-48d4-8839-f36b512"
                                        "65e10/files/big_file.dat?partNumber=2&uploadId=OWFkYTZiMTEtMDlmMi00"
                                        "NGMxLTg4MjgtNWFmNmMzZmMzNGFkLjM3MTI5YjE0LWJhZGEtNGVlZS05YjQzLWM3MTM"
                                        "zZjljY2EwOQ&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=zYdRm"
                                        "GyKqKZrRd2W%2F20240219%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=2"
                                        "0240219T145237Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=content-len"
                                        "gth%3Bhost&X-Amz-Signature=c7a21db4a8ad5d73d6599d6919f273543372b7dd"
                                        "d5247c87a01be458ccf4cff6",
                                        "n_bytes":
                                        104857600
                                    }, {
                                        "part":
                                        3,
                                        "url":
                                        "https://s3.hypatia-comp.athenarc.gr/9cb85312-cfd0-48d4-8839-f36b512"
                                        "65e10/files/big_file.dat?partNumber=3&uploadId=OWFkYTZiMTEtMDlmMi00"
                                        "NGMxLTg4MjgtNWFmNmMzZmMzNGFkLjM3MTI5YjE0LWJhZGEtNGVlZS05YjQzLWM3MTM"
                                        "zZjljY2EwOQ&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=zYdRm"
                                        "GyKqKZrRd2W%2F20240219%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=2"
                                        "0240219T145237Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=content-len"
                                        "gth%3Bhost&X-Amz-Signature=8b335fa92031c9e52bc66a2062623e8e0243151e"
                                        "20fdf04e5da55410fb6ffa7f",
                                        "n_bytes":
                                        20971520
                                    }],
                                    "finalize":
                                    "https://s3.hypatia-comp.athenarc.gr/9cb85312-cfd0-48d4-8839-f36b51265e"
                                    "10/files/big_file.dat?uploadId=OWFkYTZiMTEtMDlmMi00NGMxLTg4MjgtNWFmNmM"
                                    "zZmMzNGFkLjM3MTI5YjE0LWJhZGEtNGVlZS05YjQzLWM3MTMzZjljY2EwOQ&X-Amz-Algo"
                                    "rithm=AWS4-HMAC-SHA256&X-Amz-Credential=zYdRmGyKqKZrRd2W%2F20240219%2F"
                                    "us-east-1%2Fs3%2Faws4_request&X-Amz-Date=20240219T145237Z&X-Amz-Expire"
                                    "s=86400&X-Amz-SignedHeaders=host&X-Amz-Signature=d48e7ca7b6133729ec3dd"
                                    "a3c27d36aa16d3e862c6fe6288f8368c3e7c709ce3e"
                                }
                            }
                        },
                        request_only=False,
                        response_only=True),
                ]),
            400:
            OpenApiResponse(
                description=
                'Request was invalid. Response will contain information about potential errors in the '
                'request.'),
            401:
            OpenApiResponse(
                description=
                'Authentication failed. Perhaps no API token was provided in the `Authorization` header, '
                'or the API token was invalid.'),
            409:
            OpenApiResponse(
                description=
                'Target file already exists. Consider using overwrite')
        })
    def post(self, request):
        file_create_serializer = FileCreateSerializer(data=request.data)
        file_create_serializer.is_valid(raise_exception=True)
        validated_data = file_create_serializer.validated_data
        s3_service = S3BucketService(request.user)

        source = validated_data.pop('source', False)
        if source:
            qp_serializer = FileCreateQPSerializer(data=request.query_params)
            qp_serializer.is_valid(raise_exception=True)
            qp_serializer_validated_data = qp_serializer.validated_data

            file = s3_service.copy_object(
                source_path=source,
                destination_path=validated_data['path'],
                **qp_serializer_validated_data)
            file_ref_serializer = FileRefSerializer(file)
            return Response(status=status.HTTP_201_CREATED,
                            data=file_ref_serializer.data)
        else:
            upload_info = s3_service.issue_upload_urls(
                size=validated_data['size'], file_path=validated_data['path'])
            return Response(status=status.HTTP_201_CREATED,
                            data={
                                **validated_data, 'upload_info': upload_info
                            })


class FileDetailsAPIView(APIView):
    authentication_classes = [ApiTokenAuthentication
                              ] if settings.USE_AUTH else []
    permission_classes = [IsAuthenticated, IsUser, IsActive
                          ] if settings.USE_AUTH else []

    @extend_schema(
        summary='Retrieve file URL/metadata',
        description=
        'Endpoint that allows to retrieve metadata for an existing file or issue a download URL',
        tags=['Files'],
        parameters=[
            OpenApiParameter('action',
                             OpenApiTypes.BOOL,
                             OpenApiParameter.QUERY,
                             allow_blank=True,
                             required=False,
                             many=False,
                             enum=['download', 'stat']),
            OpenApiParameter('path',
                             OpenApiTypes.STR,
                             OpenApiParameter.PATH,
                             allow_blank=False,
                             required=True,
                             many=False)
        ],
        responses={
            200:
            OpenApiResponse(
                response=FileMetadataSerializer,
                examples=[
                    OpenApiExample(
                        'metadata-retrieved',
                        summary='Metadata retrieved',
                        value={
                            "size": 27,
                            "ts_modified": "2024-06-27T10:32:25Z"
                        },
                        request_only=False,
                        response_only=True,
                    ),
                    OpenApiExample(
                        'download-urls',
                        summary='Download URL issued',
                        value={
                            "path":
                            "files/big_file.dat",
                            "expiry":
                            "2024-02-21T17:22:37.602298",
                            "url":
                            "https://s3.hypatia-comp.athenarc.gr/9cb85312-cfd0-48d4-8839-f36b51265e10/files/big_"
                            "file.dat?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=zYdRmGyKqKZrRd2W%2F20240"
                            "220%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20240220T172238Z&X-Amz-Expires=86400"
                            "&X-Amz-SignedHeaders=host&X-Amz-Signature=74667bda38320889b0453374c0b8c02c3578f8ef2"
                            "5a28d99f7663b8b3cdd9d2b"
                        })
                ]),
            400:
            OpenApiResponse(
                description=
                'Request was invalid. Response will contain information about potential errors in the '
                'request.'),
            401:
            OpenApiResponse(
                description=
                'Authentication failed. Perhaps no API token was provided in the `Authorization` header, '
                'or the API token was invalid.'),
            404:
            OpenApiResponse(description='Provided file path wasn\'t found')
        })
    def get(self, request, path):
        qp_serializer = FileDetailsQPSerializer(data=request.query_params)
        qp_serializer.is_valid(raise_exception=True)
        qp_serializer_validated_data = qp_serializer.validated_data

        s3_service = S3BucketService(request.user)

        if qp_serializer_validated_data['action'] == 'stat':
            file_metadata = s3_service.retrieve_object(path)
            file_serializer = FileMetadataSerializer(file_metadata)
            return Response(data=file_serializer.data,
                            status=status.HTTP_200_OK)
        else:
            download_info = s3_service.issue_download_urls(path)

            return Response(status=status.HTTP_200_OK,
                            data={
                                'path': path,
                                **download_info
                            })

    @extend_schema(
        summary='Delete a file',
        description='Endpoint that allows to delete a file',
        tags=['Files'],
        responses={
            204:
            OpenApiResponse(description='File was successfully deleted'),
            400:
            OpenApiResponse(
                description=
                'Request was invalid. Response will contain information about potential errors in the '
                'request.'),
            401:
            OpenApiResponse(
                description=
                'Authentication failed. Perhaps no API token was provided in the `Authorization` header, '
                'or the API token was invalid.'),
            404:
            OpenApiResponse(description='Provided file path wasn\'t found')
        })
    def delete(self, request, path):
        s3_service = S3BucketService(request.user)

        s3_service.delete_object(path)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary='Move/rename a file',
        description='Endpoint that allows to rename/move an existing file',
        tags=['Files'],
        request=FileRefSerializer,
        parameters=[
            OpenApiParameter('overwrite',
                             OpenApiTypes.BOOL,
                             OpenApiParameter.QUERY,
                             allow_blank=True,
                             required=False,
                             many=False),
            OpenApiParameter('source',
                             OpenApiTypes.STR,
                             OpenApiParameter.PATH,
                             allow_blank=False,
                             required=True,
                             many=False)
        ],
        examples=[
            OpenApiExample(
                'move',
                summary='Move',
                description=
                'In this example a file is moved to `dir1/file0.txt`.',
                value={"path": "dir1/file0.txt"},
                request_only=True,
                response_only=False)
        ],
        responses={
            202:
            OpenApiResponse(description='File successfully moved',
                            response=FileRefSerializer,
                            examples=[
                                OpenApiExample(
                                    'moved_file',
                                    summary='Moved file path returned',
                                    value={"path": "dir0/file1.txt"},
                                    request_only=False,
                                    response_only=True,
                                ),
                            ]),
            400:
            OpenApiResponse(
                description=
                'Request was invalid. Response will contain information about potential errors in the '
                'request.'),
            401:
            OpenApiResponse(
                description=
                'Authentication failed. Perhaps no API token was provided in the `Authorization` header, '
                'or the API token was invalid.'),
            404:
            OpenApiResponse(description='Provided source file wasn\'t found'),
            409:
            OpenApiResponse(
                description=
                'Target file already exists. Consider using overwrite')
        })
    def patch(self, request, path):
        qp_serializer = FileCreateQPSerializer(data=request.query_params)
        qp_serializer.is_valid(raise_exception=True)
        qp_serializer_validated_data = qp_serializer.validated_data

        file_update_serializer = FileRefSerializer(data=request.data)
        file_update_serializer.is_valid(raise_exception=True)
        validated_data = file_update_serializer.validated_data
        s3_service = S3BucketService(request.user)

        file = s3_service.move_object(path, validated_data['path'],
                                      **qp_serializer_validated_data)
        file_serializer = FileRefSerializer(file)
        return Response(status=status.HTTP_202_ACCEPTED,
                        data=file_serializer.data)


class FilePreviewAPIView(APIView):
    authentication_classes = [ApiTokenAuthentication
                              ] if settings.USE_AUTH else []
    permission_classes = [IsAuthenticated, IsUser, IsActive
                          ] if settings.USE_AUTH else []

    @extend_schema(
        summary='Preview a file (image or CSV)',
        description=
        'Returns a presigned URL for image preview or the first few rows of a CSV file.',
        tags=['Files'],
        parameters=[
            OpenApiParameter('path',
                             OpenApiTypes.STR,
                             OpenApiParameter.QUERY,
                             required=True),
        ],
        responses={
            200: OpenApiResponse(description='Preview data returned'),
            400: OpenApiResponse(description='Invalid input'),
            404:
            OpenApiResponse(description='File not found or not supported'),
        })
    def get(self, request):
        file_path = request.query_params.get('path')
        if not file_path:
            raise ValidationError("`path` query parameter is required.")

        s3_service = S3BucketService(request.user)

        try:
            metadata = s3_service._stat_object(file_path)
        except ApplicationNotFoundError:
            raise NotFound(f"File not found at {file_path}.")

        mimetype, _ = mimetypes.guess_type(file_path)
        mimetype = mimetype or metadata.get("ContentType")

        def is_probably_csv(data: bytes) -> bool:
            try:
                sample = data[:1024].decode('utf-8', errors='ignore')
                return ',' in sample and '\n' in sample
            except Exception:
                return False

        if mimetype and mimetype.startswith('image/'):
            url = s3_service.issue_download_urls(file_path)['url']
            return Response({"type": "image", "url": url})

        raw_bytes = None
        is_csv = mimetype in ['text/csv', 'application/vnd.ms-excel']

        if not is_csv and (mimetype is None
                           or mimetype == 'application/octet-stream'):
            raw_bytes = s3_service.retrieve_object_bytes(file_path)
            is_csv = is_probably_csv(raw_bytes)

        if is_csv:
            if raw_bytes is None:
                raw_bytes = s3_service.retrieve_object_bytes(file_path)

            try:
                text_io = io.StringIO(raw_bytes.decode('utf-8'))
                reader = csv.reader(text_io)
                preview_data = [row for _, row in zip(range(10), reader)]
            except Exception as e:
                raise ValidationError(f"Failed to parse CSV: {str(e)}")

            return Response({"type": "csv", "preview": preview_data})

        raise NotFound("Preview not supported for this file type.")
