import os
from types import SimpleNamespace

import pytest

import mylar
from mylar import helpers
from mylar.downloaders.airdcpp import AirDCPP
from mylar.queues import postprocess
from mylar.PostProcessor import PostProcessor


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, get_responses=None, post_response=None):
        self.get_responses = list(get_responses or [])
        self.post_response = post_response
        self.post_calls = []

    def get(self, *args, **kwargs):
        return self.get_responses.pop(0)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_response


@pytest.fixture
def airdcpp_config(monkeypatch, tmp_path):
    config = SimpleNamespace(
        AIRDCPP_DOWNLOAD_DIR=str(tmp_path),
        DDL_LOCATION=str(tmp_path),
    )
    monkeypatch.setattr(mylar, "CONFIG", config)
    monkeypatch.setattr(mylar, "LOG_LEVEL", 0)
    return config


@pytest.mark.unit
def test_bundle_filename_handles_windows_target():
    bundle_data = {"target": r"D:\AirDC\Downloads\Renamed Comic 001.cbz"}

    assert AirDCPP._bundle_filename(bundle_data) == "Renamed Comic 001.cbz"


@pytest.mark.unit
def test_check_download_complete_uses_final_bundle_name(airdcpp_config):
    final_name = "Renamed Comic 001.cbz"
    final_path = os.path.join(airdcpp_config.AIRDCPP_DOWNLOAD_DIR, final_name)
    with open(final_path, "wb") as comic_file:
        comic_file.write(b"comic")

    downloader = AirDCPP.__new__(AirDCPP)
    downloader.api_url = "http://airdc.test/api/v1"
    downloader.headers = {}
    downloader.session = FakeSession(
        get_responses=[
            FakeResponse(
                {
                    "name": "Original Search Result.cbz",
                    "target": r"D:\AirDC\Downloads\Original Search Result.cbz",
                    "status": {"completed": False},
                    "downloaded_bytes": 2,
                    "size": 5,
                }
            ),
            FakeResponse(
                {
                    "name": final_name,
                    "target": rf"D:\AirDC\Downloads\{final_name}",
                    "status": {"completed": True},
                    "downloaded_bytes": 5,
                    "size": 5,
                }
            ),
        ]
    )

    result = downloader.check_download_complete(
        bundle_id=123,
        max_wait=1,
        check_interval=0,
    )

    assert result == {"filename": final_name, "path": final_path}


@pytest.mark.unit
def test_download_sends_no_target_and_returns_bundle_result(
    monkeypatch,
    airdcpp_config,
):
    final_name = "Final Bundle Name.cbz"
    final_path = os.path.join(airdcpp_config.AIRDCPP_DOWNLOAD_DIR, final_name)
    downloader = AirDCPP.__new__(AirDCPP)
    downloader.api_url = "http://airdc.test/api/v1"
    downloader.headers = {}
    downloader.current_search_instance_id = 77
    downloader.session = FakeSession(
        post_response=FakeResponse({"bundle_info": {"id": 123}})
    )

    checked_bundle_ids = []

    def complete(bundle_id):
        checked_bundle_ids.append(bundle_id)
        return {"filename": final_name, "path": final_path}

    monkeypatch.setattr(downloader, "check_download_complete", complete)

    result = downloader.download(
        link="TTH",
        filename="Original Search Name.cbz",
        id="issue",
        issueid="456",
        search_instance_id=77,
    )

    assert checked_bundle_ids == [123]
    assert downloader.session.post_calls[0][1]["json"] == {"priority": 4}
    assert result == {
        "success": True,
        "filename": final_name,
        "path": final_path,
    }


@pytest.mark.unit
def test_file_ops_can_override_global_move_with_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mylar,
        "CONFIG",
        SimpleNamespace(FILE_OPTS="move"),
    )
    source = tmp_path / "source.cbz"
    destination = tmp_path / "destination.cbz"
    source.write_bytes(b"comic")

    assert helpers.file_ops(
        str(source),
        str(destination),
        file_op="copy",
    )
    assert source.exists()
    assert destination.read_bytes() == b"comic"


@pytest.mark.unit
def test_airdc_force_copy_does_not_change_default_postprocessor_semantics(
    monkeypatch,
):
    monkeypatch.setattr(
        mylar,
        "CONFIG",
        SimpleNamespace(FILE_OPTS="move", IGNORE_SEARCH_WORDS=[]),
    )
    monkeypatch.setattr(mylar, "APILOCK", False)

    default_processor = PostProcessor("Comic.cbz", "/downloads")
    airdcpp_processor = PostProcessor(
        "Comic.cbz",
        "/downloads",
        force_copy=True,
    )

    assert default_processor.file_opts == "move"
    assert default_processor.file_op_override is None
    assert airdcpp_processor.file_opts == "copy"
    assert airdcpp_processor.file_op_override == "copy"


@pytest.mark.unit
def test_postprocess_queue_passes_airdc_copy_override(monkeypatch):
    captured_args = []

    class FakeProcess:
        def __init__(self, *args):
            captured_args.append(args)

        def post_process(self):
            return None

    class FakeQueue:
        def __init__(self):
            self.items = [
                {
                    "nzb_name": "Comic.cbz",
                    "nzb_folder": "/downloads",
                    "issueid": "123",
                    "comicid": "456",
                    "apicall": True,
                    "force_copy": True,
                },
                "exit",
            ]

        def qsize(self):
            return len(self.items)

        def get(self, _block):
            return self.items.pop(0)

    monkeypatch.setattr(mylar, "APILOCK", False)
    monkeypatch.setattr(mylar, "LOG_LEVEL", 0)
    monkeypatch.setattr(postprocess.process, "Process", FakeProcess)
    monkeypatch.setattr(postprocess.time, "sleep", lambda _: None)

    postprocess.postprocess_main(FakeQueue())

    assert captured_args[0][-1] is True
