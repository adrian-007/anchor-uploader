import json
import logging
import argparse
import os
import mimetypes
from anchor_session import AnchorSession
from types import SimpleNamespace


class AnchorUploader:
    def __init__(self):
        self._logger = logging.getLogger('anchor_uploader')
        self._logger.setLevel(logging.DEBUG)

        self._configure_app()

    def run(self):
        for profile in self._config.profiles:
            try:
                anchor_session = AnchorSession(profile.anchorUsername, profile.anchorPassword)
                audio_file_paths = self._list_audio_files(profile.rootDir)
                uploaded_audio_files = anchor_session.list_uploaded_files()
                audio_files_to_process = self._find_missing_audio_streams(audio_file_paths, uploaded_audio_files)

                for audio_file_path in audio_files_to_process:
                    try:
                        anchor_session.save_file_as_draft(audio_file_path)
                    except Exception as e:
                        self._logger.error(f'Exception while processing audio "{audio_file_path}": {e}')

            except Exception as e:
                self._logger.error(f'Exception while processing item: {e}')

    def _configure_app(self):
        parser = argparse.ArgumentParser()
        parser.add_argument('-c', '--config')
        args = parser.parse_args()

        if args.config is None:
            raise Exception('Config not specified')

        try:
            with open(args.config, "r") as config_file:
                self._config = json.load(config_file, object_hook=lambda d: SimpleNamespace(**d))
        except:
            raise Exception(f'Cannot read configuration file {args.config}')

    @staticmethod
    def _list_audio_files(root_dir):
        files = []
        for (root, _, file_paths) in os.walk(root_dir):
            for file_path in file_paths:
                file_path = os.path.join(root, file_path)
                mime_type, _ = mimetypes.guess_type(file_path)
                if mime_type.startswith('audio/'):
                    files.append(file_path)

        return files

    def _find_missing_audio_streams(self, audio_file_paths, uploaded_audio_files):
        results = []
        for audio_file_path in audio_file_paths:
            audio_file_name, _ = os.path.splitext(os.path.basename(audio_file_path))
            has_matching_audio = False
            for uploaded_audio_file in uploaded_audio_files:
                if uploaded_audio_file.find(audio_file_name) >= 0:
                    has_matching_audio = True
                    self._logger.info(f'"{audio_file_path}" is already uploaded as "{uploaded_audio_file}" - skipping upload')
                    break
            if has_matching_audio is False:
                results.append(audio_file_path)
        return results


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    app = AnchorUploader()
    app.run()
