import logging
import os
import mimetypes
from requests import Session
from io import SEEK_END
from time import sleep
from hashlib import md5


class AnchorSession:
    BASE_URL = 'https://anchor.fm'
    CSRF_URL = 'api/csrf'
    LOGIN_URL = 'api/login'
    AUDIO_LIBRARY = 'api/sourceaudio/audiolibrary'
    SIGNED_URL = 'api/proxy/v3/upload/signed_url'
    PROCESS_AUDIO = 'api/proxy/v3/upload/{}/process_audio'
    UPLOAD_INFO = 'api/proxy/v3/upload/{}'
    CREATE_EPISODE = 'api/podcastepisode'

    def __init__(self, username, password):
        self._logger = logging.getLogger(__name__)
        self._session = Session()
        self._login(username, password)

    def _csrf(self):
        self._logger.info(f'Getting CSRF token')
        url = f'{self.BASE_URL}/{self.CSRF_URL}'
        r = self._session.get(url)
        if r.status_code < 200 or r.status_code >= 300:
            raise Exception(f'Failed to get CSRF token, status code: {r.status_code}')
        token = r.json()['csrfToken']
        self._logger.info(f'CSRF token for current session: {token}')
        return token

    def _login(self, username, password):
        csrf_token = self._csrf()

        self._logger.info(f'Logging in as {username}')
        url = f'{self.BASE_URL}/{self.LOGIN_URL}'
        payload = {"betaCode": None, "email": username, "password": password, "_csrf": csrf_token}
        r = self._session.post(url, json=payload)
        if r.status_code < 200 or r.status_code >= 300:
            raise Exception(f'Failed to log in, status code: {r.status_code}')
        self._logger.info(f'Logged in as {username}')

    def list_uploaded_files(self):
        items = self._get_audio_library()
        return [i['caption'] for i in items]

    def save_file_as_draft(self, path):
        file_basename = os.path.basename(path)
        file_name, file_extension = os.path.splitext(file_basename)
        mime_type, _ = mimetypes.guess_type(path)

        if mime_type is None:
            raise Exception(f'Cannot determine MIME type for {path}')

        if not mime_type.startswith('audio/'):
            raise Exception(f'Invalid MIME type {mime_type}')

        with open(path, 'rb') as audio_stream:
            self._logger.info(f'"{path}" opened for reading')
            safe_file_name = md5(file_basename.encode('utf-8')).hexdigest() + f'{file_extension}'

            self._logger.info(f'Generating upload location for "{file_name}" (safe file name: {safe_file_name})')
            upload_url, request_uuid = self._get_upload_location_info(mime_type, safe_file_name)
            self._logger.info(f'Upload URL for {file_name}: {upload_url}, request UUID: {request_uuid}')

            self._logger.info(f'Uploading "{file_name}" audio stream')
            self._upload_audio_stream(upload_url, audio_stream, mime_type)

            self._logger.info(f'Initiating processing of "{file_name}" audio stream on remote server')
            processing_request_uuid = self._process_audio_stream(request_uuid, file_name)

            self._logger.info(f'Waiting for "{file_name}" processing to finish')
            audio_data = self._finish_audio_processing_status(processing_request_uuid)

            self._logger.info(f'"{file_name}" processing finished, creating episode draft')
            self._create_episode_draft(audio_data, file_name)
            self._logger.info(f'"{file_name}" episode draft created\n\n')

    def _get_audio_library(self):
        self._logger.info('Fetching audio library info')
        url = f'{self.BASE_URL}/{self.AUDIO_LIBRARY}'
        r = self._session.get(url)
        if r.status_code < 200 or r.status_code >= 300:
            raise Exception(f'Failed to get audio library, status code: {r.status_code}')
        items = r.json()["audios"]
        self._logger.info(f'Found {len(items)} items in audio library')
        return items

    def _get_upload_location_info(self, mime_type, safe_filename):
        self._logger.info(f'Getting upload location for {safe_filename}')
        url = f'{self.BASE_URL}/{self.SIGNED_URL}'
        params = {'filename': safe_filename, 'type': mime_type}
        r = self._session.get(url, params=params)
        if r.status_code < 200 or r.status_code >= 300:
            raise Exception(f'Failed to get signed URL for audio file upload, status code: {r.status_code}')
        response = r.json()
        upload_url = response['signedUrl']
        request_uuid = response['requestUuid']

        headers = {'Access-Control-Request-Method': 'PUT', 'Access-Control-Request-Headers': 'content-type',
                   'Origin': self.BASE_URL}
        r = self._session.options(upload_url, headers=headers)
        if r.status_code < 200 or r.status_code >= 300:
            raise Exception(f'Failed to get signed URL OPTIONS verbs, status code: {r.status_code}')

        if 'PUT' not in r.headers['access-control-allow-methods']:
            raise Exception(f'PUT method not allowed')

        return upload_url, request_uuid

    def _upload_audio_stream(self, upload_url, audio_stream, mime_type):
        audio_stream.seek(0, SEEK_END)
        content_length = audio_stream.tell()
        audio_stream.seek(0)

        headers = {'content-type': mime_type, 'content-length': str(content_length)}
        r = self._session.put(upload_url, audio_stream, headers=headers)
        if r.status_code < 200 or r.status_code >= 300:
            raise Exception(f'Failed to upload audio stream, status code: {r.status_code}')

    def _process_audio_stream(self, request_uuid, title):
        url = self.BASE_URL + '/' + self.PROCESS_AUDIO.format(request_uuid)
        payload = {'audioType': 'default', 'caption': title, 'isExtractedFromVideo': False, 'origin': 'podcast:upload'}
        r = self._session.post(url, json=payload)
        if r.status_code < 200 or r.status_code >= 300:
            raise Exception(f'Failed to request audio stream processing, status code: {r.status_code}')
        response = r.json()
        return response['requestUuid']

    def _finish_audio_processing_status(self, request_uuid):
        data = None
        while True:
            url = self.BASE_URL + '/' + self.UPLOAD_INFO.format(request_uuid)
            r = self._session.get(url)
            if r.status_code < 200 or r.status_code >= 300:
                raise Exception(f'Failed to request audio stream processing, status code: {r.status_code}')

            response = r.json()
            state = response['request']['state']
            data = response['data']

            if state == 'processed':
                break

            if state == 'failed':
                self._logger.error(f'Processing state: failed')
                # TODO: uploaded file should probably be deleted, but at this point we don't know the 'audioId'

            if state == 'uploaded':
                sleep(10)
                continue

            raise Exception(f'Unhandled audio stream state: {state}')

        audio_id = data['audioId']

        while True:
            item = next((i for i in self._get_audio_library() if i['audioId'] == audio_id), None)
            if item is None:
                self._logger.error(f'Could not find library item, audioId: {audio_id}')
                break

            status = item['audioTransformationStatus']
            if status != 'finished':
                self._logger.info(
                    f'Audio transformation status: {status}, waiting for transformation process to finish')
                sleep(10)
                continue

            self._logger.info(f'Audio transformation process finished')
            return item

    def _create_episode_draft(self, audio_data, title):
        url = f'{self.BASE_URL}/{self.CREATE_EPISODE}'
        payload = {
            "episodeAudios": [audio_data],
            "hourOffset": -1,
            "isDraft": True,
            "publishOn": None,
            "title": title,
            "description": ''
        }
        r = self._session.post(url, json=payload)
        if r.status_code < 200 or r.status_code >= 300:
            raise Exception(f'Failed create draft episode, status code: {r.status_code}')
