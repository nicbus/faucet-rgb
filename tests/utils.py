"""Utilities for tests"""

import os
import shutil
import subprocess
import time

import rgb_lib
from flask import Flask

from faucet_rgb import create_app as create_faucet_app
from faucet_rgb import utils
from faucet_rgb.settings import Config

BITCOIN_ARG = [
    "docker",
    "compose",
    "-f",
    "docker/docker-compose.yml",
    "exec",
    "-T",
    "-u",
    "blits",
    "bitcoind",
    "bitcoin-cli",
    "-regtest",
]
CONSIGNMENT_ENDPOINTS = ["rgbhttpjsonrpc:http://localhost:3000/json-rpc"]
ELECTRUM_URL = "tcp://localhost:50001"
NETWORK = "regtest"
USER_HEADERS = {"x-api-key": Config.API_KEY}
OPERATOR_HEADERS = {"x-api-key": Config.API_KEY_OPERATOR}


def get_test_name():
    """Get the currently running test name."""
    val = os.environ.get("PYTEST_CURRENT_TEST")
    assert val is not None, "must be called from tests"
    return val.split(":")[-1].split(" ")[0]


def get_test_datadir():
    """Get data_dir for the current test."""
    test_data = os.path.join(os.path.abspath(os.curdir), "test_data")
    return os.path.join(test_data, get_test_name())


def fund_address(addr):
    """A sendtoaddress rpc for the bitcoin node."""
    subprocess.run(
        BITCOIN_ARG + ["sendtoaddress", addr, "1"],
        capture_output=True,
        timeout=3000,
        check=True,
    )


def generate(num):
    """A generate command for the bitcoin node."""
    subprocess.run(
        BITCOIN_ARG + ["-generate", f"{num}"],
        capture_output=True,
        timeout=3000,
        check=True,
    )
    # necessary for the wallet (more accurately the electrum) to
    # notice about the UTXO after confirmation
    time.sleep(2)


def _get_user_wallet(data_dir):
    bitcoin_network = getattr(rgb_lib.BitcoinNetwork, NETWORK.upper())
    keys = rgb_lib.generate_keys(bitcoin_network)
    online, wallet = utils.wallet.init_wallet(ELECTRUM_URL, keys.xpub,
                                              keys.mnemonic, data_dir, NETWORK)
    return {"wallet": wallet, "xpub": keys.xpub, "online": online}


def prepare_user_wallets(app: Flask, num=1):
    """Prepare user wallets with UTXO.

    Its data will be put under `test_data/<function_name>/user<n>`.
    """
    users = []
    for i in range(0, num):
        data_dir = os.path.join(app.config["DATA_DIR"], f"user{i}")
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir)
        os.mkdir(data_dir)
        user = _get_user_wallet(data_dir)
        users.append(user)

    for user in users:
        addr = user["wallet"].get_address()
        fund_address(addr)

    generate(1)

    return users


def check_requests_left(app, xpub, group_to_requests_left):
    """Check requests_left for each asset group.

    requests_left is the one returned from /receive/config endpoint.

    Args:
        app (Flask): Flask app
        xpub (str): xpub for the user.
        group_to_requests_left (dict): (asset group) => (expected requests_left)
    """
    client = app.test_client()
    resp = client.get(f"/receive/config/{xpub}", headers=USER_HEADERS)
    assert resp.status_code == 200
    for group, expected in group_to_requests_left.items():
        actual = resp.json["groups"][group]["requests_left"]
        assert expected == actual


def check_receive_asset(app,
                        user,
                        group_to_request,
                        expected_status_code=200,
                        expected_asset_id_list=None):
    """Check the /receive/asset endpoint."""
    xpub = user["xpub"]
    wallet = user["wallet"]
    _ = wallet.create_utxos(user["online"], True, 1, None,
                            app.config["FEE_RATE"])
    blind_data = wallet.blind(
        None,
        None,
        None,
        consignment_endpoints=app.config["CONSIGNMENT_ENDPOINTS"])
    group_query = "" if group_to_request is None else f"?asset_group={group_to_request}"
    client = app.test_client()
    resp = client.get(
        f"/receive/asset/{xpub}/{blind_data.blinded_utxo}{group_query}",
        headers=USER_HEADERS,
    )
    assert resp.status_code == expected_status_code
    if resp.status_code == 200 and expected_asset_id_list is not None:
        assert resp.json["asset"]["asset_id"] in expected_asset_id_list


def _prepare_utxos(app):
    online, wallet = utils.wallet.init_wallet(
        app.config["ELECTRUM_URL"],
        app.config["XPUB"],
        app.config["MNEMONIC"],
        app.config["DATA_DIR"],
        app.config["NETWORK"],
    )
    wallet.refresh(online, None, [])
    addr = wallet.get_address()
    fund_address(addr)
    generate(1)
    app.config["WALLET"] = wallet
    app.config["ONLINE"] = online
    return app


# purpose of this global variable is to give a unique value to the asset metadata
# each tests in pytest are run independently and it does not share global variable
# so having global here is not a problem
_ASSET_COUNT = 0


def _issue_asset(app):
    """Issue an asset with faucet-rgb's wallet.

    Returns a tuple of 2, each item is id for the issued asset.
    """

    global _ASSET_COUNT  # pylint: disable=global-statement
    _ASSET_COUNT += 1
    wallet = app.config["WALLET"]
    online = app.config["ONLINE"]
    wallet.create_utxos(online, True, None, None, app.config["FEE_RATE"])
    rgb_20 = wallet.issue_asset_rgb20(
        online,
        ticker=f"TFT{_ASSET_COUNT}",
        name=f"test asset for rgb20 ({_ASSET_COUNT})",
        precision=0,
        amounts=[1000, 1000],
    )
    rgb_121 = wallet.issue_asset_rgb121(
        online,
        name=f"asset-rgb121-{_ASSET_COUNT}",
        description=f"test asset for rgb121 ({_ASSET_COUNT})",
        precision=0,
        amounts=[1000, 1000],
        parent_id=None,
        file_path=None,
    )

    return rgb_20.asset_id, rgb_121.asset_id


def prepare_assets(app, group_name="group_1"):
    """Issue (rgb20, rgb121) asset pair and set the config for the app.

    Issue 1000 units for each asset, and the amount to send users is 100.

    Args:
        app (Flask): Flask app to configure.
        group_name (str): Name for the asset group ("group_1" by default).
    """

    id1, id2 = _issue_asset(app)
    asset_list = [{"asset_id": a, "amount": 100} for a in [id1, id2]]
    app.config["ASSETS"][group_name] = {
        "label": f"{group_name} for the test",
        "assets": asset_list,
    }

    return app


def _get_test_app(name, custom_app_prep):
    app = Flask(name, instance_relative_config=True)

    app.config.from_object(Config)
    app.config["NAME"] = name

    app.config["DATA_DIR"] = get_test_datadir()

    # settings which usually user defines by themselves
    app.config["CONSIGNMENT_ENDPOINTS"] = CONSIGNMENT_ENDPOINTS
    app.config["ELECTRUM_URL"] = ELECTRUM_URL
    app.config["NETWORK"] = NETWORK

    bitcoin_network = getattr(rgb_lib.BitcoinNetwork, NETWORK.upper())
    keys = rgb_lib.generate_keys(bitcoin_network)

    app.config["MNEMONIC"] = keys.mnemonic
    app.config["XPUB"] = keys.xpub

    # prepare utxos and assets
    app = _prepare_utxos(app)
    app.config["ASSETS"] = {}
    app = custom_app_prep(app)

    return app


def create_test_app(name, custom_app_prep):
    """Returns a configured Flask app for the test.

    Args:
        name (str): Name of the app.
        custom_app_prep (function): Function which will be run after the app
            initialization. It must issue assets and set config for the app.
            Takes an app as an argument and returns the updated app.
    """

    def _get_app():
        return _get_test_app(name, custom_app_prep)

    return create_faucet_app(_get_app, False)
