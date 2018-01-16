# -*- coding: utf-8 -*-
# Generated by Django 1.11.7 on 2018-01-16 19:22
from __future__ import unicode_literals

import logging

from framework.auth import Auth
from osf.models import AbstractNode, Comment, Guid
from osf.models.base import Guid
from addons.wiki.models import NodeWikiPage, WikiPage, WikiVersion
from addons.wiki.utils import to_mongo_key
from django.db import migrations
logger = logging.getLogger(__name__)

def repoint_id(guid, old_wiki_page):
    old_wiki_page._id = guid._id
    old_wiki_page.save()
    return

def reverse_func(apps, schema_editor):
    for node in AbstractNode.objects.all():
        for wiki in node.wikis.filter(is_deleted=False):
            old_wiki_page_ids = node.wiki_pages_versions[to_mongo_key(wiki.page_name)]
            old_wiki_pages = NodeWikiPage.objects.filter(node_id=node.id, page_name=wiki.page_name).order_by('version')
            for index, old_wiki_page_id in enumerate(old_wiki_page_ids):
                old_wiki_page = old_wiki_pages[index]
                guid = migrate_guid_referent(old_wiki_page_id, old_wiki_page)
                repoint_id(guid, old_wiki_page)
            move_comment_target(wiki, old_wiki_page)
            update_comments_viewed_timestamp(node, wiki, old_wiki_page)
    WikiVersion.objects.all().delete()
    WikiPage.objects.all().delete()

def overwrite_created_modified(node_wiki, wiki_page, wiki_version):
    # Before running migration, temporarily replace date, created, and modified fields
    # on the WikiPage and WikiVersion models to NonNaiveDateTimeField(auto_now_add=False, default=datetime.datetime.now())
    if wiki_version.identifier == 1:
        wiki_page.created = node_wiki.created
        wiki_page.modified = node_wiki.modified
        wiki_page.date = node_wiki.date
        wiki_page.save()
    wiki_version.created = node_wiki.created
    wiki_version.modified = node_wiki.modified
    wiki_version.date = node_wiki.date
    wiki_version.save()
    return

def move_comment_target(current_target, desired_target):
    """Move the comment's target from the current target to the desired target"""
    if Comment.objects.filter(root_target=current_target.guids.all()[0]).exists():
        Comment.objects.filter(root_target=current_target.guids.all()[0]).update(root_target=Guid.load(desired_target._id))
        Comment.objects.filter(target=current_target.guids.all()[0]).update(target=Guid.load(desired_target._id))
    return

def update_comments_viewed_timestamp(node, current_wiki_object, desired_wiki_object):
    """Replace the current_wiki_object keys in the comments_viewed_timestamp dict with the desired wiki_object_id """
    for contrib in node.contributors:
        if contrib.comments_viewed_timestamp.get(current_wiki_object._id, None):
            timestamp = contrib.comments_viewed_timestamp[current_wiki_object._id]
            contrib.comments_viewed_timestamp[desired_wiki_object._id] = timestamp
            del contrib.comments_viewed_timestamp[current_wiki_object._id]
            contrib.save()
    return

def migrate_guid_referent(guid_id, desired_referent):
    guid = Guid.load(guid_id)
    guid.referent = desired_referent
    guid.save()
    return guid

def migrate_node_wiki_pages(apps, schema_editor):
    """For every node, loop through all the NodeWikiPages on node.wiki_pages_versions.  Create a WikiPage, and then a WikiVersion corresponding
    to each WikiPage.
    """
    for node in AbstractNode.objects.all():
        logger.info("For node {}".format(node._id))
        for wiki_key, version_list in node.wiki_pages_versions.iteritems():
            logger.info("....For wiki_key {}".format(wiki_key))
            for version in version_list:
                logger.info("........For NodeWikiPage {}".format(version))
                node_wiki = NodeWikiPage.load(version)
                wiki_page, wiki_version = node.create_or_update_node_wiki(node_wiki.page_name, node_wiki.content, Auth(node_wiki.user))
                overwrite_created_modified(node_wiki, wiki_page, wiki_version)
                migrate_guid_referent(node_wiki._id, wiki_page)
            move_comment_target(node_wiki, wiki_page)
            update_comments_viewed_timestamp(node, node_wiki, wiki_page)


class Migration(migrations.Migration):

    dependencies = [
        ('osf', '0076_action_rename'),
    ]

    operations = [
        migrations.RunPython(migrate_node_wiki_pages, reverse_func)
    ]
