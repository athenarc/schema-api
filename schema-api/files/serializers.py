from rest_framework import serializers

from files.models import File, Directory
from util.serializers import OmitEmptyValuesMixin


class FilesListQPSerializer(serializers.Serializer):
    subdir = serializers.CharField(default='')
    recursive = serializers.BooleanField(default=False)

    def to_internal_value(self, data):
        if data.get('recursive', 'EMPTY') == '':
            data = data.copy()
            data['recursive'] = True
        # Convert query params to a dict
        internal_data = super().to_internal_value(data)

        return internal_data


class FileDetailsQPSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['stat', 'download'], default='stat')


class FileCreateQPSerializer(serializers.Serializer):
    overwrite = serializers.BooleanField(default=False)

    def to_internal_value(self, data):
        if data.get('overwrite', 'EMPTY') == '':
            data = data.copy()
            data['overwrite'] = True
        # Convert query params to a dict
        internal_data = super().to_internal_value(data)

        return internal_data


class FilesystemEntityTypeSerializerMixin(serializers.Serializer):
    type = serializers.SerializerMethodField()

    def get_type(self, obj):
        if issubclass(obj.__class__, File):
            return 'file'
        elif issubclass(obj.__class__, Directory):
            return 'directory'
        return 'unknown'


class FileRefSerializer(serializers.Serializer):
    path = serializers.CharField()


class GenericMetadataSerializer(OmitEmptyValuesMixin, serializers.Serializer):
    size = serializers.IntegerField(required=False)
    ts_created = serializers.DateTimeField(required=False)
    ts_modified = serializers.DateTimeField(required=False)


class FileMetadataSerializer(GenericMetadataSerializer):
    pass


class DirectoryMetadataSerializer(GenericMetadataSerializer):
    pass


class FileCreateSerializer(FileRefSerializer):
    size = serializers.IntegerField(required=False, write_only=True)
    source = serializers.CharField(required=False, write_only=True)

    def validate(self, data):
        data = super().validate(data)

        if not data.get('size') and not data.get('source'):
            raise serializers.ValidationError("Either `size` must be provided (for upload URLs) or `source` (for copy)")
        return data


class FileSerializer(FileRefSerializer):
    metadata = GenericMetadataSerializer()


class FileNamedSerializer(FilesystemEntityTypeSerializerMixin, FileSerializer):
    path = None
    name = serializers.SerializerMethodField()

    def get_name(self, obj):
        name = getattr(obj, 'name', None)
        if issubclass(obj.__class__, Directory):
            name += '/'
        return name

class UnzipRequestSerializer(serializers.Serializer):
    zip_path = serializers.CharField()
    destination_path = serializers.CharField()