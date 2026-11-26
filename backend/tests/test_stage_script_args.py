import argparse

import pytest

from stage_script_args import add_project_argument, uuid_arg


def test_uuid_arg_normalises_and_rejects():
    assert uuid_arg("A5482B08-1C59-4BF0-9EB2-7EA41EB26F9C") == "a5482b08-1c59-4bf0-9eb2-7ea41eb26f9c"
    with pytest.raises(argparse.ArgumentTypeError):
        uuid_arg("not-a-project")


def test_project_argument_is_required():
    parser = argparse.ArgumentParser()
    add_project_argument(parser)
    with pytest.raises(SystemExit):
        parser.parse_args([])
