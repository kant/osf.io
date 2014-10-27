#!/usr/bin/env python
# encoding: utf-8

import mock
from nose.tools import *  # noqa

from tests.base import OsfTestCase
from tests.factories import ProjectFactory

import datetime
import urlparse

import markupsafe
from cloudstorm import sign

from website.addons.osfstorage.tests import factories

from website.addons.osfstorage import model
from website.addons.osfstorage import utils
from website.addons.osfstorage import settings


class TestHGridUtils(OsfTestCase):

    def setUp(self):
        super(TestHGridUtils, self).setUp()
        self.project = ProjectFactory()

    def test_build_urls_folder(self):
        file_tree = model.FileTree(
            path='god/save/the/queen',
            node_settings=self.project.get_addon('osfstorage'),
        )
        expected = {
            'upload': '/api/v1/project/{0}/osfstorage/files/{1}/'.format(
                self.project._id,
                file_tree.path,
            ),
            'fetch': '/api/v1/project/{0}/osfstorage/files/{1}/'.format(
                self.project._id,
                file_tree.path,
            ),
        }
        urls = utils.build_hgrid_urls(file_tree, self.project)
        assert_equal(urls, expected)


    def test_build_urls_file(self):
        file_record = model.FileRecord(
            path='kind/of/magic.mp3',
            node_settings=self.project.get_addon('osfstorage'),
        )
        expected = {
            'view': '/project/{0}/osfstorage/files/{1}/'.format(
                self.project._id,
                file_record.path,
            ),
            'download': '/project/{0}/osfstorage/files/{1}/download/'.format(
                self.project._id,
                file_record.path,
            ),
            'delete': '/api/v1/project/{0}/osfstorage/files/{1}/'.format(
                self.project._id,
                file_record.path,
            ),
        }
        urls = utils.build_hgrid_urls(file_record, self.project)
        assert_equal(urls, expected)

    def test_serialize_metadata_folder(self):
        file_tree = model.FileTree(
            path='god/save/the/queen',
            node_settings=self.project.get_addon('osfstorage'),
        )
        permissions = {'edit': False, 'view': True}
        serialized = utils.serialize_metadata_hgrid(
            file_tree,
            self.project,
            permissions,
        )
        assert_equal(serialized['addon'], 'osfstorage')
        assert_equal(serialized['path'], 'god/save/the/queen')
        assert_equal(serialized['name'], 'queen')
        assert_equal(serialized['ext'], '')
        assert_equal(serialized['kind'], 'folder')
        assert_equal(
            serialized['urls'],
            utils.build_hgrid_urls(file_tree, self.project),
        )
        assert_equal(serialized['permissions'], permissions)

    def test_serialize_metadata_file(self):
        file_record = model.FileRecord(
            path='kind/of/<strong>magic.mp3',
            node_settings=self.project.get_addon('osfstorage'),
        )
        permissions = {'edit': False, 'view': True}
        serialized = utils.serialize_metadata_hgrid(
            file_record,
            self.project,
            permissions,
        )
        assert_equal(serialized['addon'], 'osfstorage')
        assert_equal(
            serialized['path'],
            markupsafe.escape('kind/of/<strong>magic.mp3'),
        )
        assert_equal(
            serialized['name'],
            markupsafe.escape('<strong>magic.mp3'),
        )
        assert_equal(serialized['ext'], '.mp3')
        assert_equal(serialized['kind'], 'item')
        assert_equal(
            serialized['urls'],
            utils.build_hgrid_urls(file_record, self.project),
        )
        assert_equal(serialized['permissions'], permissions)

    def test_get_item_kind_folder(self):
        assert_equal(
            utils.get_item_kind(model.FileTree()),
            'folder',
        )

    def test_get_item_kind_file(self):
        assert_equal(
            utils.get_item_kind(model.FileRecord()),
            'item',
        )

    def test_get_item_kind_invalid(self):
        with assert_raises(TypeError):
            utils.get_item_kind('pizza')


@mock.patch('website.addons.osfstorage.utils.requests.request')
def test_make_signed_request(mock_request):
    expected = {'status': 'delicious'}
    mock_request.return_value.json.return_value = expected
    payload = {'peppers': True, 'sausage': True}
    signature, body = sign.build_hook_body(utils.url_signer, payload)
    resp = utils.make_signed_request(
        'POST',
        'http://frozen.pizza.com/',
        utils.url_signer,
        payload,
    )
    mock_request.assert_called_with(
        'POST',
        'http://frozen.pizza.com/',
        data=body,
        headers={
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            settings.SIGNATURE_HEADER_KEY: signature,
        },
        **settings.SIGNED_REQUEST_KWARGS
    )
    assert_equal(resp, expected)


class TestGetDownloadUrl(OsfTestCase):

    def setUp(self):
        super(TestGetDownloadUrl, self).setUp()
        self.project = ProjectFactory()
        self.node_settings = self.project.get_addon('osfstorage')
        self.path = 'frozen/pizza/reviews.gif'
        self.record = model.FileRecord.get_or_create(self.path, self.node_settings)
        for _ in range(3):
            version = factories.FileVersionFactory()
            self.record.versions.append(version)
        self.record.save()

    def test_get_filename_latest_version(self):
        filename = utils.get_filename(3, self.record.versions[-1], self.record)
        assert_equal(filename, self.record.name)

    def test_get_filename_not_latest_version(self):
        filename = utils.get_filename(2, self.record.versions[-2], self.record)
        expected = ''.join([
            'reviews-',
            self.record.versions[-2].date_modified.isoformat(),
            '.gif',
        ])
        assert_equal(filename, expected)

    @mock.patch('website.addons.osfstorage.utils.make_signed_request')
    def test_get_download_url(self, mock_request):
        url = 'http://deacon.queen.com/'
        mock_request.return_value = {'url': url}
        ret = utils.get_download_url(3, self.record.versions[-1], self.record)
        request_url = urlparse.urljoin(
            utils.choose_upload_url(),
            'urls/download/',
        )
        payload = {
            'location': self.record.versions[-1].location,
            'filename': utils.get_filename(3, self.record.versions[-1], self.record),
        }
        mock_request.assert_called_with(
            'POST',
            request_url,
            signer=utils.url_signer,
            payload=payload,
        )
        assert_equal(ret, url)


class TestSerializeRevision(OsfTestCase):

    def setUp(self):
        super(TestSerializeRevision, self).setUp()
        self.project = ProjectFactory()
        self.user = self.project.creator
        self.node_settings = self.project.get_addon('osfstorage')
        self.path = 'kind/of/magic.mp3'
        self.record = model.FileRecord.get_or_create(self.path, self.node_settings)
        self.versions = [
             factories.FileVersionFactory(
                creator=self.user,
                date_modified=datetime.datetime.utcnow(),
            )
            for _ in range(3)
        ]
        self.record.versions = self.versions
        self.record.save()

    @mock.patch('website.addons.osfstorage.utils.get_download_url')
    def download_version(self, version, mock_get_url):
        self.app.get(
            self.project.web_url_for(
                'osf_storage_download_file',
                path=self.path,
                version=version,
            ),
            auth=self.user.auth,
        )

    def test_serialize_revision(self):
        self.download_version(1)
        self.download_version(1)
        self.download_version(3)
        expected = {
            'index': 1,
            'user': {
                'name': self.user.fullname,
                'url': self.user.url,
            },
            'date': self.versions[0].date_modified.isoformat(),
            'downloads': 2,
            'urls': {
                'view': self.project.web_url_for(
                    'osf_storage_view_file',
                    path=self.path,
                    version=1,
                ),
                'download': self.project.web_url_for(
                    'osf_storage_download_file',
                    path=self.path,
                    version=1,
                ),
            },
        }
        observed = utils.serialize_revision(
            self.project,
            self.record,
            self.versions[0],
            1,
        )
        assert_equal(expected, observed)
