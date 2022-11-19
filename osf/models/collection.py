import logging

from dirtyfields import DirtyFieldsMixin
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models
from django.utils import timezone
from guardian.models import GroupObjectPermissionBase, UserObjectPermissionBase
from framework.celery_tasks.handlers import enqueue_task

from osf.models.base import BaseModel, GuidMixin
from osf.models.collection_submission import CollectionSubmission
from osf.models.mixins import GuardianMixin
from osf.models.validators import validate_title
from osf.utils.fields import NonNaiveDateTimeField
from osf.utils.permissions import ADMIN
from osf.utils.workflows import CollectionSubmissionStates
from osf.exceptions import NodeStateError
from website.util import api_v2_url


logger = logging.getLogger(__name__)


class Collection(DirtyFieldsMixin, GuidMixin, BaseModel, GuardianMixin):
    groups = {
        'read': ('read_collection', ),
        'write': ('read_collection', 'write_collection', ),
        'admin': ('read_collection', 'write_collection', 'admin_collection', )
    }
    group_format = 'collections_{self.id}_{group}'

    class Meta:
        permissions = (
            ('read_collection', 'Read Collection'),
            ('write_collection', 'Write Collection'),
            ('admin_collection', 'Admin Collection'),
        )

    provider = models.ForeignKey('AbstractProvider', blank=True, null=True, on_delete=models.CASCADE)
    creator = models.ForeignKey('OSFUser', on_delete=models.CASCADE)
    guid_links = models.ManyToManyField('Guid', through=CollectionSubmission, related_name='collections')
    collected_types = models.ManyToManyField(
        'contenttypes.ContentType',
        related_name='+',
        limit_choices_to={
            'model__in': ['abstractnode', 'basefilenode', 'collection', 'preprint']
        })
    title = models.CharField(max_length=200, validators=[validate_title])
    collected_type_choices = ArrayField(models.CharField(max_length=127), blank=True, default=list)
    status_choices = ArrayField(models.CharField(max_length=127), blank=True, default=list)
    volume_choices = ArrayField(models.CharField(max_length=127), blank=True, default=list)
    issue_choices = ArrayField(models.CharField(max_length=127), blank=True, default=list)
    program_area_choices = ArrayField(models.CharField(max_length=127), blank=True, default=list)
    school_type_choices = ArrayField(models.CharField(max_length=127), blank=True, default=list)
    study_design_choices = ArrayField(models.CharField(max_length=127), blank=True, default=list)
    is_public = models.BooleanField(default=False, db_index=True)
    is_promoted = models.BooleanField(default=False, db_index=True)
    is_bookmark_collection = models.BooleanField(default=False, db_index=True)
    deleted = NonNaiveDateTimeField(null=True, blank=True)

    def __unicode__(self):
        return '{self.title!r}, with guid {self._id!r}'.format(self=self)

    @property
    def url(self):
        return '/{}/'.format(self._id)

    @property
    def active_collection_submissions(self):
        return CollectionSubmission.objects.filter(
            collection=self
        ).exclude(
            machine_state__in=[
                CollectionSubmissionStates.REMOVED,
                CollectionSubmissionStates.REJECTED
            ],
        )

    @property
    def active_guids(self):
        return self.active_collection_submissions.values('guid')

    def get_absolute_url(self):
        return self.absolute_api_v2_url

    @property
    def absolute_api_v2_url(self):
        return api_v2_url('/collections{}'.format(self.url))

    @property
    def linked_nodes_self_url(self):
        return '{}relationships/linked_nodes/'.format(self.absolute_api_v2_url)

    @property
    def linked_registrations_self_url(self):
        return '{}relationships/linked_registrations/'.format(self.absolute_api_v2_url)

    @property
    def linked_preprints_self_url(self):
        return '{}relationships/linked_preprints/'.format(self.absolute_api_v2_url)

    @property
    def linked_preprints_related_url(self):
        return '{}linked_preprints/'.format(self.absolute_api_v2_url)

    @property
    def linked_nodes_related_url(self):
        return '{}linked_nodes/'.format(self.absolute_api_v2_url)

    @property
    def linked_registrations_related_url(self):
        return '{}linked_registrations/'.format(self.absolute_api_v2_url)

    @classmethod
    def bulk_update_search(cls, collection_submissions, op='update', index=None):
        from website import search
        try:
            search.search.bulk_update_collected_metadata(collection_submissions, op=op, index=index)
        except search.exceptions.SearchUnavailableError as e:
            logger.exception(e)

    def save(self, *args, **kwargs):
        first_save = self.id is None
        if self.is_bookmark_collection:
            if first_save and self.creator.collection_set.filter(is_bookmark_collection=True, deleted__isnull=True).exists():
                raise IntegrityError('Each user cannot have more than one Bookmark collection.')
            if self.title != 'Bookmarks':
                # Bookmark collections are always named 'Bookmarks'
                self.title = 'Bookmarks'
        saved_fields = self.get_dirty_fields() or []
        ret = super(Collection, self).save(*args, **kwargs)

        if first_save:
            # Set defaults for M2M
            content_type = ContentType.objects.filter(
                app_label='osf',
                model__in=['abstractnode', 'collection', 'preprint']
            )

            self.collected_types.add(*content_type)

        # Set up initial permissions
            self.update_group_permissions()
            self.get_group(ADMIN).user_set.add(self.creator)

        elif 'is_public' in saved_fields:
            from website.collections.tasks import on_collection_updated
            enqueue_task(on_collection_updated.s(self._id))

        return ret

    def has_permission(self, user, perm):
        return user.has_perms(self.groups[perm], self)

    def collect_object(
            self, obj, collector, collected_type=None, status=None, volume=None, issue=None,
            program_area=None, school_type=None, study_design=None):
        """ Adds object to collection, creates CollectionSubmission reference
            Performs type / metadata validation. User permissions checked in view.

        :param GuidMixin obj: Object to collect. Must be of a ContentType specified in collected_types
        :param OSFUser collector: User doing the collecting
        :param str collected_type: Metadata "type" of submission, validated against collected_type_choices
        :param str status: Metadata "status" of submission, validated against status_choices
        :return: CollectionSubmission object or raise exception
        """
        collected_type = collected_type or ''
        status = status or ''
        volume = volume or ''
        issue = issue or ''
        program_area = program_area or ''
        school_type = school_type or ''
        study_design = study_design or ''

        if not self.collected_type_choices and collected_type:
            raise ValidationError('May not specify "type" for this collection')

        if not self.status_choices and status:
            raise ValidationError('May not specify "status" for this collection')

        if not self.volume_choices and volume:
            raise ValidationError('May not specify "volume" for this collection')

        if not self.issue_choices and issue:
            raise ValidationError('May not specify "issue" for this collection')

        if not self.program_area_choices and program_area:
            raise ValidationError('May not specify "program_area" for this collection')

        if self.collected_type_choices and collected_type not in self.collected_type_choices:
            raise ValidationError('"{}" is not an acceptable "type" for this collection'.format(collected_type))

        if self.status_choices and status not in self.status_choices:
            raise ValidationError('"{}" is not an acceptable "status" for this collection'.format(status))

        if self.volume_choices and volume not in self.volume_choices:
            raise ValidationError('"{}" is not an acceptable "volume" for this collection'.format(volume))

        if self.issue_choices and issue not in self.issue_choices:
            raise ValidationError('"{}" is not an acceptable "issue" for this collection'.format(issue))

        if self.program_area_choices and program_area not in self.program_area_choices:
            raise ValidationError('"{}" is not an acceptable "program_area" for this collection'.format(program_area))

        if school_type:
            if not self.school_type_choices:
                raise ValidationError('May not specify "school_type" for this collection')
            elif school_type not in self.school_type_choices:
                raise ValidationError(f'"{school_type}" is not an acceptable "school_type" for this collection')

        if study_design:
            if not self.study_design_choices:
                raise ValidationError('May not specify "school_type" for this collection')
            elif study_design not in self.study_design_choices:
                raise ValidationError(f'"{study_design}" is not an acceptable "study_design" for this collection')

        if not any([isinstance(obj, t.model_class()) for t in self.collected_types.all()]):
            # Not all objects have a content_type_pk, have to look the other way.
            # Ideally, all objects would, and we could do:
            #   self.content_types.filter(id=obj.content_type_pk).exists()
            raise ValidationError('"{}" is not an acceptable "ContentType" for this collection'.format(ContentType.objects.get_for_model(obj).model))

        # Unique together -- self and guid
        if self.collectionsubmission_set.filter(guid=obj.guids.first()).exists():
            raise ValidationError('Object already exists in collection.')

        collection_submission = self.collectionsubmission_set.create(guid=obj.guids.first(), creator=collector)
        collection_submission.collected_type = collected_type
        collection_submission.status = status
        collection_submission.volume = volume
        collection_submission.issue = issue
        collection_submission.program_area = program_area
        collection_submission.school_type = school_type
        collection_submission.study_design = study_design
        collection_submission.save()

        return collection_submission

    def remove_object(self, obj, auth):
        """ Removes object from collection

        :param obj: object to remove from collection, if it exists. Acceptable types- CollectionSubmission, GuidMixin
        """
        if isinstance(obj, CollectionSubmission):
            if obj.collection != self:
                raise ValueError(f'Resource [{obj.guid._id}] is not part of collection {self._id}')
        else:
            # assume that we were passed the collected resource
            try:
                obj = self.collectionsubmission_set.get(guid=obj.guids.first())
            except CollectionSubmission.DoesNotExist:
                raise ValueError(f'Resource [{obj.guid._id}] is not part of collection {self._id}')

        obj.remove(user=auth.user, comment='Implict removal via remove_object', force=True)

    def delete(self):
        """ Mark collection as deleted
        """
        if self.is_bookmark_collection:
            # Not really the right exception to raise, but it's for back-compatibility
            # TODO: Use a more correct exception and catch it in the necessary places
            raise NodeStateError('Bookmark collections may not be deleted.')

        self.deleted = timezone.now()

        if self.is_public:
            self.bulk_update_search(list(self.collectionsubmission_set.all()), op='delete')

        self.save()


class CollectionUserObjectPermission(UserObjectPermissionBase):
    content_object = models.ForeignKey(Collection, on_delete=models.CASCADE)


class CollectionGroupObjectPermission(GroupObjectPermissionBase):
    content_object = models.ForeignKey(Collection, on_delete=models.CASCADE)
