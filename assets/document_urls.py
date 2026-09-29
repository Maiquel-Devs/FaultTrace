from django.urls import path

from . import views


urlpatterns = [
    path("", views.document_list, name="document_list"),
    path("new/", views.document_create, name="document_create"),
    path("<int:pk>/", views.document_detail, name="document_detail"),
    path("<int:pk>/file/", views.document_download, name="document_download"),
    path("<int:pk>/reindex/", views.document_reindex, name="document_reindex"),
]
