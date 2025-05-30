from django.urls import path

from files.views import FilesListAPIView, FileDetailsAPIView, FilePreviewAPIView

urlpatterns = [
    path('files', FilesListAPIView.as_view(), name='files_list'),
    path('files/preview', FilePreviewAPIView.as_view(), name='file-preview'),
    path('files/<path:path>', FileDetailsAPIView.as_view(), name='file_details'),
]