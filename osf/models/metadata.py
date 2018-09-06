# -*- coding: utf-8 -*-
from django.db import models

from addons.osfstorage.models import OsfStorageFile
from osf.models.base import BaseModel, ObjectIDMixin
from osf.models.metaschema import FileMetadataSchema
from osf.utils.datetime_aware_jsonfield import DateTimeAwareJSONField
from osf.utils.metadata.serializers import serializer_registry


class FileMetadataRecord(ObjectIDMixin, BaseModel):

    metadata = DateTimeAwareJSONField(default=dict, blank=True)

    file = models.ForeignKey(OsfStorageFile, related_name='records', on_delete=models.SET_NULL, null=True)
    schema = models.ForeignKey(FileMetadataSchema, related_name='records', on_delete=models.SET_NULL, null=True)

    class Meta:
        unique_together = ('file', 'schema')

    def serialize(self):
        return serializer_registry[self.schema_id].serialize(self)

    def validate(self, proposed_metadata):
        # {
        #     'funderName': 'LJAF',
        #     'geolocation': 'Earth'
        # }
        if serializer_registry[self.schema_id].validate(self, proposed_metadata):
            self.metadata = proposed_metadata
            self.save()